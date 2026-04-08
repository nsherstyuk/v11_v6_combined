"""
Tests for ORB LLM gate -- evaluate_orb_signal on filters and adapter integration.

Design decisions tested:
    1. PassthroughFilter.evaluate_orb_signal auto-approves
    2. GrokFilter.evaluate_orb_signal returns LLM decision
    3. GrokFilter double timeout -> mechanical approval (approved=True)
    4. GrokFilter rejected signal passes through
    5. Adapter LLM gate: rejection -> DONE_TODAY
    6. Adapter LLM gate: approval -> brackets proceed
    7. Adapter: no LLM filter -> gate skipped
    8. Adapter: confidence below threshold -> rejected
    9. Runner passes llm_filter to ORB adapter
"""
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from v11.llm.models import ORBSignalContext, BarData, DailyBarData
from v11.core.types import FilterDecision
from v11.v6_orb.orb_strategy import StrategyState
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import RangeInfo
from v11.live.orb_adapter import ORBAdapter
from v11.live.risk_manager import RiskManager


def _make_orb_context() -> ORBSignalContext:
    return ORBSignalContext(
        instrument="XAUUSD",
        range_high=4665.0, range_low=4616.0,
        range_size=49.0, range_size_pct=1.05, range_vs_avg=3.2,
        current_price=4640.0,
        distance_from_high=-25.0, distance_from_low=24.0,
        session="LONDON", day_of_week="Monday",
        current_time_utc="2025-04-07T08:00:00Z",
        recent_bars=[], daily_bars=[],
    )


def _make_adapter(llm_filter=None, log=None):
    """Create an ORBAdapter with mocked dependencies."""
    log = log or logging.getLogger("test_orb_gate")
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.reqMktData.return_value = MagicMock()
    ib.pendingTickersEvent = MagicMock()
    ib.sleep = MagicMock()
    contract = MagicMock()
    config = V6StrategyConfig(
        instrument="XAUUSD", range_start_hour=0, range_end_hour=6,
        trade_start_hour=8, trade_end_hour=16,
        velocity_filter_enabled=False, gap_filter_enabled=False,
        qty=1, point_value=1.0, price_decimals=2,
    )
    rm = RiskManager(
        max_daily_loss=500.0, max_daily_trades_per_strategy=10,
        max_concurrent_positions=3, log=log,
    )
    adapter = ORBAdapter(
        ib=ib, contract=contract, v6_config=config,
        risk_manager=rm, log=log, dry_run=True,
        llm_filter=llm_filter,
    )
    return adapter


# ── 1. PassthroughFilter auto-approves ───────────────────────────────────────

class TestPassthroughORB:
    @pytest.mark.asyncio
    async def test_auto_approves(self):
        from v11.llm.passthrough_filter import PassthroughFilter
        filt = PassthroughFilter()
        ctx = _make_orb_context()
        decision = await filt.evaluate_orb_signal(ctx)
        assert decision.approved is True
        assert decision.confidence == 100


# ── 2-4. GrokFilter evaluate_orb_signal ──────────────────────────────────────

class TestGrokFilterORBSuccess:
    @pytest.mark.asyncio
    async def test_approved_signal(self):
        from v11.llm.grok_filter import GrokFilter

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"approved": true, "confidence": 80, "entry": 0.0, '
            '"stop": 0.01, "target": 0.0, "reasoning": "Good ORB day", '
            '"risk_flags": []}')
        mock_response.usage = MagicMock(prompt_tokens=500, completion_tokens=50)

        with patch("v11.llm.grok_filter.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create = MagicMock(
                return_value=mock_response)
            mock_cls.return_value = mock_client

            filt = GrokFilter(api_key="test", timeout=10.0)
            ctx = _make_orb_context()
            decision = await filt.evaluate_orb_signal(ctx)

            assert decision.approved is True
            assert decision.confidence == 80

    @pytest.mark.asyncio
    async def test_rejected_signal(self):
        from v11.llm.grok_filter import GrokFilter

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"approved": false, "confidence": 30, "entry": 0.0, '
            '"stop": 0.01, "target": 0.0, "reasoning": "Extreme range day", '
            '"risk_flags": ["extreme_range"]}')
        mock_response.usage = MagicMock(prompt_tokens=500, completion_tokens=50)

        with patch("v11.llm.grok_filter.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create = MagicMock(
                return_value=mock_response)
            mock_cls.return_value = mock_client

            filt = GrokFilter(api_key="test", timeout=10.0)
            ctx = _make_orb_context()
            decision = await filt.evaluate_orb_signal(ctx)

            assert decision.approved is False
            assert "extreme_range" in decision.risk_flags


