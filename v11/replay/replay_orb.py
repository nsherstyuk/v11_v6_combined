"""ReplayORB — Bar-driven ORB replay engine.

Implements V6's MarketContext and ExecutionEngine interfaces using
1-minute bar data instead of live IBKR ticks. This allows the ORB
strategy to be replayed historically alongside Darvas/LevelRetest.

Architecture:
    - ReplayORBMarketContext: builds Asian range from bars, tracks velocity
    - ReplayORBExecutionEngine: simulates bracket fills from bar high/low
    - ReplayORBAdapter: wires strategy + context + execution + LLM gate
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, List, Callable

from ..v6_orb.orb_strategy import ORBStrategy, StrategyState
from ..v6_orb.config import StrategyConfig as V6StrategyConfig
from ..v6_orb.market_event import Tick, Fill, RangeInfo, GapMetrics
from ..v6_orb.interfaces import MarketContext, ExecutionEngine
from ..core.types import Bar as V11Bar
from ..llm.models import ORBSignalContext, DailyBarData, HourlyBarData, TrendContext


class ReplayORBMarketContext(MarketContext):
    """Bar-based MarketContext for ORB replay.

    Accumulates 1-min bars and derives:
    - Asian range (high/low during range hours)
    - Velocity (bars per minute as proxy for tick velocity)
    - Gap metrics (stubbed — not critical for LLM replay)
    - Current price (latest bar close)
    """

    def __init__(self, v6_config: V6StrategyConfig, log: logging.Logger):
        self._config = v6_config
        self._log = log

        # Bar buffer (1 day = 1440 bars)
        self._bars: deque[V11Bar] = deque(maxlen=1440)

        # Daily range cache
        self._daily_range: Optional[RangeInfo] = None
        self._daily_range_date: Optional[str] = None

        # Slow ATR for regime context
        self._slow_atr: float = 0.0
        self._slow_atr_count: int = 0
        self._slow_atr_prev_close: float = 0.0
        self._slow_atr_period: int = 1440  # 1 day

    def add_bar(self, bar: V11Bar) -> None:
        """Add a completed 1-min bar."""
        self._bars.append(bar)
        self._update_slow_atr(bar)

    def get_velocity(self, lookback_minutes: int, current_time: datetime) -> float:
        """Estimate velocity as bars-per-minute (proxy for tick velocity).

        In live, velocity = ticks/minute. In replay, we use bars/minute
        scaled by a typical tick-per-bar ratio (~10 for XAUUSD).
        """
        cutoff = current_time - timedelta(minutes=lookback_minutes)
        count = sum(1 for b in self._bars if b.timestamp >= cutoff)
        # Scale: live velocity threshold is ~200 ticks/3min ≈ 67 ticks/min
        # Each 1-min bar has ~10 ticks, so 1 bar/min ≈ 10 ticks/min
        # To match velocity_threshold=200 with 3-min lookback:
        # 200 ticks / 3 min / 10 ticks_per_bar ≈ 6.7 bars in 3 min
        # Simplify: just return count * 10 as tick-equivalent velocity
        return count * 10.0

    def get_asian_range(self, start_hour: int, end_hour: int,
                        current_time: datetime) -> Optional[RangeInfo]:
        """Compute Asian range from buffered bars."""
        today_str = current_time.strftime("%Y-%m-%d")

        # Cache hit
        if self._daily_range is not None and self._daily_range_date == today_str:
            return self._daily_range

        # Find bars within range hours for today
        range_bars = [
            b for b in self._bars
            if b.timestamp.strftime("%Y-%m-%d") == today_str
            and start_hour <= b.timestamp.hour < end_hour
        ]

        if not range_bars:
            return None

        high = max(b.high for b in range_bars)
        low = min(b.low for b in range_bars)
        start_time = range_bars[0].timestamp
        end_time = range_bars[-1].timestamp

        rng = RangeInfo(high=high, low=low, start_time=start_time, end_time=end_time)
        self._daily_range = rng
        self._daily_range_date = today_str
        return rng

    def time_is_in_trade_window(self, current_time: datetime,
                                start_hour: int, end_hour: int) -> bool:
        """Check if current time is within trading window."""
        return start_hour <= current_time.hour < end_hour

    def get_current_price(self, current_time: datetime) -> Optional[float]:
        """Return latest bar close as current price."""
        if self._bars:
            return self._bars[-1].close
        return None

    def get_gap_metrics(self, current_time: datetime,
                        gap_start_hour: int, gap_end_hour: int,
                        vol_percentile: float, range_percentile: float,
                        rolling_days: int) -> Optional[GapMetrics]:
        """Stub: gap metrics not critical for LLM replay evaluation."""
        # Return a passing result so the strategy doesn't skip days
        return GapMetrics(
            gap_volatility=0.001,
            gap_range=1.0,
            vol_passes=True,
            range_passes=True,
        )

    @property
    def slow_atr(self) -> float:
        """Current slow ATR value for regime context."""
        return self._slow_atr

    def get_daily_bars(self, n: int = 20) -> List[DailyBarData]:
        """Get last N daily bars for LLM context."""
        # Group bars by date, take OHLC per day
        daily: dict = {}
        for b in self._bars:
            date_str = b.timestamp.strftime("%Y-%m-%d")
            if date_str not in daily:
                daily[date_str] = {"o": b.open, "h": b.high, "l": b.low, "c": b.close}
            else:
                daily[date_str]["h"] = max(daily[date_str]["h"], b.high)
                daily[date_str]["l"] = min(daily[date_str]["l"], b.low)
                daily[date_str]["c"] = b.close

        # Take last N days
        sorted_dates = sorted(daily.keys())[-n:]
        return [
            DailyBarData(date=d, o=daily[d]["o"], h=daily[d]["h"],
                         l=daily[d]["l"], c=daily[d]["c"])
            for d in sorted_dates
        ]

    def get_hourly_bars(self, n_days: int = 5) -> List[HourlyBarData]:
        """Get 4-hour bars for last N days for LLM context."""
        # Group bars by date + 4-hour session
        sessions: dict = {}
        for b in self._bars:
            date_str = b.timestamp.strftime("%Y-%m-%d")
            session_idx = b.timestamp.hour // 4  # 0-5
            session_label = f"{session_idx * 4:02d}-{(session_idx + 1) * 4:02d}"
            key = (date_str, session_label)
            if key not in sessions:
                sessions[key] = {"o": b.open, "h": b.high, "l": b.low, "c": b.close}
            else:
                sessions[key]["h"] = max(sessions[key]["h"], b.high)
                sessions[key]["l"] = min(sessions[key]["l"], b.low)
                sessions[key]["c"] = b.close

        # Take last N days of sessions
        sorted_keys = sorted(sessions.keys())
        if sorted_keys:
            last_date = sorted_keys[-1][0]
            cutoff_idx = 0
            dates_seen = set()
            for i, (d, s) in enumerate(reversed(sorted_keys)):
                dates_seen.add(d)
                if len(dates_seen) > n_days:
                    cutoff_idx = len(sorted_keys) - i
                    break
            sorted_keys = sorted_keys[cutoff_idx:]

        return [
            HourlyBarData(date=d, session=s, o=sessions[(d, s)]["o"],
                          h=sessions[(d, s)]["h"], l=sessions[(d, s)]["l"],
                          c=sessions[(d, s)]["c"])
            for d, s in sorted_keys
        ]

    def get_trend_context(self) -> Optional[TrendContext]:
        """Compute derived trend features from daily bars."""
        daily_bars = self.get_daily_bars(20)
        if len(daily_bars) < 5:
            return None

        closes = [b.c for b in daily_bars]
        highs = [b.h for b in daily_bars]
        lows = [b.l for b in daily_bars]
        opens = [b.o for b in daily_bars]

        # 20-day SMA (or whatever we have)
        sma = sum(closes) / len(closes)
        current_price = closes[-1]

        # SMA slope: compare SMA of last 5 days vs prior 5 days
        if len(closes) >= 10:
            recent_sma = sum(closes[-5:]) / 5
            prior_sma = sum(closes[-10:-5]) / 5
            sma_slope = recent_sma - prior_sma
        else:
            sma_slope = 0.0

        # Consecutive up/down days
        consec_up = 0
        consec_down = 0
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] > opens[i]:
                if consec_down > 0:
                    break
                consec_up += 1
            elif closes[i] < opens[i]:
                if consec_up > 0:
                    break
                consec_down += 1
            else:
                break

        # Days since 20-day high/low
        max_high = max(highs)
        min_low = min(lows)
        days_since_high = 0
        days_since_low = 0
        for i in range(len(highs) - 1, -1, -1):
            if highs[i] == max_high:
                days_since_high = len(highs) - 1 - i
                break
        for i in range(len(lows) - 1, -1, -1):
            if lows[i] == min_low:
                days_since_low = len(lows) - 1 - i
                break

        # Position vs SMA
        if current_price > sma * 1.001:
            position = "above"
        elif current_price < sma * 0.999:
            position = "below"
        else:
            position = "neutral"

        return TrendContext(
            sma20_slope=round(sma_slope, 2),
            consecutive_up_days=consec_up,
            consecutive_down_days=consec_down,
            days_since_high=days_since_high,
            days_since_low=days_since_low,
            position_vs_20d_sma=position,
        )

    def _update_slow_atr(self, bar: V11Bar) -> None:
        """Update slow ATR (1-day period) for regime context."""
        if self._slow_atr_prev_close > 0:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._slow_atr_prev_close),
                abs(bar.low - self._slow_atr_prev_close),
            )
        else:
            tr = bar.high - bar.low

        self._slow_atr_prev_close = bar.close

        if self._slow_atr_count < self._slow_atr_period:
            self._slow_atr_count += 1
            self._slow_atr = self._slow_atr + (tr - self._slow_atr) / self._slow_atr_count
        else:
            alpha = 2.0 / (self._slow_atr_period + 1)
            self._slow_atr = self._slow_atr * (1 - alpha) + tr * alpha

    def reset_daily(self) -> None:
        """Reset daily cache."""
        self._daily_range = None
        self._daily_range_date = None


class ReplayORBExecutionEngine(ExecutionEngine):
    """Simulated execution engine for ORB replay.

    Instead of placing real IBKR orders, tracks bracket levels
    and simulates fills when bar high/low crosses the levels.
    """

    def __init__(self, v6_config: V6StrategyConfig, log: logging.Logger,
                 on_fill_callback: Optional[Callable] = None):
        self._config = v6_config
        self._log = log
        self._on_fill = on_fill_callback

        # Bracket state
        self._brackets_active: bool = False
        self._long_entry: float = 0.0
        self._short_entry: float = 0.0
        self._long_sl: float = 0.0
        self._long_tp: float = 0.0
        self._short_sl: float = 0.0
        self._short_tp: float = 0.0

        # Position state
        self._has_position: bool = False
        self._position_direction: Optional[str] = None
        self._entry_price: float = 0.0
        self._sl_price: float = 0.0
        self._tp_price: float = 0.0

    def set_orb_brackets(self, range_info: RangeInfo, rr_ratio: float) -> bool:
        """Set bracket levels from range info. Returns True (always succeeds in replay)."""
        d = self._config.price_decimals
        rs = range_info.size
        self._long_entry = range_info.high
        self._short_entry = range_info.low
        self._long_sl = range_info.low
        self._long_tp = round(range_info.high + rr_ratio * rs, d)
        self._short_sl = range_info.high
        self._short_tp = round(range_info.low - rr_ratio * rs, d)
        self._brackets_active = True
        self._log.info(
            f"ORB brackets set: LONG@{self._long_entry:.{d}f} "
            f"SL={self._long_sl:.{d}f} TP={self._long_tp:.{d}f} | "
            f"SHORT@{self._short_entry:.{d}f} "
            f"SL={self._short_sl:.{d}f} TP={self._short_tp:.{d}f}")
        return True

    def cancel_orb_brackets(self):
        """Cancel resting entry brackets."""
        if self._brackets_active and not self._has_position:
            self._brackets_active = False
            self._log.info("ORB brackets cancelled")

    def close_at_market(self):
        """Close position at current price (simulated)."""
        # Will be handled by check_bar_fills on next bar
        self._log.info("ORB: close_at_market requested")

    def modify_sl(self, new_sl_price: float):
        """Modify stop-loss price."""
        self._sl_price = new_sl_price
        self._log.info(f"ORB SL modified to {new_sl_price:.{self._config.price_decimals}f}")

    def has_position(self) -> bool:
        return self._has_position

    def has_resting_entries(self) -> bool:
        return self._brackets_active and not self._has_position

    def check_bar_fills(self, bar: V11Bar, strategy: ORBStrategy) -> Optional[Fill]:
        """Check if a bar triggers any fills. Call once per bar.

        Returns a Fill if triggered, None otherwise.
        Priority: entry fills first, then exit fills.
        """
        d = self._config.price_decimals
        ts = bar.timestamp

        # ── Check entry fills (if brackets active, no position) ──
        if self._brackets_active and not self._has_position:
            # Long entry: bar high >= long entry level
            if bar.high >= self._long_entry:
                fill = Fill(
                    timestamp=ts,
                    price=self._long_entry,
                    direction="LONG",
                    reason="ENTRY",
                )
                self._has_position = True
                self._position_direction = "LONG"
                self._entry_price = self._long_entry
                self._sl_price = self._long_sl
                self._tp_price = self._long_tp
                self._brackets_active = False
                self._log.info(
                    f"ORB ENTRY LONG @ {self._long_entry:.{d}f} "
                    f"SL={self._long_sl:.{d}f} TP={self._long_tp:.{d}f}")
                if self._on_fill:
                    self._on_fill(fill)
                return fill

            # Short entry: bar low <= short entry level
            if bar.low <= self._short_entry:
                fill = Fill(
                    timestamp=ts,
                    price=self._short_entry,
                    direction="SHORT",
                    reason="ENTRY",
                )
                self._has_position = True
                self._position_direction = "SHORT"
                self._entry_price = self._short_entry
                self._sl_price = self._short_sl
                self._tp_price = self._short_tp
                self._brackets_active = False
                self._log.info(
                    f"ORB ENTRY SHORT @ {self._short_entry:.{d}f} "
                    f"SL={self._short_sl:.{d}f} TP={self._short_tp:.{d}f}")
                if self._on_fill:
                    self._on_fill(fill)
                return fill

        # ── Check exit fills (if in position) ──
        if self._has_position:
            # Check SL hit
            if self._position_direction == "LONG" and bar.low <= self._sl_price:
                fill = Fill(
                    timestamp=ts,
                    price=self._sl_price,
                    direction="LONG",
                    reason="SL",
                )
                self._close_position(fill)
                return fill

            if self._position_direction == "SHORT" and bar.high >= self._sl_price:
                fill = Fill(
                    timestamp=ts,
                    price=self._sl_price,
                    direction="SHORT",
                    reason="SL",
                )
                self._close_position(fill)
                return fill

            # Check TP hit
            if self._position_direction == "LONG" and bar.high >= self._tp_price:
                fill = Fill(
                    timestamp=ts,
                    price=self._tp_price,
                    direction="LONG",
                    reason="TP",
                )
                self._close_position(fill)
                return fill

            if self._position_direction == "SHORT" and bar.low <= self._tp_price:
                fill = Fill(
                    timestamp=ts,
                    price=self._tp_price,
                    direction="SHORT",
                    reason="TP",
                )
                self._close_position(fill)
                return fill

            # Check market close request (from close_at_market)
            # Handled by the strategy via EOD/time_exit → close_at_market
            # We simulate by closing at bar close
            if strategy.state == StrategyState.IN_TRADE:
                # Check if strategy wants to close (EOD, time exit)
                # The strategy calls close_at_market() which we handle here
                pass

        return None

    def force_close_at(self, price: float, bar: V11Bar) -> Optional[Fill]:
        """Force close position at given price (for DAILY_RESET, EOD)."""
        if not self._has_position:
            return None

        fill = Fill(
            timestamp=bar.timestamp,
            price=price,
            direction=self._position_direction or "LONG",
            reason="MARKET",
        )
        self._close_position(fill)
        return fill

    def _close_position(self, fill: Fill) -> None:
        """Close position and notify callback."""
        self._has_position = False
        self._position_direction = None
        self._brackets_active = False
        if self._on_fill:
            self._on_fill(fill)

    def reset_daily(self) -> None:
        """Reset for new trading day."""
        self._brackets_active = False
        self._has_position = False
        self._position_direction = None


class ReplayORBAdapter:
    """Replay-compatible ORB adapter.

    Satisfies the same protocol as ORBAdapter but uses bars instead of
    live IBKR ticks. Drives the V6 ORBStrategy with synthetic ticks
    derived from bar data, and simulates fills from bar high/low.

    Also supports the LLM gate for ORB signal evaluation.
    """

    STRATEGY_NAME = "V6_ORB"

    def __init__(
        self,
        v6_config: V6StrategyConfig,
        llm_filter=None,
        llm_confidence_threshold: int = 75,
        live_config=None,
        log: logging.Logger = None,
        on_fill_callback: Optional[Callable] = None,
    ):
        self._v6_config = v6_config
        self._llm_filter = llm_filter
        self._llm_confidence_threshold = llm_confidence_threshold
        self._live_config = live_config
        self._log = log or logging.getLogger("v11_replay.orb")
        self._instrument = v6_config.instrument

        # V6 components (bar-based implementations)
        self._context = ReplayORBMarketContext(v6_config, self._log)
        self._execution = ReplayORBExecutionEngine(
            v6_config, self._log, on_fill_callback=on_fill_callback)
        self._strategy = ORBStrategy(v6_config, logger=self._log)

        # LLM gate state
        self._llm_evaluated_today: bool = False
        self._llm_approved_today: bool = False
        self._llm_pending: bool = False

        # Daily tracking
        self._current_date: Optional[str] = None
        self._bar_count: int = 0

        # PnL tracking for replay metrics
        self._trade_records: list = []

    @property
    def pair_name(self) -> str:
        return self._instrument

    @property
    def strategy_name(self) -> str:
        return self.STRATEGY_NAME

    @property
    def in_trade(self) -> bool:
        return self._execution.has_position()

    @property
    def bar_count(self) -> int:
        return self._bar_count

    def add_historical_bar(self, bar: V11Bar) -> None:
        """Seed the context with historical bars."""
        self._context.add_bar(bar)
        self._bar_count += 1

    async def on_bar(self, bar: V11Bar) -> None:
        """Process a completed 1-min bar through the ORB pipeline.

        This replaces the tick-driven on_price/on_bar split in the live
        ORBAdapter. We synthesize a tick from the bar and drive the
        strategy, then check for simulated fills.
        """
        self._context.add_bar(bar)
        self._bar_count += 1

        # ── Daily reset ──
        today_str = bar.timestamp.strftime("%Y-%m-%d")
        if today_str != self._current_date:
            self._reset_daily(today_str, bar.timestamp)

        # ── Force-close any open position at day boundary ──
        # (handled by replay_runner's DAILY_RESET logic, but also check here)

        # ── Check if trade window is closed ──
        cfg = self._v6_config
        if (bar.timestamp.hour >= cfg.trade_end_hour
                and self._strategy.state in (
                    StrategyState.IDLE, StrategyState.RANGE_READY)):
            self._strategy.state = StrategyState.DONE_TODAY
            return

        # ── LLM gate: evaluate once per day when RANGE_READY ──
        if (self._strategy.state == StrategyState.RANGE_READY
                and not self._llm_evaluated_today
                and not self._llm_pending
                and self._llm_filter is not None):
            self._llm_pending = True
            approved = await self._evaluate_orb_signal(bar.timestamp)
            self._llm_evaluated_today = True
            self._llm_pending = False
            self._llm_approved_today = approved
            if not approved:
                self._strategy.state = StrategyState.DONE_TODAY
                self._log.info("ORB: LLM gate REJECTED — done for today")
                return
            else:
                self._log.info("ORB: LLM gate PASSED — brackets eligible")

        # ── If LLM already rejected today, skip ──
        if (self._llm_evaluated_today and not self._llm_approved_today):
            return

        # ── Drive strategy with synthetic tick ──
        tick = Tick(
            timestamp=bar.timestamp,
            bid=bar.close,
            ask=bar.close,
        )
        self._strategy.on_tick(tick, self._context, self._execution)

        # ── Check simulated fills ──
        fill = self._execution.check_bar_fills(bar, self._strategy)
        if fill:
            self._strategy.on_fill(fill, self._context, self._execution)

            # Record trade for metrics
            if fill.reason == "ENTRY":
                self._log.info(
                    f"ORB ENTRY: {fill.direction} @ {fill.price:.{cfg.price_decimals}f}")
            elif fill.reason in ("SL", "TP", "MARKET", "BE"):
                pnl = self._calc_pnl(fill)
                self._trade_records.append({
                    "timestamp": fill.timestamp.isoformat(),
                    "direction": fill.direction,
                    "entry_price": self._strategy.entry_price,
                    "exit_price": fill.price,
                    "pnl": pnl,
                    "exit_reason": fill.reason,
                    "range_high": self._strategy.range.high if self._strategy.range else 0,
                    "range_low": self._strategy.range.low if self._strategy.range else 0,
                })
                self._log.info(
                    f"ORB EXIT: {fill.reason} @ {fill.price:.{cfg.price_decimals}f} "
                    f"PnL=${pnl:+.2f}")

        # ── Check EOD close for in-trade positions ──
        if (self._execution.has_position()
                and bar.timestamp.hour >= cfg.trade_end_hour):
            fill = self._execution.force_close_at(bar.close, bar)
            if fill:
                self._strategy.on_fill(fill, self._context, self._execution)
                pnl = self._calc_pnl(fill)
                self._trade_records.append({
                    "timestamp": fill.timestamp.isoformat(),
                    "direction": fill.direction,
                    "entry_price": self._strategy.entry_price,
                    "exit_price": fill.price,
                    "pnl": pnl,
                    "exit_reason": "EOD",
                    "range_high": self._strategy.range.high if self._strategy.range else 0,
                    "range_low": self._strategy.range.low if self._strategy.range else 0,
                })
                self._log.info(
                    f"ORB EOD CLOSE: @ {fill.price:.{cfg.price_decimals}f} "
                    f"PnL=${pnl:+.2f}")

    def on_price(self, price: float, now: datetime) -> None:
        """No-op in replay (bar-driven)."""
        pass

    def force_close(self, price: float, reason: str = "DAILY_RESET") -> Optional[dict]:
        """Force-close position for daily reset. Returns trade record or None."""
        if not self._execution.has_position():
            return None

        fill = self._execution.force_close_at(price, None)
        if fill:
            self._strategy.on_fill(fill, self._context, self._execution)
            pnl = self._calc_pnl(fill)
            record = {
                "timestamp": fill.timestamp.isoformat() if fill.timestamp else "",
                "direction": fill.direction,
                "entry_price": self._strategy.entry_price,
                "exit_price": fill.price,
                "pnl": pnl,
                "exit_reason": reason,
                "range_high": self._strategy.range.high if self._strategy.range else 0,
                "range_low": self._strategy.range.low if self._strategy.range else 0,
            }
            self._trade_records.append(record)
            self._log.info(
                f"ORB FORCE CLOSE ({reason}): @ {fill.price:.{self._v6_config.price_decimals}f} "
                f"PnL=${pnl:+.2f}")
            return record
        return None

    def get_status(self) -> dict:
        """Diagnostic status snapshot."""
        s = self._strategy
        return {
            "strategy_name": self.STRATEGY_NAME,
            "pair_name": self._instrument,
            "instrument": self._instrument,
            "state": s.state.value,
            "range": (f"{s.range.low:.2f}-{s.range.high:.2f}" if s.range else None),
            "in_trade": self._execution.has_position(),
            "direction": s.direction,
            "entry_price": s.entry_price,
            "sl_price": s.sl_price,
            "tp_price": s.tp_price,
            "llm_evaluated": self._llm_evaluated_today,
            "llm_approved": self._llm_approved_today,
            "bar_count": self._bar_count,
        }

    # ── LLM gate ──────────────────────────────────────────────────

    async def _evaluate_orb_signal(self, now: datetime) -> bool:
        """Evaluate ORB setup via LLM. Returns True if approved or no LLM."""
        if self._llm_filter is None:
            return True
        if not hasattr(self._llm_filter, 'evaluate_orb_signal'):
            return True

        rng = self._strategy.range
        if rng is None:
            return True

        mid = (rng.high + rng.low) / 2
        size = rng.high - rng.low
        size_pct = (size / mid * 100) if mid > 0 else 0.0

        # Compute range_vs_avg from daily bars
        daily_bars = self._context.get_daily_bars(20)
        if daily_bars:
            daily_ranges = [b.h - b.l for b in daily_bars]
            avg_range = sum(daily_ranges) / len(daily_ranges) if daily_ranges else size
            range_vs_avg = size / avg_range if avg_range > 0 else 1.0
        else:
            range_vs_avg = 1.0

        # ATR regime: compare recent bar volatility to slow ATR
        # Use average of last 14 bars' true range as "fast ATR" proxy
        atr_regime = 1.0
        if self._context.slow_atr > 0 and len(self._context._bars) >= 14:
            recent = list(self._context._bars)[-14:]
            fast_atr = sum(b.high - b.low for b in recent) / len(recent)
            atr_regime = fast_atr / self._context.slow_atr if self._context.slow_atr > 0 else 1.0

        # Current price
        price = self._context.get_current_price(now) or mid

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

        # Get expanded context
        hourly_bars = self._context.get_hourly_bars(5)
        trend_context = self._context.get_trend_context()

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
            daily_bars=daily_bars,
            hourly_bars=hourly_bars,
            trend_context=trend_context,
        )

        self._log.info(
            f"ORB LLM gate: evaluating range {rng.low:.2f}-{rng.high:.2f} "
            f"(size={size:.2f}, vs_avg={range_vs_avg:.1f}x, atr_regime={atr_regime:.2f})")

        decision = await self._llm_filter.evaluate_orb_signal(context)

        if not decision.approved:
            self._log.info(
                f"ORB LLM REJECTED: conf={decision.confidence} "
                f"reason={decision.reasoning[:100]}")
            return False

        is_fallback = "llm_fallback" in (decision.risk_flags or [])
        if not is_fallback and decision.confidence < self._llm_confidence_threshold:
            self._log.info(
                f"ORB LLM confidence {decision.confidence} "
                f"< threshold {self._llm_confidence_threshold}")
            return False

        self._log.info(
            f"ORB LLM APPROVED: conf={decision.confidence} "
            f"reason={decision.reasoning[:100]}")
        return True

    # ── Helpers ────────────────────────────────────────────────────

    def _reset_daily(self, today_str: str, now: datetime) -> None:
        """Reset for a new trading day."""
        self._current_date = today_str
        self._llm_evaluated_today = False
        self._llm_approved_today = False
        self._llm_pending = False
        self._strategy.reset_for_new_day()
        self._execution.reset_daily()
        self._context.reset_daily()

    def _calc_pnl(self, fill: Fill) -> float:
        """Calculate PnL in price units from a fill."""
        s = self._strategy
        if s.direction == "LONG":
            return fill.price - s.entry_price
        elif s.direction == "SHORT":
            return s.entry_price - fill.price
        return 0.0
