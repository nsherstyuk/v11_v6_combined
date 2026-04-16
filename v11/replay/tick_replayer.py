"""TickReplayer — Replay logged tick data through the V11 strategy pipeline.

Reads CSV tick files produced by TickLogger and feeds each mid-price
through a BarAggregator, routing completed 1-min bars to the same
engine stack as the live system (Darvas, LevelRetest, ORB).

Usage:
    python -m v11.replay.run_tick_replay --start 2026-04-15
"""
from __future__ import annotations

import csv
import gzip
import heapq
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Optional

log = logging.getLogger("tick_replayer")


# ── CSV loading ──────────────────────────────────────────────────────────────

def _parse_float(v: str) -> Optional[float]:
    """Return float or None for blank/invalid fields."""
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _iter_file(
    path: Path,
    pair: str,
) -> Generator[tuple, None, None]:
    """Yield tick tuples from one CSV or CSV.GZ file."""
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mid = _parse_float(row.get("mid", ""))
                if mid is None:
                    continue   # skip rows with no usable price
                ts_str = row.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                yield (
                    ts, pair, mid,
                    _parse_float(row.get("bid", "")),
                    _parse_float(row.get("ask", "")),
                    _parse_float(row.get("last", "")),
                    _parse_float(row.get("bid_size", "")),
                    _parse_float(row.get("ask_size", "")),
                    _parse_float(row.get("last_size", "")),
                )
    except Exception as exc:
        log.warning("Failed to read tick file %s: %s", path, exc)


def load_ticks(
    base_dir: Path,
    instruments: list[str],
    start: date,
    end: date,
) -> Generator[tuple, None, None]:
    """Yield (ts, pair, mid, bid, ask, last, bid_size, ask_size, last_size)
    tuples in ascending timestamp order across all instruments and dates.

    Missing files are skipped with a WARNING log. Rows with no mid price
    are discarded. Accepts both .csv and .csv.gz files; .gz takes precedence.
    """
    iterators = []
    current = start
    while current <= end:
        for pair in instruments:
            gz_path = base_dir / pair / f"{current}.csv.gz"
            csv_path = base_dir / pair / f"{current}.csv"
            if gz_path.exists():
                iterators.append(_iter_file(gz_path, pair))
            elif csv_path.exists():
                iterators.append(_iter_file(csv_path, pair))
            else:
                log.warning("No tick file for %s %s", pair, current)
        current += timedelta(days=1)

    yield from heapq.merge(*iterators, key=lambda t: t[0])


# ── TickReplayer ─────────────────────────────────────────────────────────────

from ..execution.bar_aggregator import BarAggregator
from ..execution.trade_manager import TradeManager
from ..config.live_config import (
    EURUSD_INSTRUMENT, XAUUSD_INSTRUMENT, USDJPY_INSTRUMENT, LiveConfig,
)
from ..config.strategy_config import EURUSD_CONFIG, XAUUSD_CONFIG, USDJPY_CONFIG
from ..live.live_engine import InstrumentEngine
from ..live.level_retest_engine import LevelRetestEngine
from ..live.risk_manager import RiskManager
from ..llm.passthrough_filter import PassthroughFilter
from .config import TickReplayConfig
from .stub_connection import StubIBKRConnection

_INSTRUMENT_CONFIGS = {
    "EURUSD": EURUSD_INSTRUMENT,
    "XAUUSD": XAUUSD_INSTRUMENT,
    "USDJPY": USDJPY_INSTRUMENT,
}
_STRATEGY_CONFIGS = {
    "EURUSD": EURUSD_CONFIG,
    "XAUUSD": XAUUSD_CONFIG,
    "USDJPY": USDJPY_CONFIG,
}


