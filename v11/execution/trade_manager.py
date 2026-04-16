"""
CENTER MODULE: Trade Manager — Handles trade lifecycle from entry to exit.

Extracted from v8's run_live.py trade management code into a standalone class.
Manages: entry execution, SL placement, fill tracking, exit execution,
commission tracking, position reconciliation, and trade logging.

CENTER elements:
    - Order submission (real money)
    - SL placement (must be atomic with entry)
    - Position reconciliation (prevents orphaned positions or double entries)
    - Fill tracking (ensures positions have stops, tracks actual vs expected fills)

CHANGES TO THIS FILE REQUIRE EXPLICIT APPROVAL.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.types import (
    Direction, BreakoutSignal, FilterDecision, TradeRecord, ExitReason,
)
from ..config.live_config import InstrumentConfig
from .ibkr_connection import IBKRConnection


TRADE_CSV_FIELDS = [
    'timestamp', 'instrument', 'direction', 'entry_price', 'exit_price',
    'fill_entry_price', 'fill_exit_price', 'stop_price', 'target_price',
    'box_top', 'box_bottom', 'quantity', 'pnl', 'engine_pnl', 'ibkr_pnl',
    'entry_commission', 'exit_commission', 'entry_slippage', 'exit_slippage',
    'exit_reason', 'buy_ratio', 'llm_confidence', 'llm_reasoning',
    'hold_bars',
]


class TradeManager:
    """Manages the full lifecycle of a single trade on a single instrument.

    Each instrument gets its own TradeManager instance.
    One trade at a time per instrument.
    """

    def __init__(
        self,
        conn: IBKRConnection,
        inst: InstrumentConfig,
        log: logging.Logger,
        trade_log_dir: Path,
        dry_run: bool = True,
        max_hold_bars: int = 120,
        auto_close_orphans: bool = False,
    ):
        self._conn = conn
        self._inst = inst
        self._log = log
        self._dry_run = dry_run
        self._max_hold_bars = max_hold_bars
        self._auto_close_orphans = auto_close_orphans
        self._trade_log_path = trade_log_dir / f"trades_{inst.pair_name.lower()}.csv"
        trade_log_dir.mkdir(parents=True, exist_ok=True)

        # Trade state
        self.in_trade: bool = False
        self.direction: Optional[Direction] = None
        self.signal_entry_price: float = 0.0
        self.stop_price: float = 0.0
        self.target_price: float = 0.0
        self.box_top: float = 0.0
        self.box_bottom: float = 0.0
        self.entry_bar_index: int = 0
        self.buy_ratio: float = 0.0
        self.llm_confidence: int = 0
        self.llm_reasoning: str = ""

        # IBKR fill tracking
        self._fill_entry_price: float = 0.0
        self._entry_commission: float = 0.0
        self._entry_trade = None
        self._sl_order = None
        self._tp_order = None

        # Trade closed callback (for auto-assessment)
        self.on_trade_closed = None  # Callable[[TradeRecord], None]

        # Daily counters
        self.daily_trades: int = 0
        self.daily_pnl: float = 0.0

    def enter_trade(
        self,
        signal: BreakoutSignal,
        decision: FilterDecision,
        buy_ratio: float,
        current_bar_index: int,
    ) -> bool:
        """Execute trade entry. Returns True if entry was successful.

        Submits market order + stop-loss order as a pair.
        Tracks actual fill price and commission.
        """
        if self.in_trade:
            self._log.warning(
                f"{self._inst.pair_name}: Already in trade, skipping entry")
            return False

        direction = signal.direction
        pf = self._inst.price_fmt

        self._log.info(
            f"SIGNAL: {self._inst.pair_name} {direction.value.upper()} | "
            f"box=[{signal.box.bottom:{pf}}, {signal.box.top:{pf}}] "
            f"breakout={signal.breakout_price:{pf}} "
            f"buy_ratio={buy_ratio:.3f} "
            f"LLM conf={decision.confidence}"
        )

        # Set trade state
        self.in_trade = True
        self.direction = direction
        self.signal_entry_price = decision.entry_price
        self.stop_price = decision.stop_price
        self.target_price = decision.target_price
        self.box_top = signal.box.top
        self.box_bottom = signal.box.bottom
        self.entry_bar_index = current_bar_index
        self.buy_ratio = buy_ratio
        self.llm_confidence = decision.confidence
        self.llm_reasoning = decision.reasoning

        # Reset fill tracking
        self._fill_entry_price = 0.0
        self._entry_commission = 0.0
        self._entry_trade = None
        self._sl_order = None
        self._tp_order = None

        if self._dry_run:
            self._log.info(f"[DRY RUN] {self._inst.pair_name}: Would enter trade")
            return True

        entry_trade = None

        # PRIMARY PATH: atomic bracket (entry+SL+TP, no naked position window)
        if self.stop_price > 0 and self.target_price > 0:
            entry_trade, self._sl_order, self._tp_order = (
                self._conn.submit_bracket_order(
                    self._inst.pair_name, direction.value, self._inst.quantity,
                    self.stop_price, self.target_price,
                    tick_size=self._inst.tick_size))
            if entry_trade is not None:
                self._entry_trade = entry_trade
                self._log.info(
                    f"{self._inst.pair_name}: BRACKET placed — "
                    f"SL={self.stop_price:{pf}} TP={self.target_price:{pf}}")
            else:
                self._sl_order = None
                self._tp_order = None
                self._log.warning(
                    f"{self._inst.pair_name}: BRACKET FAILED — "
                    f"falling back to two-step")

        # FALLBACK PATH: two-step (market order first, then SL/TP separately)
        if entry_trade is None:
            entry_trade = self._conn.submit_market_order(
                self._inst.pair_name, direction.value, self._inst.quantity)
            if entry_trade is None:
                self._log.error(
                    f"{self._inst.pair_name}: ENTRY ORDER FAILED — resetting")
                self._reset_trade_state()
                return False
            self._entry_trade = entry_trade

        # Capture fill price (applies to both bracket and two-step paths)
        fill_price = entry_trade.orderStatus.avgFillPrice
        if fill_price and fill_price > 0:
            self._fill_entry_price = fill_price
            slippage = fill_price - self.signal_entry_price
            self._log.info(
                f"FILL ENTRY: {self._inst.pair_name} "
                f"{direction.value.upper()} {self._inst.quantity} "
                f"@ {fill_price:{pf}} "
                f"(signal={self.signal_entry_price:{pf}} "
                f"slippage={slippage:{pf}})")
        else:
            self._log.warning(
                f"{self._inst.pair_name}: FILL ENTRY: avgFillPrice not available")
            self._fill_entry_price = self.signal_entry_price

        # Capture entry commission
        self._entry_commission = self._conn.get_fill_commission(entry_trade)
        if self._entry_commission > 0:
            self._log.info(
                f"{self._inst.pair_name}: ENTRY COMMISSION: "
                f"${self._entry_commission:.2f}")

        # Bracket succeeded — SL/TP already placed atomically, done
        if self._sl_order is not None:
            return True

        # Two-step SL/TP placement (for fallback path or no-TP case)
        if self.stop_price > 0:
            if self.target_price > 0:
                # OCA pair (SL + TP together)
                self._sl_order, self._tp_order = self._conn.submit_sl_tp_oca(
                    self._inst.pair_name, direction.value,
                    self._inst.quantity, self.stop_price, self.target_price,
                    tick_size=self._inst.tick_size)
                if self._sl_order is None:
                    self._log.warning(
                        f"{self._inst.pair_name}: SL/TP OCA FAILED — retrying SL-only")
                    self._conn.sleep(5)
                    self._sl_order = self._conn.submit_stop_order(
                        self._inst.pair_name, direction.value,
                        self._inst.quantity, self.stop_price,
                        tick_size=self._inst.tick_size)
                    if self._sl_order is None:
                        self._log.error(
                            f"{self._inst.pair_name}: SL ORDER FAILED TWICE — "
                            f"FORCE CLOSING POSITION (unhedged risk)")
                        self._conn.close_position(
                            self._inst.pair_name, direction.value,
                            self._inst.quantity)
                        self._poll_until_flat("forced close after SL failure")
                        self._reset_trade_state()
                        return False
                    self._log.warning(
                        f"{self._inst.pair_name}: SL placed (no TP in IBKR)")
                else:
                    self._log.info(
                        f"{self._inst.pair_name}: SL/TP OCA placed — "
                        f"SL={self.stop_price:{pf}} TP={self.target_price:{pf}}")
            else:
                # No target price — SL only
                self._sl_order = self._conn.submit_stop_order(
                    self._inst.pair_name, direction.value,
                    self._inst.quantity, self.stop_price,
                    tick_size=self._inst.tick_size)
                if self._sl_order is None:
                    self._log.warning(
                        f"{self._inst.pair_name}: SL ORDER FAILED — retrying")
                    self._conn.sleep(5)
                    self._sl_order = self._conn.submit_stop_order(
                        self._inst.pair_name, direction.value,
                        self._inst.quantity, self.stop_price,
                        tick_size=self._inst.tick_size)
                    if self._sl_order is None:
                        self._log.error(
                            f"{self._inst.pair_name}: SL ORDER FAILED TWICE — "
                            f"FORCE CLOSING POSITION (unhedged risk)")
                        self._conn.close_position(
                            self._inst.pair_name, direction.value,
                            self._inst.quantity)
                        self._poll_until_flat("forced close after SL-only failure")
                        self._reset_trade_state()
                        return False

        return True

    def check_exit(self, current_price: float, bar_high: float,
                   bar_low: float, current_bar_index: int
                   ) -> Optional[TradeRecord]:
        """Check if exit conditions are met. Returns TradeRecord if trade closed.

        Checks: SL hit, time stop, target hit.
        """
        if not self.in_trade or self.direction is None:
            return None

        bars_held = current_bar_index - self.entry_bar_index

        # SL check
        if self.stop_price > 0:
            sl_hit = False
            if self.direction == Direction.LONG and bar_low <= self.stop_price:
                sl_hit = True
            elif self.direction == Direction.SHORT and bar_high >= self.stop_price:
                sl_hit = True
            if sl_hit:
                return self._execute_exit(
                    ExitReason.SL, self.stop_price, bars_held)

        # Target check
        if self.target_price > 0:
            target_hit = False
            if self.direction == Direction.LONG and bar_high >= self.target_price:
                target_hit = True
            elif self.direction == Direction.SHORT and bar_low <= self.target_price:
                target_hit = True
            if target_hit:
                return self._execute_exit(
                    ExitReason.TARGET, self.target_price, bars_held)

        # Time stop
        if bars_held >= self._max_hold_bars:
            return self._execute_exit(
                ExitReason.TIME_STOP, current_price, bars_held)

        return None

    def force_close(self, current_price: float, reason: ExitReason,
                    current_bar_index: int) -> Optional[TradeRecord]:
        """Force close the current trade (safety limit, shutdown, etc.)."""
        if not self.in_trade:
            return None
        bars_held = current_bar_index - self.entry_bar_index
        return self._execute_exit(reason, current_price, bars_held)

    def _execute_exit(self, reason: ExitReason, exit_price: float,
                      bars_held: int) -> TradeRecord:
        """Execute exit with IBKR fill verification and trade logging."""
        pf = self._inst.price_fmt
        direction = self.direction

        # Compute engine PnL
        spread = 0.0  # spread already accounted for in entry/exit prices
        if direction == Direction.LONG:
            price_pnl = exit_price - self.signal_entry_price
        else:
            price_pnl = self.signal_entry_price - exit_price
        engine_pnl = self._inst.price_pnl_to_usd(price_pnl, exit_price)

        self._log.info(
            f"EXIT: {self._inst.pair_name} {reason.value} "
            f"{direction.value} @ {exit_price:{pf}} "
            f"PnL=${engine_pnl:+.2f} hold={bars_held}bars")

        fill_exit_price = 0.0
        exit_commission = 0.0
        ibkr_pnl = engine_pnl
        exit_trade = None

        if not self._dry_run:
            # Save order references before nulling — needed for fill capture below
            sl_order_ref = self._sl_order
            tp_order_ref = self._tp_order

            # Cancel SL and TP orders (IBKR OCA cancels the other automatically
            # when one fills, but we cancel both explicitly on software-triggered exits)
            if self._sl_order:
                try:
                    self._conn.ib.cancelOrder(self._sl_order.order)
                except Exception as e:
                    self._log.warning(
                        f"{self._inst.pair_name}: SL cancel on exit: {e}")
                self._sl_order = None
            if self._tp_order:
                try:
                    self._conn.ib.cancelOrder(self._tp_order.order)
                except Exception as e:
                    self._log.warning(
                        f"{self._inst.pair_name}: TP cancel on exit: {e}")
                self._tp_order = None

            # Brief wait for cancel ACK before submitting market close (Fix 6)
            self._conn.sleep(0.5)

            # Close at market unless IBKR already filled the exit order
            if reason != ExitReason.SL:
                if self._conn.has_position(self._inst.symbol, self._inst.sec_type):
                    exit_trade = self._conn.close_position(
                        self._inst.pair_name, direction.value,
                        self._inst.quantity)
                else:
                    self._log.info(
                        f"{self._inst.pair_name}: No position at broker "
                        f"(IBKR bracket already filled)")

            # Capture exit fill price (use saved refs — self._sl/tp_order already None)
            if exit_trade:
                fp = exit_trade.orderStatus.avgFillPrice
                if fp and fp > 0:
                    fill_exit_price = fp
            elif reason == ExitReason.SL and sl_order_ref:
                fp = sl_order_ref.orderStatus.avgFillPrice
                if fp and fp > 0:
                    fill_exit_price = fp
            elif reason == ExitReason.TARGET and tp_order_ref:
                fp = tp_order_ref.orderStatus.avgFillPrice
                if fp and fp > 0:
                    fill_exit_price = fp

            # IBKR-verified PnL
            if self._fill_entry_price > 0 and fill_exit_price > 0:
                if direction == Direction.LONG:
                    raw_pnl = fill_exit_price - self._fill_entry_price
                else:
                    raw_pnl = self._fill_entry_price - fill_exit_price
                ibkr_pnl = self._inst.price_pnl_to_usd(raw_pnl, fill_exit_price)
                self._log.info(
                    f"FILL EXIT: {self._inst.pair_name} @ {fill_exit_price:{pf}} | "
                    f"IBKR PnL=${ibkr_pnl:+.2f} (engine=${engine_pnl:+.2f})")

            # Commission
            if exit_trade:
                exit_commission = self._conn.get_fill_commission(exit_trade)
            total_comm = self._entry_commission + exit_commission
            if total_comm > 0:
                self._log.info(
                    f"{self._inst.pair_name}: COMMISSION total=${total_comm:.2f}")

        # Build trade record
        entry_slippage = (
            (self._fill_entry_price - self.signal_entry_price)
            if self._fill_entry_price > 0 else 0.0
        )
        exit_slippage = (
            (fill_exit_price - exit_price)
            if fill_exit_price > 0 else 0.0
        )

        record = TradeRecord(
            entry_time=datetime.now(timezone.utc),  # approximate
            exit_time=datetime.now(timezone.utc),
            direction=direction,
            instrument=self._inst.pair_name,
            entry_price=self.signal_entry_price,
            exit_price=exit_price,
            stop_price=self.stop_price,
            target_price=self.target_price,
            box_top=self.box_top,
            box_bottom=self.box_bottom,
            exit_reason=reason.value,
            pnl=ibkr_pnl if fill_exit_price > 0 else engine_pnl,
            engine_pnl=engine_pnl,
            ibkr_pnl=ibkr_pnl,
            hold_bars=bars_held,
            buy_ratio_at_entry=self.buy_ratio,
            llm_confidence=self.llm_confidence,
            llm_reasoning=self.llm_reasoning,
            fill_entry_price=self._fill_entry_price,
            fill_exit_price=fill_exit_price,
            entry_commission=self._entry_commission,
            exit_commission=exit_commission,
            entry_slippage=entry_slippage,
            exit_slippage=exit_slippage,
        )

        # Log to CSV
        self._log_trade_csv(record)

        # Update daily counters
        self.daily_trades += 1
        self.daily_pnl += record.pnl

        # Reset state
        self._reset_trade_state()

        # Notify callback (for auto-assessment)
        if self.on_trade_closed:
            try:
                self.on_trade_closed(record)
            except Exception as e:
                self._log.warning(f"on_trade_closed callback error: {e}")

        return record

    def _reset_trade_state(self) -> None:
        self.in_trade = False
        self.direction = None
        self.signal_entry_price = 0.0
        self.stop_price = 0.0
        self.target_price = 0.0
        self.box_top = 0.0
        self.box_bottom = 0.0
        self.entry_bar_index = 0
        self.buy_ratio = 0.0
        self.llm_confidence = 0
        self.llm_reasoning = ""
        self._fill_entry_price = 0.0
        self._entry_commission = 0.0
        self._entry_trade = None
        self._sl_order = None
        self._tp_order = None

    def _poll_until_flat(self, context: str, max_polls: int = 10) -> None:
        """Poll broker position until confirmed flat after a forced close (Fix 2)."""
        for i in range(max_polls):
            self._conn.sleep(1)
            pos = self._conn.get_position_size(
                self._inst.symbol, self._inst.sec_type)
            if abs(pos) < 0.001:
                self._log.info(
                    f"{self._inst.pair_name}: Confirmed flat after {context} "
                    f"(after {i + 1}s)")
                return
        self._log.error(
            f"{self._inst.pair_name}: NOT FLAT after {context} "
            f"and {max_polls}s polling! Manual intervention required.")

    def emergency_close(self, reason: str = "EMERGENCY") -> None:
        """Force-close any open position at market. Used during emergency shutdown.

        Unlike _execute_exit, this doesn't require bar context and works
        even if internal state is partially inconsistent.
        """
        if not self.in_trade:
            return

        self._log.critical(
            f"EMERGENCY CLOSE: {self._inst.pair_name} "
            f"{self.direction.value if self.direction else '?'} "
            f"qty={self._inst.quantity}")

        if not self._dry_run:
            # Cancel SL and TP orders
            if self._sl_order:
                try:
                    self._conn.ib.cancelOrder(self._sl_order.order)
                except Exception:
                    pass
                self._sl_order = None
            if self._tp_order:
                try:
                    self._conn.ib.cancelOrder(self._tp_order.order)
                except Exception:
                    pass
                self._tp_order = None

            # Close at market then poll/retry until confirmed flat (Fix 5)
            direction = self.direction.value if self.direction else "long"
            try:
                self._conn.close_position(
                    self._inst.pair_name, direction, self._inst.quantity)
            except Exception as e:
                self._log.error(f"Emergency close attempt 1 failed: {e}")

            for attempt in range(3):
                self._conn.sleep(2)
                pos = self._conn.get_position_size(
                    self._inst.symbol, self._inst.sec_type)
                if abs(pos) < 0.001:
                    self._log.info(
                        f"{self._inst.pair_name}: Emergency close confirmed flat")
                    break
                self._log.warning(
                    f"{self._inst.pair_name}: Still has position "
                    f"({pos}) after emergency close attempt {attempt + 1}")
                if attempt < 2:
                    try:
                        self._conn.close_position(
                            self._inst.pair_name, direction, abs(pos))
                    except Exception as e:
                        self._log.error(
                            f"Emergency close retry {attempt + 2} failed: {e}")
            else:
                self._log.error(
                    f"{self._inst.pair_name}: EMERGENCY CLOSE INCOMPLETE "
                    f"after 3 attempts! Manual intervention required.")

        # Log the emergency close as a trade record
        try:
            record = TradeRecord(
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                direction=self.direction,
                instrument=self._inst.pair_name,
                entry_price=self.signal_entry_price,
                exit_price=self.signal_entry_price,
                stop_price=self.stop_price,
                target_price=self.target_price,
                box_top=self.box_top,
                box_bottom=self.box_bottom,
                exit_reason="EMERGENCY",
                pnl=0.0,
                hold_bars=0,
                buy_ratio_at_entry=self.buy_ratio,
                llm_confidence=self.llm_confidence,
                llm_reasoning=self.llm_reasoning,
                fill_entry_price=self._fill_entry_price,
                fill_exit_price=0.0,
                entry_commission=self._entry_commission,
                exit_commission=0.0,
                entry_slippage=0.0,
                exit_slippage=0.0,
            )
            self._log_trade_csv(record)
            self.daily_trades += 1
            if self.on_trade_closed:
                try:
                    self.on_trade_closed(record)
                except Exception:
                    pass
        except Exception as e:
            self._log.error(f"Failed to log emergency close: {e}")

        self._reset_trade_state()

    def reconcile_position(self) -> None:
        """Reconcile internal state with broker after reconnect.

        If we think we're in a trade but broker has no position → reset.
        If broker has a position but we don't know about it → log warning.
        """
        broker_pos = self._conn.get_position_size(
            self._inst.symbol, self._inst.sec_type)
        broker_has_pos = abs(broker_pos) > 0

        if self.in_trade and not broker_has_pos:
            self._log.warning(
                f"{self._inst.pair_name}: RECONCILE — internal=in_trade but "
                f"broker=flat. Resetting trade state (position was closed "
                f"externally or SL filled during disconnect)")
            self._reset_trade_state()
        elif not self.in_trade and broker_has_pos:
            if self._auto_close_orphans:
                self._log.warning(
                    f"{self._inst.pair_name}: RECONCILE — internal=flat but "
                    f"broker has position={broker_pos}. Auto-closing orphan.")
                # Determine direction from broker position sign
                direction = "long" if broker_pos > 0 else "short"
                try:
                    self._conn.close_position(
                        self._inst.pair_name, direction, abs(broker_pos))
                    self._log.info(
                        f"{self._inst.pair_name}: Orphan position closed")
                except Exception as e:
                    self._log.error(
                        f"{self._inst.pair_name}: Orphan close failed: {e}")
            else:
                self._log.warning(
                    f"{self._inst.pair_name}: RECONCILE — internal=flat but "
                    f"broker has position={broker_pos}. Orphaned position "
                    f"detected — manual intervention required")
        elif self.in_trade and broker_has_pos:
            expected_qty = self._inst.quantity
            if abs(abs(broker_pos) - expected_qty) > 0.001:
                self._log.warning(
                    f"{self._inst.pair_name}: RECONCILE — SIZE MISMATCH: "
                    f"broker={broker_pos}, expected={expected_qty}")
        else:
            self._log.info(
                f"{self._inst.pair_name}: RECONCILE — "
                f"internal={'in_trade' if self.in_trade else 'flat'}, "
                f"broker={'has_position' if broker_has_pos else 'flat'} — OK")

    def reset_daily(self) -> None:
        """Reset daily counters at market open."""
        self.daily_trades = 0
        self.daily_pnl = 0.0

    def _log_trade_csv(self, record: TradeRecord) -> None:
        """Append trade record to CSV file."""
        is_new = not self._trade_log_path.exists()
        try:
            with open(self._trade_log_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=TRADE_CSV_FIELDS)
                if is_new:
                    writer.writeheader()
                row = {
                    'timestamp': record.exit_time.isoformat() if record.exit_time else '',
                    'instrument': record.instrument,
                    'direction': record.direction.value if record.direction else '',
                    'entry_price': f"{record.entry_price}",
                    'exit_price': f"{record.exit_price}",
                    'fill_entry_price': f"{record.fill_entry_price}" if record.fill_entry_price > 0 else '',
                    'fill_exit_price': f"{record.fill_exit_price}" if record.fill_exit_price > 0 else '',
                    'stop_price': f"{record.stop_price}",
                    'target_price': f"{record.target_price}",
                    'box_top': f"{record.box_top}",
                    'box_bottom': f"{record.box_bottom}",
                    'quantity': self._inst.quantity,
                    'pnl': f"{record.pnl:.2f}",
                    'engine_pnl': f"{record.engine_pnl:.2f}",
                    'ibkr_pnl': f"{record.ibkr_pnl:.2f}",
                    'entry_commission': f"{record.entry_commission:.2f}" if record.entry_commission > 0 else '',
                    'exit_commission': f"{record.exit_commission:.2f}" if record.exit_commission > 0 else '',
                    'entry_slippage': f"{record.entry_slippage}" if record.entry_slippage != 0 else '',
                    'exit_slippage': f"{record.exit_slippage}" if record.exit_slippage != 0 else '',
                    'exit_reason': record.exit_reason,
                    'buy_ratio': f"{record.buy_ratio_at_entry:.3f}",
                    'llm_confidence': record.llm_confidence,
                    'llm_reasoning': record.llm_reasoning[:200],
                    'hold_bars': record.hold_bars,
                }
                writer.writerow(row)
        except Exception as e:
            self._log.error(f"Failed to log trade CSV: {e}")
