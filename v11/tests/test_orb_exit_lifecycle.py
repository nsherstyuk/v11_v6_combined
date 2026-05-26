"""End-to-end SL/TP exit-path tests using the lifecycle harness.

As of 2026-05-26 the live system has run 4 trades, all of which exited via
the V6 trade-window-close MARKET path. The SL/TP order placement and exit
detection have *never* been exercised end-to-end live, so a latent bug
(wrong attribute, OCA mis-config, BE-classification off-by-one) would only
surface the day a fast move actually hits SL or TP — exactly the day the
exit most needs to work.

These tests drive the IBKRExecutionEngine against the stateful FakeIB from
lifecycle_harness.py:

  1. Entry fill → SL+TP OCA pair placed with the right side, prices, tif,
     triggerMethod, ocaGroup, ocaType. LONG and SHORT.
  2. SL hit → handle_exit emits Fill(reason='SL'), cancels remaining TP
     leg (defense-in-depth for IBKR's server-side OCA), resets state.
  3. TP hit → same, with reason='TP'.
  4. BE classification: SL fill within range_size*0.1 of entry reads as
     BE; outside reads as SL.
"""
import asyncio
import logging
from typing import List

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from v11.v6_orb.ibkr_executor import IBKRExecutionEngine
from v11.v6_orb.market_event import Fill, RangeInfo
from v11.tests.lifecycle_harness import FakeIB


log = logging.getLogger("test_orb_exit_lifecycle")


class _FakeContract:
    """conId-bearing contract so _cancel_all_for_contract matches all
    orders the executor places through the same FakeIB."""

    def __init__(self, symbol: str = "XAUUSD", conId: int = 1) -> None:
        self.symbol = symbol
        self.conId = conId


def _range():
    """10-wide range: high=2660, low=2650. Range size 10 → BE threshold 1.0."""
    return RangeInfo(high=2660.0, low=2650.0, start_time=None, end_time=None)


def _executor(ib: FakeIB, fills: List[Fill]) -> IBKRExecutionEngine:
    return IBKRExecutionEngine(
        ib=ib, contract=_FakeContract(), quantity=1,
        on_fill_callback=lambda f: fills.append(f),
        trade_end_hour=16, price_decimals=2, dry_run=False, logger=log,
    )


# ── Phase 2: Entry fill → SL+TP OCA placement ───────────────────────────


def test_long_entry_fill_places_sl_tp_oca_pair():
    """BUY entry fill must trigger _place_sl_tp with: SL at range.low
    (SELL STP, GTC, triggerMethod=7), TP at range.high + rr*size (SELL
    LMT, GTC), both in the same OCA group with ocaType=1."""
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)

    assert ex.set_orb_brackets(_range(), rr_ratio=2.5) is True
    buy_id = ex.buy_entry_id
    assert buy_id and ex.sell_entry_id

    # STP LMT often fills slightly past the stop on paper; pick something
    # above range.high so the BE heuristic later sees real distance.
    ib.simulate_fill(buy_id, 2660.5)
    assert ex.check_fills() is True

    assert ex._position == 1
    assert ex._direction == "LONG"
    assert ex._entry_price == 2660.5
    # Entry IDs cleared so the reconciler doesn't flag them as "missing"
    # (2026-04-21 bug fix preserved here).
    assert ex.buy_entry_id == 0
    assert ex.sell_entry_id == 0
    assert ex.sl_order_id > 0 and ex.tp_order_id > 0
    assert ex.sl_order_id != ex.tp_order_id

    # ENTRY fill emitted on the callback
    assert len(fills) == 1
    assert fills[0].reason == "ENTRY"
    assert fills[0].direction == "LONG"
    assert fills[0].price == 2660.5

    sl = next(t.order for t in ib.trades() if t.order.orderId == ex.sl_order_id)
    tp = next(t.order for t in ib.trades() if t.order.orderId == ex.tp_order_id)

    # SL: SELL STP @ range.low=2650, GTC, triggerMethod=7
    assert sl.action == "SELL"
    assert sl.orderType == "STP"
    assert sl.auxPrice == 2650.0
    assert sl.tif == "GTC"
    assert sl.triggerMethod == 7
    assert sl.ocaGroup
    assert sl.ocaType == 1

    # TP: SELL LMT @ range.high + rr*size = 2660 + 2.5*10 = 2685
    assert tp.action == "SELL"
    assert tp.orderType == "LMT"
    assert tp.lmtPrice == 2685.0
    assert tp.tif == "GTC"
    assert tp.ocaGroup == sl.ocaGroup, \
        "SL and TP must share an OCA group so a fill on one cancels the other"
    assert tp.ocaType == 1


def test_short_entry_fill_places_sl_tp_oca_pair():
    """SELL entry: SL is BUY STP at range.high; TP is BUY LMT at
    range.low - rr*size."""
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)

    assert ex.set_orb_brackets(_range(), rr_ratio=2.5) is True
    ib.simulate_fill(ex.sell_entry_id, 2649.5)
    assert ex.check_fills() is True

    assert ex._position == -1
    assert ex._direction == "SHORT"
    assert ex._entry_price == 2649.5

    sl = next(t.order for t in ib.trades() if t.order.orderId == ex.sl_order_id)
    tp = next(t.order for t in ib.trades() if t.order.orderId == ex.tp_order_id)

    assert sl.action == "BUY"
    assert sl.orderType == "STP"
    assert sl.auxPrice == 2660.0  # range.high
    assert sl.tif == "GTC"
    assert sl.triggerMethod == 7

    assert tp.action == "BUY"
    assert tp.orderType == "LMT"
    assert tp.lmtPrice == 2625.0  # range.low - rr*size = 2650 - 25
    assert tp.tif == "GTC"
    assert tp.ocaGroup == sl.ocaGroup


