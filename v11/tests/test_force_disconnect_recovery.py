"""Regression: force-disconnect + placement-stuck recovery (2026-04-23).

Incident: after a half-open socket event, the executor's raw ib.disconnect()
in the placement error handler silently no-op'd. disconnectedEvent never
fired, _connected stayed True, ensure_connected() kept returning True, and
V11 spammed "Not connected" placement errors for hours.

Fixes covered here:
  1. IBKRConnection.force_disconnect() sets _connected=False and starts the
     disconnect timer, regardless of ib_insync internal state.
  2. IBKRExecutionEngine increments _consec_placement_conn_failures on
     "Not connected"-class errors, invokes the force_disconnect callback,
     resets on success, does NOT count non-connectivity errors.
  3. placement_stuck property flips at threshold so the run_live tripwire
     can SystemExit and let the wrapper restart.
"""
import asyncio
import logging
from unittest.mock import MagicMock

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.execution.ibkr_connection import IBKRConnection
from v11.v6_orb.ibkr_executor import IBKRExecutionEngine
from v11.v6_orb.market_event import RangeInfo


log = logging.getLogger("test_force_disconnect")


# ── force_disconnect on IBKRConnection ───────────────────────────────────────

def _conn():
    c = IBKRConnection(host="127.0.0.1", port=4002, client_id=1, log=log)
    c.ib = MagicMock()
    c._connected = True
    return c


def test_force_disconnect_sets_internal_flag_false():
    c = _conn()
    c.force_disconnect("test")
    assert c._connected is False


def test_force_disconnect_starts_outage_timer():
    c = _conn()
    assert c._first_disconnect_time is None
    c.force_disconnect("test")
    assert c._first_disconnect_time is not None


def test_force_disconnect_preserves_existing_outage_timer():
    """If a disconnect timer is already running, force_disconnect must not
    reset it — otherwise persistent_failure detection breaks."""
    c = _conn()
    c._first_disconnect_time = 1000.0
    c.force_disconnect("test")
    assert c._first_disconnect_time == 1000.0


def test_force_disconnect_calls_ib_disconnect_best_effort():
    c = _conn()
    c.force_disconnect("test")
    c.ib.disconnect.assert_called_once()


def test_force_disconnect_swallows_ib_disconnect_exception():
    c = _conn()
    c.ib.disconnect.side_effect = RuntimeError("already closed")
    # Must not raise
    c.force_disconnect("test")
    assert c._connected is False


# ── placement failure tracking on IBKRExecutionEngine ────────────────────────

def _contract():
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = 1
    return c


def _executor(force_cb=None):
    ib = MagicMock()
    ib.isConnected.return_value = True
    # placeOrder raises by default — tests override per-case.
    ib.placeOrder.side_effect = ConnectionError("Not connected")
    ex = IBKRExecutionEngine(
        ib=ib, contract=_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
        force_disconnect_callback=force_cb,
    )
    return ex


def _range():
    return RangeInfo(high=4800.0, low=4780.0, start_time=None, end_time=None)


def test_placement_counter_increments_on_not_connected():
    calls = []
    ex = _executor(force_cb=lambda reason: calls.append(reason))
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.0)
    assert ok is False
    assert ex._consec_placement_conn_failures == 1
    assert calls == ["placement_not_connected"]


def test_placement_counter_accumulates_across_failures():
    calls = []
    ex = _executor(force_cb=lambda reason: calls.append(reason))
    for _ in range(3):
        ex.set_orb_brackets(_range(), rr_ratio=2.0)
    assert ex._consec_placement_conn_failures == 3
    assert len(calls) == 3


def test_placement_counter_resets_on_success():
    ex = _executor(force_cb=lambda reason: None)
    ex.set_orb_brackets(_range(), rr_ratio=2.0)
    assert ex._consec_placement_conn_failures == 1

    # Next placement succeeds.
    trade = MagicMock()
    trade.order.orderId = 42
    ex.ib.placeOrder.side_effect = None
    ex.ib.placeOrder.return_value = trade
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.0)
    assert ok is True
    assert ex._consec_placement_conn_failures == 0


def test_placement_counter_resets_on_non_connectivity_error():
    """A non-connectivity exception (e.g. ValueError) must NOT accumulate
    toward the stuck threshold — those don't indicate a bad socket."""
    ex = _executor(force_cb=lambda reason: None)
    # First, bump the counter with a connectivity error.
    ex.set_orb_brackets(_range(), rr_ratio=2.0)
    assert ex._consec_placement_conn_failures == 1
    # Now a non-connectivity error arrives.
    ex.ib.placeOrder.side_effect = ValueError("bad price")
    ex.set_orb_brackets(_range(), rr_ratio=2.0)
    assert ex._consec_placement_conn_failures == 0


def test_placement_stuck_false_below_threshold():
    ex = _executor(force_cb=lambda reason: None)
    ex._consec_placement_conn_failures = ex._placement_stuck_threshold - 1
    assert ex.placement_stuck is False


def test_placement_stuck_true_at_threshold():
    ex = _executor(force_cb=lambda reason: None)
    ex._consec_placement_conn_failures = ex._placement_stuck_threshold
    assert ex.placement_stuck is True


def test_placement_fallback_when_no_callback_wired():
    """Legacy path: no callback wired → fall back to raw ib.disconnect()
    so the code still does *something*. Not reliable on half-open sockets
    but kept as a safety net."""
    ex = _executor(force_cb=None)
    ex.set_orb_brackets(_range(), rr_ratio=2.0)
    # The fallback ib.disconnect() should have been invoked.
    ex.ib.disconnect.assert_called()


def test_force_disconnect_callback_exception_is_logged_not_raised():
    def bad_cb(reason):
        raise RuntimeError("callback exploded")
    ex = _executor(force_cb=bad_cb)
    # Must not raise — placement returns False cleanly.
    ok = ex.set_orb_brackets(_range(), rr_ratio=2.0)
    assert ok is False
    assert ex._consec_placement_conn_failures == 1
