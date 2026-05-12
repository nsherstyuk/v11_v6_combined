"""
V11 Live Trading Script — Multi-Strategy Entry Point (Phase 7).

Connects to IBKR, streams prices for configured instruments,
and runs the MultiStrategyRunner with three strategies:
    - EURUSD: Darvas Breakout + SMA(50)
    - EURUSD: 4H Level Retest
    - XAUUSD: V6 ORB (tick-driven)

Usage:
    python -m v11.live.run_live --dry-run
    python -m v11.live.run_live --port 4002
    python -m v11.live.run_live --instruments EURUSD XAUUSD
"""
from __future__ import annotations

import asyncio
import argparse
import logging
import os
import signal as signal_mod
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Python 3.14 compatibility ────────────────────────────────────────────────
# Python 3.14 changed asyncio.wait_for to use asyncio.timeout() internally,
# which requires being inside a running task. ib_insync calls wait_for from
# a sync context via nest_asyncio, which doesn't set current_task properly.
# Patch wait_for to avoid asyncio.timeout() when called outside a task.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

if sys.version_info >= (3, 14):
    _original_wait_for = asyncio.wait_for

    async def _compat_wait_for(fut, timeout, **kwargs):
        if timeout is None:
            return await fut
        fut = asyncio.ensure_future(fut)
        loop = asyncio.get_event_loop()
        timed_out = False

        def _on_timeout():
            nonlocal timed_out
            timed_out = True
            fut.cancel()

        handle = loop.call_later(timeout, _on_timeout)
        try:
            return await fut
        except asyncio.CancelledError:
            if timed_out:
                raise asyncio.TimeoutError()
            raise
        finally:
            handle.cancel()

    asyncio.wait_for = _compat_wait_for

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

import json
import pandas as pd
from dotenv import load_dotenv

# Project root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from v11.config.live_config import (
    LiveConfig, XAUUSD_INSTRUMENT, EURUSD_INSTRUMENT,
    InstrumentConfig,
)
from v11.config.strategy_config import (
    EURUSD_CONFIG, StrategyConfig,
)
from v11.core.types import Bar
from v11.execution.ibkr_connection import IBKRConnection
from v11.live.multi_strategy_runner import MultiStrategyRunner
from v11.live.risk_manager import RiskManager
from v11.llm.grok_filter import GrokFilter
from v11.llm.passthrough_filter import PassthroughFilter
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig


# ── Logging ──────────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("v11_live")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    log.addHandler(sh)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fh = logging.FileHandler(str(log_dir / f"v11_live_{ts}.log"))
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)

    for lib in ("ib_insync.wrapper", "ib_insync.client", "ib_insync"):
        logging.getLogger(lib).setLevel(logging.CRITICAL)

    return log


# ── V6 ORB Config for XAUUSD ────────────────────────────────────────────────
# Parameters from v6_orb_refactor/live/example_live_xauusd.py

XAUUSD_ORB_CONFIG = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0,
    range_end_hour=6,
    trade_start_hour=8,
    trade_end_hour=16,
    skip_weekdays=(2,),          # skip Wednesday
    rr_ratio=2.5,
    min_range_size=1.0,
    max_range_size=15.0,
    velocity_filter_enabled=False,  # Extended backtest 2026-04-16: velocity=OFF beats ON OOS (+0.057 AvgR)
    velocity_lookback_minutes=3,
    velocity_threshold=168.0,    # Retained for reference; filter is disabled
    gap_filter_enabled=True,     # V6-validated: +4.2pp WR, skip low-volatility pre-market days
    qty=1,
    point_value=1.0,
    price_decimals=2,
)


# ── Instrument/Strategy Mapping ─────────────────────────────────────────────

# Which instruments are available and what strategies run on each
INSTRUMENT_MAP: dict[str, InstrumentConfig] = {
    "EURUSD": EURUSD_INSTRUMENT,
    "XAUUSD": XAUUSD_INSTRUMENT,
}


# ── Main Trader ──────────────────────────────────────────────────────────────

