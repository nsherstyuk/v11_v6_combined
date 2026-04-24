"""
V6 IBKRExecutionEngine — copied from C:\\nautilus0\\v6_orb_refactor\\live\\ibkr_executor.py

Originally frozen. Modifications in this V11 copy (documented, not drift):
  - 2026-04-17: set_orb_brackets returns bool + resets IDs on failure
                (silent-order-failure bug fix)
  - 2026-04-20: set_orb_brackets calls ib.disconnect() on
                connectivity-class errors to trigger reconnect
                (silent half-open socket fix)
  - 2026-04-20: TIF changed from GTD to DAY (IBKR paper preset rejects
                GTD via Error 10349, silently Cancels order). DAY is
                what IBKR's preset forces anyway; matching it prevents
                the rejection.
  - 2026-04-20: orderStatusEvent listener hooked for per-order status
                tracking. When an entry order transitions to
                Cancelled/Inactive before filling, we log it and
                surface it so the strategy can handle the failure.
"""
import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional, Callable

from .interfaces import ExecutionEngine
from .market_event import Fill, RangeInfo


class IBKRExecutionEngine(ExecutionEngine):
    """
    Deep module: Hides all IBKR order management complexity.

    Strategy only calls: set_orb_brackets, cancel_orb_brackets,
    close_at_market, modify_sl, has_position, has_resting_entries.

    Internally manages: two-phase brackets, OCA groups, SL/TP after
    fill, order IDs, conId-based cancel, dry-run suppression.
    """

    def __init__(self, ib, contract, quantity: int,
                 on_fill_callback: Callable[[Fill], None],
                 trade_end_hour: int = 16,
                 price_decimals: int = 2,
                 dry_run: bool = False,
                 logger: Optional[logging.Logger] = None,
                 force_disconnect_callback: Optional[Callable[[str], None]] = None):
        self.ib = ib
        self.contract = contract
        self.quantity = quantity
        self.on_fill_callback = on_fill_callback
        self.trade_end_hour = trade_end_hour
        self._d = price_decimals
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger(__name__)
        # 2026-04-23: connection-level force-disconnect. Calling raw
        # ib.disconnect() on a half-open socket is not reliable — ib_insync
        # may silently no-op, disconnectedEvent never fires, and the main
        # loop never reconnects. The connection layer owns reliable state
        # transitions, so we route through it when available.
        self._force_disconnect = force_disconnect_callback

        # 2026-04-23: track consecutive connectivity-class placement
        # failures so the main loop can exit (→ wrapper restart) if the
        # reconnect path cannot recover. Threshold ~30s of 2s-poll spam.
        self._consec_placement_conn_failures: int = 0
        self._placement_stuck_threshold: int = 15

        # Order tracking (IDs for state persistence)
        self.buy_entry_id: int = 0
        self.sell_entry_id: int = 0
        self.sl_order_id: int = 0
        self.tp_order_id: int = 0

        # Entry-order failure detection (2026-04-20):
        # When an entry order reaches a terminal non-Filled state
        # (Cancelled, Inactive, ApiCancelled) before we see a Fill,
        # something went wrong at IBKR (TIF rejection, risk-system
        # discard, etc.). The orderStatusEvent listener sets this flag
        # and the strategy polls entry_placement_failed() to detect it.
        self._entry_failed: bool = False
        self._entry_failure_reason: str = ""
        self._hooked_status_event: bool = False

        # Position state
        self._position: int = 0  # 1=LONG, -1=SHORT, 0=flat
        self._direction: Optional[str] = None
        self._entry_price: float = 0.0

        # Range for SL/TP calculation
        self._range_info: Optional[RangeInfo] = None
        self._rr_ratio: float = 2.0

        # Trade date for OCA naming
        self._trade_date: str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

        # Throttle for broker-truth queries in close_at_market / invariant
        # checks (avoid spamming ib.positions() when strategy ticks multiple
        # times per second). 2026-04-21 incident: close_at_market fired every
        # 2s for 5h while an orphan sat unprotected — internal flag said
        # "no position" but broker had one.
        self._last_broker_truth_check_ts: float = 0.0
        self._broker_truth_interval_s: float = 30.0

        # Naked-position invariant (P2.6, 2026-04-21): if we have a position
        # and no active SL/TP at broker for > grace_s seconds, force-flatten.
        # Grace covers normal entry→SL/TP placement round-trip (~sub-second).
        self._entry_fill_ts: Optional[float] = None
        self._naked_grace_s: float = 15.0
        self._last_invariant_check_ts: float = 0.0
        self._invariant_interval_s: float = 5.0

    # ── Public interface ─────────────────────────────────────────────

    def set_orb_brackets(self, range_info: RangeInfo, rr_ratio: float):
        """Phase 1: Place ONLY entry stop orders as an OCA pair."""
        self._range_info = range_info
        self._rr_ratio = rr_ratio
        d = self._d

        if self.dry_run:
            self.logger.info(
                f"[DRY] Would place entries: BUY STP @ {range_info.high:.{d}f}, "
                f"SELL STP @ {range_info.low:.{d}f}")
            self.buy_entry_id = -1  # sentinel for dry-run
            self.sell_entry_id = -1
            return True

        from ib_insync import Order

        now = datetime.now(tz=timezone.utc)
        oca_group = (f"ORB_{self.contract.symbol}_{self._trade_date}_"
                     f"{now.strftime('%H%M%S')}")

        # TIF=DAY: IBKR paper-account preset rejects GTD with Error 10349
        # and silently Cancels the order. DAY matches what the preset
        # forces anyway. The order expires naturally at EOD; V11's
        # max_pending_hours check cancels earlier if no trigger.
        # triggerMethod=7 ("last or double bid/ask"): IBKR paper XAUUSD
        # streams bid/ask only — zero `last` trades print. The default
        # triggerMethod waits on `last`, so stops never trigger even when
        # bid/ask is $20+ past the stop. Method 7 falls back to bid/ask
        # when last is absent and uses last when present; safe on both
        # paper and live. (2026-04-24 incident: BUY-STOP @ 4711.24 sat
        # resting while price traded to 4735.82 mid for ~20 min.)
        buy_entry = Order(
            action="BUY", orderType="STP", totalQuantity=self.quantity,
            auxPrice=round(range_info.high, d),
            tif="DAY", triggerMethod=7,
            ocaGroup=oca_group, ocaType=1,
            transmit=False,
        )

        sell_entry = Order(
            action="SELL", orderType="STP", totalQuantity=self.quantity,
            auxPrice=round(range_info.low, d),
            tif="DAY", triggerMethod=7,
            ocaGroup=oca_group, ocaType=1,
            transmit=True,
        )

        try:
            # Reset failure tracking for this placement cycle and hook
            # orderStatusEvent so we see every IBKR-side transition.
            self._entry_failed = False
            self._entry_failure_reason = ""
            self._hook_status_event_once()

            buy_trade = self.ib.placeOrder(self.contract, buy_entry)
            self.ib.sleep(1)
            self.buy_entry_id = buy_trade.order.orderId

            sell_trade = self.ib.placeOrder(self.contract, sell_entry)
            self.ib.sleep(1)
            self.sell_entry_id = sell_trade.order.orderId

            self.logger.info(
                f"Entry stops placed: BUY id={self.buy_entry_id} "
                f"@ {range_info.high:.{d}f}, SELL id={self.sell_entry_id} "
                f"@ {range_info.low:.{d}f} (OCA={oca_group})")
            self._consec_placement_conn_failures = 0
            return True
        except Exception as e:
            self.logger.error(f"Entry placement failed: {e}")
            # Reset IDs so has_resting_entries() returns False
            self.buy_entry_id = 0
            self.sell_entry_id = 0
            err_str = str(e).lower()
            if "not connected" in err_str or "connection" in err_str or "timeout" in err_str:
                self._consec_placement_conn_failures += 1
                self.logger.error(
                    f"Connectivity-class error on placement "
                    f"(consec={self._consec_placement_conn_failures}/"
                    f"{self._placement_stuck_threshold}) — forcing reconnect")
                # Route through the connection's force_disconnect when
                # available (2026-04-23). Raw ib.disconnect() as a fallback
                # is NOT reliable on half-open sockets — kept only so this
                # code path still does *something* if the callback wasn't
                # wired (e.g. legacy tests).
                if self._force_disconnect is not None:
                    try:
                        self._force_disconnect("placement_not_connected")
                    except Exception as cb_err:
                        self.logger.warning(
                            f"force_disconnect callback raised: {cb_err}")
                else:
                    try:
                        self.ib.disconnect()
                    except Exception:
                        pass
            else:
                # Non-connectivity error — don't count toward stuck threshold.
                self._consec_placement_conn_failures = 0
            return False

    @property
    def placement_stuck(self) -> bool:
        """True if too many consecutive connectivity-class placement
        failures have occurred without a successful placement. The main
        loop uses this as a trip-wire: when stuck, it exits with a
        non-zero code so the auto-restart wrapper brings V11 back with a
        fresh socket (2026-04-23 incident, where V11 spammed 2h of
        'Not connected' placement errors because the reconnect path
        silently failed)."""
        return (self._consec_placement_conn_failures
                >= self._placement_stuck_threshold)

    def entry_placement_failed(self) -> bool:
        """True if an entry order reached a terminal non-Filled state
        (Cancelled/Inactive/ApiCancelled) between placement and fill.

        Set by the orderStatusEvent listener. Polled by the adapter /
        strategy to detect silent IBKR-side discards. When True, the
        caller should clear state and treat it as a failed placement
        (equivalent to set_orb_brackets() returning False)."""
        return self._entry_failed

    def entry_failure_reason(self) -> str:
        return self._entry_failure_reason

    def clear_entry_failure(self) -> None:
        """Called after the caller has handled the failure."""
        self._entry_failed = False
        self._entry_failure_reason = ""

    def _hook_status_event_once(self) -> None:
        """Attach orderStatusEvent listener. Idempotent — safe to call
        multiple times; we only attach one handler per executor."""
        if self._hooked_status_event:
            return
        try:
            self.ib.orderStatusEvent += self._on_order_status
            self._hooked_status_event = True
            self.logger.debug("Hooked orderStatusEvent listener")
        except Exception as e:
            self.logger.warning(f"Could not hook orderStatusEvent: {e}")

    def _on_order_status(self, trade) -> None:
        """Per-order status callback. Fires on every IBKR-side
        transition for orders owned by this client.

        Responsibilities:
          (a) Log every transition (paper-trail for forensics)
          (b) If an entry order reaches a terminal non-Filled state
              before we've seen a Fill, mark entry_placement_failed.

        We only act on orders we recognize (buy_entry_id or
        sell_entry_id). Other orders (SL, TP, other clients' orders)
        are ignored here."""
        try:
            oid = trade.order.orderId
            status = trade.orderStatus.status
            filled = trade.orderStatus.filled
            remaining = trade.orderStatus.remaining
            is_ours = oid in (self.buy_entry_id, self.sell_entry_id)
            if not is_ours:
                return
            self.logger.info(
                f"orderStatusEvent: id={oid} status={status} "
                f"filled={filled}/{remaining}")
            TERMINAL_NON_FILLED = ("Cancelled", "Inactive", "ApiCancelled")
            if status in TERMINAL_NON_FILLED and filled == 0:
                # Pull the last log entry for the failure reason
                reason = status
                try:
                    if trade.log:
                        last_log = trade.log[-1]
                        if last_log.message:
                            reason = f"{status}: {last_log.message}"
                except Exception:
                    pass
                self._entry_failed = True
                self._entry_failure_reason = reason
                self.logger.error(
                    f"Entry order {oid} reached terminal non-Filled state: "
                    f"{reason}. Marking entry_placement_failed.")
        except Exception as e:
            # Never let the callback crash — it runs on ib_insync's event loop
            self.logger.warning(f"orderStatusEvent handler error: {e}")

    def cancel_orb_brackets(self):
        """Cancel resting entry brackets (not SL/TP)."""
        if self.dry_run:
            self.logger.info("[DRY] Would cancel entry brackets")
            self.buy_entry_id = 0
            self.sell_entry_id = 0
            return

        self._cancel_all_for_contract()
        self.buy_entry_id = 0
        self.sell_entry_id = 0

    def close_at_market(self):
        """Close any open position at market + cancel all orders.

        Consults broker truth when internal state says flat. 2026-04-21
        incident: internal _position went to 0 after a false "vanished"
        signal on a socket disconnect, while the broker still held the
        short. close_at_market then logged "No position to close" every
        2s for 5h without ever asking IBKR. Now: if internal is flat but
        broker has a position, flatten it (throttled to once per 30s to
        avoid hammering ib.positions() under tick-rate strategy calls).
        """
        if self._position == 0:
            broker_qty = self._broker_position_throttled()
            if broker_qty is None or broker_qty == 0:
                self.logger.info("No position to close")
                return
            self.logger.critical(
                f"close_at_market: internal=flat but broker={broker_qty} "
                f"on {self.contract.symbol} — flattening orphan")
            fill_price = self._cancel_and_close()
            if fill_price is not None:
                direction = "SHORT" if broker_qty < 0 else "LONG"
                fill = Fill(
                    timestamp=datetime.now(tz=timezone.utc),
                    price=fill_price,
                    direction=direction,
                    reason="ORPHAN_FLATTEN",
                )
                self._reset_position()
                self.on_fill_callback(fill)
            return

        direction = "SHORT" if self._position == 1 else "LONG"

        if self.dry_run:
            self.logger.info(f"[DRY] Would close {self._direction} at market")
            fill = Fill(
                timestamp=datetime.now(tz=timezone.utc),
                price=self._entry_price,
                direction=direction,
                reason="MARKET",
            )
            self._reset_position()
            self.on_fill_callback(fill)
            return

        fill_price = self._cancel_and_close()
        if fill_price is not None:
            fill = Fill(
                timestamp=datetime.now(tz=timezone.utc),
                price=fill_price,
                direction=direction,
                reason="MARKET",
            )
            self._reset_position()
            self.on_fill_callback(fill)

    def modify_sl(self, new_sl_price: float):
        """Modify the active SL order price (for breakeven)."""
        d = self._d
        if self.dry_run:
            self.logger.info(
                f"[DRY] Would modify SL to {new_sl_price:.{d}f}")
            return

        if not self.sl_order_id:
            self.logger.warning("modify_sl: no SL order ID saved")
            return

        try:
            for trade in self.ib.openTrades():
                if trade.order.orderId == self.sl_order_id:
                    old_price = trade.order.auxPrice
                    trade.order.auxPrice = round(new_sl_price, d)
                    self.ib.placeOrder(self.contract, trade.order)
                    self.ib.sleep(0.5)
                    self.logger.info(
                        f"SL modified: id={self.sl_order_id} "
                        f"{old_price:.{d}f} -> {new_sl_price:.{d}f}")
                    return
            self.logger.warning(
                f"SL order {self.sl_order_id} not found in open trades")
        except Exception as e:
            self.logger.error(f"modify_sl failed: {e}")

    def has_position(self) -> bool:
        return self._position != 0

    def has_resting_entries(self) -> bool:
        return self.buy_entry_id != 0 or self.sell_entry_id != 0

    # ── Fill detection (called by Runner polling loop) ───────────────

    def check_fills(self) -> bool:
        """Poll IBKR for entry/exit fills. Returns True if a fill occurred."""
        if self.dry_run:
            return self._dry_run_fill_check()
        if not self.ib.isConnected():
            return False

        try:
            self.ib.sleep(0)  # pump events

            # Check entry fills
            if self._position == 0 and (self.buy_entry_id or self.sell_entry_id):
                return self._check_entry_fills()

            # Check exit fills
            if self._position != 0 and (self.sl_order_id or self.tp_order_id):
                filled = self._check_exit_fills()
                if filled:
                    return True

            # Naked-position invariant (P2.6): protects against cases where
            # a disconnect/cancel left the position without both SL and TP.
            if self._position != 0:
                self._check_naked_position_invariant()

        except Exception as e:
            self.logger.warning(f"check_fills error: {e}")
        return False

    # ── Private: entry fill detection & Phase 2 ─────────────────────

    def _check_entry_fills(self) -> bool:
        """Check if entry stops filled. If so, place SL/TP (Phase 2).

        On fill: zero both entry IDs. The opposite-side STP is OCA-cancelled
        by IBKR, and leaving the filled ID non-zero causes the order
        reconciler to report it as "missing" (since terminal-status orders
        aren't in the active openTrades filter). See 2026-04-21 incident.
        """
        for trade in self.ib.trades():
            oid = trade.order.orderId
            if oid == self.buy_entry_id and trade.orderStatus.status == 'Filled':
                fill_px = trade.orderStatus.avgFillPrice
                self._position = 1
                self._direction = "LONG"
                self._entry_price = fill_px
                self._entry_fill_ts = time.time()
                self.buy_entry_id = 0
                self.sell_entry_id = 0
                self._place_sl_tp("LONG", fill_px)
                self._emit_entry_fill("LONG", fill_px)
                return True
            if oid == self.sell_entry_id and trade.orderStatus.status == 'Filled':
                fill_px = trade.orderStatus.avgFillPrice
                self._position = -1
                self._direction = "SHORT"
                self._entry_price = fill_px
                self._entry_fill_ts = time.time()
                self.buy_entry_id = 0
                self.sell_entry_id = 0
                self._place_sl_tp("SHORT", fill_px)
                self._emit_entry_fill("SHORT", fill_px)
                return True
        return False

    def _place_sl_tp(self, direction: str, entry_price: float):
        """Phase 2: place SL + TP after entry fill confirmed.
        TP is range-based (V5 approach): TP = range_high + rr * range_size (LONG)
        NOT fill-price-based, to maintain parity with backtest."""
        from ib_insync import Order
        d = self._d
        ri = self._range_info
        rs = ri.high - ri.low  # range_size

        if direction == "LONG":
            sl_price = round(ri.low, d)
            tp_price = round(ri.high + self._rr_ratio * rs, d)
            sl_action = tp_action = "SELL"
        else:
            sl_price = round(ri.high, d)
            tp_price = round(ri.low - self._rr_ratio * rs, d)
            sl_action = tp_action = "BUY"

        now = datetime.now(tz=timezone.utc)
        oca_exit = (f"ORB_EXIT_{self.contract.symbol}_{self._trade_date}_"
                    f"{now.strftime('%H%M%S')}")

        sl_order = Order(
            action=sl_action, orderType="STP", totalQuantity=self.quantity,
            auxPrice=sl_price, tif="GTC", triggerMethod=7,
            ocaGroup=oca_exit, ocaType=1, transmit=False)

        tp_order = Order(
            action=tp_action, orderType="LMT", totalQuantity=self.quantity,
            lmtPrice=tp_price, tif="GTC",
            ocaGroup=oca_exit, ocaType=1, transmit=True)

        try:
            sl_trade = self.ib.placeOrder(self.contract, sl_order)
            self.ib.sleep(0.5)
            tp_trade = self.ib.placeOrder(self.contract, tp_order)
            self.ib.sleep(1)

            self.sl_order_id = sl_trade.order.orderId
            self.tp_order_id = tp_trade.order.orderId

            self.logger.info(
                f"SL/TP placed: SL id={self.sl_order_id} @ {sl_price:.{d}f}, "
                f"TP id={self.tp_order_id} @ {tp_price:.{d}f} "
                f"(OCA={oca_exit})")
        except Exception as e:
            self.logger.error(f"_place_sl_tp failed: {e}")

    def _emit_entry_fill(self, direction: str, price: float):
        fill = Fill(
            timestamp=datetime.now(tz=timezone.utc),
            price=price,
            direction=direction,
            reason="ENTRY",
        )
        self.on_fill_callback(fill)

    # ── Private: exit fill detection ────────────────────────────────

    def _check_exit_fills(self) -> bool:
        """Check if SL or TP filled."""
        d = self._d
        for trade in self.ib.trades():
            oid = trade.order.orderId
            if oid == self.tp_order_id and trade.orderStatus.status == 'Filled':
                fill_px = trade.orderStatus.avgFillPrice
                self.logger.info(f"TP filled @ {fill_px:.{d}f}")
                self._handle_exit("TP", fill_px)
                return True
            if oid == self.sl_order_id and trade.orderStatus.status == 'Filled':
                fill_px = trade.orderStatus.avgFillPrice
                reason = "BE" if self._is_be_price(fill_px) else "SL"
                self.logger.info(f"{reason} filled @ {fill_px:.{d}f}")
                self._handle_exit(reason, fill_px)
                return True

        # Fallback: position vanished without detected fill
        if self._check_position_vanished():
            price = self._get_streaming_price()
            self.logger.warning("Position vanished without detected SL/TP fill")
            self._handle_exit("CLOSED", price or self._entry_price)
            return True

        return False

    def _handle_exit(self, reason: str, fill_price: float):
        """Process any exit: cancel remaining orders, emit fill, reset."""
        self._cancel_all_for_contract()
        direction = "SHORT" if self._position == 1 else "LONG"
        fill = Fill(
            timestamp=datetime.now(tz=timezone.utc),
            price=fill_price,
            direction=direction,
            reason=reason,
        )
        self._reset_position()
        self.on_fill_callback(fill)

    def _is_be_price(self, fill_price: float) -> bool:
        """Heuristic: SL fill near entry = breakeven."""
        if self._entry_price == 0 or self._range_info is None:
            return False
        return abs(fill_price - self._entry_price) < self._range_info.size * 0.1

    def reconcile_after_reconnect(self, outage_s: float) -> str:
        """Graduated orphan recovery after IBKR reconnect (P0.3, 2026-04-21).

        Called by run_live after the socket returns. Compares internal
        position state to broker truth and takes one of:

          "flat"     — broker is flat; nothing to do.
          "ok"       — broker matches internal; no action (invariant
                        will verify SL/TP on next tick).
          "rearm"    — orphan, short outage (<REARM_MAX_OUTAGE_S) AND
                        range_info still in memory → adopt position and
                        re-place SL/TP from the same range/rr that
                        originally protected it.
          "flatten"  — orphan, long outage OR no range_info → market-close
                        the position. ORB thesis ("I saw the range, saw
                        the break, entered") is broken when we've been
                        blind for >60s, so fabricating an SL from stale
                        assumptions is unsafe.
          "error"    — could not query broker; caller should retry.

        See docs/journal/2026-04-21_live_plumbing_hardening.md §Q1.
        """
        REARM_MAX_OUTAGE_S = 60.0
        if not self.ib.isConnected():
            return "error"
        try:
            positions = self.ib.positions()
        except Exception as e:
            self.logger.warning(f"reconcile_after_reconnect positions(): {e}")
            return "error"

        broker_qty = 0.0
        for p in positions:
            if p.contract.conId == self.contract.conId:
                broker_qty = float(p.position)
                break

        if broker_qty == 0.0 and self._position == 0:
            return "flat"
        if broker_qty == 0.0 and self._position != 0:
            # Internal thought we had a position; broker says no. Likely
            # SL/TP filled during outage. Reset local state.
            self.logger.warning(
                f"reconcile: internal={self._position} but broker=flat "
                f"on {self.contract.symbol} — resetting local state "
                f"(exit happened during {outage_s:.0f}s outage)")
            self._cancel_all_for_contract()
            self._reset_position()
            return "flat"
        # broker_qty != 0
        if self._position != 0 and (
                (self._position == 1 and broker_qty > 0) or
                (self._position == -1 and broker_qty < 0)):
            # Internal and broker agree on direction; invariant will check SL/TP.
            return "ok"

        # Orphan: broker has position, internal is flat (or disagrees).
        has_range = self._range_info is not None
        if outage_s < REARM_MAX_OUTAGE_S and has_range:
            direction = "LONG" if broker_qty > 0 else "SHORT"
            # Adopt: use broker avgCost if we can, else entry_price we had.
            avg_cost = 0.0
            for p in positions:
                if p.contract.conId == self.contract.conId:
                    avg_cost = float(p.avgCost)
                    break
            entry_price = avg_cost if avg_cost > 0 else self._entry_price
            self.logger.warning(
                f"reconcile: orphan on {self.contract.symbol} "
                f"qty={broker_qty} outage={outage_s:.0f}s — re-arming "
                f"SL/TP from in-memory range (direction={direction}, "
                f"entry={entry_price})")
            # Cancel any surviving brackets from the old OCA group first
            # so we don't end up with duplicate SLs.
            self._cancel_all_for_contract()
            self._position = 1 if broker_qty > 0 else -1
            self._direction = direction
            self._entry_price = entry_price
            self._entry_fill_ts = time.time()  # reset invariant grace
            try:
                self._place_sl_tp(direction, entry_price)
            except Exception as e:
                self.logger.error(
                    f"reconcile: re-arm _place_sl_tp failed: {e} — "
                    f"falling back to flatten")
                fill_price = self._cancel_and_close()
                direction_label = "SHORT" if broker_qty < 0 else "LONG"
                fill = Fill(
                    timestamp=datetime.now(tz=timezone.utc),
                    price=fill_price if fill_price is not None else entry_price,
                    direction=direction_label,
                    reason="ORPHAN_FLATTEN",
                )
                self._reset_position()
                self.on_fill_callback(fill)
                return "flatten"
            return "rearm"

        # Long outage or no range_info — flatten.
        reason = "outage_too_long" if not has_range else "no_range_info"
        self.logger.critical(
            f"reconcile: orphan on {self.contract.symbol} "
            f"qty={broker_qty} outage={outage_s:.0f}s "
            f"range_info={'present' if has_range else 'missing'} "
            f"— FLATTENING (reason={reason})")
        # Ensure our local state won't inhibit _cancel_and_close
        self._position = 1 if broker_qty > 0 else -1
        fill_price = self._cancel_and_close()
        direction_label = "SHORT" if broker_qty < 0 else "LONG"
        fill = Fill(
            timestamp=datetime.now(tz=timezone.utc),
            price=fill_price if fill_price is not None else self._entry_price,
            direction=direction_label,
            reason="ORPHAN_FLATTEN",
        )
        self._reset_position()
        self.on_fill_callback(fill)
        return "flatten"

    def _check_naked_position_invariant(self) -> None:
        """Per-tick guard (P2.6, 2026-04-21): if we hold a position AND
        lack an active SL or TP order at the broker for longer than the
        grace window, force-flatten.

        Catches: reconnect-handler canceled a bracket leg; SL/TP placeOrder
        call returned an id but IBKR rejected it silently; any other way
        the bracket got stripped while we still hold inventory.

        Throttled (5s default) because this runs every strategy tick.
        Grace window (15s default) absorbs normal entry→bracket round-trip.
        """
        if self._position == 0:
            return
        if not self.ib.isConnected():
            return
        if self._entry_fill_ts is None:
            return
        now = time.time()
        if now - self._entry_fill_ts < self._naked_grace_s:
            return
        if now - self._last_invariant_check_ts < self._invariant_interval_s:
            return
        self._last_invariant_check_ts = now

        ACTIVE = ('Submitted', 'PreSubmitted', 'PendingSubmit', 'ApiPending')
        try:
            active_ids = {
                t.order.orderId for t in self.ib.openTrades()
                if (hasattr(t.contract, 'conId')
                    and t.contract.conId == self.contract.conId
                    and t.orderStatus.status in ACTIVE)
            }
        except Exception as e:
            self.logger.warning(f"invariant check openTrades failed: {e}")
            return

        sl_ok = self.sl_order_id > 0 and self.sl_order_id in active_ids
        tp_ok = self.tp_order_id > 0 and self.tp_order_id in active_ids
        if sl_ok and tp_ok:
            return

        age_s = now - self._entry_fill_ts
        self.logger.critical(
            f"NAKED POSITION INVARIANT: {self.contract.symbol} "
            f"pos={self._position} sl_ok={sl_ok} tp_ok={tp_ok} "
            f"age={age_s:.0f}s — force-flatten")
        direction = "SHORT" if self._position == 1 else "LONG"
        fill_price = self._cancel_and_close()
        fill = Fill(
            timestamp=datetime.now(tz=timezone.utc),
            price=fill_price if fill_price is not None else self._entry_price,
            direction=direction,
            reason="NAKED_FLATTEN",
        )
        self._reset_position()
        self.on_fill_callback(fill)

    def _broker_position_throttled(self) -> Optional[float]:
        """Query broker position for this contract, throttled.

        Returns signed position size, or None if unavailable (disconnected,
        throttled, or error). Used by close_at_market and the naked-position
        invariant to avoid calling ib.positions() on every tick.
        """
        now = time.time()
        if (now - self._last_broker_truth_check_ts
                < self._broker_truth_interval_s):
            return None
        self._last_broker_truth_check_ts = now
        try:
            if not self.ib.isConnected():
                return None
            for pos in self.ib.positions():
                if pos.contract.conId == self.contract.conId:
                    return float(pos.position)
            return 0.0
        except Exception as e:
            self.logger.warning(f"_broker_position_throttled failed: {e}")
            return None

    def _check_position_vanished(self) -> bool:
        """Check IBKR positions to see if position disappeared.
        Uses double-check pattern to avoid cache-lag false positives.

        **Connection gate (2026-04-21 incident):** ib.positions() returns []
        when the socket is dead, which this function would otherwise read as
        "position vanished" and falsely exit the trade at $0 PnL. Return
        False if we're not connected — the real position state is unknown,
        and the reconnect path is responsible for reconciling it.
        """
        try:
            if not self.ib.isConnected():
                return False
            positions = self.ib.positions()
            has_pos = any(
                p.contract.conId == self.contract.conId and abs(p.position) > 0
                for p in positions
            )
            if not has_pos:
                # Double-check after sleep; re-verify connection afterwards
                # (connection can drop during the sleep).
                self.ib.sleep(2)
                if not self.ib.isConnected():
                    return False
                positions = self.ib.positions()
                return not any(
                    p.contract.conId == self.contract.conId and abs(p.position) > 0
                    for p in positions
                )
        except Exception:
            pass
        return False

    # ── Private: conId-based cancel (V5 safety pattern) ─────────────

    def _cancel_all_for_contract(self):
        """Cancel ALL orders for this contract by conId match.
        This is the key safety pattern from V5 that prevents orphans."""
        if not self.ib.isConnected():
            return
        try:
            cancelled = 0
            for trade in self.ib.openTrades():
                if (hasattr(trade.contract, 'conId')
                        and trade.contract.conId == self.contract.conId):
                    try:
                        self.ib.cancelOrder(trade.order)
                        cancelled += 1
                        self.ib.sleep(0.3)
                    except Exception:
                        pass
            if cancelled:
                self.logger.info(
                    f"Safety cleanup: cancelled {cancelled} orders "
                    f"for {self.contract.symbol}")
        except Exception as e:
            self.logger.error(f"_cancel_all_for_contract failed: {e}")

    def _cancel_and_close(self) -> Optional[float]:
        """Cancel all orders, close position. Returns fill price or None."""
        from ib_insync import MarketOrder

        self._cancel_all_for_contract()
        self.ib.sleep(2)

        # Close any remaining position
        try:
            for pos in self.ib.positions():
                if (pos.contract.conId == self.contract.conId
                        and abs(pos.position) > 0):
                    action = "SELL" if pos.position > 0 else "BUY"
                    close_order = MarketOrder(action, abs(pos.position))
                    close_trade = self.ib.placeOrder(self.contract, close_order)
                    self.ib.sleep(3)

                    # Verify fill with retries
                    for attempt in range(3):
                        self.ib.sleep(0)
                        if close_trade.orderStatus.status == 'Filled':
                            px = close_trade.orderStatus.avgFillPrice
                            self.logger.info(
                                f"Position closed at market (fill={px})")
                            return px
                        self.ib.sleep(2)
                        still_open = any(
                            p.contract.conId == self.contract.conId
                            and abs(p.position) > 0
                            for p in self.ib.positions()
                        )
                        if not still_open:
                            px = (close_trade.orderStatus.avgFillPrice
                                  or self._get_streaming_price())
                            self.logger.info(
                                f"Position confirmed closed (attempt {attempt+1})")
                            return px

                    self.logger.error(
                        "CRITICAL: Position may still be open after close! "
                        "Manual check required.")
        except Exception as e:
            self.logger.error(f"_cancel_and_close failed: {e}")
        return None

    # ── Private: helpers ────────────────────────────────────────────

    def _reset_position(self):
        self._position = 0
        self._direction = None
        self._entry_price = 0.0
        self._entry_fill_ts = None
        self.buy_entry_id = 0
        self.sell_entry_id = 0
        self.sl_order_id = 0
        self.tp_order_id = 0

    def _get_streaming_price(self) -> Optional[float]:
        """Get current price from streaming ticker."""
        try:
            ticker = self.ib.reqMktData(self.contract, '', snapshot=True)
            self.ib.sleep(2)
            bid, ask = ticker.bid, ticker.ask
            if (isinstance(bid, float) and not math.isnan(bid) and bid > 0
                    and isinstance(ask, float) and not math.isnan(ask)):
                return (bid + ask) / 2
        except Exception:
            pass
        return None

    def _dry_run_fill_check(self) -> bool:
        """Simulate fill detection in dry-run mode using streaming price."""
        if self._position == 0 and self._range_info and self.buy_entry_id:
            price = self._get_streaming_price()
            if price is None:
                return False
            ri = self._range_info
            if price > ri.high:
                self._position = 1
                self._direction = "LONG"
                self._entry_price = ri.high
                self.buy_entry_id = 0
                self.sell_entry_id = 0
                self._emit_entry_fill("LONG", ri.high)
                return True
            if price < ri.low:
                self._position = -1
                self._direction = "SHORT"
                self._entry_price = ri.low
                self.buy_entry_id = 0
                self.sell_entry_id = 0
                self._emit_entry_fill("SHORT", ri.low)
                return True
        return False

    def set_trade_date(self, date_str: str):
        """Update trade date for OCA group naming."""
        self._trade_date = date_str

    def get_order_ids(self) -> dict:
        """Return current order IDs for state persistence."""
        return {
            "buy_entry_id": self.buy_entry_id,
            "sell_entry_id": self.sell_entry_id,
            "sl_order_id": self.sl_order_id,
            "tp_order_id": self.tp_order_id,
        }

    def restore_order_ids(self, ids: dict):
        """Restore order IDs from persisted state."""
        self.buy_entry_id = ids.get("buy_entry_id", 0)
        self.sell_entry_id = ids.get("sell_entry_id", 0)
        self.sl_order_id = ids.get("sl_order_id", 0)
        self.tp_order_id = ids.get("tp_order_id", 0)
