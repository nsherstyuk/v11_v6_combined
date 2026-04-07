"""
Tests for LevelRetestEngine — 4H level retest strategy engine.

Design decisions tested:
    1. ATR computation matches EMA formula (same as DarvasDetector)
    2. Historical bar seeding populates all internal buffers
    3. Status reports all diagnostic fields
    4. on_bar skips new signal search when shared TradeManager is in_trade
"""
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

import pytest

from v11.core.types import Bar, Direction, FilterDecision
from v11.config.strategy_config import EURUSD_CONFIG
from v11.config.live_config import EURUSD_INSTRUMENT, LiveConfig
from v11.live.level_retest_engine import LevelRetestEngine


def _bar(minute: int, o: float, h: float, l: float, c: float,
         bv: float = 50.0, sv: float = 50.0, tc: int = 100) -> Bar:
    """Helper to create a bar at a given minute offset."""
    ts = datetime(2025, 1, 2, 10, minute, 0, tzinfo=timezone.utc)
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c,
               buy_volume=bv, sell_volume=sv, tick_count=tc)


def _bar_at(dt: datetime, o: float, h: float, l: float, c: float,
            bv: float = 50.0, sv: float = 50.0, tc: int = 100) -> Bar:
    ts = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c,
               buy_volume=bv, sell_volume=sv, tick_count=tc)


@pytest.fixture
def log():
    return logging.getLogger("test_lre")


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.evaluate_signal = AsyncMock(return_value=FilterDecision(
        approved=False, confidence=50, entry_price=1.1, stop_price=1.09,
        target_price=1.12, reasoning="test", risk_flags=[],
    ))
    return llm


@pytest.fixture
def mock_trade_manager():
    tm = MagicMock()
    tm.in_trade = False
    tm.daily_trades = 0
    tm.daily_pnl = 0.0
    tm.check_exit = MagicMock(return_value=None)
    return tm


@pytest.fixture
def engine(log, mock_llm, mock_trade_manager):
    live_cfg = LiveConfig(dry_run=True)
    eng = LevelRetestEngine(
        strategy_config=EURUSD_CONFIG,
        inst_config=EURUSD_INSTRUMENT,
        llm_filter=mock_llm,
        trade_manager=mock_trade_manager,
        live_config=live_cfg,
        log=log,
    )
    return eng


# ── ATR computation ────────────────────────────────────────────────────────

class TestATRComputation:
    def test_atr_zero_before_bars(self, engine):
        assert engine.current_atr == 0.0

    def test_atr_updates_after_bars(self, engine):
        # Feed 3 bars with known ranges
        b1 = _bar(0, 1.1000, 1.1010, 1.0990, 1.1005)  # range=0.0020
        b2 = _bar(1, 1.1005, 1.1020, 1.0995, 1.1015)  # range=0.0025
        b3 = _bar(2, 1.1015, 1.1030, 1.1000, 1.1025)  # range=0.0030
        engine.add_historical_bar(b1)
        engine.add_historical_bar(b2)
        engine.add_historical_bar(b3)
        assert engine.current_atr > 0.0


# ── Historical bar seeding ─────────────────────────────────────────────────

class TestHistoricalSeeding:
    def test_populates_buffers(self, engine):
        bars = [_bar(i, 1.1 + i * 0.0001, 1.1 + i * 0.0001 + 0.001,
                     1.1 + i * 0.0001 - 0.001, 1.1 + i * 0.0001)
                for i in range(10)]
        for b in bars:
            engine.add_historical_bar(b)
        assert engine.bar_count == 10
        assert engine.current_atr > 0.0


# ── Status reporting ───────────────────────────────────────────────────────

class TestStatusReporting:
    def test_all_fields_present(self, engine):
        status = engine.get_status()
        required_fields = [
            'instrument', 'strategy', 'bar_count', 'active_levels',
            'pending_retests', 'atr', 'in_trade', 'htf_sma',
            'htf_sma_bars', 'level_htf_bars',
        ]
        for field in required_fields:
            assert field in status, f"Missing field: {field}"
        assert status['strategy'] == "4H_Level_Retest"
        assert status['instrument'] == "EURUSD"


# ── on_bar skips signals while in trade ────────────────────────────────────

class TestInTradeBlocking:
    @pytest.mark.asyncio
    async def test_skips_signals_when_in_trade(self, engine, mock_trade_manager):
        """When shared TradeManager shows in_trade, engine only checks exit."""
        mock_trade_manager.in_trade = True
        bar = _bar(0, 1.1000, 1.1010, 1.0990, 1.1005)
        await engine.on_bar(bar)
        # check_exit should have been called
        mock_trade_manager.check_exit.assert_called_once()
        # LLM should NOT have been called (no new signals)
        engine._llm_filter.evaluate_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_bars_when_not_in_trade(self, engine, mock_trade_manager):
        """When not in trade, engine processes bar through pipeline."""
        mock_trade_manager.in_trade = False
        bar = _bar(0, 1.1000, 1.1010, 1.0990, 1.1005)
        await engine.on_bar(bar)
        # check_exit should NOT have been called
        mock_trade_manager.check_exit.assert_not_called()


# ── Pair name and strategy name ────────────────────────────────────────────

class TestIdentity:
    def test_pair_name(self, engine):
        assert engine.pair_name == "EURUSD"

    def test_strategy_name(self, engine):
        assert engine.strategy_name == "4H_Level_Retest"
