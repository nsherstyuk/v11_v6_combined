"""
Tests for LLM Models — CENTER module (contract between LLM and execution).

Test Specifications:

1. LLMResponse valid JSON parsing
   Intent: Valid JSON with all required fields parses into LLMResponse correctly.
   Regression: Valid LLM output rejected, trade not executed.

2. LLMResponse confidence validation
   Intent: Confidence must be 0-100. Values outside this range are rejected.
   Regression: Confidence of 150 or -10 accepted, causing invalid trade decisions.

3. LLMResponse stop validation
   Intent: Stop price must be > 0.
   Regression: Stop of 0 or negative accepted, trade placed without valid stop loss.

4. LLMResponse handles missing risk_flags
   Intent: risk_flags defaults to empty list if not provided.
   Regression: Missing risk_flags causes parse failure, trade rejected.

5. SignalContext serialization
   Intent: SignalContext serializes to JSON that the LLM can consume.
   Regression: Serialization fails or produces invalid JSON.

6. FilterDecision frozen immutability
   Intent: FilterDecision is immutable after creation.
   Regression: Decision modified after creation, corrupting execution logic.
"""
import pytest
from pydantic import ValidationError

from v11.llm.models import LLMResponse, SignalContext, BarData
from v11.core.types import FilterDecision


class TestLLMResponseParsing:
    """Intent: Valid JSON parses correctly into LLMResponse."""

    def test_valid_response(self):
        raw = '{"approved": true, "confidence": 78, "entry": 2046.10, "stop": 2037.80, "target": 2058.00, "reasoning": "Clean breakout.", "risk_flags": []}'
        resp = LLMResponse.model_validate_json(raw)
        assert resp.approved is True
        assert resp.confidence == 78
        assert resp.entry == 2046.10
        assert resp.stop == 2037.80
        assert resp.target == 2058.00

    def test_rejected_response(self):
        raw = '{"approved": false, "confidence": 30, "entry": 2046.10, "stop": 2037.80, "target": 2058.00, "reasoning": "Counter-trend.", "risk_flags": ["counter_trend"]}'
        resp = LLMResponse.model_validate_json(raw)
        assert resp.approved is False
        assert resp.confidence == 30
        assert resp.risk_flags == ["counter_trend"]


class TestLLMResponseConfidenceValidation:
    """Intent: Confidence must be 0-100."""

    def test_confidence_too_high(self):
        raw = '{"approved": true, "confidence": 150, "entry": 100.0, "stop": 95.0, "target": 110.0, "reasoning": "test"}'
        with pytest.raises(ValidationError):
            LLMResponse.model_validate_json(raw)

    def test_confidence_negative(self):
        raw = '{"approved": true, "confidence": -10, "entry": 100.0, "stop": 95.0, "target": 110.0, "reasoning": "test"}'
        with pytest.raises(ValidationError):
            LLMResponse.model_validate_json(raw)

    def test_confidence_zero_valid(self):
        raw = '{"approved": false, "confidence": 0, "entry": 100.0, "stop": 95.0, "target": 110.0, "reasoning": "no confidence"}'
        resp = LLMResponse.model_validate_json(raw)
        assert resp.confidence == 0

    def test_confidence_100_valid(self):
        raw = '{"approved": true, "confidence": 100, "entry": 100.0, "stop": 95.0, "target": 110.0, "reasoning": "max confidence"}'
        resp = LLMResponse.model_validate_json(raw)
        assert resp.confidence == 100


class TestLLMResponseStopValidation:
    """Intent: Stop price must be > 0."""

    def test_stop_zero_rejected(self):
        raw = '{"approved": true, "confidence": 80, "entry": 100.0, "stop": 0, "target": 110.0, "reasoning": "test"}'
        with pytest.raises(ValidationError):
            LLMResponse.model_validate_json(raw)

    def test_stop_negative_rejected(self):
        raw = '{"approved": true, "confidence": 80, "entry": 100.0, "stop": -5.0, "target": 110.0, "reasoning": "test"}'
        with pytest.raises(ValidationError):
            LLMResponse.model_validate_json(raw)


class TestLLMResponseDefaults:
    """Intent: Missing optional fields get sensible defaults."""

    def test_missing_risk_flags_defaults_empty(self):
        raw = '{"approved": true, "confidence": 75, "entry": 100.0, "stop": 95.0, "target": 110.0, "reasoning": "ok"}'
        resp = LLMResponse.model_validate_json(raw)
        assert resp.risk_flags == []


class TestSignalContextSerialization:
    """Intent: SignalContext serializes to valid JSON."""

    def test_serialization_roundtrip(self):
        ctx = SignalContext(
            direction="long",
            instrument="XAUUSD",
            box_top=2050.0,
            box_bottom=2040.0,
            box_duration_bars=45,
            box_width_atr=1.2,
            breakout_price=2051.5,
            atr=8.3,
            buy_ratio_at_breakout=0.62,
            buy_ratio_trend="increasing",
            tick_quality="HIGH",
            volume_classification="CONFIRMING",
            recent_bars=[
                BarData(t="2026-01-01T00:00:00", o=2045.0, h=2046.0,
                        l=2044.0, c=2045.5, bv=50.0, sv=30.0, tc=80),
            ],
            current_time_utc="2026-01-01T14:35:00Z",
            session="LONDON_NY_OVERLAP",
        )
        json_str = ctx.model_dump_json()
        # Should be parseable back
        parsed = SignalContext.model_validate_json(json_str)
        assert parsed.instrument == "XAUUSD"
        assert parsed.direction == "long"
        assert len(parsed.recent_bars) == 1


class TestFilterDecisionImmutability:
    """Intent: FilterDecision is frozen (immutable)."""

    def test_cannot_modify_approved(self):
        fd = FilterDecision(
            approved=True, confidence=80,
            entry_price=100.0, stop_price=95.0, target_price=110.0,
            reasoning="test",
        )
        with pytest.raises(AttributeError):
            fd.approved = False

    def test_cannot_modify_confidence(self):
        fd = FilterDecision(
            approved=True, confidence=80,
            entry_price=100.0, stop_price=95.0, target_price=110.0,
            reasoning="test",
        )
        with pytest.raises(AttributeError):
            fd.confidence = 0
