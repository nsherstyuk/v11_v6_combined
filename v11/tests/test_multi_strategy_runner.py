"""
Tests for MultiStrategyRunner — Orchestrator for multi-strategy portfolio.

Design decisions tested:
    1. Adding strategies creates engine instances correctly
    2. Both strategies on same instrument share one TradeManager
    3. Different instruments get separate feeds and TradeManagers
    4. Bars routed to all strategies on the same instrument
    5. Historical seeding reaches all strategies
    6. get_all_status reports all strategies
    7. reset_daily clears risk manager and trade manager counters
    8. get_feed_pairs returns registered instruments
"""
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

import pytest

from v11.core.types import Bar, FilterDecision
from v11.config.strategy_config import EURUSD_CONFIG, XAUUSD_CONFIG
from v11.config.live_config import EURUSD_INSTRUMENT, XAUUSD_INSTRUMENT, LiveConfig
from v11.live.multi_strategy_runner import MultiStrategyRunner
from v11.live.risk_manager import RiskManager


def _bar(minute: int, price: float = 1.1000) -> Bar:
    ts = datetime(2025, 1, 2, 10, minute, 0, tzinfo=timezone.utc)
    return Bar(timestamp=ts, open=price, high=price + 0.001,
               low=price - 0.001, close=price,
               buy_volume=50, sell_volume=50, tick_count=100)


@pytest.fixture
def log():
    return logging.getLogger("test_runner")


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.connected = True
    return conn


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.evaluate_signal = AsyncMock(return_value=FilterDecision(
        approved=False, confidence=50, entry_price=1.1, stop_price=1.09,
        target_price=1.12, reasoning="test", risk_flags=[],
    ))
    return llm


@pytest.fixture
def risk_manager(log):
    return RiskManager(
        max_daily_loss=500.0,
        max_daily_trades_per_strategy=10,
        max_concurrent_positions=3,
        log=log,
    )


@pytest.fixture
def runner(mock_conn, mock_llm, risk_manager, log, tmp_path):
    live_cfg = LiveConfig(dry_run=True)
    return MultiStrategyRunner(
        conn=mock_conn,
        llm_filter=mock_llm,
        live_config=live_cfg,
        risk_manager=risk_manager,
        log=log,
        trade_log_dir=str(tmp_path / "trades"),
    )


# ── Adding strategies creates engines ──────────────────────────────────────

class TestAddStrategies:
    def test_add_darvas_strategy(self, runner):
        engine = runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        assert engine.pair_name == "EURUSD"
        assert engine.strategy_name == "Darvas_Breakout"
        assert len(runner.engines) == 1

    def test_add_level_retest_strategy(self, runner):
        engine = runner.add_level_retest_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        assert engine.pair_name == "EURUSD"
        assert engine.strategy_name == "4H_Level_Retest"
        assert len(runner.engines) == 1

    def test_add_both_on_same_instrument(self, runner):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        runner.add_level_retest_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        assert len(runner.engines) == 2
        # Both on same feed
        assert len(runner.feeds) == 1
        assert "EURUSD" in runner.feeds


# ── Shared TradeManager per instrument ─────────────────────────────────────

class TestSharedTradeManager:
    def test_same_instrument_shares_trade_manager(self, runner):
        darvas = runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        retest = runner.add_level_retest_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        # Both engines should share the same TradeManager
        assert darvas._trade_manager is retest._trade_manager


# ── Separate feeds per instrument ──────────────────────────────────────────

class TestSeparateFeeds:
    def test_different_instruments_get_different_feeds(self, runner):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        runner.add_darvas_strategy(XAUUSD_CONFIG, XAUUSD_INSTRUMENT)
        assert len(runner.feeds) == 2
        assert "EURUSD" in runner.feeds
        assert "XAUUSD" in runner.feeds
        # Different TradeManagers
        eu_tm = runner.feeds["EURUSD"].trade_manager
        xa_tm = runner.feeds["XAUUSD"].trade_manager
        assert eu_tm is not xa_tm


# ── Feed routing ───────────────────────────────────────────────────────────

class TestFeedRouting:
    @pytest.mark.asyncio
    async def test_bar_routed_to_both_strategies(self, runner):
        darvas = runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        retest = runner.add_level_retest_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        bar = _bar(0)
        initial_darvas_count = darvas.bar_count
        initial_retest_count = retest.bar_count
        await runner.on_bar("EURUSD", bar)
        assert darvas.bar_count == initial_darvas_count + 1
        assert retest.bar_count == initial_retest_count + 1

    @pytest.mark.asyncio
    async def test_bar_not_routed_to_wrong_instrument(self, runner):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        xau = runner.add_darvas_strategy(XAUUSD_CONFIG, XAUUSD_INSTRUMENT)
        bar = _bar(0)
        initial_count = xau.bar_count
        await runner.on_bar("EURUSD", bar)
        assert xau.bar_count == initial_count  # unchanged


# ── Historical seeding ─────────────────────────────────────────────────────

class TestHistoricalSeeding:
    def test_seed_reaches_all_strategies(self, runner):
        darvas = runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        retest = runner.add_level_retest_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        bars = [_bar(i) for i in range(5)]
        runner.seed_historical("EURUSD", bars)
        assert darvas.bar_count == 5
        assert retest.bar_count == 5


# ── get_all_status ─────────────────────────────────────────────────────────

class TestGetAllStatus:
    def test_reports_all_strategies(self, runner):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        runner.add_level_retest_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        status = runner.get_all_status()
        assert 'risk' in status
        assert 'strategies' in status
        assert len(status['strategies']) == 2


# ── reset_daily ────────────────────────────────────────────────────────────

class TestResetDaily:
    def test_resets_risk_and_trade_managers(self, runner, risk_manager):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        # Simulate some activity
        risk_manager.record_trade_entry("EURUSD", "Darvas")
        risk_manager.record_trade_exit("EURUSD", "Darvas", pnl=-50.0)
        runner.reset_daily()
        assert risk_manager.combined_pnl == 0.0
        assert risk_manager.combined_trades == 0


# ── get_feed_pairs ─────────────────────────────────────────────────────────

class TestGetFeedPairs:
    def test_returns_registered_instruments(self, runner):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        runner.add_darvas_strategy(XAUUSD_CONFIG, XAUUSD_INSTRUMENT)
        pairs = runner.get_feed_pairs()
        assert sorted(pairs) == ["EURUSD", "XAUUSD"]

    def test_empty_when_no_strategies(self, runner):
        assert runner.get_feed_pairs() == []


# ── has_open_positions ─────────────────────────────────────────────────────

class TestHasOpenPositions:
    def test_false_initially(self, runner):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        assert runner.has_open_positions() is False

    def test_true_when_position_open(self, runner, risk_manager):
        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        risk_manager.record_trade_entry("EURUSD", "Darvas")
        assert runner.has_open_positions() is True
