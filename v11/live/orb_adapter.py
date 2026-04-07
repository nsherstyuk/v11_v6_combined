"""
ORBAdapter — Bridges V6 ORB strategy into V11's MultiStrategyRunner.

Design (V11_DESIGN.md §11, Phase 5):
    V6 ORB is tick-driven (polled every 2s). V11 is bar-driven.
    This adapter translates between the two models:

    1. V6's LiveMarketContext manages its own IBKR tick subscription
       for velocity calculation and price buffering.
    2. on_price() throttles to V6's poll interval (2s) and drives
       strategy.on_tick() + execution.check_fills().
    3. on_bar() is a no-op — V6 doesn't use bars.
    4. Fill callbacks report to V11's RiskManager.
    5. Daily orchestration (range calc, gap injection) replaces what
       V6's LiveRunner normally does.

    Risk gate: when the strategy is in RANGE_READY (about to place
    brackets), the adapter checks V11's RiskManager. If blocked,
    the strategy is held at RANGE_READY until risk clears or the
    trade window closes.

    V6 code is NOT modified. The adapter wraps it.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ..v6_orb.orb_strategy import ORBStrategy, StrategyState
from ..v6_orb.config import StrategyConfig as V6StrategyConfig
from ..v6_orb.market_event import Tick, Fill
from ..v6_orb.live_context import LiveMarketContext
from ..v6_orb.ibkr_executor import IBKRExecutionEngine
from .risk_manager import RiskManager


class ORBAdapter:
    """Adapts V6 ORB strategy to work within V11's MultiStrategyRunner.

    Satisfies the StrategyEngine protocol:
        pair_name, in_trade, bar_count, strategy_name,
        on_bar(), on_price(), add_historical_bar(), get_status()

    Owns V6 components:
        ORBStrategy, LiveMarketContext, IBKRExecutionEngine

    Does NOT own:
        IBKR connection (shared via V11's IBKRConnection.ib)
        RiskManager (shared across all strategies)
    """

    STRATEGY_NAME = "V6_ORB"

    def __init__(
        self,
        ib,
        contract,
        v6_config: V6StrategyConfig,
        risk_manager: RiskManager,
        log: logging.Logger,
        state_dir: str = "",
        dry_run: bool = True,
        poll_interval: float = 2.0,
    ):
        """
        Args:
            ib: ib_insync.IB instance from V11's shared IBKRConnection.
            contract: Qualified IBKR Contract for XAUUSD.
            v6_config: V6 StrategyConfig with ORB parameters.
            risk_manager: V11's shared RiskManager.
            log: Logger instance.
            state_dir: Directory for V6 gap history persistence. Empty = no persistence.
            dry_run: If True, V6 execution engine suppresses real orders.
            poll_interval: Seconds between strategy.on_tick() calls (V6 default: 2).
        """
        self._ib = ib
        self._contract = contract
        self._v6_config = v6_config
        self._risk_manager = risk_manager
        self._log = log
        self._dry_run = dry_run
        self._poll_interval = poll_interval
        self._instrument = v6_config.instrument

        # ── V6 components ─────────────────────────────────────────
        self._strategy = ORBStrategy(v6_config, logger=log)

        self._context = LiveMarketContext(
            ib=ib,
            contract=contract,
            price_decimals=v6_config.price_decimals,
            state_dir=state_dir or None,
            logger=log,
        )

        self._execution = IBKRExecutionEngine(
            ib=ib,
            contract=contract,
            quantity=v6_config.qty,
            on_fill_callback=self._on_fill,
            trade_end_hour=v6_config.trade_end_hour,
            price_decimals=v6_config.price_decimals,
            dry_run=dry_run,
            logger=log,
        )

        # ── Adapter state ─────────────────────────────────────────
        self._last_poll_time: Optional[datetime] = None
        self._current_date: Optional[str] = None
        self._range_calculated: bool = False
        self._gap_calculated: bool = False

    # ── StrategyEngine protocol ───────────────────────────────────

    @property
    def pair_name(self) -> str:
        return self._instrument

    @property
    def in_trade(self) -> bool:
        return self._execution.has_position()

    @property
    def bar_count(self) -> int:
        return 0  # V6 is tick-driven, not bar-driven

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    async def on_bar(self, bar) -> None:
        """No-op. V6 is tick-driven, not bar-driven."""
        pass

    def on_price(self, price: float, now: datetime) -> None:
        """Drive the V6 strategy from V11's price tick stream.

        Called on every XAUUSD tick from the shared IBKR connection.
        Throttles to poll_interval (default 2s) to match V6's rhythm.
        """
        # ── Daily reset ───────────────────────────────────────────
        today_str = now.strftime("%Y-%m-%d")
        if today_str != self._current_date:
            self._reset_daily(today_str, now)

        # ── Throttle to poll interval ─────────────────────────────
        if self._last_poll_time is not None:
            elapsed = (now - self._last_poll_time).total_seconds()
            if elapsed < self._poll_interval:
                return
        self._last_poll_time = now

        cfg = self._v6_config

        # ── Window closed → mark done ─────────────────────────────
        if (now.hour >= cfg.trade_end_hour
                and self._strategy.state in (
                    StrategyState.IDLE, StrategyState.RANGE_READY)):
            self._strategy.state = StrategyState.DONE_TODAY
            return

        # ── Daily orchestration: Asian range ──────────────────────
        if (not self._range_calculated
                and now.hour >= cfg.range_end_hour
                and self._strategy.state == StrategyState.IDLE):
            self._calculate_range(now)

        # ── Daily orchestration: gap metrics ──────────────────────
        if (cfg.gap_filter_enabled
                and not self._gap_calculated
                and now.hour >= cfg.gap_end_hour
                and self._context.daily_range is not None):
            self._calculate_gap_metrics(now)

        # ── Check fills from execution engine ─────────────────────
        self._execution.check_fills()

        # ── Risk gate ─────────────────────────────────────────────
        # Block strategy in RANGE_READY (pre-bracket) if risk manager
        # disallows new trades. Other states must flow freely:
        #   IDLE → needs to see ticks for range setup
        #   ORDERS_PLACED → needs to monitor velocity / max pending
        #   IN_TRADE → needs to manage position (BE, time exit, EOD)
        if self._strategy.state == StrategyState.RANGE_READY:
            allowed, reason = self._risk_manager.can_trade(
                self._instrument, self.STRATEGY_NAME)
            if not allowed:
                self._log.debug(f"ORB risk gate: {reason}")
                return

        # ── Drive strategy ────────────────────────────────────────
        tick = self._get_current_tick(now)
        if tick:
            self._strategy.on_tick(tick, self._context, self._execution)

    def add_historical_bar(self, bar) -> None:
        """No-op. V6 doesn't use historical bars for warmup."""
        pass

    def get_status(self) -> dict:
        """Diagnostic status snapshot."""
        s = self._strategy
        return {
            "strategy_name": self.STRATEGY_NAME,
            "pair_name": self._instrument,
            "instrument": self._instrument,
            "state": s.state.value,
            "range": (f"{s.range.low:.{self._v6_config.price_decimals}f}-"
                      f"{s.range.high:.{self._v6_config.price_decimals}f}"
                      if s.range else None),
            "in_trade": self.in_trade,
            "direction": s.direction,
            "entry_price": s.entry_price,
            "sl_price": s.sl_price,
            "tp_price": s.tp_price,
            "range_calculated": self._range_calculated,
            "gap_calculated": self._gap_calculated,
            "current_date": self._current_date,
        }

    # ── Fill callback (from V6 execution engine) ──────────────────

    def _on_fill(self, fill: Fill):
        """Intercept V6 fills: forward to strategy, report to risk manager."""
        # Forward to V6 strategy (mirrors V6's LiveRunner.on_fill)
        self._strategy.on_fill(fill, self._context, self._execution)

        # Report to V11 risk manager
        if fill.reason == "ENTRY":
            self._risk_manager.record_trade_entry(
                self._instrument, self.STRATEGY_NAME)
            self._log.info(
                f"ORB ENTRY: {fill.direction} @ {fill.price} → "
                f"risk manager notified")

        elif fill.reason in ("SL", "TP", "BE", "MARKET", "CLOSED"):
            pnl_price = self._calc_pnl(fill.price)
            pnl_usd = round(
                pnl_price * self._v6_config.qty * self._v6_config.point_value,
                2)
            self._risk_manager.record_trade_exit(
                self._instrument, self.STRATEGY_NAME, pnl_usd)
            self._log.info(
                f"ORB EXIT: {fill.reason} @ {fill.price} "
                f"PnL=${pnl_usd:+.2f} → risk manager notified")

    def _calc_pnl(self, exit_price: float) -> float:
        """Raw price-unit PnL (not USD)."""
        s = self._strategy
        if s.direction == "LONG":
            return exit_price - s.entry_price
        elif s.direction == "SHORT":
            return s.entry_price - exit_price
        return 0.0

    # ── Daily orchestration ───────────────────────────────────────

    def _reset_daily(self, today_str: str, now: datetime):
        """Reset for a new trading day."""
        self._log.info(f"ORB: Daily reset for {today_str}")
        self._current_date = today_str
        self._range_calculated = False
        self._gap_calculated = False

        # Cancel lingering orders from previous day
        if self._strategy.state in (
                StrategyState.ORDERS_PLACED, StrategyState.IN_TRADE):
            self._execution.cancel_orb_brackets()
            if self._execution.has_position():
                self._execution.close_at_market()

        # Reset V6 strategy
        self._strategy.reset_for_new_day()

        # Reset execution trade date
        self._execution.set_trade_date(today_str)

        # Clear context daily caches
        self._context.daily_range = None
        self._context.daily_range_date = None
        self._context._current_gap_metrics = None
        self._context._gap_metrics_date = None

    def _calculate_range(self, now: datetime):
        """Calculate Asian range from IBKR historical bars."""
        cfg = self._v6_config
        self._log.info(
            f"ORB: Calculating Asian range "
            f"({cfg.range_start_hour}-{cfg.range_end_hour} UTC)")

        rng = self._context.calculate_daily_range(
            cfg.range_start_hour, cfg.range_end_hour)
        if rng:
            self._context.set_daily_range(rng, now)
            self._range_calculated = True
        else:
            self._log.warning("ORB: Failed to calculate Asian range")

    def _calculate_gap_metrics(self, now: datetime):
        """Calculate gap metrics from IBKR bars and inject into context."""
        cfg = self._v6_config
        overnight_range = (self._context.daily_range.size
                           if self._context.daily_range else 0.0)

        gap_vol, gap_range = self._context.calculate_gap_metrics_from_ibkr(
            cfg.gap_start_hour, cfg.gap_end_hour, overnight_range)

        self._context.inject_gap_data(
            now.date(), gap_vol, gap_range,
            cfg.gap_vol_percentile, cfg.gap_range_percentile,
            cfg.gap_rolling_days)
        self._gap_calculated = True

    # ── Tick construction ─────────────────────────────────────────

    def _get_current_tick(self, now: datetime) -> Optional[Tick]:
        """Create a V6 Tick from the context's streaming data."""
        if self._context._last_bid and self._context._last_ask:
            return Tick(
                timestamp=now,
                bid=self._context._last_bid,
                ask=self._context._last_ask,
            )
        return None

    # ── Lifecycle ─────────────────────────────────────────────────

    def cleanup(self):
        """Graceful shutdown: cancel orders, close position, unsubscribe."""
        self._log.info("ORB: Adapter cleanup")
        if self._strategy.state == StrategyState.ORDERS_PLACED:
            self._execution.cancel_orb_brackets()
        if self._execution.has_position():
            self._execution.close_at_market()
        self._context.disconnect()
