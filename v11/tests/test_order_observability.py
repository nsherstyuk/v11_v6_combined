"""Regression tests for the 2026-04-20 ghost-order incident.

Three fixes being validated:
  (1) TIF=DAY instead of GTD in IBKRExecutionEngine (paper preset
      rejects GTD via Error 10349, silently Cancels).
  (2) orderStatusEvent listener hooked in executor; terminal-non-Filled
      states (Cancelled/Inactive/ApiCancelled) flip entry_placement_failed.
  (3) ORB strategy polls entry_placement_failed() and falls back to
      RANGE_READY on async placement failure.

  (Reconciliation in run_live._reconcile_orders is an integration-path
  concern and is verified indirectly via its component pieces.)
"""
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pytest

from v11.v6_orb.orb_strategy import ORBStrategy, StrategyState
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import Tick, RangeInfo
from v11.v6_orb.ibkr_executor import IBKRExecutionEngine


log = logging.getLogger("test_order_observability")


def _config():
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


def _mock_ib():
    ib = MagicMock()
    ib.isConnected.return_value = True
    # Make orderStatusEvent behave like an event: support += subscription
    ib.orderStatusEvent = MagicMock()
    ib.orderStatusEvent.__iadd__ = MagicMock(return_value=ib.orderStatusEvent)
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


# ── Fix 1: TIF=DAY not GTD ──────────────────────────────────────────────────


def test_orders_use_tif_day_not_gtd():
    """Regression for 2026-04-20 ghost-order bug: IBKR paper preset
    rejects GTD (Error 10349) and silently Cancels the order. TIF must
    be DAY so the order is accepted as-is."""
    ib = _mock_ib()
    # Capture each Order object passed to placeOrder
    placed_orders = []
    def _placeOrder(contract, order):
        placed_orders.append(order)
        trade = MagicMock()
        trade.order = MagicMock()
        trade.order.orderId = len(placed_orders) + 100
        return trade
    ib.placeOrder.side_effect = _placeOrder

    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ok is True
    assert len(placed_orders) == 2
    for o in placed_orders:
        assert o.tif == "DAY", (
            f"Entry order TIF must be DAY (got {o.tif}) so IBKR's paper "
            f"preset doesn't auto-Cancel with Error 10349")
        # GTD field must be empty so we don't confuse IBKR
        assert not getattr(o, "goodTillDate", ""), \
            "goodTillDate must be empty when TIF=DAY"


# ── Fix 1b: STP orders use triggerMethod=7 (2026-04-24 incident) ───────────


def test_entry_stops_use_trigger_method_7():
    """Regression for 2026-04-24: IBKR paper XAUUSD streams bid/ask only
    (zero `last` prints). Default triggerMethod=0 waits on `last` and
    never fires. Method 7 ('last or double-bid/ask') falls back to
    bid/ask when last is absent, which is the only way STP entries
    fire on paper XAU."""
    ib = _mock_ib()
    placed_orders = []
    def _placeOrder(contract, order):
        placed_orders.append(order)
        trade = MagicMock()
        trade.order = MagicMock()
        trade.order.orderId = len(placed_orders) + 100
        return trade
    ib.placeOrder.side_effect = _placeOrder

    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ok is True
    assert len(placed_orders) == 2
    for o in placed_orders:
        assert o.orderType == "STP"
        assert o.triggerMethod == 7, (
            f"STP entry order must set triggerMethod=7 (got "
            f"{o.triggerMethod}); on paper XAUUSD, default method waits "
            f"on `last` which never prints, so the stop never fires.")


def test_sl_order_uses_trigger_method_7():
    """Same root cause as entry stops: SL is a STP order, and on paper
    XAUUSD with no `last` prints, default triggerMethod would let the
    SL sit unexecuted even as price trades through it."""
    ib = _mock_ib()
    placed_orders = []
    def _placeOrder(contract, order):
        placed_orders.append(order)
        trade = MagicMock()
        trade.order = MagicMock()
        trade.order.orderId = len(placed_orders) + 200
        return trade
    ib.placeOrder.side_effect = _placeOrder

    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex._range_info = _range()
    ex._rr_ratio = 2.5
    ex._place_sl_tp("LONG", entry_price=2660.0)

    stp_orders = [o for o in placed_orders if o.orderType == "STP"]
    assert len(stp_orders) == 1, "expected one SL (STP) order"
    sl = stp_orders[0]
    assert sl.triggerMethod == 7, (
        f"SL STP order must set triggerMethod=7 (got {sl.triggerMethod})")


# ── Fix 2: orderStatusEvent listener detects silent Cancels ────────────────


def test_status_event_listener_hooked_on_placement():
    """set_orb_brackets must hook ib.orderStatusEvent so we see every
    IBKR-side transition for entry orders."""
    ib = _mock_ib()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    assert ex._hooked_status_event is False
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ex._hooked_status_event is True, \
        "After first placement, orderStatusEvent listener must be hooked"


def test_status_event_hook_is_idempotent():
    """Calling set_orb_brackets twice must not re-hook (avoid duplicate
    callbacks causing each status event to log twice)."""
    ib = _mock_ib()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    # orderStatusEvent.__iadd__ called exactly once across two placements
    assert ib.orderStatusEvent.__iadd__.call_count == 1


def _make_trade_with_status(order_id, status, filled=0, message=""):
    """Build a MagicMock trade matching ib_insync's Trade interface."""
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = order_id
    trade.orderStatus = MagicMock()
    trade.orderStatus.status = status
    trade.orderStatus.filled = filled
    trade.orderStatus.remaining = 1 - filled
    log_entry = MagicMock()
    log_entry.message = message
    trade.log = [log_entry] if message else []
    return trade


