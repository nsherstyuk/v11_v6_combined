"""
MultiStrategyRunner — Orchestrates multiple strategies across instruments.

Design (V11_DESIGN.md §11):
    Single process, shared infrastructure:
        - IBKRConnection (one gateway, multi-instrument)
        - RiskManager (combined daily loss limit)
        - TradeLogger (unified CSV)
        - LLM Filter (shared Grok instance)

    Per-instrument data feeds:
        - BarAggregator (tick → 1-min bars)
        - Bars routed to all strategies on that instrument

    EURUSD pipeline:
        - Strategy A: DarvasBreakout (InstrumentEngine)
        - Strategy B: 4HLevelRetest (LevelRetestEngine)
        - Shared TradeManager → max 1 position per instrument

    XAUUSD pipeline:
        - Strategy C: V6 ORB (Phase 5 — ORBAdapter)

    Conflict resolution:
        - Same instrument, two signals at once → shared TradeManager blocks second
        - Cross-instrument positions → allowed, tracked by RiskManager
        - Combined daily loss limit → all strategies pause
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Protocol

from ..core.types import Bar
from ..config.live_config import InstrumentConfig, LiveConfig
from ..config.strategy_config import StrategyConfig
from ..execution.bar_aggregator import BarAggregator
from ..execution.trade_manager import TradeManager
from ..execution.ibkr_connection import IBKRConnection
from ..llm.base import LLMFilter
from .risk_manager import RiskManager
from .live_engine import InstrumentEngine
from .level_retest_engine import LevelRetestEngine
from .orb_adapter import ORBAdapter
from ..v6_orb.config import StrategyConfig as V6StrategyConfig


# ── Strategy Protocol ────────────────────────────────────────────────────────

class StrategyEngine(Protocol):
    """Minimal interface that all strategy engines must satisfy."""

    @property
    def pair_name(self) -> str: ...

    @property
    def in_trade(self) -> bool: ...

    @property
    def bar_count(self) -> int: ...

    @property
    def strategy_name(self) -> str: ...

    async def on_bar(self, bar: Bar) -> None: ...

    def on_price(self, price: float, now: datetime) -> None: ...

    def add_historical_bar(self, bar: Bar) -> None: ...

    def get_status(self) -> dict: ...


# ── Instrument Data Feed ─────────────────────────────────────────────────────

class InstrumentFeed:
    """Shared data feed for one instrument. Owns the BarAggregator and
    routes completed bars to all strategies registered on this instrument.

    Also owns the shared TradeManager for this instrument.
    """

    def __init__(
        self,
        inst_config: InstrumentConfig,
        trade_manager: TradeManager,
        log: logging.Logger,
    ):
        self.inst_config = inst_config
        self.trade_manager = trade_manager
        self._log = log
        self._aggregator = BarAggregator()
        self._strategies: List = []  # List of strategy engines

    @property
    def pair_name(self) -> str:
        return self.inst_config.pair_name

    def add_strategy(self, engine) -> None:
        """Register a strategy engine on this instrument's data feed."""
        self._strategies.append(engine)

    def on_price(self, price: float, now: datetime) -> Optional[Bar]:
        """Process a price tick. Returns completed Bar if minute boundary crossed.

        Also forwards price to all strategies for slippage tracking.
        """
        for engine in self._strategies:
            if hasattr(engine, 'on_price'):
                engine.on_price(price, now)
        return self._aggregator.on_price(price, now)

    async def on_bar(self, bar: Bar) -> None:
        """Route a completed bar to all strategies on this instrument.

        Strategies are processed sequentially. If one enters a trade,
        the shared TradeManager blocks subsequent strategies automatically.
        """
        for engine in self._strategies:
            await engine.on_bar(bar)

    def add_historical_bar(self, bar: Bar) -> None:
        """Seed historical bar to all strategies."""
        for engine in self._strategies:
            engine.add_historical_bar(bar)


# ── Multi-Strategy Runner ────────────────────────────────────────────────────

