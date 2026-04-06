"""
CENTER MODULE: Darvas Box Detection and Breakout Signal Generation.

This is the core signal-generation engine for V11. It replaces v8's
pivot_computer.py and pattern_detector.py with Darvas Box logic.

Design decisions hidden inside this module:
    - How box tops and bottoms are identified from price action
    - When a box is considered "confirmed" (complete consolidation)
    - How breakouts are validated (N consecutive bars above/below)
    - Box invalidation rules (price breaks bottom during formation)
    - ATR computation for box width validation

Interface (narrow):
    add_bar(bar) -> Optional[BreakoutSignal]
    active_box -> Optional[DarvasBox]
    current_atr -> float

Why this boundary exists:
    The Darvas detection algorithm is the most likely thing to change
    (parameter tuning, additional filters, nested box support). Hiding
    the state machine behind add_bar() means the rest of the system
    is insulated from these changes.

CHANGES TO THIS FILE REQUIRE EXPLICIT APPROVAL (center element).
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from typing import Optional

from .types import Bar, DarvasBox, BreakoutSignal, Direction
from ..config.strategy_config import StrategyConfig


class DarvasDetector:
    """Detects Darvas box formations and breakout signals from a stream of bars.

    State machine:
        SEEKING_TOP  -> price makes a new high, start counting confirm bars
        CONFIRMING_TOP -> N bars without new high -> top confirmed, seek bottom
        CONFIRMING_BOTTOM -> N bars without new low -> bottom confirmed, box formed
        BOX_ACTIVE   -> price contained in box, watching for breakout
        CONFIRMING_BREAKOUT -> M consecutive bars above top (or below bottom)

    On confirmed breakout: emits BreakoutSignal, resets to SEEKING_TOP.
    On box invalidation: resets to SEEKING_TOP.
    """

    # State constants
    SEEKING_TOP = "SEEKING_TOP"
    CONFIRMING_TOP = "CONFIRMING_TOP"
    CONFIRMING_BOTTOM = "CONFIRMING_BOTTOM"
    BOX_ACTIVE = "BOX_ACTIVE"
    CONFIRMING_BREAKOUT = "CONFIRMING_BREAKOUT"

    def __init__(self, config: StrategyConfig):
        self._config = config

        # State machine
        self._state: str = self.SEEKING_TOP
        self._bar_index: int = -1

        # Top formation tracking
        self._candidate_top: float = -math.inf
        self._candidate_top_bar: int = 0
        self._bars_since_new_high: int = 0

        # Bottom formation tracking
        self._confirmed_top: float = 0.0
        self._confirmed_top_bar: int = 0
        self._candidate_bottom: float = math.inf
        self._candidate_bottom_bar: int = 0
        self._bars_since_new_low: int = 0
        self._formation_start: int = 0

        # Active box
        self._active_box: Optional[DarvasBox] = None

        # Breakout tracking
        self._breakout_direction: Optional[Direction] = None
        self._breakout_confirm_count: int = 0
        self._breakout_price: float = 0.0

        # ATR computation (exponential moving average of true range)
        self._atr_period: int = config.atr_period
        self._atr: float = 0.0
        self._atr_count: int = 0
        self._prev_close: float = 0.0

    @property
    def active_box(self) -> Optional[DarvasBox]:
        """The currently active (confirmed) Darvas box, if any."""
        return self._active_box

    @property
    def current_atr(self) -> float:
        """Current ATR value."""
        return self._atr

    @property
    def state(self) -> str:
        """Current state machine state (for diagnostics)."""
        return self._state

    def add_bar(self, bar: Bar) -> Optional[BreakoutSignal]:
        """Process a new bar. Returns a BreakoutSignal if a confirmed breakout is detected.

        This is the single entry point. All complexity is hidden inside.
        Most bars: no signal, O(1) work, no external calls.
        """
        self._bar_index += 1
        self._update_atr(bar)

        if self._state == self.SEEKING_TOP:
            self._process_seeking_top(bar)
            return None

        elif self._state == self.CONFIRMING_TOP:
            self._process_confirming_top(bar)
            return None

        elif self._state == self.CONFIRMING_BOTTOM:
            self._process_confirming_bottom(bar)
            return None

        elif self._state == self.BOX_ACTIVE:
            return self._process_box_active(bar)

        elif self._state == self.CONFIRMING_BREAKOUT:
            return self._process_confirming_breakout(bar)

        return None

    # ── State handlers ──────────────────────────────────────────────────

    def _process_seeking_top(self, bar: Bar) -> None:
        """Look for a new high to start box formation."""
        if bar.high > self._candidate_top:
            self._candidate_top = bar.high
            self._candidate_top_bar = self._bar_index
            self._formation_start = self._bar_index
            self._bars_since_new_high = 0
            self._state = self.CONFIRMING_TOP
        # else: keep seeking, no high worth tracking yet

    def _process_confirming_top(self, bar: Bar) -> None:
        """Count bars since the last new high. If N bars pass without new high, top is confirmed."""
        if bar.high > self._candidate_top:
            # New high — reset confirmation counter
            self._candidate_top = bar.high
            self._candidate_top_bar = self._bar_index
            self._bars_since_new_high = 0
        else:
            self._bars_since_new_high += 1
            if self._bars_since_new_high >= self._config.top_confirm_bars:
                # Top confirmed — start tracking bottom
                self._confirmed_top = self._candidate_top
                self._confirmed_top_bar = self._candidate_top_bar
                self._candidate_bottom = min(
                    bar.low, self._candidate_top  # initialize with current bar
                )
                # Scan back: the bottom candidate is the lowest low since the top
                # Since we only have the current bar, we track going forward
                self._candidate_bottom = bar.low
                self._candidate_bottom_bar = self._bar_index
                self._bars_since_new_low = 0
                self._state = self.CONFIRMING_BOTTOM

    def _process_confirming_bottom(self, bar: Bar) -> None:
        """Track the lowest low after top confirmation. If N bars pass without new low, bottom confirmed."""
        if bar.low < self._candidate_bottom:
            # New low — reset confirmation counter
            self._candidate_bottom = bar.low
            self._candidate_bottom_bar = self._bar_index
            self._bars_since_new_low = 0
        else:
            self._bars_since_new_low += 1
            if self._bars_since_new_low >= self._config.bottom_confirm_bars:
                # Bottom confirmed — validate and form box
                self._try_form_box(bar)

        # Invalidation: if price breaks above the confirmed top during bottom formation,
        # treat it as a potential new top and restart
        if bar.high > self._confirmed_top:
            self._candidate_top = bar.high
            self._candidate_top_bar = self._bar_index
            self._bars_since_new_high = 0
            self._state = self.CONFIRMING_TOP

    def _try_form_box(self, bar: Bar) -> None:
        """Validate box dimensions and form it if valid."""
        top = self._confirmed_top
        bottom = self._candidate_bottom
        width = top - bottom

        # Width validation
        if self._atr > 0:
            width_atr = width / self._atr
            if width_atr < self._config.min_box_width_atr:
                # Box too narrow (noise) — reset
                self._reset_to_seeking()
                return
            if width_atr > self._config.max_box_width_atr:
                # Box too wide (meaningless) — reset
                self._reset_to_seeking()
                return

        # Duration validation
        duration = self._bar_index - self._formation_start
        if duration < self._config.min_box_duration:
            # Box formed too quickly — reset
            self._reset_to_seeking()
            return

        # Box is valid — activate it
        self._active_box = DarvasBox(
            top=top,
            bottom=bottom,
            top_confirmed_at=self._confirmed_top_bar,
            bottom_confirmed_at=self._candidate_bottom_bar,
            formation_start=self._formation_start,
            duration_bars=duration,
            atr_at_formation=self._atr,
        )
        self._breakout_confirm_count = 0
        self._breakout_direction = None
        self._state = self.BOX_ACTIVE

    def _process_box_active(self, bar: Bar) -> Optional[BreakoutSignal]:
        """Price is contained in the box. Watch for breakout."""
        box = self._active_box
        if box is None:
            self._reset_to_seeking()
            return None

        # Check for upside breakout start
        if bar.close > box.top:
            self._breakout_direction = Direction.LONG
            self._breakout_confirm_count = 1
            self._breakout_price = bar.close
            self._state = self.CONFIRMING_BREAKOUT
            # Check if single-bar confirmation is enough
            if self._config.breakout_confirm_bars <= 1:
                return self._emit_signal(bar)
            return None

        # Check for downside breakout start
        if bar.close < box.bottom:
            self._breakout_direction = Direction.SHORT
            self._breakout_confirm_count = 1
            self._breakout_price = bar.close
            self._state = self.CONFIRMING_BREAKOUT
            if self._config.breakout_confirm_bars <= 1:
                return self._emit_signal(bar)
            return None

        # Price contained — box remains active
        return None

    def _process_confirming_breakout(self, bar: Bar) -> Optional[BreakoutSignal]:
        """Count consecutive bars confirming the breakout direction."""
        box = self._active_box
        if box is None:
            self._reset_to_seeking()
            return None

        if self._breakout_direction == Direction.LONG:
            if bar.close > box.top:
                self._breakout_confirm_count += 1
                self._breakout_price = bar.close
                if self._breakout_confirm_count >= self._config.breakout_confirm_bars:
                    return self._emit_signal(bar)
            else:
                # Failed breakout — price fell back into box
                self._breakout_confirm_count = 0
                self._breakout_direction = None
                # Check if it broke down instead
                if bar.close < box.bottom:
                    self._breakout_direction = Direction.SHORT
                    self._breakout_confirm_count = 1
                    self._breakout_price = bar.close
                    if self._config.breakout_confirm_bars <= 1:
                        return self._emit_signal(bar)
                else:
                    self._state = self.BOX_ACTIVE

        elif self._breakout_direction == Direction.SHORT:
            if bar.close < box.bottom:
                self._breakout_confirm_count += 1
                self._breakout_price = bar.close
                if self._breakout_confirm_count >= self._config.breakout_confirm_bars:
                    return self._emit_signal(bar)
            else:
                # Failed breakout — price moved back into box
                self._breakout_confirm_count = 0
                self._breakout_direction = None
                # Check if it broke up instead
                if bar.close > box.top:
                    self._breakout_direction = Direction.LONG
                    self._breakout_confirm_count = 1
                    self._breakout_price = bar.close
                    if self._config.breakout_confirm_bars <= 1:
                        return self._emit_signal(bar)
                else:
                    self._state = self.BOX_ACTIVE

        return None

    # ── Helpers ─────────────────────────────────────────────────────────

    def _emit_signal(self, bar: Bar) -> BreakoutSignal:
        """Create and return a BreakoutSignal, then reset state."""
        signal = BreakoutSignal(
            timestamp=bar.timestamp,
            direction=self._breakout_direction,
            box=self._active_box,
            breakout_price=self._breakout_price,
            breakout_bar_index=self._bar_index,
            atr=self._atr,
        )
        # Reset: after a breakout, start looking for the next box
        self._active_box = None
        self._reset_to_seeking()
        return signal

    def _reset_to_seeking(self) -> None:
        """Reset state machine to seek a new box top."""
        self._state = self.SEEKING_TOP
        self._candidate_top = -math.inf
        self._candidate_top_bar = 0
        self._bars_since_new_high = 0
        self._candidate_bottom = math.inf
        self._candidate_bottom_bar = 0
        self._bars_since_new_low = 0
        self._breakout_direction = None
        self._breakout_confirm_count = 0
        self._breakout_price = 0.0

    def _update_atr(self, bar: Bar) -> None:
        """Update ATR using exponential moving average of true range."""
        if self._prev_close > 0:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        else:
            tr = bar.high - bar.low

        self._prev_close = bar.close

        if self._atr_count < self._atr_period:
            # Seed phase: simple average
            self._atr_count += 1
            self._atr = self._atr + (tr - self._atr) / self._atr_count
        else:
            # EMA update
            alpha = 2.0 / (self._atr_period + 1)
            self._atr = self._atr * (1 - alpha) + tr * alpha

    def reset(self) -> None:
        """Full reset — clear all state. Used between trading sessions."""
        self._state = self.SEEKING_TOP
        self._bar_index = -1
        self._candidate_top = -math.inf
        self._candidate_top_bar = 0
        self._bars_since_new_high = 0
        self._confirmed_top = 0.0
        self._confirmed_top_bar = 0
        self._candidate_bottom = math.inf
        self._candidate_bottom_bar = 0
        self._bars_since_new_low = 0
        self._formation_start = 0
        self._active_box = None
        self._breakout_direction = None
        self._breakout_confirm_count = 0
        self._breakout_price = 0.0
        self._atr = 0.0
        self._atr_count = 0
        self._prev_close = 0.0