def test_cancelled_entry_before_fill_flips_failure_flag():
    """When an entry order transitions to Cancelled before any Fill,
    entry_placement_failed() must become True."""
    ib = _mock_ib()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    assert ex.entry_placement_failed() is False

    # Simulate IBKR cancelling our BUY entry silently
    trade = _make_trade_with_status(
        order_id=ex.buy_entry_id, status="Cancelled", filled=0,
        message="Error 10349: Order TIF was set to DAY based on order preset.")
    ex._on_order_status(trade)

    assert ex.entry_placement_failed() is True
    assert "Cancelled" in ex.entry_failure_reason()
    assert "10349" in ex.entry_failure_reason()


def test_inactive_entry_before_fill_also_flips_failure():
    """Inactive status is another terminal non-Filled state IBKR uses."""
    ib = _mock_ib()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    trade = _make_trade_with_status(
        order_id=ex.sell_entry_id, status="Inactive", filled=0)
    ex._on_order_status(trade)
    assert ex.entry_placement_failed() is True


def test_filled_entry_does_not_flip_failure():
    """A filled entry is success, not failure — don't flip the flag."""
    ib = _mock_ib()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    trade = _make_trade_with_status(
        order_id=ex.buy_entry_id, status="Filled", filled=1)
    ex._on_order_status(trade)
    assert ex.entry_placement_failed() is False


def test_unrelated_order_status_ignored():
    """Status events for orders we don't own (SL, TP, other clients')
    must not flip our flag — that would cause false-positive failures."""
    ib = _mock_ib()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    # Some random other order ID we don't recognize
    trade = _make_trade_with_status(
        order_id=9999, status="Cancelled", filled=0)
    ex._on_order_status(trade)
    assert ex.entry_placement_failed() is False


def test_clear_entry_failure_resets_state():
    """After handling a failure, caller should be able to clear it so
    subsequent placements start clean."""
    ib = _mock_ib()
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    trade = _make_trade_with_status(
        order_id=ex.buy_entry_id, status="Cancelled", filled=0)
    ex._on_order_status(trade)
    assert ex.entry_placement_failed() is True
    ex.clear_entry_failure()
    assert ex.entry_placement_failed() is False
    assert ex.entry_failure_reason() == ""


# ── Fix 3: Strategy falls back to RANGE_READY on async failure ─────────────


class _FakeContext:
    def __init__(self, range_info):
        self.daily_range = range_info
        self.daily_range_date = datetime(2025, 1, 15).date()

    def time_is_in_trade_window(self, now, start, end):
        return start <= now.hour < end

    def get_asian_range(self, *_):
        return self.daily_range

    def is_within_range(self, price):
        return self.daily_range.low <= price <= self.daily_range.high

    def get_velocity(self, *_):
        return 0.0

    def get_current_price(self, *_):
        return (self.daily_range.low + self.daily_range.high) / 2

    def get_gap_metrics(self, *args):
        from v11.v6_orb.market_event import GapMetrics
        return GapMetrics(gap_volatility=0.0, gap_range=0.0,
                          vol_passes=True, range_passes=True)


class _FakeExec:
    """Minimal ExecutionEngine stand-in that supports the new
    entry_placement_failed protocol."""
    def __init__(self):
        self._failed = False
        self._reason = ""
        self._cancelled_count = 0
        self._cleared = False

    def set_orb_brackets(self, *args, **kwargs):
        return True

    def cancel_orb_brackets(self):
        self._cancelled_count += 1

    def entry_placement_failed(self):
        return self._failed

    def entry_failure_reason(self):
        return self._reason

    def clear_entry_failure(self):
        self._failed = False
        self._reason = ""
        self._cleared = True

    # Strategy also calls these; keep as no-ops
    def has_position(self):
        return False

    def check_fills(self):
        pass

    def close_at_market(self):
        pass


def test_strategy_falls_back_to_range_ready_on_async_failure():
    """When strategy is in ORDERS_PLACED and executor.entry_placement_failed()
    returns True, strategy must revert to RANGE_READY, cancel the (likely
    already-dead) brackets, and clear the failure so it can retry."""
    cfg = _config()
    strategy = ORBStrategy(cfg, logger=log)
    strategy.range = _range()
    strategy.state = StrategyState.ORDERS_PLACED
    strategy.orders_placed_time = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)

    ctx = _FakeContext(strategy.range)
    exec_ = _FakeExec()
    exec_._failed = True
    exec_._reason = "Cancelled: Error 10349"

    tick = Tick(
        timestamp=datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc),
        bid=2655.0, ask=2655.1,
    )
    strategy.on_tick(tick, ctx, exec_)

    assert strategy.state == StrategyState.RANGE_READY, \
        "Strategy must fall back to RANGE_READY after async placement failure"
    assert exec_._cancelled_count == 1, \
        "Strategy should call cancel_orb_brackets even if orders are already dead"
    assert exec_._cleared is True, \
        "Strategy must clear the executor's failure flag after handling"
    assert strategy.orders_placed_time is None, \
        "orders_placed_time must reset so max_pending_hours timer is fresh"


def test_strategy_does_not_fall_back_when_no_failure():
    """Normal case — no async failure — strategy should remain in
    ORDERS_PLACED and behave as before."""
    cfg = _config()
    strategy = ORBStrategy(cfg, logger=log)
    strategy.range = _range()
    strategy.state = StrategyState.ORDERS_PLACED
    strategy.orders_placed_time = datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc)

    ctx = _FakeContext(strategy.range)
    exec_ = _FakeExec()
    exec_._failed = False

    tick = Tick(
        timestamp=datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc),
        bid=2655.0, ask=2655.1,
    )
    strategy.on_tick(tick, ctx, exec_)

    assert strategy.state == StrategyState.ORDERS_PLACED
    assert exec_._cancelled_count == 0
