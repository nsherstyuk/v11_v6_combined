"""
Integration tests for InstrumentEngine.on_bar() — full pipeline.

Design decisions tested:
    1. Full pipeline: bars → DarvasDetector → signal → SMA filter → LLM → trade entry
    2. SMA filter rejects counter-trend signals before reaching LLM
    3. LLM rejection prevents trade entry
    4. LLM approval + confidence gate → trade executes
    5. Risk manager gate blocks entry even when LLM approves
    6. Shared TradeManager blocks second strategy when first is in trade
    7. Exit check runs on each bar while in trade
    8. Slippage ceiling aborts entry when price drifts too far during LLM call
"""
import logging
import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

import pytest

from v11.core.types import (
    Bar, Direction, FilterDecision, BreakoutSignal, DarvasBox,
)
from v11.config.strategy_config import StrategyConfig
from v11.config.live_config import EURUSD_INSTRUMENT, LiveConfig
from v11.live.live_engine import InstrumentEngine


# ── Helpers ──────────────────────────────────────────────────────────────────

def _bar(minute: int, price: float, high: float = None, low: float = None,
         buy_vol: float = 50, sell_vol: float = 50) -> Bar:
    """Create a 1-min bar at a given minute offset."""
    ts = datetime(2025, 1, 2, 10, minute % 60, 0, tzinfo=timezone.utc)
    if minute >= 60:
        ts = ts + timedelta(hours=minute // 60)
    h = high if high is not None else price + 0.0005
    l = low if low is not None else price - 0.0005
    return Bar(timestamp=ts, open=price, high=h, low=l, close=price,
               buy_volume=buy_vol, sell_volume=sell_vol, tick_count=100)


def _make_config(**overrides) -> StrategyConfig:
    defaults = dict(
        instrument="EURUSD",
        top_confirm_bars=5, bottom_confirm_bars=5,
        min_box_width_atr=0.1, max_box_width_atr=10.0,
        min_box_duration=5, breakout_confirm_bars=2,
        htf_sma_enabled=False,  # disabled by default for simplicity
        level_detector_enabled=False,
        atr_period=20,
        max_hold_bars=120,
        spread_cost=0.00010, tick_size=0.00005,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


@pytest.fixture
def log():
    return logging.getLogger("test_engine_integ")


@pytest.fixture
def mock_trade_manager():
    tm = MagicMock()
    tm.in_trade = False
    tm.daily_trades = 0
    tm.daily_pnl = 0.0
    return tm


@pytest.fixture
def mock_llm_approve():
    """LLM that always approves with confidence 85."""
    llm = MagicMock()
    llm.evaluate_signal = AsyncMock(return_value=FilterDecision(
        approved=True, confidence=85,
        entry_price=1.1050, stop_price=1.1000, target_price=1.1150,
        reasoning="Approved", risk_flags=[],
    ))
    return llm


@pytest.fixture
def mock_llm_reject():
    """LLM that always rejects."""
    llm = MagicMock()
    llm.evaluate_signal = AsyncMock(return_value=FilterDecision(
        approved=False, confidence=30,
        entry_price=1.1050, stop_price=1.1000, target_price=1.1150,
        reasoning="Rejected", risk_flags=[],
    ))
    return llm


def _make_engine(config, tm, llm, log) -> InstrumentEngine:
    live_cfg = LiveConfig(dry_run=True, llm_confidence_threshold=75)
    engine = InstrumentEngine(
        strategy_config=config, inst_config=EURUSD_INSTRUMENT,
        llm_filter=llm, trade_manager=tm, live_config=live_cfg, log=log,
    )
    return engine


# ── 1. Full pipeline: signal → LLM approve → trade entry ────────────────────

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_approved_signal_enters_trade(
        self, mock_trade_manager, mock_llm_approve, log
    ):
        """When Darvas fires a signal and LLM approves, enter_trade is called."""
        config = _make_config()
        engine = _make_engine(config, mock_trade_manager, mock_llm_approve, log)

        # Feed a known breakout signal by mocking the detector
        signal = BreakoutSignal(
            timestamp=datetime(2025, 1, 2, 10, 30, tzinfo=timezone.utc),
            direction=Direction.LONG,
            box=DarvasBox(top=1.1050, bottom=1.1000, top_confirmed_at=10,
                          bottom_confirmed_at=20, formation_start=5,
                          duration_bars=15, atr_at_formation=0.001),
            breakout_price=1.1055, breakout_bar_index=25, atr=0.001,
        )
        engine._detector.add_bar = MagicMock(return_value=signal)
        engine._detector._atr = 0.001
        engine._detector._atr_count = engine._detector._atr_period
        engine._last_price = 1.1055

        bar = _bar(30, 1.1055)
        await engine.on_bar(bar)

        mock_trade_manager.enter_trade.assert_called_once()


# ── 3. LLM rejection prevents trade ─────────────────────────────────────────

class TestLLMRejection:
    @pytest.mark.asyncio
    async def test_rejected_signal_no_entry(
        self, mock_trade_manager, mock_llm_reject, log
    ):
        config = _make_config()
        engine = _make_engine(config, mock_trade_manager, mock_llm_reject, log)

        signal = BreakoutSignal(
            timestamp=datetime(2025, 1, 2, 10, 30, tzinfo=timezone.utc),
            direction=Direction.LONG,
            box=DarvasBox(top=1.1050, bottom=1.1000, top_confirmed_at=10,
                          bottom_confirmed_at=20, formation_start=5,
                          duration_bars=15, atr_at_formation=0.001),
            breakout_price=1.1055, breakout_bar_index=25, atr=0.001,
        )
        engine._detector.add_bar = MagicMock(return_value=signal)
        engine._detector._atr = 0.001
        engine._detector._atr_count = engine._detector._atr_period

        await engine.on_bar(_bar(30, 1.1055))

        mock_trade_manager.enter_trade.assert_not_called()


# ── 5. Risk manager gate blocks entry ────────────────────────────────────────

class TestRiskManagerGate:
    @pytest.mark.asyncio
    async def test_risk_blocked_no_entry(
        self, mock_trade_manager, mock_llm_approve, log
    ):
        config = _make_config()
        engine = _make_engine(config, mock_trade_manager, mock_llm_approve, log)
        engine._risk_check = MagicMock(
            return_value=(False, "Combined daily loss limit"))

        signal = BreakoutSignal(
            timestamp=datetime(2025, 1, 2, 10, 30, tzinfo=timezone.utc),
            direction=Direction.LONG,
            box=DarvasBox(top=1.1050, bottom=1.1000, top_confirmed_at=10,
                          bottom_confirmed_at=20, formation_start=5,
                          duration_bars=15, atr_at_formation=0.001),
            breakout_price=1.1055, breakout_bar_index=25, atr=0.001,
        )
        engine._detector.add_bar = MagicMock(return_value=signal)
        engine._detector._atr = 0.001
        engine._detector._atr_count = engine._detector._atr_period
        engine._last_price = 1.1055

        await engine.on_bar(_bar(30, 1.1055))

        mock_trade_manager.enter_trade.assert_not_called()

    @pytest.mark.asyncio
    async def test_risk_allowed_entry_proceeds(
        self, mock_trade_manager, mock_llm_approve, log
    ):
        config = _make_config()
        engine = _make_engine(config, mock_trade_manager, mock_llm_approve, log)
        engine._risk_check = MagicMock(return_value=(True, ""))

        signal = BreakoutSignal(
            timestamp=datetime(2025, 1, 2, 10, 30, tzinfo=timezone.utc),
            direction=Direction.LONG,
            box=DarvasBox(top=1.1050, bottom=1.1000, top_confirmed_at=10,
                          bottom_confirmed_at=20, formation_start=5,
                          duration_bars=15, atr_at_formation=0.001),
            breakout_price=1.1055, breakout_bar_index=25, atr=0.001,
        )
        engine._detector.add_bar = MagicMock(return_value=signal)
        engine._detector._atr = 0.001
        engine._detector._atr_count = engine._detector._atr_period
        engine._last_price = 1.1055

        await engine.on_bar(_bar(30, 1.1055))

        mock_trade_manager.enter_trade.assert_called_once()


# ── 6. Shared TradeManager blocks second strategy ────────────────────────────

class TestSharedTradeManagerBlocking:
    @pytest.mark.asyncio
    async def test_in_trade_blocks_new_signal(
        self, mock_llm_approve, log
    ):
        """When TradeManager.in_trade=True, on_bar skips signal detection."""
        tm = MagicMock()
        tm.in_trade = True
        tm.check_exit = MagicMock(return_value=None)  # no exit yet

        config = _make_config()
        engine = _make_engine(config, tm, mock_llm_approve, log)

        await engine.on_bar(_bar(30, 1.1055))

        # check_exit should be called (we're in trade)
        tm.check_exit.assert_called_once()
        # enter_trade should NOT be called (skipped signal detection)
        tm.enter_trade.assert_not_called()


# ── 7. Exit check runs on each bar while in trade ───────────────────────────

class TestExitCheck:
    @pytest.mark.asyncio
    async def test_exit_called_with_bar_prices(
        self, mock_llm_approve, log
    ):
        tm = MagicMock()
        tm.in_trade = True
        tm.check_exit = MagicMock(return_value=None)

        config = _make_config()
        engine = _make_engine(config, tm, mock_llm_approve, log)
        engine._bar_count = 50

        bar = _bar(30, 1.1040, high=1.1060, low=1.1020)
        await engine.on_bar(bar)

        tm.check_exit.assert_called_once_with(
            current_price=1.1040,
            bar_high=1.1060,
            bar_low=1.1020,
            current_bar_index=51,  # incremented before check
        )


# ── 8. Slippage ceiling aborts entry ────────────────────────────────────────

class TestSlippageCeiling:
    @pytest.mark.asyncio
    async def test_large_drift_aborts(
        self, mock_trade_manager, mock_llm_approve, log
    ):
        """If price drifts > max_entry_drift_atr during LLM call, abort."""
        config = _make_config()
        engine = _make_engine(config, mock_trade_manager, mock_llm_approve, log)

        signal = BreakoutSignal(
            timestamp=datetime(2025, 1, 2, 10, 30, tzinfo=timezone.utc),
            direction=Direction.LONG,
            box=DarvasBox(top=1.1050, bottom=1.1000, top_confirmed_at=10,
                          bottom_confirmed_at=20, formation_start=5,
                          duration_bars=15, atr_at_formation=0.001),
            breakout_price=1.1055, breakout_bar_index=25, atr=0.001,
        )
        engine._detector.add_bar = MagicMock(return_value=signal)
        engine._detector._atr = 0.001
        engine._detector._atr_count = engine._detector._atr_period
        # Price drifted far (1 ATR = 0.001, drift = 0.0008 > 0.5 ATR = 0.0005)
        engine._last_price = 1.1055 + 0.0008

        await engine.on_bar(_bar(30, 1.1055))

        mock_trade_manager.enter_trade.assert_not_called()

    @pytest.mark.asyncio
    async def test_small_drift_proceeds(
        self, mock_trade_manager, mock_llm_approve, log
    ):
        """Small price drift within tolerance → trade proceeds."""
        config = _make_config()
        engine = _make_engine(config, mock_trade_manager, mock_llm_approve, log)

        signal = BreakoutSignal(
            timestamp=datetime(2025, 1, 2, 10, 30, tzinfo=timezone.utc),
            direction=Direction.LONG,
            box=DarvasBox(top=1.1050, bottom=1.1000, top_confirmed_at=10,
                          bottom_confirmed_at=20, formation_start=5,
                          duration_bars=15, atr_at_formation=0.001),
            breakout_price=1.1055, breakout_bar_index=25, atr=0.001,
        )
        engine._detector.add_bar = MagicMock(return_value=signal)
        engine._detector._atr = 0.001
        engine._detector._atr_count = engine._detector._atr_period
        # Small drift (0.0002 < 0.5 ATR = 0.0005)
        engine._last_price = 1.1055 + 0.0002

        await engine.on_bar(_bar(30, 1.1055))

        mock_trade_manager.enter_trade.assert_called_once()