class V11LiveTrader:
    """Multi-strategy live trader using MultiStrategyRunner.

    Wires three strategies across two instruments:
        EURUSD: Darvas Breakout + 4H Level Retest (shared TradeManager)
        XAUUSD: V6 ORB (own execution engine via adapter)
    """

    def __init__(self, live_cfg: LiveConfig, log: logging.Logger,
                 use_llm: bool = True):
        self.live_cfg = live_cfg
        self.log = log
        self._shutdown = False
        self._current_trading_date: str = ""  # for daily reset detection
        self._last_price_time: dict[str, float] = {}  # pair -> epoch of last price update
        self._session_reset_done: bool = False  # 5 PM ET broker session reset guard

        # IBKR connection (shared)
        self.conn = IBKRConnection(
            host=live_cfg.ibkr_host,
            port=live_cfg.ibkr_port,
            client_id=live_cfg.ibkr_client_id,
            log=log,
        )

        # LLM filter (shared)
        if use_llm:
            load_dotenv(ROOT / ".env")
            api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY", "")
            if not api_key:
                raise ValueError(
                    "No API key found. Set XAI_API_KEY or GROK_API_KEY in .env")
            grok_log_dir = ROOT / live_cfg.grok_log_dir
            self.llm_filter = GrokFilter(
                api_key=api_key,
                model=live_cfg.llm_model,
                base_url=live_cfg.llm_base_url,
                timeout=live_cfg.llm_timeout_seconds,
                signal_timeout=live_cfg.signal_llm_timeout_seconds,
                log_dir=str(grok_log_dir),
            )
            log.info("LLM filter: Grok (active)")
        else:
            self.llm_filter = PassthroughFilter(rr_ratio=2.0)
            log.info("LLM filter: DISABLED (mechanical auto-approve)")

        # Risk manager (portfolio-level, across all strategies)
        self.risk_manager = RiskManager(
            max_daily_loss=live_cfg.max_daily_loss,
            max_daily_trades_per_strategy=live_cfg.max_daily_trades,
            max_concurrent_positions=live_cfg.max_concurrent_positions,
            log=log,
            max_daily_loss_per_strategy=live_cfg.max_daily_loss_per_strategy,
        )

        # Multi-strategy runner
        trade_log_dir = ROOT / "v11" / "live" / "trades"
        self.runner = MultiStrategyRunner(
            conn=self.conn,
            llm_filter=self.llm_filter,
            live_config=live_cfg,
            risk_manager=self.risk_manager,
            log=log,
            trade_log_dir=str(trade_log_dir),
        )

        # Track which instruments are active
        self._active_pairs: list[str] = []

        # Tick logging for replay data capture
        self._tick_logger = None
        if live_cfg.tick_logging:
            from v11.replay.tick_logger import TickLogger
            tick_log_dir = ROOT / str(live_cfg.tick_log_dir)
            self._tick_logger = TickLogger(base_dir=tick_log_dir)
            log.info(f"Tick logging enabled -> {tick_log_dir}")

    def _wire_strategies(self) -> None:
        """Add strategies to the runner based on configured instruments."""
        active_pairs = {i.pair_name for i in self.live_cfg.instruments}

        if "EURUSD" in active_pairs and self.live_cfg.darvas_enabled:
            # Strategy A: Darvas Breakout + SMA(50)
            self.runner.add_darvas_strategy(
                strategy_config=EURUSD_CONFIG,
                inst_config=EURUSD_INSTRUMENT,
            )
            # Strategy B: 4H Level Retest
            self.runner.add_level_retest_strategy(
                strategy_config=EURUSD_CONFIG,
                inst_config=EURUSD_INSTRUMENT,
            )
            self._active_pairs.append("EURUSD")

        if "XAUUSD" in active_pairs:
            # Strategy C: V6 ORB (tick-driven)
            state_dir = str(ROOT / "v11" / "live" / "state")
            self.runner.add_orb_strategy(
                v6_config=XAUUSD_ORB_CONFIG,
                inst_config=XAUUSD_INSTRUMENT,
                state_dir=state_dir,
            )
            self._active_pairs.append("XAUUSD")

    def _seed_historical(self) -> None:
        """Load historical bars for all instruments with active feeds."""
        for pair in self.runner.get_feed_pairs():
            self.log.info(f"Seeding {pair} buffer...")
            df = self.conn.fetch_historical_bars(
                pair, duration="5 D", bar_size="1 min")
            if df.empty:
                self.log.warning(f"{pair}: No historical bars")
                continue
            bars = []
            for _, row in df.iterrows():
                ts = pd.Timestamp(row['date'])
                if ts.tzinfo is None:
                    ts = ts.tz_localize('UTC')
                vol = row.get('volume', 0)
                bv = vol / 2 if vol > 0 else 0.0
                sv = vol / 2 if vol > 0 else 0.0
                tc = int(vol) if vol > 0 else 0
                bars.append(Bar(
                    timestamp=ts.to_pydatetime(),
                    open=row['open'], high=row['high'],
                    low=row['low'], close=row['close'],
                    buy_volume=bv, sell_volume=sv, tick_count=tc,
                ))
            self.runner.seed_historical(pair, bars)

        # Fetch daily + 4h bars for ORB LLM context
        self._refresh_llm_context()

    def _refresh_llm_context(self) -> None:
        """Refresh daily and 4-hour bars for ORB LLM context.

        Called at startup and on each date change (UTC midnight) so the
        LLM always has up-to-date trend / regime information.
        Works for any instrument that has an ORB engine with _daily_bars.
        """
        for engine in self.runner.engines:
            if not hasattr(engine, '_daily_bars'):
                continue
            pair = engine.pair_name

            # Daily bars (20 days)
            self.log.info(f"Refreshing daily bars for {pair} (ORB context)...")
            df = self.conn.fetch_historical_bars(
                pair, duration="20 D", bar_size="1 day")
            if not df.empty:
                from v11.llm.models import DailyBarData
                daily_bars = []
                for _, row in df.iterrows():
                    daily_bars.append(DailyBarData(
                        date=str(row['date'])[:10],
                        o=row['open'], h=row['high'],
                        l=row['low'], c=row['close'],
                    ))
                engine._daily_bars = daily_bars
                self.log.info(
                    f"{pair}: Refreshed {len(daily_bars)} daily bars for ORB LLM")

            # 4-hour bars (5 days)
            self.log.info(f"Refreshing 4-hour bars for {pair} (ORB context)...")
            df_4h = self.conn.fetch_historical_bars(
                pair, duration="5 D", bar_size="4 hours")
            if not df_4h.empty:
                from v11.llm.models import HourlyBarData
                hourly_bars = []
                for _, row in df_4h.iterrows():
                    date_str = str(row['date'])
                    hour = 0
                    if ' ' in date_str:
                        try:
                            hour = int(date_str.split(' ')[1].split(':')[0])
                        except (ValueError, IndexError):
                            pass
                    session = f"{(hour // 4) * 4:02d}-{((hour // 4) + 1) * 4:02d}"
                    hourly_bars.append(HourlyBarData(
                        date=date_str[:10],
                        o=row['open'], h=row['high'],
                        l=row['low'], c=row['close'],
                        session=session,
                    ))
                engine._hourly_bars = hourly_bars
                self.log.info(
                    f"{pair}: Refreshed {len(hourly_bars)} 4-hour bars for ORB LLM")

    def run(self) -> None:
        """Main trading loop."""
        def on_signal(sig, frame):
            self.log.info(f"Signal {sig} received — shutting down")
            self._shutdown = True
        signal_mod.signal(signal_mod.SIGINT, on_signal)
        signal_mod.signal(signal_mod.SIGTERM, on_signal)

        # Clean up stale emergency state from previous session
        stale_state = ROOT / "v11" / "live" / "state" / "emergency_shutdown.json"
        if stale_state.exists():
            stale_state.unlink()
            self.log.info("Removed stale emergency_shutdown.json from previous session")

        # Initial connection with extended retries (Gateway may still be starting/authenticating)
        connected = False
        for startup_attempt in range(6):  # 6 rounds × 3 retries each = up to ~3 minutes
            if self.conn.connect():
                connected = True
                break
            if startup_attempt < 5:
                wait = 15
                self.log.info(
                    f"Initial connect failed (round {startup_attempt + 1}/6) "
                    f"— Gateway may still be starting. Retrying in {wait}s...")
                time.sleep(wait)
        if not connected:
            self.log.error("Cannot connect to IBKR after extended retries — exiting")
            sys.exit(1)

        # Qualify contracts and start price streams
        for inst_cfg in self.live_cfg.instruments:
            self.conn.qualify_contract(inst_cfg)
            self.conn.start_price_stream(inst_cfg)

        # Wire strategies (after contracts are qualified — ORB needs contract)
        self._wire_strategies()

        # Seed historical data
        self._seed_historical()

        # Log readiness
        status = self.runner.get_all_status()
        for s in status['strategies']:
            self.log.info(
                f"  {s.get('strategy_name', '?')}: "
                f"bars={s.get('bar_count', 0)}")
        self.log.info(
            f"Runner ready: {len(status['strategies'])} strategies "
            f"on {status['instruments']}")

        poll_interval = 1.0
        last_status_log = 0.0
        last_order_reconcile = 0.0
        loop = asyncio.new_event_loop()

        while not self._shutdown:
            try:
                was_connected = self.conn.connected
                if not self.conn.ensure_connected():
                    if self.conn.persistent_failure:
                        self.log.critical("IBKR persistent failure — EMERGENCY SHUTDOWN")
                        self._emergency_shutdown("persistent_ibkr_failure")
                        break
                    self.log.error("Connection lost — waiting 10s")
                    time.sleep(10)
                    continue

                # Reconnected
                if not was_connected and self.conn.connected:
                    # Just reconnected — re-point ORB engines at the
                    # fresh ib instance BEFORE reconciling, so the
                    # reconcile_after_reconnect path queries the live
                    # socket. IBKRConnection.connect() reassigns
                    # self.ib = IB() and ORB-owned components otherwise
                    # keep a handle on the stale, disconnected one
                    # (Phase 1, 2026-05-11 remediation).
                    self.runner.rebind_orb_connections()
                    self._reconcile_positions()

                now = datetime.now(timezone.utc)

                # Daily reset at date change (UTC midnight)
                today_str = now.strftime("%Y-%m-%d")
                if self._current_trading_date and today_str != self._current_trading_date:
                    self.log.info(
                        f"Date changed {self._current_trading_date} -> {today_str} -- "
                        f"resetting daily counters")
                    self.runner.reset_daily()
                    self._session_reset_done = False  # allow 5 PM ET reset for new day
                    self._refresh_llm_context()  # refresh daily/4h bars for new day
                self._current_trading_date = today_str

                # Broker session reset at 5 PM ET (FX market close)
                # This aligns PnL limits with the actual trading session boundary
                et_now = now.astimezone(ZoneInfo("America/New_York"))
                if (et_now.hour == 17 and et_now.minute < 2
                        and not self._session_reset_done
                        and et_now.weekday() < 5):  # Mon-Fri only
                    self.log.info(
                        f"5 PM ET broker session close — resetting daily counters")
                    self.runner.reset_daily()
                    self._session_reset_done = True
                # Clear the guard after 6 PM ET so next day can trigger again
                if et_now.hour >= 18:
                    self._session_reset_done = False

                for pair in self._active_pairs:
                    price = self.conn.get_mid_price(pair)
                    if price is None:
                        continue

                    # Tick logging for replay
                    if self._tick_logger is not None:
                        ticker = self.conn.get_ticker(pair)
                        self._tick_logger.record(
                            pair, now, price,
                            bid=ticker.bid if ticker else None,
                            ask=ticker.ask if ticker else None,
                            last=ticker.last if ticker else None,
                            bid_size=ticker.bidSize if ticker else None,
                            ask_size=ticker.askSize if ticker else None,
                            last_size=ticker.lastSize if ticker else None,
                        )

                    # Track price freshness
                    self._last_price_time[pair] = time.time()

                    completed_bar = self.runner.on_price(pair, price, now)
                    if completed_bar is not None:
                        loop.run_until_complete(
                            self.runner.on_bar(pair, completed_bar))

                # Periodic status log (every 5 minutes)
                if time.time() - last_status_log > 300:
                    self._check_price_staleness()
                    self._log_status()
                    self._write_heartbeat()
                    last_status_log = time.time()

                # Periodic order reconciliation (every 60s).
                # Catches silent IBKR-side order discards that don't
                # propagate as exceptions or via orderStatusEvent (e.g.
                # the 2026-04-20 incident where entry orders were
                # Cancelled by IBKR's preset but V11 never learned).
                if time.time() - last_order_reconcile > 60:
                    self._reconcile_orders()
                    last_order_reconcile = time.time()

                # Placement-stuck tripwire (2026-04-23). If any ORB engine
                # has been failing to place orders with "Not connected" for
                # >~30s despite the reconnect path, exit non-zero so the
                # start_v11.bat auto-restart wrapper brings V11 back with
                # a fresh socket. This is the belt-and-suspenders path that
                # would have saved 4h of unproductive spam on 2026-04-23.
                for engine in self.runner.engines:
                    ex = getattr(engine, "_execution", None)
                    if ex is not None and getattr(ex, "placement_stuck", False):
                        pair = getattr(engine, "pair_name", "?")
                        self.log.critical(
                            f"STUCK: {pair} executor stuck after "
                            f"{ex._consec_placement_conn_failures} consecutive "
                            f"connectivity-class placement failures — exiting "
                            f"with code 1 so wrapper restarts with fresh socket")
                        self._cleanup()
                        sys.exit(1)

                self.conn.sleep(poll_interval)

            except KeyboardInterrupt:
                self.log.info("Keyboard interrupt")
                self._shutdown = True
            except Exception as e:
                self.log.error(f"ERROR in main loop: {e}", exc_info=True)
                time.sleep(10)

        self.log.info("Shutting down...")
        self._cleanup()
        self.conn.disconnect()
        loop.close()
        self.log.info("V11 live trader stopped.")

    def _log_status(self) -> None:
        """Log status for all strategies and risk manager."""
        now_utc = datetime.now(timezone.utc)
        status = self.runner.get_all_status()
        risk = status['risk']
        self.log.info(
            f"[RISK] PnL=${risk['combined_pnl']:+.2f} "
            f"trades={risk['combined_trades']} "
            f"positions={len(risk['open_positions'])}"
            f"/{self.live_cfg.max_concurrent_positions}")
        for s in status['strategies']:
            extra = ""
            proximity = ""
            name = s.get('strategy_name', '?')
            if name == 'V6_ORB':
                state = s.get('state', '?')
                rng = s.get('range')
                extra = f" state={state} range={rng or 'none'}"

                # ORB proximity / schedule info
                price = s.get('current_price')
                range_end = s.get('range_end_hour', 6)
                trade_end = s.get('trade_end_hour', 16)
                llm_done = s.get('llm_evaluated', False)
                llm_pending = s.get('llm_gate_pending', False)
                h = now_utc.hour

                if state == 'IDLE' and not s.get('range_calculated'):
                    if h < range_end:
                        proximity = f" | WAITING: range calc at {range_end}:00 UTC ({range_end - h}h away)"
                    else:
                        proximity = " | range calc pending (waiting for ticks)"
                elif state == 'IDLE' and s.get('range_calculated') and rng:
                    proximity = " | range ready, waiting for trade window"
                elif state == 'RANGE_READY':
                    if llm_pending:
                        proximity = " | LLM EVALUATING..."
                    elif not llm_done:
                        proximity = " | LLM gate pending (next bar)"
                    else:
                        resting = s.get('has_resting_entries', False)
                        d2h = s.get('dist_to_high')
                        d2l = s.get('dist_to_low')
                        vel = s.get('velocity', 0)
                        vel_thresh = s.get('velocity_threshold', 0)
                        ticks_3m = s.get('tick_count_3m', 0)
                        if resting and price and d2h is not None:
                            proximity = (f" | orders LIVE buy@high sell@low"
                                         f" price={price:.2f}"
                                         f" dist_high={d2h:+.2f}"
                                         f" dist_low={d2l:+.2f}")
                        elif vel_thresh > 0:
                            vel_pct = vel / vel_thresh * 100 if vel_thresh else 0
                            proximity = (f" | vel={vel:.0f}/{vel_thresh:.0f}"
                                         f"({vel_pct:.0f}%)"
                                         f" ticks3m={ticks_3m}"
                                         f" dist_high={d2h:+.2f}"
                                         f" dist_low={d2l:+.2f}")
                        else:
                            proximity = " | brackets eligible"
                elif state == 'DONE_TODAY':
                    skip_reason = s.get('skip_reason')
                    if skip_reason:
                        proximity = f" | done: {skip_reason}"
                    elif llm_done:
                        proximity = " | done (post-window)"
                    else:
                        proximity = f" | done (window closes {trade_end}:00 UTC)"
                elif state in ('ORDERS_PLACED', 'IN_TRADE'):
                    d2h = s.get('dist_to_high')
                    d2l = s.get('dist_to_low')
                    if price and d2h is not None:
                        proximity = (f" | price={price:.2f}"
                                     f" dist_high={d2h:+.2f}"
                                     f" dist_low={d2l:+.2f}")

            elif name == 'Darvas_Breakout':
                box = s.get('active_box')
                det_state = s.get('detector_state', '?')
                prog = s.get('formation_progress', {})
                if box:
                    box_str = f"[{box.bottom:.5f}-{box.top:.5f}]"
                    # Distance to breakout
                    last_price = s.get('last_close', 0)
                    if last_price and box.top:
                        dist = last_price - box.top
                        atr = s.get('atr', 0)
                        dist_atr = f" ({dist/atr:.1f}ATR)" if atr > 0 else ""
                        sma = s.get('htf_sma')
                        sma_dir = ""
                        if sma and last_price:
                            sma_dir = " SMA:UP" if last_price > sma else " SMA:DOWN"
                        proximity = (f" | price={last_price:.5f}"
                                     f" dist_breakout={dist:+.5f}{dist_atr}{sma_dir}")
                elif det_state == 'CONFIRMING_TOP':
                    box_str = (f"forming top={prog.get('candidate_top', 0):.5f} "
                               f"{prog.get('bars_confirmed', 0)}/{prog.get('bars_needed', 0)}")
                    bars_left = prog.get('bars_needed', 15) - prog.get('bars_confirmed', 0)
                    proximity = f" | ~{bars_left} bars to confirm top"
                elif det_state == 'CONFIRMING_BOTTOM':
                    box_str = (f"top={prog.get('confirmed_top', 0):.5f} "
                               f"bot={prog.get('candidate_bottom', 0):.5f} "
                               f"{prog.get('bars_confirmed', 0)}/{prog.get('bars_needed', 0)}")
                    bars_left = prog.get('bars_needed', 15) - prog.get('bars_confirmed', 0)
                    proximity = f" | ~{bars_left} bars to confirm box"
                elif det_state == 'CONFIRMING_BREAKOUT':
                    box_str = (f"BREAKOUT {prog.get('direction', '?')} "
                               f"{prog.get('confirm_count', 0)}/{prog.get('confirm_needed', 0)}")
                    proximity = " | BREAKOUT in progress -> LLM gate next"
                else:
                    box_str = "seeking"
                    proximity = " | no box forming"
                sma = s.get('htf_sma')
                sma_str = f"{sma:.5f}" if sma else "warming"
                extra = (f" det={det_state}"
                         f" box={box_str}"
                         f" atr={s.get('atr', 0):.5f}"
                         f" sma={sma_str}({s.get('htf_sma_bars', 0)})")

            elif name == '4H_Level_Retest':
                sma = s.get('htf_sma')
                sma_str = f"{sma:.5f}" if sma else "warming"
                nearest = s.get('nearest_level')
                if nearest:
                    nd = s.get('nearest_dist', 0)
                    atr = s.get('atr', 0)
                    nd_atr = f"{nd/atr:.1f}ATR" if atr > 0 else "?"
                    near_str = f" near={nearest.level_type.value}@{nearest.price:.5f}({nd_atr})"
                    if atr > 0 and nd / atr < 2.0:
                        proximity = f" | CLOSE to level ({nd_atr} away)"
                    elif s.get('active_levels', 0) == 0:
                        proximity = " | no levels detected yet"
                    else:
                        proximity = f" | {s.get('active_levels', 0)} levels, nearest {nd_atr} away"
                else:
                    near_str = ""
                    proximity = f" | {s.get('active_levels', 0)} levels detected" if s.get('active_levels', 0) else " | no levels yet"
                buf = s.get('buffer_fill', '?')
                extra = (f" levels={s.get('active_levels', 0)}"
                         f" pending={s.get('pending_retests', 0)}"
                         f"{near_str}"
                         f" atr={s.get('atr', 0):.5f}"
                         f" sma={sma_str}({s.get('htf_sma_bars', 0)})"
                         f" htf={s.get('level_htf_bars', 0)}({buf})")

            self.log.info(
                f"[STATUS] {name} "
                f"on {s.get('pair_name', '?')}: "
                f"bars={s.get('bar_count', 0)} "
                f"in_trade={s.get('in_trade', False)}{extra}{proximity}")

        # Last LLM decision summary
        self._log_last_llm_decision()

    def _log_last_llm_decision(self) -> None:
        """Log the most recent LLM decision for visibility."""
        from v11.llm.grok_filter import GrokFilter
        ledger = None
        if isinstance(self.llm_filter, GrokFilter) and self.llm_filter._ledger:
            ledger = self.llm_filter._ledger
        if not ledger:
            return
        all_decisions = ledger.get_all()
        if not all_decisions:
            self.log.info("[LLM] No decisions yet (fresh ledger)")
            return
        last = all_decisions[0]  # newest first
        grade = ""
        if last.outcome.assessed:
            grade = f" -> {last.outcome.grade}"
        stats = ledger.stats
        self.log.info(
            f"[LLM] Last: {last.strategy} {last.instrument} "
            f"{last.decision}(conf={last.confidence}){grade} "
            f"| {last.reasoning[:80]}")
        self.log.info(
            f"[LLM] Ledger: {stats['total']} decisions, "
            f"{stats['assessed']} assessed, "
            f"accuracy={stats['accuracy_pct']}%"
            f" (C={stats['correct']} W={stats['wrong']} M={stats['missed']})")

    def _reconcile_orders(self) -> None:
        """Periodic reconciliation of V11's internal order state vs IBKR's
        actual order book.

        Scenario this catches (2026-04-20 incident): V11's executor places
        orders, gets orderIds back, updates internal state to ORDERS_PLACED.
        IBKR silently discards one or both (e.g. TIF rejection, risk-system
        discard). orderStatusEvent *should* fire, but if for any reason
        it doesn't (handler not hooked, event loop starved, etc.), V11's
        internal state drifts from reality.

        This method queries ib.openTrades() and cross-references against
        each ORB adapter's (buy_entry_id, sell_entry_id). If V11 thinks
        an order is live but IBKR has no record, log ERROR and notify
        the adapter so it can surface the failure.
        """
        if not self.conn.connected:
            return
        TERMINAL = ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')
        ACTIVE = ('Submitted', 'PreSubmitted', 'PendingSubmit', 'ApiPending')
        try:
            all_trades = list(self.conn.ib.trades())
            live_order_ids = {t.order.orderId for t in all_trades
                              if t.orderStatus.status in ACTIVE}
            terminal_ids = {t.order.orderId for t in all_trades
                            if t.orderStatus.status in TERMINAL}
        except Exception as e:
            self.log.warning(f"Order reconcile: could not fetch trades: {e}")
            return

        for engine in getattr(self.runner, "engines", []):
            exec_ = getattr(engine, "_execution", None)
            if exec_ is None:
                continue
            buy_id = getattr(exec_, "buy_entry_id", 0)
            sell_id = getattr(exec_, "sell_entry_id", 0)
            if buy_id <= 0 and sell_id <= 0:
                continue  # nothing V11 thinks is live for this engine

            # An ID that IBKR reports in a terminal state (Filled/Cancelled)
            # is NOT missing — it's just done. The executor should have
            # cleared it; if it didn't, clean it up here silently rather
            # than raising a false "discarded by IBKR" alarm.
            #  — fixes 2026-04-21 false-positive reconcile incident.
            for oid, attr in ((buy_id, "buy_entry_id"), (sell_id, "sell_entry_id")):
                if oid > 0 and oid in terminal_ids:
                    setattr(exec_, attr, 0)
            buy_id = getattr(exec_, "buy_entry_id", 0)
            sell_id = getattr(exec_, "sell_entry_id", 0)
            if buy_id <= 0 and sell_id <= 0:
                continue

            expected = {oid for oid in (buy_id, sell_id) if oid > 0}
            missing = expected - live_order_ids
            if missing:
                self.log.error(
                    f"ORDER RECONCILE: {engine.pair_name} executor has "
                    f"ids {sorted(expected)} but IBKR openTrades shows "
                    f"only {sorted(expected & live_order_ids)}. "
                    f"MISSING={sorted(missing)}. Order(s) likely "
                    f"discarded by IBKR. Clearing IDs so strategy can "
                    f"recover state on next tick.")
                # Surface this via executor's failure flag if available,
                # and reset order IDs so has_resting_entries() returns
                # False, allowing the strategy to retry placement or
                # go DONE_TODAY depending on context.
                if hasattr(exec_, "_entry_failed"):
                    exec_._entry_failed = True
                    exec_._entry_failure_reason = (
                        f"reconcile: missing orders at IBKR {sorted(missing)}")
                if buy_id in missing:
                    exec_.buy_entry_id = 0
                if sell_id in missing:
                    exec_.sell_entry_id = 0

    def _is_market_closed_window(self, now_utc: datetime) -> bool:
        """True during the weekly FX/XAU market closure.

        Forex / gold spot close ~Fri 21:00 UTC and reopen ~Sun 22:00 UTC.
        During this window no ticks flow by design — if the staleness
        watchdog kept running it would trip `price_feed_dead` every
        10 min, emergency-shutdown + wrapper-restart ~270 times across
        a weekend. Skip escalation during this window; regular operation
        resumes automatically when ticks flow again at the Sunday open.
        """
        wd = now_utc.weekday()  # Mon=0 ... Sun=6
        hour = now_utc.hour
        if wd == 4 and hour >= 21:   # Fri 21:00 UTC onward
            return True
        if wd == 5:                  # All of Sat
            return True
        if wd == 6 and hour < 22:    # Sun before 22:00 UTC
            return True
        return False

    def _check_price_staleness(self, now_utc: datetime | None = None) -> None:
        """Check for stale price feeds and log warnings/errors.

        Escalation: 60s warn → 300s restart stream → 600s emergency shutdown.
        Skipped during the weekly market-close window (Fri 21:00 UTC →
        Sun 22:00 UTC) since no ticks are expected.

        now_utc: injectable for deterministic testing; defaults to UTC now.
        """
        if self._is_market_closed_window(now_utc or datetime.now(timezone.utc)):
            return
        now = time.time()
        for pair in self._active_pairs:
            last = self._last_price_time.get(pair)
            if last is None:
                # Never received a price — only warn if connected
                if self.conn.connected:
                    self.log.warning(
                        f"PRICE STALE: {pair} — no price received since startup")
                continue
            stale_s = now - last
            if stale_s > 600:
                self.log.critical(
                    f"PRICE DEAD: {pair} — no price for {stale_s:.0f}s "
                    f"(>600s). Stream restart failed — EMERGENCY SHUTDOWN.")
                self._emergency_shutdown("price_feed_dead")
                return
            elif stale_s > 300:
                self.log.error(
                    f"PRICE STALE: {pair} — no price for {stale_s:.0f}s "
                    f"(>300s). Attempting market data stream restart.")
                # Try restarting the market data stream
                self.conn.restart_price_stream(pair)
            elif stale_s > 60:
                self.log.warning(
                    f"PRICE STALE: {pair} — no price for {stale_s:.0f}s")

    def _write_heartbeat(self) -> None:
        """Write heartbeat.json for external monitoring.

        External scripts can check if this file is older than 10 minutes
        to detect a hung/frozen process and kill + restart it.
        """
        state_dir = ROOT / "v11" / "live" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        heartbeat_file = state_dir / "heartbeat.json"
        try:
            status = self.runner.get_all_status()
            risk = status['risk']

            # P2.7 (2026-04-21): per-instrument protection view. Lets the
            # stop/alert script detect a naked position (in_trade=true but
            # has_sl=false) even if logs are being missed.
            broker_by_conid: dict[int, float] = {}
            try:
                if self.conn.connected:
                    for p in self.conn.ib.positions():
                        cid = getattr(p.contract, "conId", None)
                        if cid is not None:
                            broker_by_conid[cid] = float(p.position)
            except Exception:
                pass

            active_ids_by_conid: dict[int, set[int]] = {}
            try:
                if self.conn.connected:
                    for t in self.conn.ib.openTrades():
                        st = getattr(t.orderStatus, "status", "")
                        if st in ("Submitted", "PreSubmitted",
                                  "PendingSubmit", "ApiPending"):
                            cid = getattr(t.contract, "conId", None)
                            oid = getattr(t.order, "orderId", None)
                            if cid is not None and oid is not None:
                                active_ids_by_conid.setdefault(cid, set()).add(oid)
            except Exception:
                pass

            engine_by_pair = {
                getattr(e, "pair_name", None): e
                for e in self.runner.engines
                if getattr(e, "pair_name", None)
            }

            strategies_out = []
            for s in status['strategies']:
                entry = {
                    "name": s.get('strategy_name', '?'),
                    "pair": s.get('pair_name', '?'),
                    "in_trade": s.get('in_trade', False),
                    "bars": s.get('bar_count', 0),
                    "has_sl": None,
                    "has_tp": None,
                    "broker_pos": None,
                    "internal_pos": None,
                }
                eng = engine_by_pair.get(entry["pair"])
                execn = getattr(eng, "_execution", None) if eng else None
                if execn is not None:
                    cid = getattr(execn.contract, "conId", None)
                    active = active_ids_by_conid.get(cid, set())
                    sl = getattr(execn, "sl_order_id", 0) or 0
                    tp = getattr(execn, "tp_order_id", 0) or 0
                    entry["has_sl"] = bool(sl) and sl in active
                    entry["has_tp"] = bool(tp) and tp in active
                    entry["internal_pos"] = getattr(execn, "_position", 0)
                    if cid is not None:
                        entry["broker_pos"] = broker_by_conid.get(cid, 0.0)
                strategies_out.append(entry)

            state = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "connected": self.conn.connected,
                "persistent_failure": self.conn.persistent_failure,
                "instruments": self._active_pairs,
                "pnl": risk['combined_pnl'],
                "trades": risk['combined_trades'],
                "positions": len(risk['open_positions']),
                "strategies": strategies_out,
            }
            heartbeat_file.write_text(json.dumps(state, indent=2, default=str))
        except Exception as e:
            self.log.debug(f"Heartbeat write failed: {e}")

    def _reconcile_positions(self) -> None:
        """Reconcile internal trade state with broker after reconnect.

        Two-level reconciliation:
        1. TradeManager: per-instrument internal state vs broker
        2. RiskManager: portfolio-level position tracking vs broker
        """
        try:
            outage_s = float(self.conn.last_outage_s)
        except (TypeError, ValueError):
            outage_s = 0.0
        self.log.info(
            f"Reconciling positions after reconnect (outage={outage_s:.1f}s)...")

        # 1a. ORB engines: graduated recovery (P0.3, 2026-04-21). If outage
        #     was brief AND we still have the range in memory, re-arm SL/TP
        #     instead of flattening; otherwise flatten. This replaces the
        #     blanket cancel_orders_for() that wiped an active SL leg on
        #     2026-04-21 and left a naked short for ~7h.
        orb_pairs_handled: set[str] = set()
        for engine in self.runner.engines:
            execn = getattr(engine, "_execution", None)
            if execn is None or not hasattr(execn, "reconcile_after_reconnect"):
                continue
            pair = getattr(engine, "pair_name", None)
            try:
                outcome = execn.reconcile_after_reconnect(outage_s)
            except Exception as e:
                self.log.error(
                    f"reconcile_after_reconnect raised for {pair}: {e}")
                outcome = "error"
            self.log.info(f"reconcile[{pair}] -> {outcome}")
            if pair:
                orb_pairs_handled.add(pair)

        # 1b. Non-ORB feeds keep the legacy reconcile path (cancel opens +
        #     trade_manager.reconcile_position). Skip any pair already
        #     handled by the ORB graduated path above.
        for feed in self.runner.feeds.values():
            pair = feed.inst_config.pair_name
            if pair in orb_pairs_handled:
                continue
            self.conn.cancel_orders_for(pair)
            feed.trade_manager.reconcile_position()

        # 2. Portfolio-level reconciliation (RiskManager vs broker)
        broker_positions = self.conn.get_broker_positions()

        # Build set of instruments with broker positions
        broker_instruments = set()
        for pos in broker_positions:
            pair = f"{pos.contract.symbol}{pos.contract.currency}"
            # Match against our configured instruments
            for inst in self.live_cfg.instruments:
                if inst.symbol == pos.contract.symbol and abs(pos.position) > 0:
                    broker_instruments.add(inst.pair_name)
                    break

        # Risk manager thinks these instruments have positions
        rm_positions = self.risk_manager.get_open_instruments()

        # Broker has position but risk manager doesn't know
        for inst in broker_instruments - rm_positions:
            self.log.warning(
                f"SYNC: Broker has position on {inst} but risk manager doesn't. "
                f"Adding to risk manager.")
            # Find which strategy owns it from TradeManager
            for feed in self.runner.feeds.values():
                if feed.inst_config.pair_name == inst and feed.trade_manager.in_trade:
                    self.risk_manager.record_trade_entry(
                        inst, feed.trade_manager._strategy_name
                        if hasattr(feed.trade_manager, '_strategy_name')
                        else "UNKNOWN")

        # Risk manager thinks there's a position but broker doesn't
        for inst in rm_positions - broker_instruments:
            self.log.warning(
                f"SYNC: Risk manager thinks {inst} has position but broker is flat. "
                f"Removing from risk manager.")
            strategy = self.risk_manager.get_position_strategy(inst)
            self.risk_manager.record_trade_exit(inst, strategy, pnl=0.0)

        self.log.info(
            f"Reconciliation complete: broker={broker_instruments}, "
            f"risk_mgr={rm_positions}")

    def _emergency_shutdown(self, reason: str) -> None:
        """Emergency shutdown: log state, attempt to close positions, write state file, exit."""
        self.log.critical(f"EMERGENCY SHUTDOWN: {reason}")

        # Log all open positions
        status = self.runner.get_all_status()
        risk = status['risk']
        self.log.critical(
            f"Emergency state: PnL=${risk['combined_pnl']:+.2f} "
            f"trades={risk['combined_trades']} "
            f"positions={risk['open_positions']}")

        # Try to cancel all open orders
        try:
            self.conn.cancel_all_orders()
            self.log.info("Cancelled all open orders")
        except Exception as e:
            self.log.error(f"Failed to cancel orders: {e}")

        # ORB-aware emergency close (Phase 3, 2026-05-11 remediation).
        # cancel_all_orders above wipes SL/TP, so any ORB-side orphan
        # position is now unprotected. Route through the executor's
        # broker-truth-aware close_at_market path BEFORE the legacy
        # TradeManager block below so an ORB orphan gets flattened
        # before the process exits. Best-effort, runs only if the
        # connection is live (a stale socket can't close anything).
        if self.conn.connected:
            for engine in self.runner.engines:
                if hasattr(engine, "emergency_close"):
                    try:
                        engine.emergency_close(reason)
                    except Exception as e:
                        self.log.error(
                            f"engine.emergency_close failed: {e}")

        # Try one final reconnect to close positions
        if not self.conn.connected:
            self.log.info("Attempting final reconnect to close positions...")
            try:
                if self.conn.connect():
                    for feed in self.runner.feeds.values():
                        tm = feed.trade_manager
                        if tm.in_trade:
                            self.log.critical(
                                f"Emergency closing {tm._inst.pair_name} "
                                f"dir={tm._direction} qty={tm._qty}")
                            try:
                                tm.emergency_close("EMERGENCY_SHUTDOWN")
                            except Exception as e:
                                self.log.error(f"Emergency close failed: {e}")
            except Exception as e:
                self.log.error(f"Final reconnect failed: {e}")

        # Write emergency state file for post-mortem
        state_dir = ROOT / "v11" / "live" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "emergency_shutdown.json"
        try:
            state = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "pnl": risk['combined_pnl'],
                "trades": risk['combined_trades'],
                "positions": risk['open_positions'],
                "strategies": status['strategies'],
            }
            state_file.write_text(json.dumps(state, indent=2, default=str))
            self.log.info(f"Emergency state written to {state_file}")
        except Exception as e:
            self.log.error(f"Failed to write emergency state: {e}")

        # Clean up and exit with error code
        self._cleanup()
        try:
            self.conn.disconnect()
        except Exception:
            pass
        self.log.critical("V11 exiting with error code 1 — wrapper script should restart")
        sys.exit(1)

    def _cleanup(self) -> None:
        """Clean up on shutdown. ORB adapter handles its own cleanup."""
        if self._tick_logger is not None:
            self._tick_logger.close()
        for engine in self.runner.engines:
            if hasattr(engine, 'cleanup'):
                engine.cleanup()


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="V11 Multi-Strategy Live Trader")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without submitting orders (default: True)")
    parser.add_argument("--live", action="store_true",
                        help="Submit real orders (overrides --dry-run)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002)
    parser.add_argument("--client-id", type=int, default=11)
    parser.add_argument("--instruments", nargs="+",
                        default=["XAUUSD"],
                        help="Instruments to trade (default: XAUUSD)")
    parser.add_argument("--max-daily-loss", type=float, default=500.0,
                        help="Combined daily loss limit in USD")
    parser.add_argument("--no-llm", action="store_true",
                        help="Disable Grok LLM filter (mechanical signals only)")
    args = parser.parse_args()

    # Dry-run by default unless --live is specified
    dry_run = not args.live

    # Build instrument list
    instruments = []
    for name in args.instruments:
        name = name.upper()
        if name in INSTRUMENT_MAP:
            instruments.append(INSTRUMENT_MAP[name])
        else:
            print(f"Unknown instrument: {name}. Available: {list(INSTRUMENT_MAP.keys())}")
            sys.exit(1)

    live_cfg = LiveConfig(
        ibkr_host=args.host,
        ibkr_port=args.port,
        ibkr_client_id=args.client_id,
        instruments=instruments,
        dry_run=dry_run,
        max_daily_loss=args.max_daily_loss,
    )
    live_cfg.validate()

    log_dir = ROOT / "v11" / "live" / "logs"
    log = setup_logging(log_dir)

    log.info("=" * 60)
    log.info("V11 — Multi-Strategy Portfolio")
    log.info(f"  Instruments: {[i.pair_name for i in instruments]}")
    active_strats = "ORB (XAUUSD)" if not live_cfg.darvas_enabled else "Darvas+SMA, 4H Retest (EURUSD) + ORB (XAUUSD)"
    log.info(f"  Strategies:  {active_strats}")
    log.info(f"  Dry-run:     {dry_run}")
    use_llm = not args.no_llm
    log.info(f"  LLM:         {'grok (active)' if use_llm else 'DISABLED'}")
    if use_llm:
        log.info(f"  Confidence:  Darvas/4H >= {live_cfg.llm_confidence_threshold}, "
                 f"ORB >= {live_cfg.orb_confidence_threshold}")
    log.info(f"  Daily loss:  ${live_cfg.max_daily_loss:.0f}")
    log.info("=" * 60)

    trader = V11LiveTrader(live_cfg, log, use_llm=use_llm)
    trader.run()


if __name__ == "__main__":
    main()