# ── SL hit path (LONG and SHORT) ────────────────────────────────────────


def test_long_sl_fill_emits_sl_exit_and_resets():
    """SL fill on a LONG: emit Fill(reason='SL', direction='SHORT'), cancel
    remaining TP leg, reset all order IDs and position state."""
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.buy_entry_id, 2660.5)
    ex.check_fills()
    fills.clear()  # drop the ENTRY fill — testing the exit

    ib.simulate_fill(ex.sl_order_id, 2650.0)  # far from entry → SL, not BE
    assert ex.check_fills() is True

    assert len(fills) == 1
    exit_fill = fills[0]
    assert exit_fill.reason == "SL"
    # Exit direction is the closing side: SHORT closes a LONG.
    assert exit_fill.direction == "SHORT"
    assert exit_fill.price == 2650.0

    assert ex._position == 0
    assert ex._direction is None
    assert ex.sl_order_id == 0
    assert ex.tp_order_id == 0


def test_short_sl_fill_emits_sl_exit_and_resets():
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.sell_entry_id, 2649.5)
    ex.check_fills()
    fills.clear()

    ib.simulate_fill(ex.sl_order_id, 2660.0)
    assert ex.check_fills() is True

    assert len(fills) == 1
    assert fills[0].reason == "SL"
    assert fills[0].direction == "LONG"  # LONG closes a SHORT
    assert fills[0].price == 2660.0
    assert ex._position == 0


# ── TP hit path (LONG and SHORT) ────────────────────────────────────────


def test_long_tp_fill_emits_tp_exit_and_resets():
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.buy_entry_id, 2660.5)
    ex.check_fills()
    fills.clear()

    ib.simulate_fill(ex.tp_order_id, 2685.0)
    assert ex.check_fills() is True

    assert len(fills) == 1
    assert fills[0].reason == "TP"
    assert fills[0].direction == "SHORT"  # closing a LONG
    assert fills[0].price == 2685.0
    assert ex._position == 0
    assert ex.sl_order_id == 0
    assert ex.tp_order_id == 0


def test_short_tp_fill_emits_tp_exit_and_resets():
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.sell_entry_id, 2649.5)
    ex.check_fills()
    fills.clear()

    ib.simulate_fill(ex.tp_order_id, 2625.0)
    assert ex.check_fills() is True

    assert len(fills) == 1
    assert fills[0].reason == "TP"
    assert fills[0].direction == "LONG"
    assert fills[0].price == 2625.0
    assert ex._position == 0


# ── BE vs SL classification ─────────────────────────────────────────────


def test_sl_fill_near_entry_classified_as_be():
    """_is_be_price: |fill - entry| < range_size * 0.1 → BE.
    Here range_size=10 → threshold=1.0. Entry=2660.5; SL fill at 2660.0
    is 0.5 away → should classify as BE."""
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.buy_entry_id, 2660.5)
    ex.check_fills()
    fills.clear()

    ib.simulate_fill(ex.sl_order_id, 2660.0)
    ex.check_fills()

    assert len(fills) == 1
    assert fills[0].reason == "BE", \
        "SL fill within range_size*0.1 of entry must classify as BE"


def test_sl_fill_far_from_entry_classified_as_sl():
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.buy_entry_id, 2660.5)
    ex.check_fills()
    fills.clear()

    ib.simulate_fill(ex.sl_order_id, 2650.0)  # 10.5 away → > 1.0 threshold
    ex.check_fills()

    assert len(fills) == 1
    assert fills[0].reason == "SL"


# ── OCA defense-in-depth: SL fill cancels surviving TP leg ──────────────


def test_sl_fill_cancels_remaining_tp_leg():
    """In production IBKR's server-side OCA cancels TP when SL fills. The
    executor's _cancel_all_for_contract is the defense-in-depth backstop
    that ensures TP is gone client-side even if the broker's OCA misfired.
    Verify the TP leg is removed from openTrades after SL fill is processed."""
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.buy_entry_id, 2660.5)
    ex.check_fills()
    tp_id = ex.tp_order_id

    # Sanity: TP is live in openTrades
    tp_before = [t for t in ib.openTrades() if t.order.orderId == tp_id]
    assert len(tp_before) == 1
    assert tp_before[0].orderStatus.status == "Submitted"

    ib.simulate_fill(ex.sl_order_id, 2650.0)
    ex.check_fills()

    tp_after = [t for t in ib.openTrades() if t.order.orderId == tp_id]
    assert tp_after == [], \
        "Executor must cancel the TP leg when SL fills"


def test_tp_fill_cancels_remaining_sl_leg():
    """Symmetric to the SL test: TP fill must remove SL from openTrades."""
    ib = FakeIB()
    fills: List[Fill] = []
    ex = _executor(ib, fills)
    ex.set_orb_brackets(_range(), rr_ratio=2.5)
    ib.simulate_fill(ex.buy_entry_id, 2660.5)
    ex.check_fills()
    sl_id = ex.sl_order_id

    ib.simulate_fill(ex.tp_order_id, 2685.0)
    ex.check_fills()

    sl_after = [t for t in ib.openTrades() if t.order.orderId == sl_id]
    assert sl_after == [], \
        "Executor must cancel the SL leg when TP fills"
