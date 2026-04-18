"""
V11 Live Engine — Orchestrates Darvas detection, LLM filtering, and trade execution.

Per-instrument engine: each instrument gets its own LiveEngine instance with
its own DarvasDetector, ImbalanceClassifier, TradeManager, and BarAggregator.

Data flow (from V11_DESIGN.md §5):
    IBKR stream → BarAggregator → RollingBuffer → DarvasDetector
    → On breakout: ImbalanceClassifier enrichment → LLM Filter → TradeManager
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Optional, List

from ..core.types import Bar, BreakoutSignal, VolumeAnalysis, Direction, ImbalanceClassification
from ..core.darvas_detector import DarvasDetector
from ..core.imbalance_classifier import ImbalanceClassifier
from ..core.htf_sma_filter import IncrementalHTFSMAFilter
from ..config.strategy_config import StrategyConfig
from ..config.live_config import InstrumentConfig, LiveConfig
from ..execution.bar_aggregator import BarAggregator
from ..execution.trade_manager import TradeManager
from ..llm.base import LLMFilter
from ..llm.models import SignalContext, BarData


class RollingBuffer:
    """Rolling buffer of bars. Ported from v8, simplified."""

    def __init__(self, max_size: int = 500):
        self.max_size = max_size
        self._bars: deque[Bar] = deque(maxlen=max_size)

    def add_bar(self, bar: Bar) -> None:
        self._bars.append(bar)

    def __len__(self) -> int:
        return len(self._bars)

    def get_bars(self, n: Optional[int] = None) -> List[Bar]:
        """Get last n bars (or all if n is None)."""
        if n is None:
            return list(self._bars)
        return list(self._bars)[-n:]

    @property
    def latest(self) -> Optional[Bar]:
        return self._bars[-1] if self._bars else None


class InstrumentEngine:
    """Per-instrument processing engine.

    Each instrument has its own:
    - DarvasDetector (signal generation)
    - ImbalanceClassifier (volume analysis)
    - RollingBuffer (bar history)
    - BarAggregator (tick-to-bar conversion)
    - TradeManager (execution)
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        inst_config: InstrumentConfig,
        llm_filter: LLMFilter,
        trade_manager: TradeManager,
        live_config: LiveConfig,
        log: logging.Logger,
    ):
        self.strategy_config = strategy_config
        self.inst_config = inst_config
        self._llm_filter = llm_filter
        self._trade_manager = trade_manager
        self._live_config = live_config
        self._log = log

        # Deep modules
        self._detector = DarvasDetector(strategy_config)
        self._classifier = ImbalanceClassifier(
            max_lookback=20,
            min_bar_ticks=strategy_config.min_bar_ticks,
        )

        # HTF SMA direction filter (V11_DESIGN.md §10)
        self._sma_filter: Optional[IncrementalHTFSMAFilter] = None
        if strategy_config.htf_sma_enabled:
            self._sma_filter = IncrementalHTFSMAFilter(
                bar_minutes=strategy_config.htf_sma_bar_minutes,
                sma_period=strategy_config.htf_sma_period,
            )

        # Buffer and aggregator
        self._buffer = RollingBuffer(max_size=live_config.buffer_size)
        self._aggregator = BarAggregator()

        # Bar counter
        self._bar_count: int = 0

        # Slow ATR for regime context (1440 bars = 1 day of 1-min bars)
        self._slow_atr_period: int = 1440
        self._slow_atr: float = 0.0
        self._slow_atr_count: int = 0
        self._slow_atr_prev_close: float = 0.0

        # Last known price (for slippage ceiling check after LLM latency)
        self._last_price: float = 0.0
        self._last_price_ts: float = 0.0  # epoch seconds (Fix 7)

        # Strategy identifier (set by MultiStrategyRunner)
        self.strategy_name: str = "Darvas_Breakout"

        # Risk manager callback (set by MultiStrategyRunner)
        self._risk_check = None

    @property
    def pair_name(self) -> str:
        return self.inst_config.pair_name

    @property
    def in_trade(self) -> bool:
        return self._trade_manager.in_trade

    @property
    def bar_count(self) -> int:
        return self._bar_count

    def on_price(self, price: float, now: datetime) -> Optional[Bar]:
        """Process a price tick. Returns completed Bar if minute boundary crossed."""
        self._last_price = price
        self._last_price_ts = time.time()  # Fix 7
        return self._aggregator.on_price(price, now)

    async def on_bar(self, bar: Bar) -> None:
        """Process a completed bar through the full pipeline.

        1. Add to buffer and classifier
        2. Feed to DarvasDetector
        3. If breakout: enrich with volume analysis, call LLM, execute trade
        4. If in trade: check exit conditions
        """
        self._buffer.add_bar(bar)
        self._classifier.add_bar(bar)
        if self._sma_filter is not None:
            self._sma_filter.add_bar(bar)
        self._update_slow_atr(bar)
        self._bar_count += 1

        # Check exit first (if in trade)
        if self._trade_manager.in_trade:
            record = self._trade_manager.check_exit(
                current_price=bar.close,
                bar_high=bar.high,
                bar_low=bar.low,
                current_bar_index=self._bar_count,
            )
            if record:
                self._log.info(
                    f"{self.pair_name}: Trade closed — "
                    f"{record.exit_reason} PnL=${record.pnl:+.2f}")
            return  # Don't look for new signals while in a trade

        # Feed bar to Darvas detector
        state_before = self._detector.state
        signal = self._detector.add_bar(bar)
        state_after = self._detector.state

        # Log detector state transitions (DEBUG — file only)
        if state_after != state_before:
            prog = self._detector.formation_progress
            self._log.debug(
                f"{self.pair_name}[Darvas]: {state_before} -> {state_after} "
                f"bar#{self._bar_count} close={bar.close} {prog}")

        # Log formation progress every 60 bars (~1 hour)
        if self._bar_count % 60 == 0:
            prog = self._detector.formation_progress
            state = prog["state"]
            if state == "CONFIRMING_TOP":
                self._log.debug(
                    f"{self.pair_name}[Darvas]: Forming top — "
                    f"candidate={prog['candidate_top']:.5f} "
                    f"confirm={prog['bars_confirmed']}/{prog['bars_needed']} bars "
                    f"close={bar.close}")
            elif state == "CONFIRMING_BOTTOM":
                self._log.debug(
                    f"{self.pair_name}[Darvas]: Forming bottom — "
                    f"top={prog['confirmed_top']:.5f} "
                    f"candidate_bot={prog['candidate_bottom']:.5f} "
                    f"confirm={prog['bars_confirmed']}/{prog['bars_needed']} bars "
                    f"close={bar.close}")
            elif state == "BOX_ACTIVE":
                dist_top = bar.close - prog.get("box_top", 0)
                dist_bot = bar.close - prog.get("box_bottom", 0)
                atr = self._detector.current_atr
                self._log.debug(
                    f"{self.pair_name}[Darvas]: BOX ACTIVE "
                    f"[{prog.get('box_bottom', 0):.5f}-{prog.get('box_top', 0):.5f}] "
                    f"close={bar.close} dist_top={dist_top:+.5f} dist_bot={dist_bot:+.5f} "
                    f"({dist_top/atr:+.1f}ATR/{dist_bot/atr:+.1f}ATR)" if atr > 0 else "")
            elif state == "CONFIRMING_BREAKOUT":
                self._log.debug(
                    f"{self.pair_name}[Darvas]: BREAKOUT CONFIRMING — "
                    f"{prog.get('direction', '?')} "
                    f"confirm={prog.get('confirm_count', 0)}/{prog.get('confirm_needed', 0)} bars "
                    f"close={bar.close}")

        if signal is not None:
            self._log.info(
                f"{self.pair_name}: DARVAS SIGNAL — "
                f"{signal.direction.value} breakout @ {signal.breakout_price} "
                f"box=[{signal.box.bottom:.5f}-{signal.box.top:.5f}] "
                f"atr={signal.atr:.5f}")
            await self._handle_signal(signal, bar)

    async def _handle_signal(self, signal: BreakoutSignal, bar: Bar) -> None:
        """Handle a Darvas breakout signal: enrich, filter, execute."""
        # Safety check
        safety = self._check_safety()
        if safety:
            self._log.warning(f"{self.pair_name}: SAFETY LIMIT: {safety}")
            return

        # HTF SMA direction filter (V11_DESIGN.md §10)
        if self._sma_filter is not None:
            if not self._sma_filter.is_aligned(
                signal.direction, signal.breakout_price,
            ):
                sma_val = self._sma_filter.current_sma
                self._log.info(
                    f"{self.pair_name}: SMA FILTER REJECTED — "
                    f"{signal.direction.value} breakout @ {signal.breakout_price} "
                    f"vs SMA={sma_val:.5f}")
                return

        # Volume analysis enrichment
        volume = self._build_volume_analysis(signal)

        # Volume imbalance gate (Decision #18: DIVERGENT rejects mechanically)
        if volume.classification == ImbalanceClassification.DIVERGENT:
            self._log.info(
                f"{self.pair_name}: VOLUME REJECTED — "
                f"{volume.classification.value} "
                f"(buy_ratio={volume.buy_ratio_at_breakout:.3f})")
            return

        # Guard: check BEFORE LLM call to avoid spending latency on a taken slot
        # (Fix 4: secondary check after drift remains as race-condition guard)
        if self._trade_manager.in_trade:
            self._log.info(
                f"{self.pair_name}[{self.strategy_name}]: "
                f"TradeManager already in trade, skipping signal")
            return

        # Build LLM context
        context = self._build_signal_context(signal, volume, bar)

        # Call LLM filter
        self._log.info(
            f"{self.pair_name}: Breakout {signal.direction.value} "
            f"@ {signal.breakout_price} — calling LLM filter...")

        decision = await self._llm_filter.evaluate_signal(context)

        # Check approval + confidence
        if not decision.approved:
            self._log.info(
                f"{self.pair_name}: LLM REJECTED — "
                f"conf={decision.confidence} reason={decision.reasoning[:100]}")
            return

        if decision.confidence < self._live_config.llm_confidence_threshold:
            self._log.info(
                f"{self.pair_name}: LLM confidence {decision.confidence} "
                f"< threshold {self._live_config.llm_confidence_threshold}")
            return

        # ── Slippage ceiling check (Fix 7: skip if price data is stale) ────
        atr = self._detector.current_atr
        if atr > 0 and self._last_price > 0:
            price_age_s = (time.time() - self._last_price_ts
                           if self._last_price_ts > 0 else 0)
            if price_age_s > 30:
                self._log.warning(
                    f"{self.pair_name}: DRIFT CHECK SKIPPED — "
                    f"last price is {price_age_s:.0f}s stale")
            else:
                drift = abs(self._last_price - signal.breakout_price)
                max_drift = self._live_config.max_entry_drift_atr * atr
                if drift > max_drift:
                    self._log.warning(
                        f"{self.pair_name}: ENTRY DRIFT ABORT — "
                        f"price moved {drift:.4f} ({drift/atr:.2f} ATR) "
                        f"during LLM latency. Max allowed: {max_drift:.4f} "
                        f"({self._live_config.max_entry_drift_atr} ATR). "
                        f"Breakout={signal.breakout_price}, "
                        f"current={self._last_price}")
                    return

        # Secondary in_trade guard (race condition: another strategy may have
        # entered during LLM latency)
        if self._trade_manager.in_trade:
            self._log.info(
                f"{self.pair_name}[{self.strategy_name}]: "
                f"TradeManager already in trade, skipping signal")
            return

        # Risk manager gate (portfolio-level check)
        if self._risk_check is not None:
            allowed, reason = self._risk_check(self.pair_name, self.strategy_name)
            if not allowed:
                self._log.warning(
                    f"{self.pair_name}[{self.strategy_name}]: RISK REJECTED — {reason}")
                return

        # Execute trade
        self._trade_manager.enter_trade(
            signal=signal,
            decision=decision,
            buy_ratio=volume.buy_ratio_at_breakout,
            current_bar_index=self._bar_count,
        )

    def _build_volume_analysis(self, signal: BreakoutSignal) -> VolumeAnalysis:
        """Build volume analysis data for a breakout signal."""
        window = self.strategy_config.imbalance_window
        br = self._classifier.get_buy_ratio(window)
        import math
        if math.isnan(br):
            br = 0.5

        classification = self._classifier.classify(
            signal.direction, window,
            self.strategy_config.divergence_threshold)
        trend = self._classifier.get_trend()
        tick_quality = self._classifier.get_tick_quality(window)

        return VolumeAnalysis(
            buy_ratio_at_breakout=br,
            buy_ratio_trend=trend,
            tick_quality=tick_quality,
            classification=classification,
        )

    def _build_signal_context(
        self, signal: BreakoutSignal, volume: VolumeAnalysis, bar: Bar,
    ) -> SignalContext:
        """Package everything for the LLM filter."""
        # Recent bars for context
        recent = self._buffer.get_bars(self._live_config.llm_bars_context)
        recent_bar_data = [
            BarData(
                t=b.timestamp.isoformat(),
                o=b.open, h=b.high, l=b.low, c=b.close,
                bv=b.buy_volume, sv=b.sell_volume, tc=b.tick_count,
            )
            for b in recent
        ]

        # Determine trading session
        session = self._determine_session(bar.timestamp)

        # Compute ATR regime (fast ATR / slow ATR)
        atr_vs_avg = signal.atr / self._slow_atr if self._slow_atr > 0 else 1.0

        return SignalContext(
            direction=signal.direction.value,
            instrument=self.inst_config.pair_name,
            box_top=signal.box.top,
            box_bottom=signal.box.bottom,
            box_duration_bars=signal.box.duration_bars,
            box_width_atr=signal.box.width_atr,
            breakout_price=signal.breakout_price,
            atr=signal.atr,
            atr_vs_avg=round(atr_vs_avg, 2),
            buy_ratio_at_breakout=volume.buy_ratio_at_breakout,
            buy_ratio_trend=volume.buy_ratio_trend,
            tick_quality=volume.tick_quality.value,
            volume_classification=volume.classification.value,
            recent_bars=recent_bar_data,
            current_time_utc=bar.timestamp.isoformat(),
            session=session,
        )

    def _determine_session(self, ts: datetime) -> str:
        """Determine trading session from UTC timestamp."""
        hour = ts.hour
        if 0 <= hour < 8:
            return "ASIAN"
        elif 8 <= hour < 13:
            return "LONDON"
        elif 13 <= hour < 17:
            return "LONDON_NY_OVERLAP"
        elif 17 <= hour < 22:
            return "NY"
        else:
            return "ASIAN"

    def _update_slow_atr(self, bar: Bar) -> None:
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

    def _check_safety(self) -> Optional[str]:
        """Check daily trade and loss limits."""
        tm = self._trade_manager
        if self._live_config.max_daily_trades > 0:
            if tm.daily_trades >= self._live_config.max_daily_trades:
                return (f"Daily trade limit: {tm.daily_trades}/"
                        f"{self._live_config.max_daily_trades}")
        if self._live_config.max_daily_loss > 0:
            if tm.daily_pnl <= -self._live_config.max_daily_loss:
                return (f"Daily loss limit: ${tm.daily_pnl:.2f} <= "
                        f"-${self._live_config.max_daily_loss:.2f}")
        return None

    def reset_session(self) -> None:
        """Reset detector state at session boundary (e.g. 5 PM ET).

        Mirrors the backtest behavior where each session gets a fresh
        DarvasDetector. Without this, the detector can get stuck in
        partially-formed box states indefinitely.
        Uses reset_formation() to preserve ATR — only the box state machine resets.
        """
        self._detector.reset_formation()
        self._log.info(
            f"{self.pair_name}[{self.strategy_name}]: "
            f"Session reset — detector formation state cleared (ATR preserved)")

    def add_historical_bar(self, bar: Bar) -> None:
        """Add a historical bar to seed the buffer and detector."""
        self._buffer.add_bar(bar)
        self._classifier.add_bar(bar)
        self._detector.add_bar(bar)
        if self._sma_filter is not None:
            self._sma_filter.add_bar(bar)
        self._update_slow_atr(bar)
        self._bar_count += 1

    def get_status(self) -> dict:
        """Get current engine status for diagnostics."""
        last_close = 0.0
        bars = self._buffer.get_bars(1)
        if bars:
            last_close = bars[-1].close
        return {
            'strategy_name': self.strategy_name,
            'pair_name': self.pair_name,
            'instrument': self.pair_name,
            'bar_count': self._bar_count,
            'buffer_size': len(self._buffer),
            'detector_state': self._detector.state,
            'active_box': self._detector.active_box,
            'formation_progress': self._detector.formation_progress,
            'atr': self._detector.current_atr,
            'in_trade': self._trade_manager.in_trade,
            'daily_trades': self._trade_manager.daily_trades,
            'daily_pnl': self._trade_manager.daily_pnl,
            'htf_sma': self._sma_filter.current_sma if self._sma_filter else None,
            'htf_sma_bars': self._sma_filter.htf_bars_count if self._sma_filter else 0,
            'last_close': last_close,
        }
