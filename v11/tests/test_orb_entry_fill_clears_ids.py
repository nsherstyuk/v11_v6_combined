"""Regression: entry fill must clear buy/sell_entry_id (both).

2026-04-21 incident: entry STP filled, executor transitioned to _position=-1
and placed SL/TP brackets, but left sell_entry_id set to the filled order's
id. The periodic order reconciler in run_live.py filters openTrades() to
active statuses only; a Filled order is absent from that set, so the
reconciler flagged the entry id as MISSING and wiped executor state mid-trade.

This regression test pins the invariant: after a detected entry fill, both
entry IDs must be zero.
"""
import asyncio
import logging
from unittest.mock import MagicMock

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.v6_orb.market_event import RangeInfo
from v11.v6_orb.ibkr_executor import IBKRExecutionEngine


log = logging.getLogger("test_entry_fill_clears_ids")


def _mock_contract():
    c = MagicMock()
    c.symbol = "XAUUSD"
    c.conId = 1
    return c


def _trade(order_id: int, status: str, avg_fill_price: float = 0.0):
    t = MagicMock()
    t.order = MagicMock()
    t.order.orderId = order_id
    t.orderStatus = MagicMock()
    t.orderStatus.status = status
    t.orderStatus.avgFillPrice = avg_fill_price
    t.contract = _mock_contract()
    return t


def _make_executor(ib):
    ex = IBKRExecutionEngine(
        ib=ib, contract=_mock_contract(), quantity=1,
        on_fill_callback=lambda f: None,
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )
    # Simulate post-set_orb_brackets state: both entry IDs populated,
    # range info cached, no position yet.
    ex.buy_entry_id = 84
    ex.sell_entry_id = 85
    ex._range_info = RangeInfo(high=4830.0, low=4783.24,
                                start_time=None, end_time=None)
    ex._rr_ratio = 2.0
    ex._trade_date = "2026-04-21"
    return ex


def test_sell_entry_fill_clears_both_entry_ids():
    """SHORT fill: sell_entry_id was the filled order; buy_entry_id is the
    OCA-cancelled opposite side. Both must be zeroed."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    # SELL entry filled @ 4783.14 (the 2026-04-21 incident case)
    fill_trade = _trade(85, "Filled", avg_fill_price=4783.14)
    # Opposite side cancelled by OCA
    cancelled_trade = _trade(84, "Cancelled")
    ib.trades.return_value = [fill_trade, cancelled_trade]

    # placeOrder returns mock trades for SL/TP
    sl_trade = MagicMock(); sl_trade.order = MagicMock(); sl_trade.order.orderId = 86
    tp_trade = MagicMock(); tp_trade.order = MagicMock(); tp_trade.order.orderId = 87
    ib.placeOrder.side_effect = [sl_trade, tp_trade]

    ex = _make_executor(ib)
    ex._check_entry_fills()

    assert ex._position == -1
    assert ex._direction == "SHORT"
    assert ex.buy_entry_id == 0, "opposite-side entry id must be cleared"
    assert ex.sell_entry_id == 0, "filled entry id must be cleared"
    assert ex.has_resting_entries() is False


def test_buy_entry_fill_clears_both_entry_ids():
    ib = MagicMock()
    ib.isConnected.return_value = True
    fill_trade = _trade(84, "Filled", avg_fill_price=4830.50)
    cancelled_trade = _trade(85, "Cancelled")
    ib.trades.return_value = [fill_trade, cancelled_trade]
    sl_trade = MagicMock(); sl_trade.order = MagicMock(); sl_trade.order.orderId = 86
    tp_trade = MagicMock(); tp_trade.order = MagicMock(); tp_trade.order.orderId = 87
    ib.placeOrder.side_effect = [sl_trade, tp_trade]

    ex = _make_executor(ib)
    ex._check_entry_fills()

    assert ex._position == 1
    assert ex._direction == "LONG"
    assert ex.buy_entry_id == 0
    assert ex.sell_entry_id == 0
    assert ex.has_resting_entries() is False
