"""Regression tests for the 2026-04-20 silent half-open socket incident.

Failure mode (from v11_live_20260419_233434.log):
    1. IB Gateway connection entered a half-open state after midnight
       reconnect — TCP socket up, ib_insync isConnected()==True, but
       API calls started raising "Not connected" silently.
    2. V11's passive disconnect listener (disconnectedEvent) never fired
       because the socket looked alive.
    3. Range calc's IBKR bar fetch failed, fell back to tick buffer
       (only 2h of overnight data), produced a bogus 4.5-point XAUUSD
       range and proceeded to RANGE_READY.
    4. Every placeOrder attempt failed "Not connected" but state machine
       retried forever. Trading day was lost.

Two fixes (this file regresses both):
  (a) ibkr_executor + live_context: on connectivity-class errors, call
      ib.disconnect() explicitly to fire disconnectedEvent and trigger
      the existing reconnect flow.
  (b) live_context.calculate_daily_range: reject ranges < 0.1% of mid
      price regardless of per-config min_range_pct (plausibility floor).
"""
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

# ib_insync touches the asyncio loop on import; pytest doesn't set one up.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pytest

from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.ibkr_executor import IBKRExecutionEngine
from v11.v6_orb.market_event import RangeInfo


log = logging.getLogger("test_silent_half_open")


def _config() -> V6StrategyConfig:
    return V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=8, trade_end_hour=16,
        rr_ratio=2.5,
        min_range_size=1.0, max_range_size=50.0,
        min_range_pct=0.0001,  # very lax — sanity floor should still catch bogus range
        velocity_filter_enabled=False,
        gap_filter_enabled=False,
        qty=1, point_value=1.0, price_decimals=2,
    )


def _mock_ib_with_connectivity_error():
    """Mock ib_insync.IB that raises 'Not connected' on placeOrder
    but still claims isConnected()==True (the silent half-open case)."""
    ib = MagicMock()
    ib.isConnected.return_value = True  # TCP layer looks fine
    ib.placeOrder.side_effect = ConnectionError("Not connected")
    return ib


def _mock_contract():
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = 1
    return c


def _range():
    return RangeInfo(high=2660.0, low=2650.0, start_time=None, end_time=None)


# ── Fix (a): executor calls ib.disconnect() on connectivity errors ──────────


def test_executor_forces_disconnect_on_not_connected():
    """Regression for 2026-04-20: when placeOrder raises 'Not connected',
    the executor must call ib.disconnect() so the main loop's reconnect
    flow fires. Without this, ib.isConnected() stays True and the loop
    never reconnects."""
    ib = _mock_ib_with_connectivity_error()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ok is False
    # The critical assertion: ib.disconnect() was called to force reconnect
    ib.disconnect.assert_called_once()


def test_executor_does_not_disconnect_on_unrelated_errors():
    """When placeOrder fails for a NON-connectivity reason (e.g. invalid
    order params), don't wastefully tear down the socket."""
    ib = _mock_ib_with_connectivity_error()
    ib.placeOrder.side_effect = ValueError("Invalid order type")
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ok is False
    ib.disconnect.assert_not_called()


# ── Fix (b): range sanity check ─────────────────────────────────────────────


class _StubContextForRangeCheck:
    """Wraps the real calculate_daily_range logic without needing a full
    LiveMarketContext — we're testing the plausibility filter, not IBKR.
    """
    def __init__(self, primary_result, fallback_result):
        self._primary = primary_result
        self._fallback = fallback_result
        self.logger = log
        self._d = 2

    # Mirror the real calculate_daily_range implementation — we're
    # regressing the SANITY_MIN_RANGE_PCT threshold behavior.
    def calculate_range_from_ibkr_bars(self, *_):
        return self._primary

    def calculate_range_from_ticks(self, *_):
        return self._fallback

    # Bring in the real method under test
    from v11.v6_orb.live_context import LiveMarketContext
    calculate_daily_range = LiveMarketContext.calculate_daily_range


def test_sanity_check_rejects_absurdly_small_range():
    """A 4.52-point range at $4793 mid is 0.09% — physically absurd for
    XAUUSD (normal is 0.4-2%). This is exactly what the 2026-04-20
    incident produced via the tick-buffer fallback. Must be rejected."""
    bogus = RangeInfo(
        high=4795.52, low=4791.00,
        start_time=datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc),
    )
    ctx = _StubContextForRangeCheck(primary_result=None, fallback_result=bogus)
    result = ctx.calculate_daily_range(0, 6)
    assert result is None, \
        "Range of 4.52 points at $4793 mid (0.09%) must be rejected as implausible"


def test_sanity_check_accepts_plausible_range():
    """A realistic 35-point XAUUSD Asian range (0.7% of mid) must pass."""
    healthy = RangeInfo(
        high=4779.77, low=4744.77,
        start_time=datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc),
    )
    ctx = _StubContextForRangeCheck(primary_result=healthy, fallback_result=None)
    result = ctx.calculate_daily_range(0, 6)
    assert result is not None
    assert result.size == pytest.approx(35.0, abs=0.01)


def test_sanity_check_accepts_wide_range():
    """A 69.83-point range (same as 2026-04-20 post-restart) must pass."""
    wide = RangeInfo(
        high=4814.60, low=4744.77,
        start_time=datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc),
    )
    ctx = _StubContextForRangeCheck(primary_result=wide, fallback_result=None)
    result = ctx.calculate_daily_range(0, 6)
    assert result is not None
    assert result.size == pytest.approx(69.83, abs=0.01)


def test_sanity_check_at_threshold_boundary():
    """0.1% exactly should pass (inclusive boundary is the floor)."""
    # Mid = 2000.0; 0.1% = 2.0
    at_boundary = RangeInfo(
        high=2001.0, low=1999.0,
        start_time=datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc),
    )
    ctx = _StubContextForRangeCheck(primary_result=at_boundary, fallback_result=None)
    result = ctx.calculate_daily_range(0, 6)
    # 2.0 / 2000.0 = 0.001 exactly; the filter uses `< 0.001` so boundary passes
    assert result is not None


def test_sanity_check_primary_none_and_fallback_bogus():
    """Primary fails (common case on half-open socket), fallback produces
    bogus range from stale tick buffer — end result must be None, not
    the bogus range forwarded as if it were valid."""
    bogus_fallback = RangeInfo(
        high=4793.0, low=4791.0,  # 2 pt range, ~0.04%
        start_time=datetime(2026, 4, 20, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 4, 20, 6, 0, tzinfo=timezone.utc),
    )
    ctx = _StubContextForRangeCheck(primary_result=None,
                                     fallback_result=bogus_fallback)
    result = ctx.calculate_daily_range(0, 6)
    assert result is None, \
        "Fallback-from-ticks must still be subjected to the plausibility check"
