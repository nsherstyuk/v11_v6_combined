"""CLI entry point for historical replay.

Usage:
    python -m v11.replay.run_replay --instrument EURUSD --start 2025-01-01 --end 2025-03-31
    python -m v11.replay.run_replay --instrument EURUSD USDJPY --start 2025-01-01 --end 2025-03-31 --llm cached
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime

# ── Python 3.14 compatibility (same patch as run_live.py) ──────────────────
# Python 3.14 changed asyncio.wait_for to use asyncio.timeout() internally,
# which requires being inside a running task. ib_insync calls wait_for from
# a sync context via nest_asyncio, which doesn't set current_task properly.
try:
    asyncio.get_event_loop()
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

import nest_asyncio
nest_asyncio.apply()

# Project root
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from v11.backtest.data_loader import load_instrument_bars
from v11.replay.config import ReplayConfig
from v11.replay.replay_runner import ReplayRunner


def setup_logging(verbosity: str) -> None:
    level = logging.DEBUG if verbosity == "verbose" else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy loggers
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay historical bars through V11 live strategy code")

    p.add_argument("--instrument", nargs="+", required=True,
                   help="Instruments to replay (e.g. EURUSD USDJPY)")
    p.add_argument("--start", required=True,
                   help="Start date (YYYY-MM-DD)")
    p.add_argument("--end", required=True,
                   help="End date (YYYY-MM-DD)")
    p.add_argument("--llm", default="passthrough",
                   choices=["passthrough", "live", "cached"],
                   help="LLM filter mode (default: passthrough)")
    p.add_argument("--grok-key", default="",
                   help="Grok API key (or set XAI_API_KEY env var)")
    p.add_argument("--verbosity", default="normal",
                   choices=["quiet", "normal", "verbose"],
                   help="Console output verbosity")
    p.add_argument("--output-dir", default="v11/replay/results",
                   help="Output directory for results")
    p.add_argument("--seed-bars", type=int, default=500,
                   help="Bars to seed before replay starts")
    p.add_argument("--confidence", type=int, default=75,
                   help="LLM confidence threshold")

    return p.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbosity)
    log = logging.getLogger("v11_replay")

    # Resolve API key from args or env
    grok_key = args.grok_key or os.environ.get("XAI_API_KEY", "") or os.environ.get("GROK_API_KEY", "")

    config = ReplayConfig(
        instruments=[i.upper() for i in args.instrument],
        start_date=args.start,
        end_date=args.end,
        llm_mode=args.llm,
        grok_api_key=grok_key,
        llm_confidence_threshold=args.confidence,
        output_dir=args.output_dir,
        event_verbosity=args.verbosity,
        seed_bars=args.seed_bars,
    )

    print(f"Replay: {', '.join(config.instruments)} "
          f"from {config.start_date} to {config.end_date} "
          f"(LLM: {config.llm_mode})")
    print()

    # Load historical data
    bars_by_instrument = {}
    for instrument in config.instruments:
        start_dt = datetime.strptime(config.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(config.end_date, "%Y-%m-%d")

        log.info(f"Loading {instrument} bars from {config.start_date} to {config.end_date}...")
        bars = load_instrument_bars(instrument, start=start_dt, end=end_dt)
        log.info(f"Loaded {len(bars)} bars for {instrument}")

        if len(bars) < config.seed_bars + 100:
            log.error(f"{instrument}: only {len(bars)} bars, need at least "
                      f"{config.seed_bars + 100} (seed + 100 replay). Skipping.")
            continue

        bars_by_instrument[instrument] = bars

    if not bars_by_instrument:
        log.error("No instruments with sufficient data. Exiting.")
        sys.exit(1)

    # Run replay
    runner = ReplayRunner(config)
    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(runner.run(bars_by_instrument))

    # Print final stats
    m = result.get("metrics", {})
    print(f"\nDone. {m.get('total_trades', 0)} trades, "
          f"PnL=${m.get('net_pnl', 0):+.2f}")


if __name__ == "__main__":
    main()