class TestGrokFilterORBTimeout:
    @pytest.mark.asyncio
    async def test_double_timeout_returns_mechanical_approval(self):
        """If LLM times out twice, proceed mechanically (approved)."""
        from v11.llm.grok_filter import GrokFilter

        with patch("v11.llm.grok_filter.OpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create = MagicMock(
                side_effect=asyncio.TimeoutError("timeout"))
            mock_cls.return_value = mock_client

            filt = GrokFilter(api_key="test", timeout=1.0)
            ctx = _make_orb_context()
            decision = await filt.evaluate_orb_signal(ctx)

            assert decision.approved is True
            assert "llm_fallback" in decision.risk_flags
            assert mock_client.chat.completions.create.call_count == 2


# ── 5-8. Adapter LLM gate ───────────────────────────────────────────────────

class TestAdapterLLMGate:
    @pytest.mark.asyncio
    async def test_llm_rejection_returns_false(self):
        """LLM rejects -> _evaluate_orb_signal returns False."""
        reject_filter = MagicMock()
        reject_filter.evaluate_orb_signal = AsyncMock(return_value=FilterDecision(
            approved=False, confidence=30,
            entry_price=0.0, stop_price=0.0, target_price=0.0,
            reasoning="Bad day", risk_flags=["extreme_range"],
        ))
        adapter = _make_adapter(llm_filter=reject_filter)

        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._strategy.range = RangeInfo(
            high=4665.0, low=4616.0, start_time=None, end_time=None)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is False

    @pytest.mark.asyncio
    async def test_llm_approval_returns_true(self):
        """LLM approves -> returns True."""
        approve_filter = MagicMock()
        approve_filter.evaluate_orb_signal = AsyncMock(return_value=FilterDecision(
            approved=True, confidence=85,
            entry_price=0.0, stop_price=0.0, target_price=0.0,
            reasoning="Good day",
        ))
        adapter = _make_adapter(llm_filter=approve_filter)

        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._strategy.range = RangeInfo(
            high=4665.0, low=4616.0, start_time=None, end_time=None)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is True

    @pytest.mark.asyncio
    async def test_no_llm_filter_skips_gate(self):
        """When llm_filter is None, gate returns True."""
        adapter = _make_adapter(llm_filter=None)

        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._strategy.range = RangeInfo(
            high=4665.0, low=4616.0, start_time=None, end_time=None)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is True

    @pytest.mark.asyncio
    async def test_confidence_below_threshold_rejects(self):
        """Approved but low confidence -> rejected."""
        low_conf_filter = MagicMock()
        low_conf_filter.evaluate_orb_signal = AsyncMock(return_value=FilterDecision(
            approved=True, confidence=50,
            entry_price=0.0, stop_price=0.0, target_price=0.0,
            reasoning="Marginal",
        ))
        adapter = _make_adapter(llm_filter=low_conf_filter)
        adapter._llm_confidence_threshold = 75

        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._strategy.range = RangeInfo(
            high=4665.0, low=4616.0, start_time=None, end_time=None)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is False

    def test_daily_bars_initialized_empty(self):
        adapter = _make_adapter()
        assert adapter._daily_bars == []


# ── 9. Runner wiring ────────────────────────────────────────────────────────

class TestRunnerWiringORBLLM:
    def test_runner_passes_llm_to_orb_adapter(self, tmp_path):
        from v11.config.live_config import XAUUSD_INSTRUMENT, LiveConfig
        from v11.live.multi_strategy_runner import MultiStrategyRunner

        log = logging.getLogger("test_runner_orb")
        mock_conn = MagicMock()
        mock_conn._contracts = {"XAUUSD": MagicMock()}
        mock_conn.ib = MagicMock()
        mock_conn.ib.isConnected.return_value = True
        mock_conn.ib.reqMktData.return_value = MagicMock()
        mock_conn.ib.pendingTickersEvent = MagicMock()
        mock_conn.ib.sleep = MagicMock()

        mock_llm = MagicMock()
        rm = RiskManager(
            max_daily_loss=500.0, max_daily_trades_per_strategy=10,
            max_concurrent_positions=3, log=log)
        live_cfg = LiveConfig(dry_run=True)

        runner = MultiStrategyRunner(
            conn=mock_conn, llm_filter=mock_llm,
            live_config=live_cfg, risk_manager=rm, log=log,
            trade_log_dir=str(tmp_path),
        )

        v6_config = V6StrategyConfig(
            instrument="XAUUSD", velocity_filter_enabled=False,
            gap_filter_enabled=False, qty=1, point_value=1.0,
            price_decimals=2,
        )
        adapter = runner.add_orb_strategy(v6_config, XAUUSD_INSTRUMENT)
        assert adapter._llm_filter is mock_llm
