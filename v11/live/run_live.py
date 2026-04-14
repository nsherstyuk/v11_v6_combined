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
    velocity_filter_enabled=True,
    velocity_lookback_minutes=3,
    velocity_threshold=168.0,    # P50 from research
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

    # Max disconnect duration before emergency shutdown (seconds)
    MAX_DISCONNECT_SECONDS = 300  # 5 minutes — must match IBKRConnection.MAX_RECONNECT_DURATION

    def __init__(self, live_cfg: LiveConfig, log: logging.Logger,
                 use_llm: bool = True):
        self.live_cfg = live_cfg
        self.log = log
        self._shutdown = False
        self._current_trading_date: str = ""  # for daily reset detection
        self._disconnect_start: Optional[float] = None  # epoch time of first disconnect
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

    def _wire_strategies(self) -> None:
        """Add strategies to the runner based on configured instruments."""
        active_pairs = {i.pair_name for i in self.live_cfg.instruments}

        if "EURUSD" in active_pairs:
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

        # Fetch daily bars for ORB LLM context
        for pair in self.runner.get_feed_pairs():
            if pair == "XAUUSD":
                self.log.info(f"Fetching daily bars for {pair} (ORB context)...")
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
                    for engine in self.runner.engines:
                        if hasattr(engine, '_daily_bars') and engine.pair_name == pair:
                            engine._daily_bars = daily_bars
                            self.log.info(
                                f"{pair}: Loaded {len(daily_bars)} daily bars for ORB LLM")

                # Fetch 4-hour bars for last 5 days
                self.log.info(f"Fetching 4-hour bars for {pair} (ORB context)...")
                df_4h = self.conn.fetch_historical_bars(
                    pair, duration="5 D", bar_size="4 hours")
                if not df_4h.empty:
                    from v11.llm.models import HourlyBarData
                    hourly_bars = []
                    for _, row in df_4h.iterrows():
                        date_str = str(row['date'])
                        # Extract hour from datetime string (e.g. "2026-04-07 08:00:00")
                        hour = 0
                        if ' ' in date_str:
                            try:
                                hour = int(date_str.split(' ')[1].split(':')[0])
                            except (ValueError, IndexError):
                                pass
                        # Session format: "00-04", "04-08", etc.
                        session = f"{(hour // 4) * 4:02d}-{((hour // 4) + 1) * 4:02d}"
                        hourly_bars.append(HourlyBarData(
                            date=date_str[:10],
                            o=row['open'], h=row['high'],
                            l=row['low'], c=row['close'],
                            session=session,
                        ))
                    for engine in self.runner.engines:
                        if hasattr(engine, '_hourly_bars') and engine.pair_name == pair:
                            engine._hourly_bars = hourly_bars
                            self.log.info(
                                f"{pair}: Loaded {len(hourly_bars)} 4-hour bars for ORB LLM")

    def run(self) -> None:
        """Main trading loop."""
        def on_signal(sig, frame):
            self.log.info(f"Signal {sig} received — shutting down")
            self._shutdown = True
        signal_mod.signal(signal_mod.SIGINT, on_signal)
        signal_mod.signal(signal_mod.SIGTERM, on_signal)

        if not self.conn.connect():
            self.log.error("Cannot connect to IBKR — exiting")
            return

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
        loop = asyncio.new_event_loop()

        while not self._shutdown:
            try:
                was_connected = self.conn.connected
                if not self.conn.ensure_connected():
                    # Track disconnect duration
                    if self._disconnect_start is None:
                        self._disconnect_start = time.time()
                    elapsed = time.time() - self._disconnect_start

                    if elapsed > self.MAX_DISCONNECT_SECONDS:
                        self.log.critical(
                            f"IBKR down for {elapsed:.0f}s — EMERGENCY SHUTDOWN")
                        self._emergency_shutdown("persistent_ibkr_failure")
                        break

                    self.log.error(
                        f"Connection lost — waiting 10s "
                        f"(down {elapsed:.0f}s/{self.MAX_DISCONNECT_SECONDS}s)")
                    time.sleep(10)
                    continue

                # Reconnected — clear timer and reconcile
                if self._disconnect_start is not None:
                    self.log.info(
                        f"Reconnected after {time.time() - self._disconnect_start:.0f}s")
                    self._disconnect_start = None
                if not was_connected and self.conn.connected:
                    # Just reconnected — reconcile positions
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
                self._current_trading_date = today_str

                # Broker session reset at 5 PM ET (FX market close)
                # This aligns PnL limits with the actual trading session boundary
                from zoneinfo import ZoneInfo
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
                    last_status_log = time.time()

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
        status = self.runner.get_all_status()
        risk = status['risk']
        self.log.info(
            f"[RISK] PnL=${risk['combined_pnl']:+.2f} "
            f"trades={risk['combined_trades']} "
            f"positions={len(risk['open_positions'])}"
            f"/{self.live_cfg.max_concurrent_positions}")
        for s in status['strategies']:
            extra = ""
            name = s.get('strategy_name', '?')
            if name == 'V6_ORB':
                extra = (f" state={s.get('state', '?')}"
                         f" range={s.get('range', 'none')}"
                         f" range_calc={s.get('range_calculated', '?')}")
            elif name == 'Darvas_Breakout':
                box = s.get('active_box')
                det_state = s.get('detector_state', '?')
                prog = s.get('formation_progress', {})
                if box:
                    box_str = f"[{box.bottom:.5f}-{box.top:.5f}]"
                elif det_state == 'CONFIRMING_TOP':
                    box_str = (f"forming top={prog.get('candidate_top', 0):.5f} "
                               f"{prog.get('bars_confirmed', 0)}/{prog.get('bars_needed', 0)}")
                elif det_state == 'CONFIRMING_BOTTOM':
                    box_str = (f"top={prog.get('confirmed_top', 0):.5f} "
                               f"bot={prog.get('candidate_bottom', 0):.5f} "
                               f"{prog.get('bars_confirmed', 0)}/{prog.get('bars_needed', 0)}")
                elif det_state == 'CONFIRMING_BREAKOUT':
                    box_str = (f"BREAKOUT {prog.get('direction', '?')} "
                               f"{prog.get('confirm_count', 0)}/{prog.get('confirm_needed', 0)}")
                else:
                    box_str = "seeking"
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
                else:
                    near_str = ""
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
                f"in_trade={s.get('in_trade', False)}{extra}")

    def _check_price_staleness(self) -> None:
        """Check for stale price feeds and log warnings/errors."""
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
            if stale_s > 300:
                self.log.error(
                    f"PRICE STALE: {pair} — no price for {stale_s:.0f}s "
                    f"(>300s). Attempting market data stream restart.")
                # Try restarting the market data stream
                try:
                    contract = self.conn._contracts.get(pair)
                    if contract:
                        # Cancel old stream
                        try:
                            self.conn.ib.cancelMktData(contract)
                        except Exception:
                            pass
                        # Re-subscribe
                        ticker = self.conn.ib.reqMktData(
                            contract, '', snapshot=False,
                            regulatorySnapshot=False)
                        self.conn.ib.sleep(2)
                        self.conn._tickers[pair] = ticker
                        self.log.info(f"Restarted market data stream for {pair}")
                except Exception as e:
                    self.log.error(f"Failed to restart stream for {pair}: {e}")
            elif stale_s > 60:
                self.log.warning(
                    f"PRICE STALE: {pair} — no price for {stale_s:.0f}s")

    def _reconcile_positions(self) -> None:
        """Reconcile internal trade state with broker after reconnect.

        Two-level reconciliation:
        1. TradeManager: per-instrument internal state vs broker
        2. RiskManager: portfolio-level position tracking vs broker
        """
        self.log.info("Reconciling positions after reconnect...")

        # 1. Per-instrument reconciliation (TradeManager handles orphan detection)
        for feed in self.runner.feeds.values():
            feed.trade_manager.reconcile_position()

        # 2. Portfolio-level reconciliation (RiskManager vs broker)
        try:
            broker_positions = self.conn.ib.positions()
        except Exception as e:
            self.log.error(f"Failed to query broker positions: {e}")
            return

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
        rm_positions = set(self.risk_manager._positions.keys())

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
            strategy = self.risk_manager._positions.get(inst, "UNKNOWN")
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
                        default=["EURUSD", "XAUUSD"],
                        help="Instruments to trade (default: EURUSD XAUUSD)")
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
    log.info(f"  Strategies:  Darvas+SMA, 4H Retest (EURUSD) + ORB (XAUUSD)")
    log.info(f"  Dry-run:     {dry_run}")
    use_llm = not args.no_llm
    log.info(f"  LLM:         {'grok (active)' if use_llm else 'DISABLED'}")
    if use_llm:
        log.info(f"  Confidence:  Darvas >= {live_cfg.llm_confidence_threshold}, "
                 f"ORB >= {live_cfg.orb_confidence_threshold}")
    log.info(f"  Daily loss:  ${live_cfg.max_daily_loss:.0f}")
    log.info("=" * 60)

    trader = V11LiveTrader(live_cfg, log, use_llm=use_llm)
    trader.run()


if __name__ == "__main__":
    main()
