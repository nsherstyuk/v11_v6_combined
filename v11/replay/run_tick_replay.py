"""CLI entry point for tick-data replay.

Usage:
    # Replay all instruments, date range
    python -m v11.replay.run_tick_replay --start 2026-04-01 --end 2026-04-15

    # Single day
    python -m v11.replay.run_tick_replay --start 2026-04-15

    # Specific instrument with live LLM
    python -m v11.replay.run_tick_replay --start 2026-04-01 --end 2026-04-15 \\
        --instruments EURUSD --llm live
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# ── Python 3.14 compatibility (same as run_live.py) ─────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from v11.replay.config import TickReplayConfig
from v11.replay.tick_replayer import TickReplayer


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("run_tick_replay")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay live-logged tick data through V11 strategy pipeline")

    p.add_argument("--start", required=True,
                   help="First date to replay (YYYY-MM-DD)")
    p.add_argument("--end",
                   help="Last date inclusive (YYYY-MM-DD); defaults to --start")
    p.add_argument("--instruments", nargs="+", default=["EURUSD", "XAUUSD"],
                   help="Instruments to replay (default: EURUSD XAUUSD)")
    p.add_argument("--tick-dir", default="data/ticks",
                   help="Directory containing tick CSV files (default: data/ticks)")
    p.add_argument("--llm", default="passthrough",
                   choices=["passthrough", "live"],
                   help="LLM filter mode (default: passthrough)")
    p.add_argument("--output-dir", default="v11/replay/results",
                   help="Output directory for trade logs and summary")

    return p.parse_args()


def main() -> None:
    args = parse_args()
    log = setup_logging()

    end_date = args.end or args.start

    grok_key = (os.environ.get("OPENROUTER_API_KEY", "") or
                os.environ.get("XAI_API_KEY", "") or
                os.environ.get("GROK_API_KEY", ""))

    cfg = TickReplayConfig(
        instruments=[i.upper() for i in args.instruments],
        start_date=args.start,
        end_date=end_date,
        tick_dir=args.tick_dir,
        llm_mode=args.llm,
        grok_api_key=grok_key,
        output_dir=args.output_dir,
    )

    log.info("Tick replay: %s → %s  instruments=%s  llm=%s",
             cfg.start_date, cfg.end_date,
             cfg.instruments, cfg.llm_mode)

    replayer = TickReplayer(cfg)
    asyncio.run(replayer.run())


if __name__ == "__main__":
    main()
