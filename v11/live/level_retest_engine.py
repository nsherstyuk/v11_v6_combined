"""
4H Level Retest Strategy Engine — Processes 1-min bars for level retest signals.

Design (V11_DESIGN.md §11, §12):
    Signal flow:
        1-min bar → IncrementalSwingLevelDetector (4H levels)
                   → RetestDetector (break → pullback → rebreak)
                   → SMA direction filter
                   → CONFIRMING volume filter
                   → LLM evaluation
                   → TradeManager (shared with DarvasBreakout on same instrument)

    This engine is structurally parallel to InstrumentEngine (Darvas), but uses
    a different signal generator. Both share the same TradeManager per instrument,
    which enforces max 1 position per instrument automatically.

    ATR is computed internally via a simple EMA of true range on 1-min bars,
    matching the approach in DarvasDetector.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from datetime import datetime
from typing import Optional, List

from ..core.types import Bar, RetestSignal, VolumeAnalysis, Direction
from ..core.level_detector import IncrementalSwingLevelDetector
from ..core.retest_detector import RetestDetector
from ..core.imbalance_classifier import ImbalanceClassifier
from ..core.htf_sma_filter import IncrementalHTFSMAFilter
from ..config.strategy_config import StrategyConfig
from ..config.live_config import InstrumentConfig, LiveConfig
from ..execution.trade_manager import TradeManager
from ..llm.base import LLMFilter
from ..llm.models import SignalContext, BarData
from .live_engine import RollingBuffer


STRATEGY_NAME = "4H_Level_Retest"


class LevelRetestEngine:
    """Per-instrument engine for 4H level retest signals.

    Interface (narrow):
        on_bar(bar) -> None          (process a completed 1-min bar)
        add_historical_bar(bar)      (seed buffers)
        in_trade -> bool
        bar_count -> int
        pair_name -> str
        get_status() -> dict
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
        self.strategy_name = STRATEGY_NAME
        self._llm_filter = llm_filter
        self._trade_manager = trade_manager
        self._live_config = live_config
        self._log = log

        # 4H Swing Level Detector (incremental, resamples 1-min → 4H internally)
        self._level_detector = IncrementalSwingLevelDetector(
            htf_bar_minutes=strategy_config.level_htf_bar_minutes,
            left_bars=strategy_config.level_left_bars,
            right_bars=strategy_config.level_right_bars,
            expiry_hours=strategy_config.level_expiry_hours,
            merge_distance=strategy_config.level_merge_distance,
        )

        # Retest Detector (break → pullback → rebreak state machine)
        self._retest_detector = RetestDetector(
            min_pullback_bars=strategy_config.retest_min_pullback_bars,
            max_pullback_bars=strategy_config.retest_max_pullback_bars,
            cooldown_bars=strategy_config.retest_cooldown_bars,
        )

        # Volume classifier
        self._classifier = ImbalanceClassifier(
            max_lookback=20,
            min_bar_ticks=strategy_config.min_bar_ticks,
        )

        # HTF SMA direction filter
        self._sma_filter: Optional[IncrementalHTFSMAFilter] = None
        if strategy_config.htf_sma_enabled:
            self._sma_filter = IncrementalHTFSMAFilter(
                bar_minutes=strategy_config.htf_sma_bar_minutes,
                sma_period=strategy_config.htf_sma_period,
            )

        # Rolling buffer for LLM context
        self._buffer = RollingBuffer(max_size=live_config.buffer_size)

        # ATR computation (EMA of true range, same as DarvasDetector)
        self._atr_period: int = strategy_config.atr_period
        self._atr: float = 0.0
        self._atr_count: int = 0
        self._prev_close: float = 0.0

        # Bar counter
        self._bar_count: int = 0

        # Last known price (for slippage ceiling check)
        self._last_price: float = 0.0

        # Risk manager callback (set by MultiStrategyRunner)
        self._risk_check: Optional[object] = None

    @property
    def pair_name(self) -> str:
        return self.inst_config.pair_name

    @property
    def in_trade(self) -> bool:
        return self._trade_manager.in_trade

    @property
    def bar_count(self) -> int:
        return self._bar_count

    @property
    def current_atr(self) -> float:
        return self._atr

    def on_price(self, price: float, now: datetime) -> None:
        """Track latest price for slippage ceiling check."""
        self._last_price = price

    async def on_bar(self, bar: Bar) -> None:
        """Process a completed 1-min bar through the level retest pipeline.

        1. Update buffer, classifier, SMA filter, level detector
        2. Feed bar + active levels to retest detector
        3. On retest signal: check SMA, volume, risk, LLM, execute
        4. If in trade: check exit conditions
        """
        self._buffer.add_bar(bar)
        self._classifier.add_bar(bar)
        if self._sma_filter is not None:
            self._sma_filter.add_bar(bar)
        self._level_detector.add_bar(bar)
        self._update_atr(bar)
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
                    f"{self.pair_name}[{STRATEGY_NAME}]: Trade closed — "
                    f"{record.exit_reason} PnL=${record.pnl:+.2f}")
            return  # Don't look for new signals while in a trade

        # Get active levels and check for retest signals
        active_levels = self._level_detector.get_active_levels()

        # Log level activity periodically (every 60 bars = ~1 hour)
        if self._bar_count % 60 == 0:
            pending = self._retest_detector.pending_count
            self._log.debug(
                f"{self.pair_name}[{STRATEGY_NAME}]: "
                f"levels={len(active_levels)} pending_retests={pending} "
                f"atr={self._atr:.5f} "
                f"htf_bars={self._level_detector.htf_bars_count} "
                f"close={bar.close}")

        if not active_levels:
            return

        signals = self._retest_detector.add_bar(bar, active_levels, self._atr)

        for signal in signals:
            self._log.info(
                f"{self.pair_name}[{STRATEGY_NAME}]: RETEST SIGNAL — "
                f"{signal.direction.value} @ {signal.breakout_price} "
                f"level={signal.level_price:.5f} atr={self._atr:.5f}")
            await self._handle_retest_signal(signal, bar)
            # Only process the first signal per bar (first signal wins)
            break

    async def _handle_retest_signal(self, signal: RetestSignal, bar: Bar) -> None:
        """Handle a retest signal: filter, evaluate, execute."""
        # SMA direction filter
        if self._sma_filter is not None:
            if not self._sma_filter.is_aligned(
                signal.direction, signal.breakout_price,
            ):
                sma_val = self._sma_filter.current_sma
                self._log.info(
                    f"{self.pair_name}[{STRATEGY_NAME}]: SMA FILTER REJECTED — "
                    f"{signal.direction.value} retest @ {signal.breakout_price} "
                    f"vs SMA={sma_val}")
                return

        # Volume analysis enrichment
        volume = self._build_volume_analysis(signal.direction)

        # CONFIRMING volume filter
        from ..core.types import ImbalanceClassification
        if volume.classification != ImbalanceClassification.CONFIRMING:
            self._log.info(
                f"{self.pair_name}[{STRATEGY_NAME}]: VOLUME REJECTED — "
                f"{volume.classification.value} "
                f"(buy_ratio={volume.buy_ratio_at_breakout:.3f})")
            return

        # Risk manager check (if set by MultiStrategyRunner)
        if self._risk_check is not None:
            allowed, reason = self._risk_check(self.pair_name, self.strategy_name)
            if not allowed:
                self._log.warning(
                    f"{self.pair_name}[{STRATEGY_NAME}]: RISK REJECTED — {reason}")
                return

        # Build LLM context
        context = self._build_signal_context(signal, volume, bar)

        self._log.info(
            f"{self.pair_name}[{STRATEGY_NAME}]: Retest {signal.direction.value} "
            f"@ {signal.breakout_price} level={signal.level_price} "
            f"pb={signal.pullback_bars}bars — calling LLM filter...")

        decision = await self._llm_filter.evaluate_signal(context)

        if not decision.approved:
            self._log.info(
                f"{self.pair_name}[{STRATEGY_NAME}]: LLM REJECTED — "
                f"conf={decision.confidence} reason={decision.reasoning[:100]}")
            return

        if decision.confidence < self._live_config.llm_confidence_threshold:
            self._log.info(
                f"{self.pair_name}[{STRATEGY_NAME}]: LLM confidence "
                f"{decision.confidence} < threshold "
                f"{self._live_config.llm_confidence_threshold}")
            return

        # Slippage ceiling check
        if self._atr > 0 and self._last_price > 0:
            drift = abs(self._last_price - signal.breakout_price)
            max_drift = self._live_config.max_entry_drift_atr * self._atr
            if drift > max_drift:
                self._log.warning(
                    f"{self.pair_name}[{STRATEGY_NAME}]: ENTRY DRIFT ABORT — "
                    f"drift={drift:.4f} ({drift/self._atr:.2f} ATR) "
                    f"max={max_drift:.4f}")
                return

        # Compute SL/TP from retest signal (V11_DESIGN.md §12)
        sl_offset = self.strategy_config.retest_sl_atr_offset * signal.atr
        rr_ratio = self.strategy_config.retest_rr_ratio

        if signal.direction == Direction.LONG:
            sl = signal.level_price - sl_offset
            risk = signal.breakout_price - sl
            tp = signal.breakout_price + risk * rr_ratio
        else:
            sl = signal.level_price + sl_offset
            risk = sl - signal.breakout_price
            tp = signal.breakout_price - risk * rr_ratio

        # Override LLM's entry/stop/target with our structural levels
        from ..core.types import FilterDecision
        adjusted_decision = FilterDecision(
            approved=decision.approved,
            confidence=decision.confidence,
            entry_price=signal.breakout_price,
            stop_price=sl,
            target_price=tp,
            reasoning=decision.reasoning,
            risk_flags=decision.risk_flags,
        )

        # Build a synthetic BreakoutSignal to satisfy TradeManager interface
        from ..core.types import BreakoutSignal, DarvasBox
        synthetic_box = DarvasBox(
            top=signal.level_price if signal.direction == Direction.LONG else signal.breakout_price,
            bottom=signal.breakout_price if signal.direction == Direction.LONG else signal.level_price,
            top_confirmed_at=signal.break_bar_index,
            bottom_confirmed_at=signal.break_bar_index,
            formation_start=signal.break_bar_index,
            duration_bars=signal.pullback_bars,
            atr_at_formation=signal.atr,
        )
        synthetic_signal = BreakoutSignal(
            timestamp=signal.timestamp,
            direction=signal.direction,
            box=synthetic_box,
            breakout_price=signal.breakout_price,
            breakout_bar_index=signal.rebreak_bar_index,
            atr=signal.atr,
        )

        self._trade_manager.enter_trade(
            signal=synthetic_signal,
            decision=adjusted_decision,
            buy_ratio=volume.buy_ratio_at_breakout,
            current_bar_index=self._bar_count,
        )

    def _build_volume_analysis(self, direction: Direction) -> VolumeAnalysis:
        """Build volume analysis data for a retest signal."""
        window = self.strategy_config.imbalance_window
        br = self._classifier.get_buy_ratio(window)
        if math.isnan(br):
            br = 0.5

        classification = self._classifier.classify(
            direction, window,
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
        self, signal: RetestSignal, volume: VolumeAnalysis, bar: Bar,
    ) -> SignalContext:
        """Package everything for the LLM filter."""
        recent = self._buffer.get_bars(self._live_config.llm_bars_context)
        recent_bar_data = [
            BarData(
                t=b.timestamp.isoformat(),
                o=b.open, h=b.high, l=b.low, c=b.close,
                bv=b.buy_volume, sv=b.sell_volume, tc=b.tick_count,
            )
            for b in recent
        ]

        session = self._determine_session(bar.timestamp)

        return SignalContext(
            direction=signal.direction.value,
            instrument=self.inst_config.pair_name,
            box_top=signal.level_price,
            box_bottom=signal.level_price,
            box_duration_bars=signal.pullback_bars,
            box_width_atr=0.0,
            breakout_price=signal.breakout_price,
            atr=signal.atr,
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

    def _update_atr(self, bar: Bar) -> None:
        """Update ATR using EMA of true range (same formula as DarvasDetector)."""
        if self._prev_close == 0:
            self._prev_close = bar.close
            return

        true_range = max(
            bar.high - bar.low,
            abs(bar.high - self._prev_close),
            abs(bar.low - self._prev_close),
        )
        self._prev_close = bar.close

        self._atr_count += 1
        if self._atr_count == 1:
            self._atr = true_range
        else:
            alpha = 2.0 / (self._atr_period + 1)
            self._atr = alpha * true_range + (1 - alpha) * self._atr

    def add_historical_bar(self, bar: Bar) -> None:
        """Add a historical bar to seed buffers."""
        self._buffer.add_bar(bar)
        self._classifier.add_bar(bar)
        self._level_detector.add_bar(bar)
        if self._sma_filter is not None:
            self._sma_filter.add_bar(bar)
        self._update_atr(bar)
        self._bar_count += 1

    def get_status(self) -> dict:
        """Get current engine status for diagnostics."""
        active_levels = self._level_detector.get_active_levels()
        return {
            'strategy_name': self.strategy_name,
            'pair_name': self.pair_name,
            'instrument': self.pair_name,
            'strategy': STRATEGY_NAME,
            'bar_count': self._bar_count,
            'active_levels': len(active_levels),
            'pending_retests': self._retest_detector.pending_count,
            'atr': self._atr,
            'in_trade': self._trade_manager.in_trade,
            'htf_sma': self._sma_filter.current_sma if self._sma_filter else None,
            'htf_sma_bars': self._sma_filter.htf_bars_count if self._sma_filter else 0,
            'level_htf_bars': self._level_detector.htf_bars_count,
        }
