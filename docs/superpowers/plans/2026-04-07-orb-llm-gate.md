# ORB LLM Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Grok LLM evaluation to V6 ORB strategy, gating bracket placement on contextual approval (macro regime, session dynamics, price action).

**Architecture:** New `ORBSignalContext` Pydantic model carries range stats + daily bars + recent 1-min bars to Grok via a new `evaluate_orb_signal()` method on GrokFilter. The ORB adapter calls this gate after risk check, before allowing velocity/bracket placement. On timeout, retry once then fall back to mechanical.

**Tech Stack:** Python 3.14, Pydantic v2, openai AsyncOpenAI (xAI endpoint), ib_insync, pytest + pytest-asyncio

---

### Task 1: Add DailyBarData and ORBSignalContext models

**Files:**
- Modify: `v11/llm/models.py`
- Test: `v11/tests/test_llm_models.py`

- [ ] **Step 1: Write failing tests for new models**

Add to `v11/tests/test_llm_models.py`:

```python
# ── ORBSignalContext ──────────────────────────────────────────────────────────

class TestDailyBarData:
    def test_create_daily_bar(self):
        from v11.llm.models import DailyBarData
        bar = DailyBarData(date="2025-04-07", o=4600.0, h=4665.0, l=4590.0, c=4620.0)
        assert bar.date == "2025-04-07"
        assert bar.h == 4665.0


class TestORBSignalContext:
    def test_create_context(self):
        from v11.llm.models import ORBSignalContext, DailyBarData, BarData
        ctx = ORBSignalContext(
            instrument="XAUUSD",
            range_high=4665.0,
            range_low=4616.0,
            range_size=49.0,
            range_size_pct=1.05,
            range_vs_avg=3.2,
            current_price=4640.0,
            distance_from_high=25.0,
            distance_from_low=24.0,
            session="LONDON",
            day_of_week="Monday",
            current_time_utc="2025-04-07T08:00:00Z",
            recent_bars=[],
            daily_bars=[],
        )
        assert ctx.instrument == "XAUUSD"
        assert ctx.range_size_pct == 1.05

    def test_serialization_roundtrip(self):
        from v11.llm.models import ORBSignalContext
        ctx = ORBSignalContext(
            instrument="XAUUSD",
            range_high=4665.0, range_low=4616.0,
            range_size=49.0, range_size_pct=1.05, range_vs_avg=3.2,
            current_price=4640.0,
            distance_from_high=25.0, distance_from_low=24.0,
            session="LONDON", day_of_week="Monday",
            current_time_utc="2025-04-07T08:00:00Z",
            recent_bars=[], daily_bars=[],
        )
        json_str = ctx.model_dump_json()
        restored = ORBSignalContext.model_validate_json(json_str)
        assert restored.range_high == 4665.0
        assert restored.range_vs_avg == 3.2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest v11/tests/test_llm_models.py::TestDailyBarData -v && pytest v11/tests/test_llm_models.py::TestORBSignalContext -v`
Expected: FAIL with `ImportError: cannot import name 'DailyBarData'`

- [ ] **Step 3: Implement models**

Add to end of `v11/llm/models.py`:

