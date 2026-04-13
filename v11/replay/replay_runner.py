"""ReplayRunner — Feed historical bars through live strategy engines.

Reuses InstrumentEngine, LevelRetestEngine, RiskManager, TradeManager
exactly as the live system does. Replaces IBKR with stub connection
and BarAggregator with direct bar injection.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..core.types import Bar
from ..config.strategy_config import StrategyConfig, EURUSD_CONFIG, USDJPY_CONFIG, XAUUSD_CONFIG
from ..config.live_config import (
    LiveConfig, InstrumentConfig,
    EURUSD_INSTRUMENT, USDJPY_INSTRUMENT, XAUUSD_INSTRUMENT,
)
from ..execution.trade_manager import TradeManager
from ..live.live_engine import InstrumentEngine
from ..live.level_retest_engine import LevelRetestEngine
from ..live.risk_manager import RiskManager
from ..llm.passthrough_filter import PassthroughFilter

from .config import ReplayConfig
from .stub_connection import StubIBKRConnection
from .cached_filter import CachedFilter
from .event_logger import EventLogger
from .metrics import compute_metrics

log = logging.getLogger("v11_replay")

# Map instrument names to configs
STRATEGY_CONFIGS = {
    "EURUSD": EURUSD_CONFIG,
    "USDJPY": USDJPY_CONFIG,
    "XAUUSD": XAUUSD_CONFIG,
}

INSTRUMENT_CONFIGS = {
    "EURUSD": EURUSD_INSTRUMENT,
    "USDJPY": USDJPY_INSTRUMENT,
    "XAUUSD": XAUUSD_INSTRUMENT,
}


class ReplayRunner:
    """Replays historical bars through the live strategy pipeline.

    Usage:
        runner = ReplayRunner(config)
        result = await runner.run(bars_by_instrument)
    """

    def __init__(self, config: ReplayConfig):
        self._config = config
        config.validate()

        # Output directory
        self._output_dir = Path(config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Stub connection (TradeManager never uses it in dry_run)
        self._conn = StubIBKRConnection()

        # LLM filter
        self._llm_filter = self._build_llm_filter()

        # Live config (controls thresholds, buffer sizes, etc.)
        self._live_config = LiveConfig(
            dry_run=True,
            llm_confidence_threshold=config.llm_confidence_threshold,
            max_daily_trades=config.max_daily_trades,
            max_daily_loss=config.max_daily_loss,
            max_concurrent_positions=config.max_concurrent_positions,
        )

        # Risk manager
        self._risk_manager = RiskManager(
            max_daily_loss=config.max_daily_loss,
            max_daily_trades_per_strategy=config.max_daily_trades,
            max_concurrent_positions=config.max_concurrent_positions,
            log=log,
        )

        # Event logger
        self._event_logger = EventLogger(
            output_path=str(self._output_dir / "replay_events.jsonl"),
            verbosity=config.event_verbosity,
        )

        # Per-instrument engines (built in run())
        self._engines: Dict[str, List] = {}  # pair -> [engine, ...]
        self._trade_managers: Dict[str, TradeManager] = {}

    def _build_llm_filter(self):
        """Build LLM filter based on config mode."""
        if self._config.llm_mode == "passthrough":
            return PassthroughFilter()

        if self._config.llm_mode == "cached":
            inner = None
            if self._config.grok_api_key:
                from ..llm.grok_filter import GrokFilter
                inner = GrokFilter(
                    api_key=self._config.grok_api_key,
                    model=self._config.grok_model,
                )
            return CachedFilter(
                inner_filter=inner,
                cache_path=self._config.llm_cache_path,
            )

        if self._config.llm_mode == "live":
            from ..llm.grok_filter import GrokFilter
            return GrokFilter(
                api_key=self._config.grok_api_key,
                model=self._config.grok_model,
            )

        return PassthroughFilter()

    def _build_engines(self, instrument: str) -> List:
        """Create Darvas + LevelRetest engines for one instrument."""
        strategy_config = STRATEGY_CONFIGS.get(instrument)
        inst_config = INSTRUMENT_CONFIGS.get(instrument)
        if strategy_config is None or inst_config is None:
            raise ValueError(f"No config for instrument: {instrument}")

        trade_manager = TradeManager(
            conn=self._conn,
            inst=inst_config,
            log=log,
            trade_log_dir=self._output_dir / "trades",
            dry_run=True,
        )
        self._trade_managers[instrument] = trade_manager

        darvas = InstrumentEngine(
            strategy_config=strategy_config,
            inst_config=inst_config,
            llm_filter=self._llm_filter,
            trade_manager=trade_manager,
            live_config=self._live_config,
            log=log,
        )
        darvas.strategy_name = "Darvas_Breakout"
        darvas._risk_check = self._risk_manager.can_trade

        retest = LevelRetestEngine(
            strategy_config=strategy_config,
            inst_config=inst_config,
            llm_filter=self._llm_filter,
            trade_manager=trade_manager,
            live_config=self._live_config,
            log=log,
        )
        retest._risk_check = self._risk_manager.can_trade

        return [darvas, retest]

    async def run(self, bars_by_instrument: Dict[str, List[Bar]]) -> dict:
        """Run the full replay.

        Args:
            bars_by_instrument: {"EURUSD": [Bar, ...], "USDJPY": [Bar, ...]}

        Returns:
            Summary dict with bars_processed, trade_count, metrics, etc.
        """
        result = {"bars_processed": {}, "trades": [], "metrics": {}}

        # Build engines for each instrument
        for instrument in self._config.instruments:
            if instrument not in bars_by_instrument:
                log.warning(f"No bars provided for {instrument}, skipping")
                continue
            self._engines[instrument] = self._build_engines(instrument)

        # Process each instrument
        for instrument, bars in bars_by_instrument.items():
            if instrument not in self._engines:
                continue

            engines = self._engines[instrument]
            seed_count = min(self._config.seed_bars, len(bars))
            replay_bars = bars[seed_count:]

            log.info(f"Replay {instrument}: {len(bars)} total bars, "
                     f"{seed_count} seeded, {len(replay_bars)} replayed")

            # Seed historical bars
            for bar in bars[:seed_count]:
                for engine in engines:
                    engine.add_historical_bar(bar)

            self._event_logger.emit(
                "SESSION_START", strategy="ALL", instrument=instrument,
                timestamp=replay_bars[0].timestamp.isoformat() if replay_bars else "",
                data={"total_bars": len(bars), "seed_bars": seed_count,
                      "replay_bars": len(replay_bars)},
            )

            # Track current date for daily resets
            current_date: Optional[str] = None

            # Replay loop
            for i, bar in enumerate(replay_bars):
                bar_date = bar.timestamp.strftime("%Y-%m-%d")

                # Daily reset on date change
                if current_date is not None and bar_date != current_date:
                    self._risk_manager.reset_daily()
                    tm = self._trade_managers[instrument]
                    tm.reset_daily()
                    self._event_logger.emit(
                        "DAILY_RESET", strategy="ALL", instrument=instrument,
                        timestamp=bar.timestamp.isoformat(), data={},
                    )

                    # Force-close any open trade on date boundary
                    if tm.in_trade:
                        record = tm.force_close(
                            bar.close, "DAILY_RESET", tm.entry_bar_index)
                        if record:
                            self._event_logger.emit(
                                "TRADE_EXITED", strategy="ALL",
                                instrument=instrument,
                                timestamp=bar.timestamp.isoformat(),
                                data={
                                    "instrument": instrument,
                                    "strategy": "ALL",
                                    "pnl": record.pnl,
                                    "exit_reason": "DAILY_RESET",
                                    "hold_bars": 0,
                                    "llm_confidence": 0,
                                },
                            )

                # Session gap detection (>30 min gap between bars)
                if i > 0:
                    prev_ts = replay_bars[i - 1].timestamp
                    gap_min = (bar.timestamp - prev_ts).total_seconds() / 60
                    if gap_min > 30:
                        self._event_logger.emit(
                            "SESSION_GAP", strategy="ALL",
                            instrument=instrument,
                            timestamp=bar.timestamp.isoformat(),
                            data={"gap_minutes": gap_min},
                        )

                current_date = bar_date

                # Feed price to engines (slippage tracking)
                for engine in engines:
                    engine.on_price(bar.close, bar.timestamp)

                # Process the bar through all engines
                for engine in engines:
                    tm = self._trade_managers[instrument]
                    was_in_trade = tm.in_trade
                    pnl_before = tm.daily_pnl
                    trades_before = tm.daily_trades

                    await engine.on_bar(bar)

                    # Detect trade entry
                    if not was_in_trade and tm.in_trade:
                        self._event_logger.emit(
                            "TRADE_ENTERED", strategy=engine.strategy_name,
                            instrument=instrument,
                            timestamp=bar.timestamp.isoformat(),
                            data={
                                "direction": tm.direction.value if tm.direction else "?",
                                "entry_price": tm.signal_entry_price,
                                "stop_price": tm.stop_price,
                                "target_price": tm.target_price,
                                "llm_confidence": tm.llm_confidence,
                            },
                        )

                    # Detect trade exit (dual check per review adjustment #5)
                    if tm.daily_trades > trades_before or (was_in_trade and not tm.in_trade):
                        pnl_delta = tm.daily_pnl - pnl_before
                        self._event_logger.emit(
                            "TRADE_EXITED", strategy=engine.strategy_name,
                            instrument=instrument,
                            timestamp=bar.timestamp.isoformat(),
                            data={
                                "instrument": instrument,
                                "strategy": engine.strategy_name,
                                "pnl": pnl_delta,
                                "exit_reason": "check_exit",
                                "hold_bars": 0,
                                "llm_confidence": 0,
                            },
                        )

                # Progress logging every 10000 bars
                if (i + 1) % 10000 == 0:
                    log.info(f"Replay {instrument}: {i + 1}/{len(replay_bars)} bars")

            result["bars_processed"][instrument] = len(replay_bars)

        # Save LLM cache if applicable
        if isinstance(self._llm_filter, CachedFilter):
            self._llm_filter.save()

        # Compute metrics from trade records
        result["event_counts"] = self._event_logger.get_counts()
        result["trade_records"] = self._event_logger.trade_records
        result["metrics"] = compute_metrics(self._event_logger.trade_records)

        # Write summary
        self._write_summary(result)
        self._event_logger.close()

        return result

    def _write_summary(self, result: dict) -> None:
        """Write human-readable summary file."""
        summary_path = self._output_dir / "replay_summary.txt"
        m = result["metrics"]
        counts = result.get("event_counts", {})

        lines = [
            f"Replay: {', '.join(self._config.instruments)} "
            f"{self._config.start_date} to {self._config.end_date} "
            f"(LLM: {self._config.llm_mode})",
            "",
        ]

        for inst, n in result.get("bars_processed", {}).items():
            lines.append(f"  {inst}: {n} bars replayed")
        lines.append("")

        lines.append(f"Events: {dict(counts)}")
        lines.append("")

        lines.append("Metrics:")
        lines.append(f"  Total trades: {m['total_trades']}")
        lines.append(f"  Net PnL: ${m['net_pnl']:+.2f}")
        lines.append(f"  Win rate: {m['win_rate']:.1%}")
        lines.append(f"  Profit factor: {m['profit_factor']:.2f}")
        lines.append(f"  Sharpe: {m['sharpe']:.2f}")
        lines.append(f"  Max drawdown: ${m['max_drawdown']:.2f} ({m['max_drawdown_pct']:.1f}%)")
        lines.append(f"  Avg winner: ${m['avg_winner']:+.2f}")
        lines.append(f"  Avg loser: ${m['avg_loser']:+.2f}")

        summary_path.write_text("\n".join(lines))
        log.info(f"Summary written to {summary_path}")
        print("\n" + "\n".join(lines))
