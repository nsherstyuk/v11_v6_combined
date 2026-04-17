"""
ORBAdapter — Bridges V6 ORB strategy into V11's MultiStrategyRunner.

Design (V11_DESIGN.md §11, Phase 5):
    V6 ORB is tick-driven (polled every 2s). V11 is bar-driven.
    This adapter translates between the two models:

    1. V6's LiveMarketContext manages its own IBKR tick subscription
       for velocity calculation and price buffering.
    2. on_price() throttles to V6's poll interval (2s) and drives
       strategy.on_tick() + execution.check_fills().
    3. on_bar() evaluates the LLM gate when pending (async context needed).
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
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..v6_orb.orb_strategy import ORBStrategy, StrategyState
from ..v6_orb.config import StrategyConfig as V6StrategyConfig
from ..v6_orb.market_event import Tick, Fill
from ..v6_orb.live_context import LiveMarketContext
from ..v6_orb.ibkr_executor import IBKRExecutionEngine
from .risk_manager import RiskManager
from ..llm.models import TrendContext


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
        llm_filter=None,
        llm_confidence_threshold: int = 75,
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
            llm_filter: Optional LLM filter for ORB gate. None = skip gate.
            llm_confidence_threshold: Minimum confidence to approve (default 75).
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

        # ── LLM gate (optional) ───────────────────────────────────
        self._llm_filter = llm_filter
        self._llm_confidence_threshold = llm_confidence_threshold
        self._daily_bars: list = []
        self._hourly_bars: list = []  # 4-hour bars for last 5 days (set by run_live)
        self._llm_evaluated_today: bool = False
        self._llm_gate_pending: bool = False
        self._llm_gate_time: Optional[datetime] = None

        # ── Slow ATR for regime context ────────────────────────────
        self._slow_atr: float = 0.0
        self._slow_atr_count: int = 0
        self._slow_atr_prev_close: float = 0.0
        self._slow_atr_period: int = 1440  # 1 day of 1-min bars

        # ── Adapter state ─────────────────────────────────────────
        self._last_poll_time: Optional[datetime] = None
        self._current_date: Optional[str] = None
        self._range_calculated: bool = False
        self._gap_calculated: bool = False

        # ── Bar-level velocity (replaces tick-stream velocity) ─────
        # V6's LiveMarketContext.get_velocity() counts ticks from its
        # own tick_buffer, which receives IBKR snapshot ticks at ~60/min
        # (constant regardless of market activity). The V6 threshold of
        # 168 was calibrated on bar-level tick_count data (mean 144/min,
        # 30.7% of minutes exceed 168). We override get_velocity on the
        # context instance to use bar tick_counts instead.
        self._bar_buffer: deque = deque(maxlen=60)  # 60 bars = 1 hour
        _adapter = self
        self._context.get_velocity = (
            lambda lookback, ts: _adapter._compute_bar_velocity(lookback, ts)
        )

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

    def on_price(self, price: float, now: datetime) -> None:
        """Drive the V6 strategy from V11's price tick stream.

        Called on every XAUUSD tick from the shared IBKR connection.
        Throttles to poll_interval (default 2s) to match V6's rhythm.
        """
        # ── Update slow ATR from tick ──────────────────────────────
        self._update_slow_atr_tick(price)

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

        # ── Window closed -- mark done ─────────────────────────────
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
        #   IDLE -- needs to see ticks for range setup
        #   ORDERS_PLACED -- needs to monitor velocity / max pending
        #   IN_TRADE -- needs to manage position (BE, time exit, EOD)
        if self._strategy.state == StrategyState.RANGE_READY:
            allowed, reason = self._risk_manager.can_trade(
                self._instrument, self.STRATEGY_NAME)
            if not allowed:
                self._log.info(f"ORB risk gate BLOCKED: {reason}")
                return

            # ── LLM gate (once per day, deferred to on_bar for async) ─
            if not self._llm_evaluated_today and not self._llm_gate_pending:
                self._llm_gate_pending = True
                self._llm_gate_time = now
                self._log.info("ORB: LLM gate pending (will evaluate on next bar)")
                return  # Hold at RANGE_READY until on_bar evaluates
            if self._llm_gate_pending:
                return  # Still waiting for on_bar to evaluate

        # ── Stale breakout check ────────────────────────────────────
        # V6 only checks this AFTER velocity passes, which creates a
        # deadlock: velocity never passes → stale breakout never fires.
        # Check here so we don't sit in RANGE_READY forever with price
        # already outside the range.
        if (self._strategy.state == StrategyState.RANGE_READY
                and self._llm_evaluated_today
                and self._strategy.range):
            mid_price = None
            if self._context._last_bid and self._context._last_ask:
                mid_price = (self._context._last_bid + self._context._last_ask) / 2
            if mid_price is not None:
                r = self._strategy.range
                if mid_price > r.high or mid_price < r.low:
                    self._log.info(
                        f"ORB stale breakout: price={mid_price:.{self._v6_config.price_decimals}f} "
                        f"outside range "
                        f"[{r.low:.{self._v6_config.price_decimals}f}-"
                        f"{r.high:.{self._v6_config.price_decimals}f}], "
                        f"skipping (velocity never reached)")
                    self._strategy.state = StrategyState.DONE_TODAY
                    return

        # ── Drive strategy ────────────────────────────────────────
        # Log state transitions for diagnostics
        state_before = self._strategy.state
        tick = self._get_current_tick(now)
        if tick is None:
            # Log tick failure periodically (every 60s to avoid spam)
            if (not hasattr(self, '_last_tick_warn') or
                    (now - self._last_tick_warn).total_seconds() > 60):
                self._log.warning(
                    f"ORB: No tick available (bid={self._context._last_bid}, "
                    f"ask={self._context._last_ask})")
                self._last_tick_warn = now
            return

        self._strategy.on_tick(tick, self._context, self._execution)

        state_after = self._strategy.state
        if state_after != state_before:
            self._log.info(
                f"ORB state: {state_before.value} -> {state_after.value}")

    async def on_bar(self, bar) -> None:
        """Store bar for velocity calculation; evaluate LLM gate when pending."""
        # Replace BarAggregator's snapshot tick_count (~60/min constant) with
        # real market tick count from IBKR historical data. This is required for
        # the velocity filter threshold (168) to work as calibrated.
        bar = await self._enrich_bar_tick_count(bar)
        self._bar_buffer.append(bar)

        if not self._llm_gate_pending:
            return

        self._llm_gate_pending = False
        self._llm_evaluated_today = True
        now = self._llm_gate_time or bar.timestamp

        approved = await self._evaluate_orb_signal(now)
        if not approved:
            self._strategy.state = StrategyState.DONE_TODAY
            self._log.info("ORB state: RANGE_READY -> DONE_TODAY (LLM rejected)")
        else:
            self._log.info("ORB: LLM gate passed — brackets eligible")

    def add_historical_bar(self, bar) -> None:
        """No-op. V6 doesn't use historical bars for warmup."""
        pass

    def get_status(self) -> dict:
        """Diagnostic status snapshot."""
        s = self._strategy
        cfg = self._v6_config

        # Current price from tick stream
        current_price = None
        if self._context._last_bid and self._context._last_ask:
            current_price = (self._context._last_bid + self._context._last_ask) / 2

        # Distance to range boundaries (if range exists and we have price)
        dist_to_high = None
        dist_to_low = None
        if s.range and current_price:
            dist_to_high = current_price - s.range.high
            dist_to_low = current_price - s.range.low

        # Velocity info for diagnostics (bar-level, reflects real market activity)
        now_utc = datetime.now(tz=timezone.utc).replace(microsecond=0)
        velocity = 0.0
        tick_count = 0
        if cfg.velocity_filter_enabled:
            velocity = self._compute_bar_velocity(
                cfg.velocity_lookback_minutes, now_utc)
            cutoff = now_utc - timedelta(minutes=cfg.velocity_lookback_minutes)
            tick_count = sum(
                b.tick_count for b in self._bar_buffer
                if b.timestamp >= cutoff)

        # Resting order info
        has_resting = self._execution.has_resting_entries()
        order_ids = self._execution.get_order_ids()

        return {
            "strategy_name": self.STRATEGY_NAME,
            "pair_name": self._instrument,
            "instrument": self._instrument,
            "state": s.state.value,
            "range": (f"{s.range.low:.{cfg.price_decimals}f}-"
                      f"{s.range.high:.{cfg.price_decimals}f}"
                      if s.range else None),
            "in_trade": self.in_trade,
            "direction": s.direction,
            "entry_price": s.entry_price,
            "sl_price": s.sl_price,
            "tp_price": s.tp_price,
            "range_calculated": self._range_calculated,
            "gap_calculated": self._gap_calculated,
            "current_date": self._current_date,
            "current_price": current_price,
            "dist_to_high": dist_to_high,
            "dist_to_low": dist_to_low,
            "has_resting_entries": has_resting,
            "buy_entry_id": order_ids.get("buy_entry_id", 0),
            "sell_entry_id": order_ids.get("sell_entry_id", 0),
            "velocity": velocity,
            "velocity_threshold": cfg.velocity_threshold if cfg.velocity_filter_enabled else 0,
            "tick_count_3m": tick_count,
            "range_start_hour": cfg.range_start_hour,
            "range_end_hour": cfg.range_end_hour,
            "trade_end_hour": cfg.trade_end_hour,
            "llm_evaluated": self._llm_evaluated_today,
            "llm_gate_pending": self._llm_gate_pending,
            "llm_threshold": self._llm_confidence_threshold,
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
                f"ORB ENTRY: {fill.direction} @ {fill.price} -- "
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
                f"PnL=${pnl_usd:+.2f} -- risk manager notified")

            # Auto-assess ORB decision
            self._assess_exit(fill, pnl_usd)

    def _calc_pnl(self, exit_price: float) -> float:
        """Raw price-unit PnL (not USD)."""
        s = self._strategy
        if s.direction == "LONG":
            return exit_price - s.entry_price
        elif s.direction == "SHORT":
            return s.entry_price - exit_price
        return 0.0

    def _assess_exit(self, fill: Fill, pnl_usd: float) -> None:
        """Auto-assess the LLM decision after an ORB trade exits.

        Uses the public LLMFilter protocol — no-op for stateless filters.
        """
        if not self._llm_filter:
            return

        rng = self._strategy.range
        range_high = rng.high if rng else 0
        range_low = rng.low if rng else 0

        self._llm_filter.record_orb_outcome(
            instrument=self._instrument,
            decision_date=(
                fill.timestamp.strftime("%Y-%m-%d") if fill.timestamp else ""),
            approved=self._llm_evaluated_today,  # True = LLM allowed brackets
            entry_price=self._strategy.entry_price,
            exit_price=fill.price,
            exit_reason=fill.reason,
            pnl=pnl_usd,
            range_high=range_high,
            range_low=range_low,
        )
        self._llm_filter.refresh_feedback()

    # ── LLM gate ──────────────────────────────────────────────────

    async def _evaluate_orb_signal(self, now: datetime) -> bool:
        """Evaluate ORB setup via LLM. Returns True if approved or no LLM.

        Called once per day when state first reaches RANGE_READY.
        """
        if self._llm_filter is None:
            return True

        if not hasattr(self._llm_filter, 'evaluate_orb_signal'):
            return True  # Legacy filters without ORB support

        rng = self._strategy.range
        if rng is None:
            return True

        from ..llm.models import ORBSignalContext, DailyBarData, TrendContext

        mid = (rng.high + rng.low) / 2
        size = rng.high - rng.low
        size_pct = (size / mid * 100) if mid > 0 else 0.0

        # Compute range_vs_avg from daily bars
        if self._daily_bars:
            daily_ranges = [b.h - b.l for b in self._daily_bars if hasattr(b, 'h')]
            avg_range = sum(daily_ranges) / len(daily_ranges) if daily_ranges else size
            range_vs_avg = size / avg_range if avg_range > 0 else 1.0
        else:
            range_vs_avg = 1.0

        # Current price from context
        price = self._context.get_current_price(now)
        if price is None:
            price = mid

        # Session label
        hour = now.hour
        if 0 <= hour < 8:
            session = "ASIAN_CLOSE"
        elif 8 <= hour < 13:
            session = "LONDON"
        elif 13 <= hour < 17:
            session = "LONDON_NY_OVERLAP"
        else:
            session = "NY"

        # ATR regime: use slow ATR if available, else rough proxy
        if self._slow_atr > 0:
            # Fast ATR proxy: recent range / slow_atr
            fast_atr_proxy = size  # range size as proxy for fast ATR
            atr_regime = fast_atr_proxy / self._slow_atr if self._slow_atr > 0 else 1.0
        else:
            atr_regime = 1.0

        # Compute trend context from daily bars
        trend_context = self._compute_trend_context(price)

        context = ORBSignalContext(
            instrument=self._instrument,
            range_high=rng.high,
            range_low=rng.low,
            range_size=round(size, 2),
            range_size_pct=round(size_pct, 3),
            range_vs_avg=round(range_vs_avg, 2),
            atr_regime=round(atr_regime, 2),
            current_price=price,
            distance_from_high=round(price - rng.high, 2),
            distance_from_low=round(price - rng.low, 2),
            session=session,
            day_of_week=now.strftime("%A"),
            current_time_utc=now.isoformat(),
            recent_bars=[],
            daily_bars=list(self._daily_bars),
            hourly_bars=list(self._hourly_bars),
            trend_context=trend_context,
        )

        self._log.info(
            f"ORB LLM gate: evaluating range {rng.low:.2f}-{rng.high:.2f} "
            f"(size={size:.2f}, vs_avg={range_vs_avg:.1f}x)")

        decision = await self._llm_filter.evaluate_orb_signal(context)

        if not decision.approved:
            self._log.info(
                f"ORB LLM REJECTED: conf={decision.confidence} "
                f"reason={decision.reasoning[:100]}")
            return False

        # Mechanical fallback (timeout) bypasses confidence check — intent is to approve
        is_fallback = "llm_fallback" in (decision.risk_flags or [])
        if not is_fallback and decision.confidence < self._llm_confidence_threshold:
            self._log.info(
                f"ORB LLM confidence {decision.confidence} "
                f"< threshold {self._llm_confidence_threshold}")
            return False

        if is_fallback:
            self._log.info(
                f"ORB LLM timed out — proceeding mechanically (fallback)")
            return True

        self._log.info(
            f"ORB LLM APPROVED: conf={decision.confidence} "
            f"reason={decision.reasoning[:100]}")
        return True

    def _compute_trend_context(self, current_price: float) -> Optional[TrendContext]:
        """Compute trend context from daily bars for LLM."""
        if len(self._daily_bars) < 5:
            return None

        bars = self._daily_bars
        closes = [b.c for b in bars]

        # SMA20 (or whatever we have)
        sma_period = min(20, len(closes))
        sma = sum(closes[-sma_period:]) / sma_period

        # SMA slope: compare SMA to SMA 3 bars ago
        if len(closes) >= sma_period + 3:
            sma_prev = sum(closes[-sma_period - 3:-3]) / sma_period
            sma_slope = (sma - sma_prev) / sma_prev * 100 if sma_prev > 0 else 0.0
        else:
            sma_slope = 0.0

        # Consecutive up/down days
        consecutive_up = 0
        consecutive_down = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                consecutive_up += 1
            else:
                break
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                consecutive_down += 1
            else:
                break

        # Days since 20-day high/low
        highs = [b.h for b in bars[-sma_period:]]
        lows = [b.l for b in bars[-sma_period:]]
        max_high = max(highs)
        min_low = min(lows)
        days_since_high = 0
        days_since_low = 0
        for i in range(len(highs) - 1, -1, -1):
            if highs[i] >= max_high - 0.01:
                break
            days_since_high += 1
        for i in range(len(lows) - 1, -1, -1):
            if lows[i] <= min_low + 0.01:
                break
            days_since_low += 1

        # Position vs SMA (string label: "above", "below", "neutral")
        position_vs_sma = ((current_price - sma) / sma * 100) if sma > 0 else 0.0
        if position_vs_sma > 0.1:
            position_label = "above"
        elif position_vs_sma < -0.1:
            position_label = "below"
        else:
            position_label = "neutral"

        return TrendContext(
            sma20_slope=round(sma_slope, 4),
            consecutive_up_days=consecutive_up,
            consecutive_down_days=consecutive_down,
            days_since_high=days_since_high,
            days_since_low=days_since_low,
            position_vs_20d_sma=position_label,
        )

    # ── Daily orchestration ───────────────────────────────────────

    def _reset_daily(self, today_str: str, now: datetime):
        """Reset for a new trading day."""
        self._log.info(f"ORB: Daily reset for {today_str}")
        self._current_date = today_str
        self._range_calculated = False
        self._gap_calculated = False
        self._llm_evaluated_today = False
        self._llm_gate_pending = False

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

    # ── Bar-level velocity ────────────────────────────────────────

    async def _enrich_bar_tick_count(self, bar):
        """Replace BarAggregator snapshot tick_count with real IBKR tick count.

        BarAggregator counts on_price() calls (~60/min regardless of activity).
        IBKR MIDPOINT historical bars include real market tick activity in the
        volume field, matching the distribution the velocity threshold was
        calibrated on (mean ~144, variance 1-933).

        Falls back to original bar if the IBKR request fails or returns no data.
        """
        from dataclasses import replace as dc_replace
        try:
            ibkr_bars = await self._ib.reqHistoricalDataAsync(
                self._contract,
                endDateTime='',
                durationStr='60 S',
                barSizeSetting='1 min',
                whatToShow='MIDPOINT',
                useRTH=False,
                formatDate=2,
            )
            if ibkr_bars:
                tc = int(getattr(ibkr_bars[-1], 'volume', 0))
                if tc > 0:
                    return dc_replace(bar, tick_count=tc)
        except Exception as exc:
            self._log.debug(f"ORB: tick_count enrichment failed: {exc}")
        return bar

    def _compute_bar_velocity(self, lookback_minutes: int,
                              now: Optional[datetime] = None) -> float:
        """Velocity (ticks/min) from recent bar tick_counts.

        Replaces LiveMarketContext.get_velocity() which counts IBKR snapshot
        ticks (~60/min constant). Bar tick_counts reflect actual market activity
        and match the distribution V6's 168 threshold was calibrated on.

        Args:
            lookback_minutes: Window size (matches V6 velocity_lookback_minutes).
            now: Reference time (default: UTC now). Injected for testability.
        """
        if now is None:
            now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=lookback_minutes)
        recent = [b for b in self._bar_buffer if b.timestamp >= cutoff]
        if not recent:
            return 0.0
        return sum(b.tick_count for b in recent) / max(lookback_minutes, 1)

    # ── Slow ATR ──────────────────────────────────────────────────

    def _update_slow_atr_tick(self, price: float) -> None:
        """Update slow ATR from tick prices (approximation).

        Uses price movement as a proxy for true range since we don't
        have bar OHLC in the tick-driven adapter. This is an approximation
        but sufficient for regime context (elevated vs normal vs depressed).
        """
        if self._slow_atr_prev_close > 0:
            # Approximate true range as absolute price change
            tr = abs(price - self._slow_atr_prev_close)
        else:
            tr = 0.0

        self._slow_atr_prev_close = price

        if tr > 0:
            if self._slow_atr_count < self._slow_atr_period:
                self._slow_atr_count += 1
                self._slow_atr = self._slow_atr + (tr - self._slow_atr) / self._slow_atr_count
            else:
                alpha = 2.0 / (self._slow_atr_period + 1)
                self._slow_atr = self._slow_atr * (1 - alpha) + tr * alpha
