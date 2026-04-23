"""Regression: reconcile_after_reconnect graduated recovery (P0.3).

2026-04-21 incident: reconnect handler called cancel_orders_for(pair) which
wiped the surviving SL leg and left the position naked. reconcile_after_reconnect
replaces that blanket cancel with a graduated response:

  - outage <60s AND range_info in memory → re-arm SL/TP, keep position
  - outage >=60s OR no range_info         → flatten, emit ORPHAN_FLATTEN

Contract covered here:
  "flat"     broker 0, internal 0       → no-op
  "flat"     broker 0, internal != 0    → reset local (SL/TP filled in outage)
  "ok"       broker and internal agree  → no-op (invariant takes over)
  "rearm"    orphan short outage + range → _place_sl_tp called, no fill emitted
  "flatten"  orphan long outage         → _cancel_and_close + ORPHAN_FLATTEN fill
  "flatten"  orphan no range            → same, regardless of outage length
  "error"    disconnected               → skip, caller retries
"""
import asyncio
import logging
from unittest.mock import MagicMock, patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.v6_orb.ibkr_executor import IBKRExecutionEngine
from v11.v6_orb.market_event import RangeInfo


log = logging.getLogger("test_reconcile")


def _contract(conid=1):
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = conid
    return c


def _pos(conid, qty, avg_cost=0.0):
    p = MagicMock()
    p.contract.conId = conid
    p.position = qty
    p.avgCost = avg_cost
    return p


def _make(ib, fills):
    return IBKRExecutionEngine(
        ib=ib, contract=_contract(), quantity=1,
        on_fill_callback=lambda f: fills.append(f),
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )


def _range():
    return RangeInfo(high=4800.0, low=4780.0, start_time=None, end_time=None)


def test_reconcile_flat_when_both_flat():
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = []
    fills = []
    ex = _make(ib, fills)
    assert ex.reconcile_after_reconnect(outage_s=5.0) == "flat"
    assert fills == []


def test_reconcile_flat_resets_when_internal_thought_in_trade():
    """Broker flat, internal thought we were short → SL/TP filled during outage."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = []
    fills = []
    ex = _make(ib, fills)
    ex._position = -1
    ex._direction = "SHORT"
    ex._entry_price = 4783.14

    with patch.object(ex, "_cancel_all_for_contract") as cancel:
        outcome = ex.reconcile_after_reconnect(outage_s=120.0)
        cancel.assert_called_once()

    assert outcome == "flat"
    assert ex._position == 0
    assert fills == []  # no ORPHAN_FLATTEN — broker already flat


def test_reconcile_ok_when_agree():
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = [_pos(1, -1.0, avg_cost=4783.14)]
    fills = []
    ex = _make(ib, fills)
    ex._position = -1
    ex._direction = "SHORT"
    ex._entry_price = 4783.14

    assert ex.reconcile_after_reconnect(outage_s=3.0) == "ok"
    assert ex._position == -1
    assert fills == []


def test_reconcile_rearm_short_outage_with_range():
    """Orphan + outage <60s + range_info present → adopt + re-place SL/TP."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = [_pos(1, -1.0, avg_cost=4783.14)]
    fills = []
    ex = _make(ib, fills)
    # Internal flat (e.g., after false "vanished" reset), range still held.
    ex._position = 0
    ex._range_info = _range()

    with patch.object(ex, "_cancel_all_for_contract") as cancel, \
         patch.object(ex, "_place_sl_tp") as place:
        outcome = ex.reconcile_after_reconnect(outage_s=12.0)
        cancel.assert_called_once()
        place.assert_called_once()
        args, _ = place.call_args
        assert args[0] == "SHORT"
        assert args[1] == 4783.14  # avgCost preferred

    assert outcome == "rearm"
    assert ex._position == -1
    assert ex._direction == "SHORT"
    assert fills == []  # no ORPHAN_FLATTEN — we re-armed


def test_reconcile_flatten_long_outage():
    """Orphan + outage >=60s → flatten regardless of range_info."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = [_pos(1, -1.0, avg_cost=4783.14)]
    fills = []
    ex = _make(ib, fills)
    ex._position = 0
    ex._range_info = _range()  # present but ignored due to outage

    with patch.object(ex, "_cancel_and_close", return_value=4799.0) as cnc:
        outcome = ex.reconcile_after_reconnect(outage_s=120.0)
        cnc.assert_called_once()

    assert outcome == "flatten"
    assert len(fills) == 1
    assert fills[0].reason == "ORPHAN_FLATTEN"
    assert fills[0].direction == "SHORT"
    assert fills[0].price == 4799.0
    assert ex._position == 0


def test_reconcile_flatten_no_range():
    """Orphan with no range_info → flatten even on short outage."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = [_pos(1, 1.0, avg_cost=4700.0)]
    fills = []
    ex = _make(ib, fills)
    ex._position = 0
    ex._range_info = None

    with patch.object(ex, "_cancel_and_close", return_value=4701.0) as cnc:
        outcome = ex.reconcile_after_reconnect(outage_s=5.0)
        cnc.assert_called_once()

    assert outcome == "flatten"
    assert len(fills) == 1
    assert fills[0].reason == "ORPHAN_FLATTEN"
    assert fills[0].direction == "LONG"


def test_reconcile_rearm_falls_back_to_flatten_on_place_sl_tp_exception():
    """If _place_sl_tp raises during re-arm we must flatten, not leave naked."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = [_pos(1, -1.0, avg_cost=4783.14)]
    fills = []
    ex = _make(ib, fills)
    ex._position = 0
    ex._range_info = _range()

    with patch.object(ex, "_cancel_all_for_contract"), \
         patch.object(ex, "_place_sl_tp", side_effect=RuntimeError("boom")), \
         patch.object(ex, "_cancel_and_close", return_value=4800.0) as cnc:
        outcome = ex.reconcile_after_reconnect(outage_s=10.0)
        cnc.assert_called_once()

    assert outcome == "flatten"
    assert len(fills) == 1
    assert fills[0].reason == "ORPHAN_FLATTEN"


def test_reconcile_error_when_disconnected():
    ib = MagicMock()
    ib.isConnected.return_value = False
    fills = []
    ex = _make(ib, fills)
    assert ex.reconcile_after_reconnect(outage_s=5.0) == "error"
    ib.positions.assert_not_called()


def test_reconcile_error_when_positions_raises():
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.side_effect = RuntimeError("socket gone")
    fills = []
    ex = _make(ib, fills)
    assert ex.reconcile_after_reconnect(outage_s=5.0) == "error"
    assert fills == []
