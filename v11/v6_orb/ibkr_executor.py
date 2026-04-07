"""
V6 IBKRExecutionEngine — copied from C:\\nautilus0\\v6_orb_refactor\\live\\ibkr_executor.py
DO NOT MODIFY — frozen V6 code. Only import paths changed.
"""
import logging
import math
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
                 logger: Optional[logging.Logger] = None):
        self.ib = ib
        self.contract = contract
        self.quantity = quantity
        self.on_fill_callback = on_fill_callback
        self.trade_end_hour = trade_end_hour
        self._d = price_decimals
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger(__name__)

        # Order tracking (IDs for state persistence)
        self.buy_entry_id: int = 0
        self.sell_entry_id: int = 0
        self.sl_order_id: int = 0
        self.tp_order_id: int = 0

        # Position state
        self._position: int = 0  # 1=LONG, -1=SHORT, 0=flat
        self._direction: Optional[str] = None
        self._entry_price: float = 0.0

        # Range for SL/TP calculation
        self._range_info: Optional[RangeInfo] = None
        self._rr_ratio: float = 2.0

        # Trade date for OCA naming
        self._trade_date: str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

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
            return

        from ib_insync import Order

        now = datetime.now(tz=timezone.utc)
        oca_group = (f"ORB_{self.contract.symbol}_{self._trade_date}_"
                     f"{now.strftime('%H%M%S')}")

        # GTD expiry at trade_end_hour
        trade_end = now.replace(
            hour=self.trade_end_hour, minute=0, second=0, microsecond=0)
        gtd_time = trade_end.strftime("%Y%m%d %H:%M:%S UTC")

        buy_entry = Order(
            action="BUY", orderType="STP", totalQuantity=self.quantity,
            auxPrice=round(range_info.high, d),
            tif="GTD", goodTillDate=gtd_time,
            ocaGroup=oca_group, ocaType=1,
            transmit=False,
        )

        sell_entry = Order(
            action="SELL", orderType="STP", totalQuantity=self.quantity,
            auxPrice=round(range_info.low, d),
            tif="GTD", goodTillDate=gtd_time,
            ocaGroup=oca_group, ocaType=1,
            transmit=True,
        )

        try:
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
        except Exception as e:
            self.logger.error(f"Entry placement failed: {e}")

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
        """Close any open position at market + cancel all orders."""
        if self._position == 0:
            self.logger.info("No position to close")
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
                return self._check_exit_fills()

        except Exception as e:
            self.logger.warning(f"check_fills error: {e}")
        return False

    # ── Private: entry fill detection & Phase 2 ─────────────────────

    def _check_entry_fills(self) -> bool:
        """Check if entry stops filled. If so, place SL/TP (Phase 2)."""
        for trade in self.ib.trades():
            oid = trade.order.orderId
            if oid == self.buy_entry_id and trade.orderStatus.status == 'Filled':
                fill_px = trade.orderStatus.avgFillPrice
                self._position = 1
                self._direction = "LONG"
                self._entry_price = fill_px
                self._place_sl_tp("LONG", fill_px)
                self._emit_entry_fill("LONG", fill_px)
                return True
            if oid == self.sell_entry_id and trade.orderStatus.status == 'Filled':
                fill_px = trade.orderStatus.avgFillPrice
                self._position = -1
                self._direction = "SHORT"
                self._entry_price = fill_px
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
            auxPrice=sl_price, tif="GTC",
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

    def _check_position_vanished(self) -> bool:
        """Check IBKR positions to see if position disappeared.
        Uses double-check pattern to avoid cache-lag false positives."""
        try:
            positions = self.ib.positions()
            has_pos = any(
                p.contract.conId == self.contract.conId and abs(p.position) > 0
                for p in positions
            )
            if not has_pos:
                # Double-check after sleep
                self.ib.sleep(2)
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