class MultiStrategyRunner:
    """Orchestrates multiple strategies across multiple instruments.

    Interface (narrow):
        add_strategy(pair_name, strategy_config, inst_config, strategy_type)
        on_price(pair_name, price, now) -> Optional[Bar]
        on_bar(pair_name, bar) -> None
        seed_historical(pair_name, bars) -> None
        get_all_status() -> dict
        reset_daily() -> None

    The runner owns:
        - RiskManager (combined risk across all strategies)
        - Per-instrument InstrumentFeed (shared BarAggregator + TradeManager)
        - Strategy engine instances
    """

    def __init__(
        self,
        conn: IBKRConnection,
        llm_filter: LLMFilter,
        live_config: LiveConfig,
        risk_manager: RiskManager,
        log: logging.Logger,
        trade_log_dir: str = "",
    ):
        self._conn = conn
        self._llm_filter = llm_filter
        self._live_config = live_config
        self._risk_manager = risk_manager
        self._log = log
        self._trade_log_dir = trade_log_dir

        # Per-instrument feeds: pair_name -> InstrumentFeed
        self._feeds: Dict[str, InstrumentFeed] = {}

        # All strategy engines (flat list for status/shutdown)
        self._engines: List = []

    @property
    def risk_manager(self) -> RiskManager:
        return self._risk_manager

    @property
    def feeds(self) -> Dict[str, InstrumentFeed]:
        return self._feeds

    @property
    def engines(self) -> List:
        return self._engines

    def _get_or_create_feed(
        self,
        inst_config: InstrumentConfig,
    ) -> InstrumentFeed:
        """Get existing feed for instrument or create a new one."""
        pair = inst_config.pair_name
        if pair not in self._feeds:
            from pathlib import Path
            trade_log_dir = Path(self._trade_log_dir) if self._trade_log_dir else Path("v11/live/trades")

            trade_manager = TradeManager(
                conn=self._conn,
                inst=inst_config,
                log=self._log,
                trade_log_dir=trade_log_dir,
                dry_run=self._live_config.dry_run,
            )
            self._feeds[pair] = InstrumentFeed(
                inst_config=inst_config,
                trade_manager=trade_manager,
                log=self._log,
            )
        return self._feeds[pair]

    def add_darvas_strategy(
        self,
        strategy_config: StrategyConfig,
        inst_config: InstrumentConfig,
    ) -> InstrumentEngine:
        """Add a Darvas breakout strategy for an instrument.

        Returns the created InstrumentEngine for diagnostics.
        """
        feed = self._get_or_create_feed(inst_config)

        engine = InstrumentEngine(
            strategy_config=strategy_config,
            inst_config=inst_config,
            llm_filter=self._llm_filter,
            trade_manager=feed.trade_manager,
            live_config=self._live_config,
            log=self._log,
        )
        # Tag with strategy_name for identification
        engine.strategy_name = "Darvas_Breakout"

        # Wire risk check callback
        engine._risk_check = self._risk_manager.can_trade

        feed.add_strategy(engine)
        self._engines.append(engine)

        self._log.info(
            f"RUNNER: Added Darvas_Breakout on {feed.pair_name}")
        return engine

    def add_level_retest_strategy(
        self,
        strategy_config: StrategyConfig,
        inst_config: InstrumentConfig,
    ) -> LevelRetestEngine:
        """Add a 4H level retest strategy for an instrument.

        Returns the created LevelRetestEngine for diagnostics.
        """
        feed = self._get_or_create_feed(inst_config)

        engine = LevelRetestEngine(
            strategy_config=strategy_config,
            inst_config=inst_config,
            llm_filter=self._llm_filter,
            trade_manager=feed.trade_manager,
            live_config=self._live_config,
            log=self._log,
        )

        # Wire risk check callback
        engine._risk_check = self._risk_manager.can_trade

        feed.add_strategy(engine)
        self._engines.append(engine)

        self._log.info(
            f"RUNNER: Added {engine.strategy_name} on {feed.pair_name}")
        return engine

    def add_orb_strategy(
        self,
        v6_config: V6StrategyConfig,
        inst_config: InstrumentConfig,
        state_dir: str = "",
        poll_interval: float = 2.0,
    ) -> ORBAdapter:
        """Add V6 ORB strategy for an instrument (typically XAUUSD).

        Unlike Darvas/Retest strategies, ORB is tick-driven and uses its
        own V6 execution engine (IBKRExecutionEngine). It does NOT use
        V11's TradeManager or BarAggregator.

        The adapter plugs into the InstrumentFeed so it receives
        on_price() calls from the shared data pipeline. on_bar() is a
        no-op since V6 operates on raw ticks.

        Returns the created ORBAdapter for diagnostics.
        """
        feed = self._get_or_create_feed(inst_config)

        # ORB needs raw ib instance + qualified contract
        contract = self._conn._contracts.get(inst_config.pair_name)
        if contract is None:
            raise ValueError(
                f"Contract not qualified for {inst_config.pair_name}. "
                f"Call conn.qualify_contract() first.")

        adapter = ORBAdapter(
            ib=self._conn.ib,
            contract=contract,
            v6_config=v6_config,
            risk_manager=self._risk_manager,
            log=self._log,
            state_dir=state_dir,
            dry_run=self._live_config.dry_run,
            poll_interval=poll_interval,
        )

        feed.add_strategy(adapter)
        self._engines.append(adapter)

        self._log.info(
            f"RUNNER: Added {adapter.strategy_name} on {feed.pair_name}")
        return adapter

    def on_price(self, pair_name: str, price: float, now: datetime) -> Optional[Bar]:
        """Process a price tick for an instrument. Returns Bar if minute crossed."""
        feed = self._feeds.get(pair_name)
        if feed is None:
            return None
        return feed.on_price(price, now)

    async def on_bar(self, pair_name: str, bar: Bar) -> None:
        """Route a completed bar to all strategies on this instrument."""
        feed = self._feeds.get(pair_name)
        if feed is None:
            return
        await feed.on_bar(bar)

    def seed_historical(self, pair_name: str, bars: List[Bar]) -> None:
        """Seed historical bars to all strategies on an instrument."""
        feed = self._feeds.get(pair_name)
        if feed is None:
            self._log.warning(f"RUNNER: No feed for {pair_name}, skipping seed")
            return
        for bar in bars:
            feed.add_historical_bar(bar)
        self._log.info(
            f"RUNNER: Seeded {len(bars)} bars on {pair_name}")

    def reset_daily(self) -> None:
        """Reset all daily counters across risk manager and trade managers."""
        self._risk_manager.reset_daily()
        for feed in self._feeds.values():
            feed.trade_manager.reset_daily()
        self._log.info("RUNNER: Daily reset complete")

    def get_all_status(self) -> dict:
        """Comprehensive status across all strategies and instruments."""
        strategy_status = []
        for engine in self._engines:
            strategy_status.append(engine.get_status())
        return {
            'risk': self._risk_manager.get_status(),
            'strategies': strategy_status,
            'instruments': list(self._feeds.keys()),
        }

    def get_feed_pairs(self) -> List[str]:
        """List of instrument pair names with active feeds."""
        return list(self._feeds.keys())

    def has_open_positions(self) -> bool:
        """True if any strategy on any instrument has an open position."""
        return self._risk_manager.open_position_count > 0
