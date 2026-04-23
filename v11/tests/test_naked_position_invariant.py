"""Regression: naked-position invariant must force-flatten when a position
is held at the broker but SL or TP is not active, past the grace window.

2026-04-21 incident: reconnect handler cancelled an OCA bracket leg and
adopted the orphan into the risk manager without replacing SL/TP. Position
sat unprotected for 7h. P2.6 is the backstop that would have caught this
at the next tick past the 15s grace.

Contract:
  - pos != 0 AND active_bracket_ids does NOT contain both sl_order_id and
    tp_order_id AND age > grace_s => force-flatten (NAKED_FLATTEN fill).
  - Grace window suppresses false positives in first 15s after entry.
  - Not connected => skip (handled by reconnect path).
"""
import asyncio
import logging
from unittest.mock import MagicMock, patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.v6_orb.ibkr_executor import IBKRExecutionEngine


log = logging.getLogger("test_naked_invariant")


def _mock_contract():
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = 1
    return c


def _active_trade(order_id: int, conid: int = 1, status: str = "Submitted"):
    t = MagicMock()
    t.order = MagicMock()
    t.order.orderId = order_id
    t.orderStatus = MagicMock()
    t.orderStatus.status = status
    t.contract = MagicMock()
    t.contract.conId = conid
    return t


def _make(ib, fills, grace=15.0, interval=0.0):
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: fills.append(f),
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    ex._naked_grace_s = grace
    ex._invariant_interval_s = interval
    return ex


def _put_in_trade(ex, age_s: float, sl_id=86, tp_id=87):
    """Post-entry state: long 1, SL + TP IDs set, fill timestamp in the past."""
    import time as _time
    ex._position = -1
    ex._direction = "SHORT"
    ex._entry_price = 4783.14
    ex.sl_order_id = sl_id
    ex.tp_order_id = tp_id
    ex._entry_fill_ts = _time.time() - age_s


def test_invariant_skipped_inside_grace_window():
    """Fresh fill, no active SL/TP yet — must NOT flatten within grace."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.openTrades.return_value = []  # neither SL nor TP active

    fills = []
    ex = _make(ib, fills, grace=15.0)
    _put_in_trade(ex, age_s=5.0)  # 5s after fill

    with patch.object(ex, "_cancel_and_close") as cnc:
        ex._check_naked_position_invariant()
        cnc.assert_not_called()
    assert fills == []
    assert ex._position == -1  # unchanged


def test_invariant_flattens_when_naked_past_grace():
    """Past grace, no active SL/TP, broker holds position → must flatten."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.openTrades.return_value = []  # no active orders

    fills = []
    ex = _make(ib, fills, grace=15.0)
    _put_in_trade(ex, age_s=30.0)

    with patch.object(ex, "_cancel_and_close", return_value=4800.0) as cnc:
        ex._check_naked_position_invariant()
        cnc.assert_called_once()

    assert len(fills) == 1
    assert fills[0].reason == "NAKED_FLATTEN"
    assert ex._position == 0  # reset


def test_invariant_quiet_when_both_brackets_active():
    """Normal in-trade state: SL and TP both in active openTrades → no flatten."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.openTrades.return_value = [_active_trade(86), _active_trade(87)]

    fills = []
    ex = _make(ib, fills, grace=15.0)
    _put_in_trade(ex, age_s=60.0)

    with patch.object(ex, "_cancel_and_close") as cnc:
        ex._check_naked_position_invariant()
        cnc.assert_not_called()
    assert fills == []
    assert ex._position == -1


def test_invariant_fires_when_only_sl_missing():
    """SL got canceled mid-trade (2026-04-21 reconnect leg-cancel) → naked."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    # TP still active; SL not present
    ib.openTrades.return_value = [_active_trade(87)]

    fills = []
    ex = _make(ib, fills, grace=15.0)
    _put_in_trade(ex, age_s=30.0)

    with patch.object(ex, "_cancel_and_close", return_value=4800.0):
        ex._check_naked_position_invariant()

    assert len(fills) == 1
    assert fills[0].reason == "NAKED_FLATTEN"


def test_invariant_skipped_when_disconnected():
    ib = MagicMock()
    ib.isConnected.return_value = False
    fills = []
    ex = _make(ib, fills, grace=15.0)
    _put_in_trade(ex, age_s=30.0)

    with patch.object(ex, "_cancel_and_close") as cnc:
        ex._check_naked_position_invariant()
        cnc.assert_not_called()
    assert fills == []


def test_invariant_skipped_when_flat():
    ib = MagicMock()
    ib.isConnected.return_value = True
    fills = []
    ex = _make(ib, fills, grace=15.0)
    # No position, no fill timestamp
    ex._position = 0

    with patch.object(ex, "_cancel_and_close") as cnc:
        ex._check_naked_position_invariant()
        cnc.assert_not_called()
    ib.openTrades.assert_not_called()
