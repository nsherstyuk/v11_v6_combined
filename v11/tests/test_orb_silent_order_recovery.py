"""Integration tests for the silent-order-failure bug fix.

The production bug (fixed in commit 1923d80):
    IBKRExecutionEngine.set_orb_brackets swallowed placement exceptions,
    the strategy unconditionally transitioned to ORDERS_PLACED on phantom
    orders, and the trading day was lost when the 4h timeout cancelled the
    non-existent orders.

These tests drive the REAL V6 ORBStrategy + IBKRExecutionEngine with a
mocked ib_insync IB client, simulating placement failures, to verify:

1. If set_orb_brackets raises, it returns False (not silently swallowing).
2. Strategy stays in RANGE_READY on failure (not phantom ORDERS_PLACED).
3. Order IDs are reset so has_resting_entries() returns False.
4. Next tick retries placement — so transient disconnects auto-recover.
5. On eventual success, strategy transitions to ORDERS_PLACED normally.
"""
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

# ib_insync touches the asyncio event loop on import; pytest doesn't
# set one up by default. Mirror the same safety from run_live.py.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pytest

from v11.v6_orb.orb_strategy import ORBStrategy, StrategyState
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import Tick, RangeInfo
from v11.v6_orb.ibkr_executor import IBKRExecutionEngine


log = logging.getLogger("test_silent_order_recovery")


def _config() -> V6StrategyConfig:
    return V6StrategyConfig(
        instrument="XAUUSD",
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=8, trade_end_hour=16,
        rr_ratio=2.5,
        min_range_size=1.0, max_range_size=50.0,
        velocity_filter_enabled=False,
        gap_filter_enabled=False,
        qty=1, point_value=1.0, price_decimals=2,
    )


def _mock_ib(raise_on_place: bool = False):
    ib = MagicMock()
    ib.isConnected.return_value = True
    if raise_on_place:
        # Simulate IBKR disconnection: placeOrder raises
        ib.placeOrder.side_effect = ConnectionError("Not connected")
    else:
        trade = MagicMock()
        trade.order = MagicMock()
        trade.order.orderId = 42
        ib.placeOrder.return_value = trade
    return ib


def _mock_contract():
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = 1
    return c


def _range():
    return RangeInfo(high=2660.0, low=2650.0, start_time=None, end_time=None)


# ── Executor-level tests ────────────────────────────────────────────────────


def test_set_orb_brackets_returns_true_on_success():
    ib = _mock_ib(raise_on_place=False)
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ok is True
    # Order IDs populated (something non-zero)
    assert ex.buy_entry_id != 0
    assert ex.sell_entry_id != 0


def test_set_orb_brackets_returns_false_on_exception():
    ib = _mock_ib(raise_on_place=True)
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ok is False
    # Order IDs reset so has_resting_entries() is False
    assert ex.buy_entry_id == 0
    assert ex.sell_entry_id == 0
    assert ex.has_resting_entries() is False


def test_dry_run_path_returns_true():
    """dry_run=True short-circuits placement; must still return True."""
    ib = _mock_ib(raise_on_place=True)  # should never be called
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=True, logger=log,
    )
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ok is True
    ib.placeOrder.assert_not_called()


# ── Strategy-level recovery tests ───────────────────────────────────────────


class _FakeContext:
    """Minimal MarketContext stand-in for ORBStrategy."""
    def __init__(self, range_info: RangeInfo):
        self.daily_range = range_info
        self.daily_range_date = datetime(2025, 1, 15, 8, 0, tzinfo=timezone.utc).date()

    def time_is_in_trade_window(self, now, start_hour, end_hour):
        return start_hour <= now.hour < end_hour

    def get_asian_range(self, start_hour, end_hour):
        return self.daily_range

    def is_within_range(self, price):
        return self.daily_range.low <= price <= self.daily_range.high

    def get_velocity(self, lookback, now):
        # Filter disabled in config, but strategy may still call
        return 0.0

    def get_current_price(self, now):
        return (self.daily_range.low + self.daily_range.high) / 2

    def get_gap_metrics(self, now, gs, ge, vp, rp, rd):
        from v11.v6_orb.market_event import GapMetrics
        return GapMetrics(
            gap_volatility=0.0, gap_range=0.0,
            vol_passes=True, range_passes=True,
        )


def test_strategy_stays_range_ready_when_placement_fails():
    """Simulate the production bug scenario: placement fails → strategy
    must NOT transition to ORDERS_PLACED."""
    cfg = _config()
    strategy = ORBStrategy(cfg, logger=log)
    strategy.range = _range()
    strategy.state = StrategyState.RANGE_READY

    ib = _mock_ib(raise_on_place=True)  # Disconnected
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ctx = _FakeContext(strategy.range)

    # Tick inside the range triggers the placement path
    tick = Tick(
        timestamp=datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
        bid=2655.0, ask=2655.1,
    )
    strategy.on_tick(tick, ctx, ex)

    # PRODUCTION BUG REGRESSION: these two must hold
    assert strategy.state == StrategyState.RANGE_READY, \
        "Strategy must NOT transition to ORDERS_PLACED on failed placement"
    assert ex.has_resting_entries() is False, \
        "Executor must report no resting entries so retry can happen"


def test_strategy_retries_and_recovers_after_reconnect():
    """Simulate: first tick = disconnected, placement fails → stay RANGE_READY.
    Second tick = reconnected, placement succeeds → transition to ORDERS_PLACED."""
    cfg = _config()
    strategy = ORBStrategy(cfg, logger=log)
    strategy.range = _range()
    strategy.state = StrategyState.RANGE_READY

    ib = _mock_ib(raise_on_place=True)  # Start disconnected
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ctx = _FakeContext(strategy.range)

    tick1 = Tick(
        timestamp=datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
        bid=2655.0, ask=2655.1,
    )
    strategy.on_tick(tick1, ctx, ex)
    assert strategy.state == StrategyState.RANGE_READY

    # "IBKR reconnects" — placeOrder now succeeds
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = 100
    ib.placeOrder.side_effect = None
    ib.placeOrder.return_value = trade

    tick2 = Tick(
        timestamp=datetime(2025, 1, 15, 10, 0, 2, tzinfo=timezone.utc),
        bid=2655.2, ask=2655.3,
    )
    strategy.on_tick(tick2, ctx, ex)

    assert strategy.state == StrategyState.ORDERS_PLACED, \
        "After reconnect, next tick should successfully place brackets"
    assert ex.has_resting_entries() is True, \
        "Executor should have resting entries after successful placement"
