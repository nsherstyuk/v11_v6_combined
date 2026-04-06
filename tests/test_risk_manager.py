"""
Tests for RiskManager — verifies that per-trade risk, daily loss, and confidence
gates work correctly.

Design decisions tested:
  1. Per-trade risk must not exceed MAX_RISK_PER_TRADE * ACCOUNT_SIZE
  2. Daily loss limit halts all trading when cumulative committed risk >= 3%
  3. Confidence threshold gates orders at >= CONFIDENCE_THRESHOLD
  4. Stop must be below entry for long trades
"""

import sys
import os
from datetime import date

# Ensure project root is on sys.path so imports work from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import RiskManager
from config import MAX_RISK_PER_TRADE, ACCOUNT_SIZE, DAILY_LOSS_LIMIT, CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# 1. Per-trade risk cap
# Intent: A single trade must never risk more than 1% of account.
# Regression: An oversized trade is accepted and placed with real money.
# ---------------------------------------------------------------------------

def test_rejects_trade_exceeding_max_risk():
    rm = RiskManager()
    max_risk_dollars = ACCOUNT_SIZE * MAX_RISK_PER_TRADE  # e.g. $100
    # Construct a trade whose total risk clearly exceeds the limit
    risk_per_share = 1.0  # entry - stop = $1
    shares = int(max_risk_dollars / risk_per_share) + 10  # exceed by 10 shares
    trade = {
        "ticker": "TEST",
        "confidence": 85,
        "entry": 10.0,
        "stop": 9.0,
        "shares": shares,
    }
    assert not rm.check_trade(trade), "Trade exceeding max risk should be rejected"


def test_accepts_trade_within_risk_limit():
    rm = RiskManager()
    trade = {
        "ticker": "TEST",
        "confidence": 85,
        "entry": 10.0,
        "stop": 9.5,
        "shares": 50,
    }
    # risk = 0.5 * 50 = $25.  Max = $100 (1% of $10k default).  Should pass.
    assert rm.check_trade(trade), "Trade within risk limit should be accepted"


# ---------------------------------------------------------------------------
# 2. Daily loss limit
# Intent: When cumulative committed risk hits 3% of account, halt all trades.
# Regression: Agent keeps placing orders after daily limit is reached.
# ---------------------------------------------------------------------------

def test_daily_loss_limit_halts_trading():
    rm = RiskManager()
    daily_limit = ACCOUNT_SIZE * DAILY_LOSS_LIMIT  # e.g. $300

    # Record trades until we exceed the daily limit
    trade_a = {"ticker": "A", "confidence": 90, "entry": 10.0, "stop": 9.0, "shares": 50}
    trade_b = {"ticker": "B", "confidence": 90, "entry": 10.0, "stop": 9.0, "shares": 50}
    trade_c = {"ticker": "C", "confidence": 90, "entry": 10.0, "stop": 9.0, "shares": 50}

    # Each trade commits $50 risk (1.0 * 50).  Record enough to exceed $300.
    for _ in range(int(daily_limit / 50) + 1):
        rm.record_trade(trade_a)

    # Next trade should be rejected because daily limit is breached
    new_trade = {"ticker": "NEW", "confidence": 90, "entry": 10.0, "stop": 9.5, "shares": 10}
    assert not rm.check_trade(new_trade), "Trades should be rejected after daily loss limit is hit"


def test_daily_counters_reset_on_new_day():
    rm = RiskManager()
    # Simulate accumulated loss
    rm.daily_loss = ACCOUNT_SIZE * DAILY_LOSS_LIMIT + 1
    rm.orders_placed_today = 99

    # Force a new-day reset by backdating current_date
    rm.current_date = date(2000, 1, 1)
    rm._reset_if_new_day()

    assert rm.daily_loss == 0.0, "Daily loss should reset to 0 on a new day"
    assert rm.orders_placed_today == 0, "Orders count should reset to 0 on a new day"


# ---------------------------------------------------------------------------
# 3. Confidence threshold
# Intent: Only execute trades where Grok confidence >= CONFIDENCE_THRESHOLD.
# Regression: Low-confidence suggestions get executed as real orders.
# ---------------------------------------------------------------------------

def test_rejects_low_confidence_trade():
    rm = RiskManager()
    trade = {
        "ticker": "TEST",
        "confidence": CONFIDENCE_THRESHOLD - 1,
        "entry": 10.0,
        "stop": 9.5,
        "shares": 10,
    }
    assert not rm.check_trade(trade), "Trade below confidence threshold should be rejected"


def test_accepts_trade_at_confidence_threshold():
    rm = RiskManager()
    trade = {
        "ticker": "TEST",
        "confidence": CONFIDENCE_THRESHOLD,
        "entry": 10.0,
        "stop": 9.5,
        "shares": 10,
    }
    assert rm.check_trade(trade), "Trade at exactly the confidence threshold should be accepted"


# ---------------------------------------------------------------------------
# 4. Stop must be below entry (long-only)
# Intent: For long trades, stop above entry is invalid and must be rejected.
# Regression: Invalid stop-loss passes risk check, trade placed with no downside protection.
# ---------------------------------------------------------------------------

def test_rejects_stop_above_entry():
    rm = RiskManager()
    trade = {
        "ticker": "TEST",
        "confidence": 90,
        "entry": 10.0,
        "stop": 11.0,
        "shares": 10,
    }
    assert not rm.check_trade(trade), "Stop above entry should be rejected for long trades"


def test_rejects_stop_equal_to_entry():
    rm = RiskManager()
    trade = {
        "ticker": "TEST",
        "confidence": 90,
        "entry": 10.0,
        "stop": 10.0,
        "shares": 10,
    }
    assert not rm.check_trade(trade), "Stop equal to entry should be rejected"
