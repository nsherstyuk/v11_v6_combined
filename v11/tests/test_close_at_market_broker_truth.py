"""Regression: close_at_market must flatten broker-held orphan positions
even when internal state says flat.

2026-04-21 incident: after a false 'vanished' event, internal _position
went to 0 while broker still held -1 XAUUSD. After 12:00 UTC the strategy
tick loop called close_at_market every 2s; each call only checked the
internal flag and logged 'No position to close' — orphan sat unprotected
for 5h until manual intervention.

Contract: when internal=flat but broker has a position, close_at_market
must emit a CRITICAL log and flatten the broker-side position. Throttled
to once per 30s so tick-rate invocation doesn't spam ib.positions().
"""
import asyncio
import logging
from unittest.mock import MagicMock, patch

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.v6_orb.ibkr_executor import IBKRExecutionEngine


log = logging.getLogger("test_close_broker_truth")


def _mock_contract():
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = 1
    return c


def _pos(conid: int, qty: float):
    p = MagicMock()
    p.contract.conId = conid
    p.position = qty
    return p


def _make_exec(ib, fills):
    fills_list = fills
    def cb(f):
        fills_list.append(f)
    return IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=cb,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )


def test_close_at_market_detects_orphan_and_flattens():
    """Internal flat + broker has -1 → must flatten via _cancel_and_close."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = [_pos(1, -1.0)]

    fills = []
    ex = _make_exec(ib, fills)
    # _position is 0 by default
    assert ex._position == 0

    # Patch _cancel_and_close to verify it's invoked and return a fill price
    with patch.object(ex, "_cancel_and_close", return_value=4711.92) as cnc:
        ex.close_at_market()
        cnc.assert_called_once()

    assert len(fills) == 1
    assert fills[0].reason == "ORPHAN_FLATTEN"
    assert fills[0].direction == "SHORT"  # broker was -1 → flattening BUYs → direction we were in = SHORT
    assert fills[0].price == 4711.92


def test_close_at_market_true_flat_does_nothing():
    """Internal flat + broker flat → log 'No position to close', no flatten."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = []  # broker flat

    fills = []
    ex = _make_exec(ib, fills)
    with patch.object(ex, "_cancel_and_close") as cnc:
        ex.close_at_market()
        cnc.assert_not_called()

    assert fills == []


def test_broker_position_throttled():
    """Second call within throttle interval returns None."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.positions.return_value = [_pos(1, -1.0)]

    fills = []
    ex = _make_exec(ib, fills)
    ex._broker_truth_interval_s = 30.0

    first = ex._broker_position_throttled()
    assert first == -1.0
    second = ex._broker_position_throttled()
    assert second is None  # throttled
    # positions() called only once
    assert ib.positions.call_count == 1


def test_broker_position_throttled_disconnected_returns_none():
    ib = MagicMock()
    ib.isConnected.return_value = False
    fills = []
    ex = _make_exec(ib, fills)
    assert ex._broker_position_throttled() is None
    ib.positions.assert_not_called()
