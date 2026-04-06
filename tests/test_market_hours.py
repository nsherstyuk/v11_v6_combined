"""
Tests for market hours schedule — verifies the 3-phase system works correctly.

Design decisions tested:
  5. No orders outside REGULAR RTH (9:30 AM – 4:00 PM ET weekdays)
  6. seconds_until_next_active() correctly skips weekends
"""

import sys
import os
from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import get_market_status, seconds_until_next_active, ET, ANALYSIS_START

# ---------------------------------------------------------------------------
# 5. Market hours: no orders outside REGULAR phase
# Intent: Orders may only be placed during RTH (9:30 AM – 4:00 PM ET weekdays).
# Regression: Orders placed at 2 AM, on weekends, or in pre-market.
# ---------------------------------------------------------------------------

def _mock_now(year, month, day, hour, minute):
    """Create a timezone-aware datetime in ET for patching."""
    return datetime(year, month, day, hour, minute, 0, tzinfo=ET)


def test_regular_hours_is_rth():
    # Monday 10:30 AM ET → should be REGULAR
    mock_time = _mock_now(2026, 3, 30, 10, 30)  # Monday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        status = get_market_status()
    assert status["phase"] == "REGULAR"
    assert status["is_rth"] is True
    assert status["is_active"] is True


def test_premarket_no_orders():
    # Monday 8:00 AM ET → should be PRE-MARKET (active but not RTH)
    mock_time = _mock_now(2026, 3, 30, 8, 0)  # Monday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        status = get_market_status()
    assert status["phase"] == "PRE-MARKET"
    assert status["is_rth"] is False
    assert status["is_active"] is True


def test_after_hours_sleeping():
    # Monday 5:00 PM ET → AFTER-HOURS / SLEEPING
    mock_time = _mock_now(2026, 3, 30, 17, 0)  # Monday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        status = get_market_status()
    assert status["phase"] == "SLEEPING"
    assert status["is_rth"] is False
    assert status["is_active"] is False
    assert status["session"] == "AFTER-HOURS"


def test_overnight_sleeping():
    # Monday 3:00 AM ET → OVERNIGHT / SLEEPING
    mock_time = _mock_now(2026, 3, 30, 3, 0)  # Monday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        status = get_market_status()
    assert status["phase"] == "SLEEPING"
    assert status["is_rth"] is False
    assert status["is_active"] is False
    assert status["session"] == "OVERNIGHT"


def test_weekend_sleeping():
    # Saturday 12:00 PM ET → WEEKEND / SLEEPING
    mock_time = _mock_now(2026, 3, 28, 12, 0)  # Saturday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        status = get_market_status()
    assert status["phase"] == "SLEEPING"
    assert status["session"] == "WEEKEND"
    assert status["is_rth"] is False
    assert status["is_active"] is False


def test_market_open_boundary():
    # Monday 9:30 AM ET → exactly REGULAR
    mock_time = _mock_now(2026, 3, 30, 9, 30)  # Monday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        status = get_market_status()
    assert status["phase"] == "REGULAR"
    assert status["is_rth"] is True


def test_market_close_boundary():
    # Monday 4:00 PM ET → exactly SLEEPING (close is exclusive)
    mock_time = _mock_now(2026, 3, 30, 16, 0)  # Monday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        status = get_market_status()
    assert status["phase"] == "SLEEPING"
    assert status["is_rth"] is False


# ---------------------------------------------------------------------------
# 6. seconds_until_next_active() skips weekends
# Intent: Agent sleeps until next weekday ANALYSIS_START, never wakes on Sat/Sun.
# Regression: Agent sleeps wrong duration and misses Monday open or wakes on Saturday.
# ---------------------------------------------------------------------------

def test_seconds_until_next_active_skips_weekend():
    # Friday 5 PM ET → should skip Saturday and Sunday, wake Monday 7 AM
    mock_time = _mock_now(2026, 3, 27, 17, 0)  # Friday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        secs = seconds_until_next_active()

    # Friday 5PM → Monday 7AM = 62 hours = 223,200 seconds
    expected = 62 * 3600
    # Allow 1-second tolerance for rounding
    assert abs(secs - expected) <= 1, f"Expected ~{expected}s, got {secs}s"


def test_seconds_until_next_active_same_day():
    # Monday 3 AM ET (before ANALYSIS_START 7 AM) → should wake at 7 AM today
    mock_time = _mock_now(2026, 3, 30, 3, 0)  # Monday
    with patch("main.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        secs = seconds_until_next_active()

    # 3 AM → 7 AM = 4 hours = 14,400 seconds
    expected = 4 * 3600
    assert abs(secs - expected) <= 1, f"Expected ~{expected}s, got {secs}s"
