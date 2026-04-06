"""
V11 Live Trading Script — Main entry point.

Connects to IBKR, streams prices for all configured instruments,
and runs the Darvas + LLM filter pipeline for each.

Usage:
    python -m v11.live.run_live --dry-run
    python -m v11.live.run_live --port 4002
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal as signal_mod
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

import pandas as pd
from dotenv import load_dotenv

# Project root
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from v11.config.live_config import (
    LiveConfig, XAUUSD_INSTRUMENT, EURUSD_INSTRUMENT, USDJPY_INSTRUMENT,
)
from v11.config.strategy_config import (
    XAUUSD_CONFIG, EURUSD_CONFIG, USDJPY_CONFIG, StrategyConfig,
)
from v11.core.types import Bar, ExitReason
from v11.execution.ibkr_connection import IBKRConnection
from v11.execution.trade_manager import TradeManager
from v11.live.live_engine import InstrumentEngine
from v11.llm.grok_filter import GrokFilter


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


# ── Instrument/Config Mapping ────────────────────────────────────────────────

INSTRUMENT_MAP = {
    "XAUUSD": (XAUUSD_INSTRUMENT, XAUUSD_CONFIG),
    "EURUSD": (EURUSD_INSTRUMENT, EURUSD_CONFIG),
    "USDJPY": (USDJPY_INSTRUMENT, USDJPY_CONFIG),
}


# ── Main Trader ──────────────────────────────────────────────────────────────

class V11LiveTrader:
    """Multi-instrument live trader orchestrator."""

    def __init__(self, live_cfg: LiveConfig, log: logging.Logger):
        self.live_cfg = live_cfg
        self.log = log
        self._shutdown = False

        # Load API key
        load_dotenv(ROOT / ".env")
        api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY", "")
        if not api_key:
            raise ValueError(
                "No API key found. Set XAI_API_KEY or GROK_API_KEY in .env")

        # IBKR connection (shared across instruments)
        self.conn = IBKRConnection(
            host=live_cfg.ibkr_host,
            port=live_cfg.ibkr_port,
            client_id=live_cfg.ibkr_client_id,
            log=log,
        )

        # LLM filter (shared across instruments)
        grok_log_dir = ROOT / live_cfg.grok_log_dir
        self.llm_filter = GrokFilter(
            api_key=api_key,
            model=live_cfg.llm_model,
            timeout=live_cfg.llm_timeout_seconds,
            log_dir=str(grok_log_dir),
        )

        # Per-instrument engines
        self.engines: dict[str, InstrumentEngine] = {}
        log_dir = ROOT / "v11" / "live" / "logs"
        trade_log_dir = ROOT / "v11" / "live" / "trades"

        for inst_cfg in live_cfg.instruments:
            pair = inst_cfg.pair_name
            if pair not in INSTRUMENT_MAP:
                log.warning(f"Unknown instrument {pair}, skipping")
                continue

            _, strategy_cfg = INSTRUMENT_MAP[pair]

            trade_mgr = TradeManager(
                conn=self.conn,
                inst=inst_cfg,
                log=log,
                trade_log_dir=trade_log_dir,
                dry_run=live_cfg.dry_run,
            )

            engine = InstrumentEngine(
                strategy_config=strategy_cfg,
                inst_config=inst_cfg,
                llm_filter=self.llm_filter,
                trade_manager=trade_mgr,
                live_config=live_cfg,
                log=log,
            )
            self.engines[pair] = engine

    def seed_buffers(self):
        """Load historical bars for all instruments."""
        for pair, engine in self.engines.items():
            self.log.info(f"Seeding {pair} buffer...")
            df = self.conn.fetch_historical_bars(
                pair, duration="28800 S", bar_size="1 min")
            if df.empty:
                self.log.warning(f"{pair}: No historical bars")
                continue
            count = 0
            for _, row in df.iterrows():
                ts = pd.Timestamp(row['date'])
                if ts.tzinfo is None:
                    ts = ts.tz_localize('UTC')
                vol = row.get('volume', 0)
                bv = vol / 2 if vol > 0 else 0.0
                sv = vol / 2 if vol > 0 else 0.0
                tc = int(vol) if vol > 0 else 0
                bar = Bar(
                    timestamp=ts.to_pydatetime(),
                    open=row['open'], high=row['high'],
                    low=row['low'], close=row['close'],
                    buy_volume=bv, sell_volume=sv, tick_count=tc,
                )
                engine.add_historical_bar(bar)
                count += 1
            self.log.info(f"{pair}: Seeded {count} bars")

    def run(self):
        """Main trading loop."""
        def on_signal(sig, frame):
            self.log.info(f"Signal {sig} received — shutting down")
            self._shutdown = True
        signal_mod.signal(signal_mod.SIGINT, on_signal)
        signal_mod.signal(signal_mod.SIGTERM, on_signal)

        if not self.conn.connect():
            self.log.error("Cannot connect to IBKR — exiting")
            return

        # Qualify contracts and start streams
        for inst_cfg in self.live_cfg.instruments:
            self.conn.qualify_contract(inst_cfg)
            self.conn.start_price_stream(inst_cfg)

        self.seed_buffers()

        for pair, engine in self.engines.items():
            self.log.info(
                f"{pair}: Engine ready. Buffer: {engine.bar_count} bars. "
                f"Dry-run: {self.live_cfg.dry_run}")

        poll_interval = 1.0
        last_status_log = 0
        loop = asyncio.new_event_loop()

        while not self._shutdown:
            try:
                if not self.conn.ensure_connected():
                    self.log.error("Connection lost — waiting 10s")
                    time.sleep(10)
                    continue

                now = datetime.now(timezone.utc)

                for pair, engine in self.engines.items():
                    price = self.conn.get_mid_price(pair)
                    if price is None:
                        continue

                    completed_bar = engine.on_price(price, now)
                    if completed_bar is not None:
                        loop.run_until_complete(engine.on_bar(completed_bar))

                # Periodic status log
                if time.time() - last_status_log > 300:
                    for pair, engine in self.engines.items():
                        status = engine.get_status()
                        self.log.info(
                            f"[STATUS] {pair}: bars={status['bar_count']} "
                            f"state={status['detector_state']} "
                            f"ATR={status['atr']:.4f} "
                            f"trades={status['daily_trades']} "
                            f"PnL=${status['daily_pnl']:+.2f}")
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

    def _cleanup(self):
        """Close all open positions on shutdown."""
        for pair, engine in self.engines.items():
            if engine.in_trade:
                self.log.warning(f"{pair}: Open trade at shutdown — closing")
                if not self.live_cfg.dry_run:
                    engine._trade_manager.force_close(
                        current_price=0.0,
                        reason=ExitReason.SHUTDOWN,
                        current_bar_index=engine.bar_count,
                    )


# ── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="V11 Live Trader")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without submitting orders")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002)
    parser.add_argument("--client-id", type=int, default=11)
    parser.add_argument("--instruments", nargs="+",
                        default=["XAUUSD", "EURUSD", "USDJPY"],
                        help="Instruments to trade")
    args = parser.parse_args()

    # Build instrument list
    instruments = []
    for name in args.instruments:
        name = name.upper()
        if name in INSTRUMENT_MAP:
            inst_cfg, _ = INSTRUMENT_MAP[name]
            instruments.append(inst_cfg)
        else:
            print(f"Unknown instrument: {name}")
            sys.exit(1)

    live_cfg = LiveConfig(
        ibkr_host=args.host,
        ibkr_port=args.port,
        ibkr_client_id=args.client_id,
        instruments=instruments,
        dry_run=args.dry_run,
    )
    live_cfg.validate()

    log_dir = ROOT / "v11" / "live" / "logs"
    log = setup_logging(log_dir)

    log.info("=" * 60)
    log.info("V11 — Darvas Box + Volume Imbalance + LLM Filter")
    log.info(f"  Instruments: {[i.pair_name for i in instruments]}")
    log.info(f"  Dry-run:     {live_cfg.dry_run}")
    log.info(f"  LLM:         {live_cfg.llm_model}")
    log.info(f"  Confidence:  >= {live_cfg.llm_confidence_threshold}")
    log.info("=" * 60)

    trader = V11LiveTrader(live_cfg, log)
    trader.run()


if __name__ == "__main__":
    main()