class TickReplayer:
    """Replays logged IBKR tick CSV files through the V11 strategy pipeline.

    For each instrument: feeds ticks through a BarAggregator, routes
    completed 1-min bars to Darvas / LevelRetest / ORB engines.
    LLM calls are live (passthrough or live Grok) — no caching.
    No sleep() calls anywhere; replay runs at CPU speed.
    """

    def __init__(self, config: TickReplayConfig) -> None:
        self._config = config
        config.validate()

        self._tick_dir = Path(config.tick_dir)
        self._instruments = config.instruments
        self._start = date.fromisoformat(config.start_date)
        self._end = date.fromisoformat(config.end_date)
        self._output_dir = Path(config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Shared infrastructure (dry_run — no IBKR)
        self._conn = StubIBKRConnection()
        self._llm_filter = self._build_llm_filter()
        self._live_config = LiveConfig(
            dry_run=True,
            llm_confidence_threshold=config.llm_confidence_threshold,
            max_daily_loss=config.max_daily_loss,
            max_daily_trades=config.max_daily_trades,
            max_concurrent_positions=config.max_concurrent_positions,
        )
        self._risk_manager = RiskManager(
            max_daily_loss=config.max_daily_loss,
            max_daily_trades_per_strategy=config.max_daily_trades,
            max_concurrent_positions=config.max_concurrent_positions,
            log=log,
        )

        # Per-instrument: BarAggregator + engines + trade managers
        self._aggregators: dict[str, BarAggregator] = {}
        self._engines: dict[str, list] = {}
        self._trade_managers: dict[str, TradeManager] = {}
        self._current_date: dict[str, str] = {}

        for pair in self._instruments:
            self._aggregators[pair] = BarAggregator()
            self._engines[pair] = self._build_engines(pair)

    def _build_llm_filter(self):
        if self._config.llm_mode == "passthrough":
            return PassthroughFilter()
        from ..llm.grok_filter import GrokFilter
        return GrokFilter(
            api_key=self._config.grok_api_key,
            model=self._config.grok_model,
            base_url=self._config.llm_base_url,
            log_dir=str(self._output_dir / "grok_logs"),
        )

    def _build_engines(self, pair: str) -> list:
        """Create strategy engines for one instrument."""
        strategy_cfg = _STRATEGY_CONFIGS.get(pair)
        inst_cfg = _INSTRUMENT_CONFIGS.get(pair)
        if strategy_cfg is None or inst_cfg is None:
            raise ValueError(f"No strategy/instrument config for {pair}")

        tm = TradeManager(
            conn=self._conn,
            inst=inst_cfg,
            log=log,
            trade_log_dir=self._output_dir / "trades",
            dry_run=True,
        )
        self._trade_managers[pair] = tm

        darvas = InstrumentEngine(
            strategy_config=strategy_cfg,
            inst_config=inst_cfg,
            llm_filter=self._llm_filter,
            trade_manager=tm,
            live_config=self._live_config,
            log=log,
        )
        darvas.strategy_name = "Darvas_Breakout"
        darvas._risk_check = self._risk_manager.can_trade

        retest = LevelRetestEngine(
            strategy_config=strategy_cfg,
            inst_config=inst_cfg,
            llm_filter=self._llm_filter,
            trade_manager=tm,
            live_config=self._live_config,
            log=log,
        )
        retest._risk_check = self._risk_manager.can_trade

        engines: list = [darvas, retest]

        # ORB for XAUUSD
        if pair == "XAUUSD":
            from ..v6_orb.config import StrategyConfig as V6StrategyConfig
            from .replay_orb import ReplayORBAdapter
            v6_cfg = V6StrategyConfig(
                instrument="XAUUSD",
                velocity_filter_enabled=False,
                max_pending_hours=8,
                trade_end_hour=20,
            )
            orb = ReplayORBAdapter(
                v6_config=v6_cfg,
                llm_filter=self._llm_filter,
                llm_confidence_threshold=self._config.llm_confidence_threshold,
                live_config=self._live_config,
                log=log,
            )
            engines.append(orb)

        return engines

    async def run(self) -> dict:
        """Run the full replay. Returns per-instrument stats dict."""
        stats: dict[str, dict] = {
            pair: {"bars": 0}
            for pair in self._instruments
        }

        from .replay_orb import ReplayORBAdapter

        for tick in load_ticks(self._tick_dir, self._instruments,
                               self._start, self._end):
            ts, pair, mid = tick[0], tick[1], tick[2]

            # Feed tick through BarAggregator
            agg = self._aggregators.get(pair)
            if agg is None:
                continue
            bar = agg.on_price(mid, ts)
            if bar is None:
                continue

            stats[pair]["bars"] += 1

            # Daily reset on date change
            date_str = ts.strftime("%Y-%m-%d")
            if self._current_date.get(pair) and date_str != self._current_date[pair]:
                self._risk_manager.reset_daily()
                tm = self._trade_managers.get(pair)
                if tm:
                    tm.reset_daily()
            self._current_date[pair] = date_str

            # Route bar through engines
            for engine in self._engines.get(pair, []):
                engine.on_price(mid, ts)
                await engine.on_bar(bar)

        self._print_summary(stats)
        return stats

    def _print_summary(self, stats: dict) -> None:
        print(f"\n── Tick Replay {self._config.start_date} → "
              f"{self._config.end_date} ({', '.join(self._instruments)}) ──")
        total_pnl = 0.0
        for pair, s in stats.items():
            tm = self._trade_managers.get(pair)
            trades = tm.daily_trades if tm else 0
            pnl = tm.daily_pnl if tm else 0.0
            total_pnl += pnl
            print(f"  {pair:8s}  bars={s['bars']}  trades={trades}"
                  f"  PnL=${pnl:+.2f}")
        print(f"  Total PnL (dry run): ${total_pnl:+.2f}")
        print("─" * 60)