```python
class DailyBarData(BaseModel):
    """Compact daily bar for ORB LLM context."""
    date: str       # YYYY-MM-DD
    o: float        # open
    h: float        # high
    l: float        # low
    c: float        # close


class ORBSignalContext(BaseModel):
    """Everything the LLM receives when ORB is in RANGE_READY.

    This is the complete input for the ORB LLM gate.
    Grok decides whether to place brackets based on this context.
    """
    signal_type: str = "ORB_RANGE_READY"
    instrument: str

    # Range stats
    range_high: float
    range_low: float
    range_size: float               # absolute (e.g. $48.16)
    range_size_pct: float           # as % of midpoint (e.g. 1.05)
    range_vs_avg: float             # ratio vs 10-day average range

    # Current price
    current_price: float
    distance_from_high: float       # current_price - range_high (negative if below)
    distance_from_low: float        # current_price - range_low (negative if below)

    # Timing
    session: str                    # ASIAN_CLOSE, LONDON, LONDON_NY_OVERLAP, NY
    day_of_week: str                # Monday, Tuesday, etc.
    current_time_utc: str           # ISO format

    # Bar context
    recent_bars: List[BarData]      # last 360 1-min bars (6 hours)
    daily_bars: List[DailyBarData]  # last 10 daily bars
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest v11/tests/test_llm_models.py -v`
Expected: All pass (existing 12 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add v11/llm/models.py v11/tests/test_llm_models.py
git commit -m "feat: add ORBSignalContext and DailyBarData models for ORB LLM gate"
```

---

### Task 2: Add ORB prompt template

**Files:**
- Modify: `v11/llm/prompt_templates.py`
- Test: `v11/tests/test_llm_models.py` (prompt is edge, light test)

- [ ] **Step 1: Write failing test**

Add to `v11/tests/test_llm_models.py`:

```python
class TestORBPromptTemplate:
    def test_prompt_contains_context(self):
        from v11.llm.prompt_templates import ORB_SYSTEM_PROMPT, build_orb_signal_prompt
        prompt = build_orb_signal_prompt('{"range_high": 4665.0}')
        assert "4665.0" in prompt
        assert len(ORB_SYSTEM_PROMPT) > 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest v11/tests/test_llm_models.py::TestORBPromptTemplate -v`
Expected: FAIL with `ImportError: cannot import name 'ORB_SYSTEM_PROMPT'`

- [ ] **Step 3: Implement prompt template**

Add to end of `v11/llm/prompt_templates.py`:

```python
ORB_SYSTEM_PROMPT = """You are a professional gold (XAUUSD) trading analyst evaluating an Opening Range Breakout (ORB) setup.

The strategy places bracket orders at the Asian session range high and low. If price breaks above the range, a long entry triggers. If price breaks below, a short entry triggers. Your job is to decide whether TODAY is a good day to place these brackets.

You receive:
- Today's Asian range (high, low, size, size relative to recent average)
- Last 10 daily bars (macro trend and volatility context)
- Last 6 hours of 1-minute bars (recent price action and momentum)
- Current session and time

Evaluate:
1. MACRO REGIME: Is gold trending normally, or in a news-driven spike/crash? ORB works best in normal trending conditions. Extreme gap days, tariff/geopolitical shocks, or panic moves produce unreliable breakouts.
2. RANGE QUALITY: Is today's range normal or extreme? A range_vs_avg above 2.5 suggests abnormal volatility. Very tight ranges (range_vs_avg below 0.5) may produce false breakouts.
3. SESSION DYNAMICS: Will the upcoming session (London, NY) likely extend or reverse the Asian move? London open tends to continue Asian trends; NY can reverse.
4. DIRECTIONAL MOMENTUM: Has price been moving strongly in one direction? Breakouts aligned with existing momentum have higher follow-through.

You must respond with ONLY a JSON object:
{
    "approved": true/false,
    "confidence": 0-100,
    "entry": 0.0,
    "stop": 0.0,
    "target": 0.0,
    "reasoning": "<brief explanation of your decision>",
    "risk_flags": ["flag1", "flag2"]
}

Rules:
- entry, stop, target: set to 0.0 (ORB brackets are mechanical, you cannot modify them)
- confidence 0-100: how confident you are that today is a good ORB day
- risk_flags: concerns (e.g., "extreme_range", "news_driven", "counter_session", "low_momentum", "pre_nfp")
- Be conservative: when in doubt, reject. Missing a trade costs nothing; entering a bad setup costs money.
"""


def build_orb_signal_prompt(context_json: str) -> str:
    """Build the user prompt from an ORBSignalContext JSON string."""
    return f"""Evaluate this Opening Range Breakout setup for XAUUSD:

{context_json}

Should bracket orders be placed at the range high and low today?
Respond with ONLY a JSON object as specified in your instructions."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest v11/tests/test_llm_models.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add v11/llm/prompt_templates.py v11/tests/test_llm_models.py
git commit -m "feat: add ORB-specific system prompt and prompt builder"
```

---

### Task 3: Add evaluate_orb_signal to GrokFilter and PassthroughFilter

**Files:**
- Modify: `v11/llm/grok_filter.py`
- Modify: `v11/llm/passthrough_filter.py`
- Modify: `v11/llm/base.py`
- Test: `v11/tests/test_orb_llm_gate.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `v11/tests/test_orb_llm_gate.py`:

```python
"""
Tests for ORB LLM gate — evaluate_orb_signal on GrokFilter and PassthroughFilter.

Design decisions tested:
    1. GrokFilter.evaluate_orb_signal calls LLM with ORB prompt
    2. PassthroughFilter.evaluate_orb_signal auto-approves
    3. Timeout retry: first timeout -> retry -> success
    4. Double timeout -> proceed mechanically (approved=True)
    5. Confidence below threshold is still respected by caller (not filter)
"""
import asyncio
import logging
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from v11.llm.models import ORBSignalContext, BarData, DailyBarData
from v11.core.types import FilterDecision


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


class TestPassthroughORB:
    @pytest.mark.asyncio
    async def test_auto_approves(self):
        from v11.llm.passthrough_filter import PassthroughFilter
        filt = PassthroughFilter()
        ctx = _make_orb_context()
        decision = await filt.evaluate_orb_signal(ctx)
        assert decision.approved is True
        assert decision.confidence == 100


class TestGrokFilterORBTimeout:
    @pytest.mark.asyncio
    async def test_double_timeout_returns_mechanical_approval(self):
        """If LLM times out twice, proceed mechanically (approved)."""
        from v11.llm.grok_filter import GrokFilter

        with patch("v11.llm.grok_filter.AsyncOpenAI") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                side_effect=asyncio.TimeoutError("timeout"))
            mock_client_cls.return_value = mock_client

            filt = GrokFilter(api_key="test", timeout=1.0)
            ctx = _make_orb_context()
            decision = await filt.evaluate_orb_signal(ctx)

            assert decision.approved is True
            assert "timeout" in decision.reasoning.lower() or "mechanical" in decision.reasoning.lower()
            # Should have been called twice (original + retry)
            assert mock_client.chat.completions.create.call_count == 2


class TestGrokFilterORBSuccess:
    @pytest.mark.asyncio
    async def test_approved_signal(self):
        from v11.llm.grok_filter import GrokFilter

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"approved": true, "confidence": 80, "entry": 0.0, '
            '"stop": 0.0, "target": 0.0, "reasoning": "Good ORB day", '
            '"risk_flags": []}')
        mock_response.usage = MagicMock(prompt_tokens=500, completion_tokens=50)

        with patch("v11.llm.grok_filter.AsyncOpenAI") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response)
            mock_client_cls.return_value = mock_client

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
            '"stop": 0.0, "target": 0.0, "reasoning": "Extreme range day", '
            '"risk_flags": ["extreme_range"]}')
        mock_response.usage = MagicMock(prompt_tokens=500, completion_tokens=50)

        with patch("v11.llm.grok_filter.AsyncOpenAI") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response)
            mock_client_cls.return_value = mock_client

            filt = GrokFilter(api_key="test", timeout=10.0)
            ctx = _make_orb_context()
            decision = await filt.evaluate_orb_signal(ctx)

            assert decision.approved is False
            assert "extreme_range" in decision.risk_flags
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest v11/tests/test_orb_llm_gate.py -v`
Expected: FAIL with `AttributeError: 'PassthroughFilter' object has no attribute 'evaluate_orb_signal'`

- [ ] **Step 3: Add evaluate_orb_signal to PassthroughFilter**

Add to `v11/llm/passthrough_filter.py`, after the existing `evaluate_signal` method:

```python
    async def evaluate_orb_signal(self, context) -> FilterDecision:
        """Auto-approve ORB signals -- LLM filter disabled."""
        log.info(
            f"Passthrough: AUTO-APPROVE ORB {context.instrument} "
            f"range={context.range_low:.2f}-{context.range_high:.2f}")
        return FilterDecision(
            approved=True,
            confidence=100,
            entry_price=0.0,
            stop_price=0.0,
            target_price=0.0,
            reasoning="Mechanical approval -- LLM filter disabled",
        )
```

- [ ] **Step 4: Add evaluate_orb_signal to GrokFilter**

Add to `v11/llm/grok_filter.py`, after the existing `evaluate_signal` method. Import the new models and prompts at top of file:

```python
from .models import SignalContext, LLMResponse, ORBSignalContext
from .prompt_templates import (
    SYSTEM_PROMPT, build_signal_prompt,
    ORB_SYSTEM_PROMPT, build_orb_signal_prompt,
)
```

Then add the method:

```python
    async def evaluate_orb_signal(self, context: ORBSignalContext) -> FilterDecision:
        """Evaluate an ORB setup via Grok. Retry once on timeout, then proceed mechanically.

        Unlike evaluate_signal (which rejects on failure), ORB falls back to
        mechanical approval because the ORB edge exists without the LLM.
        """
        context_json = context.model_dump_json(indent=2)
        prompt = build_orb_signal_prompt(context_json)

        for attempt in range(2):  # original + 1 retry
            timeout = self._timeout if attempt == 0 else 5.0
            start_time = time.monotonic()
            raw_response = None

            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": ORB_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    timeout=timeout,
                )
                raw_response = response.choices[0].message.content
                latency = time.monotonic() - start_time

                llm_resp = LLMResponse.model_validate_json(raw_response)

                decision = FilterDecision(
                    approved=llm_resp.approved,
                    confidence=llm_resp.confidence,
                    entry_price=0.0,  # ORB brackets are mechanical
                    stop_price=0.0,
                    target_price=0.0,
                    reasoning=llm_resp.reasoning,
                    risk_flags=llm_resp.risk_flags,
                )

                logger.info(
                    f"ORB LLM [{self._model}] {context.instrument}: "
                    f"approved={decision.approved} conf={decision.confidence} "
                    f"latency={latency:.1f}s")

                self._log_conversation(
                    context=context,
                    raw_response=raw_response,
                    decision=decision,
                    latency=latency,
                    tokens_in=response.usage.prompt_tokens if response.usage else 0,
                    tokens_out=response.usage.completion_tokens if response.usage else 0,
                )

                return decision

            except (TimeoutError, asyncio.TimeoutError):
                latency = time.monotonic() - start_time
                if attempt == 0:
                    logger.warning(
                        f"ORB LLM timeout ({latency:.1f}s) -- retrying with 5s timeout")
                    continue
                else:
                    logger.warning(
                        f"ORB LLM double timeout -- proceeding mechanically")

            except ValidationError as e:
                logger.error(f"ORB LLM validation failed: {e}")
                logger.error(f"Raw response: {raw_response}")
                break  # don't retry validation errors

            except Exception as e:
                logger.error(f"ORB LLM call failed: {e}")
                break  # don't retry unknown errors

        # Fallback: proceed mechanically (place brackets)
        self._log_conversation(
            context=context,
            raw_response=raw_response or "",
            decision=None,
            latency=time.monotonic() - start_time,
            error="ORB LLM failed/timed out -- mechanical fallback",
        )

        return FilterDecision(
            approved=True,
            confidence=0,
            entry_price=0.0,
            stop_price=0.0,
            target_price=0.0,
            reasoning="ORB LLM unavailable -- mechanical fallback (brackets placed)",
            risk_flags=["llm_fallback"],
        )
```

Note: `_log_conversation` accepts `SignalContext` but we're passing `ORBSignalContext`. Both are Pydantic BaseModels with `.model_dump()`, so the logging code will work. Verify the `_log_conversation` method uses `context.model_dump()` (it does, at line 170).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest v11/tests/test_orb_llm_gate.py -v`
Expected: All 4 tests pass

- [ ] **Step 6: Run full suite**

Run: `pytest v11/tests/ -v`
Expected: All pass (263 existing + 7 new = 270)

- [ ] **Step 7: Commit**

```bash
git add v11/llm/grok_filter.py v11/llm/passthrough_filter.py v11/tests/test_orb_llm_gate.py
git commit -m "feat: add evaluate_orb_signal to GrokFilter with retry+mechanical fallback"
```

---

### Task 4: Wire LLM filter into ORBAdapter and add daily bar fetching

**Files:**
- Modify: `v11/live/orb_adapter.py`
- Modify: `v11/live/multi_strategy_runner.py`
- Test: `v11/tests/test_orb_llm_gate.py` (extend)

- [ ] **Step 1: Write failing tests for adapter LLM gate**

Add to `v11/tests/test_orb_llm_gate.py`:

```python
from v11.v6_orb.orb_strategy import StrategyState
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import RangeInfo
from v11.live.orb_adapter import ORBAdapter
from v11.live.risk_manager import RiskManager


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


class TestAdapterLLMGate:
    @pytest.mark.asyncio
    async def test_llm_rejection_sets_done_today(self):
        """LLM rejects -> state becomes DONE_TODAY, no brackets."""
        reject_filter = MagicMock()
        reject_filter.evaluate_orb_signal = AsyncMock(return_value=FilterDecision(
            approved=False, confidence=30,
            entry_price=0.0, stop_price=0.0, target_price=0.0,
            reasoning="Bad day", risk_flags=["extreme_range"],
        ))
        adapter = _make_adapter(llm_filter=reject_filter)

        # Set up RANGE_READY state with a range
        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._strategy.range = RangeInfo(
            high=4665.0, low=4616.0, open=4620.0, close=4640.0)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is False

    @pytest.mark.asyncio
    async def test_llm_approval_allows_brackets(self):
        """LLM approves -> returns True, brackets proceed."""
        approve_filter = MagicMock()
        approve_filter.evaluate_orb_signal = AsyncMock(return_value=FilterDecision(
            approved=True, confidence=85,
            entry_price=0.0, stop_price=0.0, target_price=0.0,
            reasoning="Good day",
        ))
        adapter = _make_adapter(llm_filter=approve_filter)

        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._strategy.range = RangeInfo(
            high=4665.0, low=4616.0, open=4620.0, close=4640.0)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is True

    @pytest.mark.asyncio
    async def test_no_llm_filter_skips_gate(self):
        """When llm_filter is None, gate is skipped (returns True)."""
        adapter = _make_adapter(llm_filter=None)

        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._strategy.range = RangeInfo(
            high=4665.0, low=4616.0, open=4620.0, close=4640.0)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is True

    def test_daily_bars_stored(self):
        """Daily bars list exists on adapter after init."""
        adapter = _make_adapter()
        assert hasattr(adapter, '_daily_bars')
        assert adapter._daily_bars == []

    @pytest.mark.asyncio
    async def test_confidence_below_threshold_rejects(self):
        """Approved but confidence below threshold -> rejected."""
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
            high=4665.0, low=4616.0, open=4620.0, close=4640.0)
        adapter._range_calculated = True
        adapter._daily_bars = []

        result = await adapter._evaluate_orb_signal(
            datetime(2025, 4, 7, 8, 0, tzinfo=timezone.utc))
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest v11/tests/test_orb_llm_gate.py::TestAdapterLLMGate -v`
Expected: FAIL — ORBAdapter doesn't accept `llm_filter` parameter yet

- [ ] **Step 3: Modify ORBAdapter to accept LLM filter and daily bars**

In `v11/live/orb_adapter.py`, modify `__init__` to accept new parameters:

```python
    def __init__(
        self,
        ib,
        contract,
        v6_config: V6StrategyConfig,
        risk_manager: RiskManager,
        log: logging.Logger,
        state_dir: str = "",
        dry_run: bool = True,
        poll_interval: float = 2.0,
        llm_filter=None,
        llm_confidence_threshold: int = 75,
    ):
```

Add to init body after existing adapter state:

```python
        # ── LLM gate (optional) ──────────────────────────────────────
        self._llm_filter = llm_filter
        self._llm_confidence_threshold = llm_confidence_threshold
        self._daily_bars: list = []
        self._llm_evaluated_today: bool = False
```

- [ ] **Step 4: Add _evaluate_orb_signal method to ORBAdapter**

Add new method to ORBAdapter class. Add imports at top of file:

```python
import asyncio
from ..llm.models import ORBSignalContext, BarData, DailyBarData
```

Then the method:

```python
    async def _evaluate_orb_signal(self, now: datetime) -> bool:
        """Evaluate ORB setup via LLM. Returns True if approved or no LLM.

        Called once per day when state first reaches RANGE_READY.
        """
        if self._llm_filter is None:
            return True

        if not hasattr(self._llm_filter, 'evaluate_orb_signal'):
            return True

        rng = self._strategy.range
        if rng is None:
            return True

        mid = (rng.high + rng.low) / 2
        size = rng.high - rng.low
        size_pct = (size / mid * 100) if mid > 0 else 0.0

        # Compute range_vs_avg from daily bars
        if self._daily_bars:
            daily_ranges = [b.h - b.l for b in self._daily_bars if hasattr(b, 'h')]
            avg_range = sum(daily_ranges) / len(daily_ranges) if daily_ranges else size
            range_vs_avg = size / avg_range if avg_range > 0 else 1.0
        else:
            range_vs_avg = 1.0

        # Current price from context
        price = self._context.get_current_price(now)
        if price is None:
            price = mid

        # Session label
        hour = now.hour
        if 0 <= hour < 8:
            session = "ASIAN_CLOSE"
        elif 8 <= hour < 13:
            session = "LONDON"
        elif 13 <= hour < 17:
            session = "LONDON_NY_OVERLAP"
        else:
            session = "NY"

        # Build recent 1-min bars from context tick buffer (if available)
        recent_bars = []  # populated from context if available

        # Build daily bar data
        daily_bar_data = []
        for b in self._daily_bars:
            daily_bar_data.append(DailyBarData(
                date=b.date if hasattr(b, 'date') else "",
                o=b.o if hasattr(b, 'o') else b.open,
                h=b.h if hasattr(b, 'h') else b.high,
                l=b.l if hasattr(b, 'l') else b.low,
                c=b.c if hasattr(b, 'c') else b.close,
            ))

        context = ORBSignalContext(
            instrument=self._instrument,
            range_high=rng.high,
            range_low=rng.low,
            range_size=size,
            range_size_pct=round(size_pct, 3),
            range_vs_avg=round(range_vs_avg, 2),
            current_price=price,
            distance_from_high=round(price - rng.high, 2),
            distance_from_low=round(price - rng.low, 2),
            session=session,
            day_of_week=now.strftime("%A"),
            current_time_utc=now.isoformat(),
            recent_bars=recent_bars,
            daily_bars=daily_bar_data,
        )

        self._log.info(
            f"ORB LLM gate: evaluating range {rng.low:.2f}-{rng.high:.2f} "
            f"(size={size:.2f}, vs_avg={range_vs_avg:.1f}x)")

        decision = await self._llm_filter.evaluate_orb_signal(context)

        if not decision.approved:
            self._log.info(
                f"ORB LLM REJECTED: conf={decision.confidence} "
                f"reason={decision.reasoning[:100]}")
            return False

        if decision.confidence < self._llm_confidence_threshold:
            self._log.info(
                f"ORB LLM confidence {decision.confidence} "
                f"< threshold {self._llm_confidence_threshold}")
            return False

        self._log.info(
            f"ORB LLM APPROVED: conf={decision.confidence} "
            f"reason={decision.reasoning[:100]}")
        return True
```

- [ ] **Step 5: Wire LLM gate into on_price flow**

In `on_price()`, after the risk gate block and before the "Drive strategy" section, add the LLM gate. The gate runs once per day (when state first reaches RANGE_READY):

Replace the existing RANGE_READY risk gate + drive strategy section with:

```python
        # ── Risk gate ─────────────────────────────────────────────
        if self._strategy.state == StrategyState.RANGE_READY:
            allowed, reason = self._risk_manager.can_trade(
                self._instrument, self.STRATEGY_NAME)
            if not allowed:
                self._log.info(f"ORB risk gate BLOCKED: {reason}")
                return

            # ── LLM gate (once per day) ───────────────────────────
            if not self._llm_evaluated_today:
                self._llm_evaluated_today = True
                import asyncio
                loop = asyncio.get_event_loop()
                approved = loop.run_until_complete(
                    self._evaluate_orb_signal(now))
                if not approved:
                    self._strategy.state = StrategyState.DONE_TODAY
                    self._log.info("ORB state: RANGE_READY -> DONE_TODAY (LLM rejected)")
                    return
```

Also add `self._llm_evaluated_today = False` to the `_reset_daily` method.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest v11/tests/test_orb_llm_gate.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add v11/live/orb_adapter.py v11/tests/test_orb_llm_gate.py
git commit -m "feat: wire LLM gate into ORB adapter (RANGE_READY -> evaluate -> place/skip)"
```

---

### Task 5: Wire LLM filter through MultiStrategyRunner and run_live.py

**Files:**
- Modify: `v11/live/multi_strategy_runner.py`
- Modify: `v11/live/run_live.py`

- [ ] **Step 1: Pass llm_filter to ORBAdapter in MultiStrategyRunner.add_orb_strategy**

In `v11/live/multi_strategy_runner.py`, modify `add_orb_strategy`:

```python
    def add_orb_strategy(
        self,
        v6_config: V6StrategyConfig,
        inst_config: InstrumentConfig,
        state_dir: str = "",
        poll_interval: float = 2.0,
    ) -> ORBAdapter:
```

In the adapter creation, add `llm_filter` and `llm_confidence_threshold`:

```python
        adapter = ORBAdapter(
            ib=self._conn.ib,
            contract=contract,
            v6_config=v6_config,
            risk_manager=self._risk_manager,
            log=self._log,
            state_dir=state_dir,
            dry_run=self._live_config.dry_run,
            poll_interval=poll_interval,
            llm_filter=self._llm_filter,
            llm_confidence_threshold=self._live_config.llm_confidence_threshold,
        )
```

- [ ] **Step 2: Add daily bar fetching to run_live.py seeding**

In `V11LiveTrader._seed_historical`, after the existing seed loop, add daily bar fetch for XAUUSD:

```python
        # Fetch daily bars for ORB LLM context
        for pair in self.runner.get_feed_pairs():
            if pair == "XAUUSD":
                self.log.info(f"Fetching daily bars for {pair} (ORB context)...")
                df = self.conn.fetch_historical_bars(
                    pair, duration="15 D", bar_size="1 day")
                if not df.empty:
                    from v11.llm.models import DailyBarData
                    daily_bars = []
                    for _, row in df.iterrows():
                        daily_bars.append(DailyBarData(
                            date=str(row['date'])[:10],
                            o=row['open'], h=row['high'],
                            l=row['low'], c=row['close'],
                        ))
                    # Find the ORB adapter and set daily bars
                    for engine in self.runner.engines:
                        if hasattr(engine, '_daily_bars') and engine.pair_name == pair:
                            engine._daily_bars = daily_bars
                            self.log.info(
                                f"{pair}: Loaded {len(daily_bars)} daily bars for ORB LLM")
```

- [ ] **Step 3: Run full test suite**

Run: `pytest v11/tests/ -v`
Expected: All pass (should be ~275 tests)

- [ ] **Step 4: Commit**

```bash
git add v11/live/multi_strategy_runner.py v11/live/run_live.py
git commit -m "feat: wire ORB LLM gate through runner and entry point with daily bar fetch"
```

---

### Task 6: Final integration test and full suite verification

**Files:**
- Test: `v11/tests/test_orb_llm_gate.py` (extend)

- [ ] **Step 1: Add integration test for full wiring**

Add to `v11/tests/test_orb_llm_gate.py`:

```python
from v11.config.live_config import XAUUSD_INSTRUMENT, LiveConfig
from v11.live.multi_strategy_runner import MultiStrategyRunner


class TestRunnerWiringORBLLM:
    def test_runner_passes_llm_to_orb_adapter(self, tmp_path):
        """MultiStrategyRunner.add_orb_strategy passes llm_filter to adapter."""
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
```

- [ ] **Step 2: Run full test suite**

Run: `pytest v11/tests/ -v`
Expected: All pass, zero regressions

- [ ] **Step 3: Commit**

```bash
git add v11/tests/test_orb_llm_gate.py
git commit -m "test: add runner wiring integration test for ORB LLM gate"
```

---

### Task 7: Update docs

**Files:**
- Modify: `docs/PROJECT_STATUS.md`
- Modify: `docs/V11_DESIGN.md`
- Create: `docs/journal/2026-04-07_orb_llm_gate_session.md`

- [ ] **Step 1: Update PROJECT_STATUS.md**

Update build status table: add ORB LLM gate row. Update test count. Update Phase 10 status.

- [ ] **Step 2: Update V11_DESIGN.md**

Add section on ORB LLM gate: where it sits in the flow, what context it receives, timeout/retry behavior.

- [ ] **Step 3: Create session journal**

Create `docs/journal/2026-04-07_orb_llm_gate_session.md` with: what was built, design decisions, files changed, test results, risk assessment.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: ORB LLM gate design, journal, status update"
```
