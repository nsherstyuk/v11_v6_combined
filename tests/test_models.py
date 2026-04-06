"""
Tests for Pydantic schema validation — verifies that Grok response contract
is enforced at the boundary.

Design decisions tested:
  - GrokDecision validates structure, types, and constraints
  - Invalid responses are rejected (not silently accepted)
  - Empty trades list is a valid response
"""

import sys
import os
import json

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import GrokDecision, TradeRecommendation
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Intent: Grok response must conform to the defined schema.
# Regression: Malformed Grok output silently flows into RiskManager / order placement.
# ---------------------------------------------------------------------------

def test_valid_response_parses():
    raw = json.dumps({
        "trades": [{
            "ticker": "EEIQ",
            "action": "BUY",
            "shares": 100,
            "entry": 8.5,
            "stop": 7.8,
            "target": 12.0,
            "confidence": 85,
            "reason": "Momentum"
        }]
    })
    decision = GrokDecision.model_validate_json(raw)
    assert len(decision.trades) == 1
    assert decision.trades[0].ticker == "EEIQ"
    assert decision.trades[0].shares == 100


def test_empty_trades_is_valid():
    raw = json.dumps({"trades": []})
    decision = GrokDecision.model_validate_json(raw)
    assert decision.trades == []


def test_rejects_non_buy_action():
    raw = json.dumps({
        "trades": [{
            "ticker": "TEST",
            "action": "SELL",
            "shares": 10,
            "entry": 10.0,
            "stop": 9.0,
            "confidence": 80,
            "reason": "Test"
        }]
    })
    with pytest.raises(ValidationError):
        GrokDecision.model_validate_json(raw)


def test_rejects_negative_shares():
    raw = json.dumps({
        "trades": [{
            "ticker": "TEST",
            "action": "BUY",
            "shares": -5,
            "entry": 10.0,
            "stop": 9.0,
            "confidence": 80,
            "reason": "Test"
        }]
    })
    with pytest.raises(ValidationError):
        GrokDecision.model_validate_json(raw)


def test_rejects_confidence_over_100():
    raw = json.dumps({
        "trades": [{
            "ticker": "TEST",
            "action": "BUY",
            "shares": 10,
            "entry": 10.0,
            "stop": 9.0,
            "confidence": 150,
            "reason": "Test"
        }]
    })
    with pytest.raises(ValidationError):
        GrokDecision.model_validate_json(raw)


def test_rejects_missing_required_field():
    # Missing "reason" field
    raw = json.dumps({
        "trades": [{
            "ticker": "TEST",
            "action": "BUY",
            "shares": 10,
            "entry": 10.0,
            "stop": 9.0,
            "confidence": 80,
        }]
    })
    with pytest.raises(ValidationError):
        GrokDecision.model_validate_json(raw)


def test_rejects_string_shares():
    raw = json.dumps({
        "trades": [{
            "ticker": "TEST",
            "action": "BUY",
            "shares": "lots",
            "entry": 10.0,
            "stop": 9.0,
            "confidence": 80,
            "reason": "Test"
        }]
    })
    with pytest.raises(ValidationError):
        GrokDecision.model_validate_json(raw)


def test_target_is_optional():
    raw = json.dumps({
        "trades": [{
            "ticker": "TEST",
            "action": "BUY",
            "shares": 10,
            "entry": 10.0,
            "stop": 9.0,
            "confidence": 80,
            "reason": "Test"
        }]
    })
    decision = GrokDecision.model_validate_json(raw)
    assert decision.trades[0].target is None
