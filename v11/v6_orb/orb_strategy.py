"""
V6 ORBStrategy — copied from C:\\nautilus0\\v6_orb_refactor\\strategy\\orb_strategy.py
DO NOT MODIFY — frozen V6 code. Only import paths changed.
"""
import enum
from datetime import datetime
from typing import Optional
import logging

from .config import StrategyConfig
from .market_event import Tick, Fill, RangeInfo
from .interfaces import MarketContext, ExecutionEngine


class StrategyState(enum.Enum):
    IDLE = "IDLE"                   # Waiting for range to form
    RANGE_READY = "RANGE_READY"     # Range formed, monitoring velocity
    ORDERS_PLACED = "ORDERS_PLACED" # Velocity high, resting brackets active
    IN_TRADE = "IN_TRADE"           # Entry filled, managing position
    DONE_TODAY = "DONE_TODAY"       # Trade finished or skipped, done until tomorrow


class ORBStrategy:
    """
    Pure ORB strategy logic. Environment-agnostic state machine.

    Handles: range validation, velocity gating, breakeven, max_pending_hours,
    time_exit, and EOD close. Does NOT know about IBKR, CSV logging, or files.
    """
    def __init__(self, config: StrategyConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.state = StrategyState.IDLE
        self.range: Optional[RangeInfo] = None
        self.logger = logger or logging.getLogger(__name__)
        self._d = config.price_decimals

        # Entry tracking (populated on ENTRY fill)
        self.direction: Optional[str] = None
        self.entry_price: float = 0.0
        self.entry_time: Optional[datetime] = None
        self.sl_price: float = 0.0
        self.tp_price: float = 0.0
        self.be_applied: bool = False

        # Orders tracking
        self.orders_placed_time: Optional[datetime] = None

    def on_tick(self, tick: Tick, context: MarketContext, execution: ExecutionEngine):
        """Called on every tick/poll. Pure state machine."""
        if self.state == StrategyState.DONE_TODAY:
            return

        # STATE: IDLE -> RANGE_READY
        if self.state == StrategyState.IDLE:
            self._handle_idle(tick, context)
            return

        # STATE: RANGE_READY <-> ORDERS_PLACED
        if self.state in (StrategyState.RANGE_READY, StrategyState.ORDERS_PLACED):
            self._handle_range_and_orders(tick, context, execution)
            return

        # STATE: IN_TRADE
        if self.state == StrategyState.IN_TRADE:
            self._handle_in_trade(tick, context, execution)
            return

    def on_fill(self, fill: Fill, context: MarketContext, execution: ExecutionEngine):
        """Called when the execution engine reports a fill."""
        d = self._d
        if fill.reason == "ENTRY":
            self.direction = fill.direction
            self.entry_price = fill.price
            self.entry_time = fill.timestamp
            self.be_applied = False

            # SL/TP from range levels (V5 approach: TP relative to range, not fill price)
            rs = self.range.size
            if fill.direction == "LONG":
                self.sl_price = self.range.low
                self.tp_price = round(self.range.high + self.config.rr_ratio * rs, d)
            else:
                self.sl_price = self.range.high
                self.tp_price = round(self.range.low - self.config.rr_ratio * rs, d)

            self.logger.info(
                f"ENTRY: {fill.direction} @ {fill.price:.{d}f} | "
                f"SL={self.sl_price:.{d}f} TP={self.tp_price:.{d}f}")
            self.state = StrategyState.IN_TRADE

        elif fill.reason in ("SL", "TP", "MARKET", "BE"):
            pnl = self._calc_pnl(fill.price)
            self.logger.info(
                f"{fill.reason}: @ {fill.price:.{d}f} | PnL={pnl:+.{d}f}")
            self.state = StrategyState.DONE_TODAY

    # ── State handlers ──────────────────────────────────────────────

    def _handle_idle(self, tick: Tick, context: MarketContext):
        cfg = self.config
        if not context.time_is_in_trade_window(
                tick.timestamp, cfg.trade_start_hour, cfg.trade_end_hour):
            return

        self.range = context.get_asian_range(
            cfg.range_start_hour, cfg.range_end_hour, tick.timestamp)

        if self.range and self._range_is_valid(tick):
            d = self._d
            self.logger.info(
                f"Range: {self.range.low:.{d}f} - {self.range.high:.{d}f} "
                f"(size: {self.range.size:.{d}f})")

            # Gap filter: skip day if gap period was too quiet
            if cfg.gap_filter_enabled:
                gap = context.get_gap_metrics(
                    tick.timestamp,
                    cfg.gap_start_hour, cfg.gap_end_hour,
                    cfg.gap_vol_percentile, cfg.gap_range_percentile,
                    cfg.gap_rolling_days)
                if gap is not None:
                    vol_ok = gap.vol_passes
                    range_ok = (gap.range_passes
                                if cfg.gap_range_filter_enabled else True)
                    if not vol_ok or not range_ok:
                        self.logger.info(
                            f"Gap filter SKIP: vol={gap.gap_volatility:.6f} "
                            f"({'PASS' if vol_ok else 'FAIL'}), "
                            f"range={gap.gap_range:.3f} "
                            f"({'PASS' if range_ok else 'FAIL'})")
                        self.state = StrategyState.DONE_TODAY
                        return
                    self.logger.info(
                        f"Gap filter PASS: vol={gap.gap_volatility:.6f}, "
                        f"range={gap.gap_range:.3f}")

            self.state = StrategyState.RANGE_READY
        else:
            if self.range:
                self.logger.info(
                    f"Range invalid (size: {self.range.size:.{self._d}f}), done")
            else:
                self.logger.info("No range data available, done for today")
            self.state = StrategyState.DONE_TODAY

    def _handle_range_and_orders(self, tick: Tick, context: MarketContext,
                                  execution: ExecutionEngine):
        cfg = self.config

        # Window closed?
        if not context.time_is_in_trade_window(
                tick.timestamp, cfg.trade_start_hour, cfg.trade_end_hour):
            if self.state == StrategyState.ORDERS_PLACED:
                self.logger.info("Trade window closed, canceling brackets")
                execution.cancel_orb_brackets()
            self.state = StrategyState.DONE_TODAY
            return

        # Max pending hours: cancel if entries rest too long
        if (self.state == StrategyState.ORDERS_PLACED
                and cfg.max_pending_hours > 0
                and self.orders_placed_time):
            elapsed_h = (tick.timestamp - self.orders_placed_time).total_seconds() / 3600
            if elapsed_h >= cfg.max_pending_hours:
                self.logger.warning(
                    f"Orders pending > {cfg.max_pending_hours}h, cancelling")
                execution.cancel_orb_brackets()
                self.state = StrategyState.DONE_TODAY
                return

        # Velocity filter
        if cfg.velocity_filter_enabled:
            vel = context.get_velocity(cfg.velocity_lookback_minutes, tick.timestamp)
            velocity_ok = vel >= cfg.velocity_threshold
        else:
            vel = 0.0
            velocity_ok = True

        if velocity_ok:
            if self.state == StrategyState.RANGE_READY:
                # Guard: skip if price already outside range (stale breakout)
                price = context.get_current_price(tick.timestamp)
                if price is not None and self.range:
                    if price > self.range.high or price < self.range.low:
                        self.logger.info(
                            f"Price {price:.{self._d}f} already outside range "
                            f"[{self.range.low:.{self._d}f}-{self.range.high:.{self._d}f}], "
                            f"skipping stale breakout")
                        self.state = StrategyState.DONE_TODAY
                        return
                self.logger.info(
                    f"Velocity {vel:.0f} >= {cfg.velocity_threshold:.0f}, "
                    f"placing brackets")
                execution.set_orb_brackets(self.range, cfg.rr_ratio)
                self.orders_placed_time = tick.timestamp
                self.state = StrategyState.ORDERS_PLACED
        else:
            if self.state == StrategyState.ORDERS_PLACED:
                # Hysteresis: use 90% threshold to avoid cycling
                cancel_threshold = cfg.velocity_threshold * 0.9
                if vel < cancel_threshold:
                    self.logger.info(
                        f"Velocity {vel:.0f} < {cancel_threshold:.0f} "
                        f"(90% of {cfg.velocity_threshold:.0f}), pulling orders")
                    execution.cancel_orb_brackets()
                    self.orders_placed_time = None
                    self.state = StrategyState.RANGE_READY

    def _handle_in_trade(self, tick: Tick, context: MarketContext,
                          execution: ExecutionEngine):
        cfg = self.config

        # EOD close
        if not context.time_is_in_trade_window(
                tick.timestamp, cfg.trade_start_hour, cfg.trade_end_hour):
            self.logger.info("Trade window closed, closing at market")
            execution.close_at_market()
            # State transition happens in on_fill(MARKET)
            return

        # Time-based exit
        if (cfg.time_exit_minutes > 0 and self.entry_time):
            elapsed_min = (tick.timestamp - self.entry_time).total_seconds() / 60
            if elapsed_min >= cfg.time_exit_minutes:
                self.logger.info(
                    f"Time exit: {elapsed_min:.0f}min >= "
                    f"{cfg.time_exit_minutes}min, closing at market")
                execution.close_at_market()
                return

        # Breakeven rule
        if (not self.be_applied
                and cfg.be_hours < 999
                and self.entry_time):
            elapsed_h = (tick.timestamp - self.entry_time).total_seconds() / 3600
            if elapsed_h >= cfg.be_hours:
                self._apply_breakeven(tick, context, execution)

    def _apply_breakeven(self, tick: Tick, context: MarketContext,
                          execution: ExecutionEngine):
        """Move SL to entry + offset. Guard: skip if price already past new SL."""
        cfg = self.config
        d = self._d

        if self.direction == "LONG":
            new_sl = round(self.entry_price + cfg.be_offset, d)
        else:
            new_sl = round(self.entry_price - cfg.be_offset, d)

        # Guard: don't move SL if price already past it
        price = context.get_current_price(tick.timestamp)
        if price is not None:
            if self.direction == "LONG" and price < new_sl:
                self.logger.info(
                    f"BE skipped: price {price:.{d}f} < new_sl {new_sl:.{d}f}")
                return
            if self.direction == "SHORT" and price > new_sl:
                self.logger.info(
                    f"BE skipped: price {price:.{d}f} > new_sl {new_sl:.{d}f}")
                return

        old_sl = self.sl_price
        execution.modify_sl(new_sl)
        self.sl_price = new_sl
        self.be_applied = True
        self.logger.info(
            f"BE applied: SL {old_sl:.{d}f} -> {new_sl:.{d}f}")

    # ── Helpers ──────────────────────────────────────────────────────

    def _range_is_valid(self, tick: Tick) -> bool:
        """Validate range using % of price (V5 approach)."""
        cfg = self.config
        r = self.range
        mid = (r.high + r.low) / 2
        if mid <= 0:
            return False
        pct = r.size / mid * 100
        if pct < cfg.min_range_pct:
            self.logger.warning(f"Range too tight ({pct:.3f}%)")
            return False
        if pct > cfg.max_range_pct:
            self.logger.warning(f"Range too wide ({pct:.2f}%)")
            return False
        return True

    def _calc_pnl(self, exit_price: float) -> float:
        if self.direction == "LONG":
            return exit_price - self.entry_price
        else:
            return self.entry_price - exit_price

    # ── State persistence ────────────────────────────────────────────

    def get_state_snapshot(self) -> dict:
        """Pure dict for Runner to save to JSON."""
        return {
            "state": self.state.value,
            "range_high": self.range.high if self.range else None,
            "range_low": self.range.low if self.range else None,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "entry_time": self.entry_time.isoformat() if self.entry_time else None,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "be_applied": self.be_applied,
            "orders_placed_time": (self.orders_placed_time.isoformat()
                                   if self.orders_placed_time else None),
        }

    def restore_state(self, snapshot: dict):
        """Restore from dict."""
        self.state = StrategyState(snapshot["state"])
        if snapshot.get("range_high") is not None and snapshot.get("range_low") is not None:
            self.range = RangeInfo(
                high=snapshot["range_high"],
                low=snapshot["range_low"],
                start_time=None,
                end_time=None,
            )
        self.direction = snapshot.get("direction")
        self.entry_price = snapshot.get("entry_price", 0.0)
        et = snapshot.get("entry_time")
        self.entry_time = datetime.fromisoformat(et) if et else None
        self.sl_price = snapshot.get("sl_price", 0.0)
        self.tp_price = snapshot.get("tp_price", 0.0)
        self.be_applied = snapshot.get("be_applied", False)
        opt = snapshot.get("orders_placed_time")
        self.orders_placed_time = datetime.fromisoformat(opt) if opt else None

    def reset_for_new_day(self):
        """Reset all state for a new trading day."""
        self.state = StrategyState.IDLE
        self.range = None
        self.direction = None
        self.entry_price = 0.0
        self.entry_time = None
        self.sl_price = 0.0
        self.tp_price = 0.0
        self.be_applied = False
        self.orders_placed_time = None
