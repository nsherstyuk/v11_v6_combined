"""
Retest Detector — Break → pullback → rebreak state machine for 4H level signals.

Design decisions (V11_DESIGN.md §12):
    - WATCHING: level is active, monitor 1-min bars for initial break
    - BROKEN: 1-min bar closes beyond the level. Do NOT enter. Wait for pullback.
    - RETESTING: price pulls back toward the level. Timer starts.
    - REBREAK: 1-min bar closes beyond the level again → entry signal.
    - EXPIRED: pullback/rebreak doesn't happen within max_pullback_bars → reset.
    - Cooldown: after entry or expiry at a level, ignore it for cooldown_bars.
    - One pending retest per level (no duplicate tracking for same level).

Two usage modes:
    1. add_bar() — incremental, feed 1-min bars one at a time (live + backtest).
    2. RetestDetector is stateful; call reset() between instruments or sessions.

The detector does NOT own levels. It receives active levels from the
SwingLevelDetector (or BatchSwingLevelDetector) on each bar and tracks
state per level internally.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .types import Bar, Direction, LevelType, RetestSignal, RetestState, SwingLevel


# ── Internal tracking for a single level's retest state ───────────────────

@dataclass
class _PendingRetest:
    """Mutable internal state for a level being tracked through the retest cycle."""
    level: SwingLevel
    direction: Direction
    break_bar_index: int
    state: RetestState = RetestState.BROKEN
    pulled_back: bool = False


def _level_key(level: SwingLevel) -> Tuple[str, float]:
    """Unique key for a level: (level_type, price)."""
    return (level.level_type.value, level.price)


# ── Retest Detector ──────────────────────────────────────────────────────

class RetestDetector:
    """Monitors 1-min bars for break → pullback → rebreak at swing levels.

    Interface (narrow):
        add_bar(bar, active_levels, atr) -> List[RetestSignal]
        get_pending_count() -> int
        reset()

    The detector is agnostic to how levels are produced — it works with any
    List[SwingLevel] provided on each bar. Levels that disappear from the
    active list (expired upstream) are automatically cleaned up.
    """

    def __init__(self, min_pullback_bars: int, max_pullback_bars: int,
                 cooldown_bars: int):
        """
        Args:
            min_pullback_bars: Minimum bars after break before rebreak is valid.
            max_pullback_bars: Maximum bars for the full break → rebreak cycle.
            cooldown_bars: After entry or expiry, ignore the level for this many bars.
        """
        self._min_pb = min_pullback_bars
        self._max_pb = max_pullback_bars
        self._cooldown = cooldown_bars

        self._bar_index: int = -1
        self._pending: Dict[Tuple[str, float], _PendingRetest] = {}
        self._cooldowns: Dict[Tuple[str, float], int] = {}  # key -> bar index when cooldown started

    @property
    def pending_count(self) -> int:
        """Number of levels currently being tracked (BROKEN or RETESTING)."""
        return len(self._pending)

    def get_pending_details(self) -> List[dict]:
        """Diagnostic snapshot of all pending retests.

        Read-only edge access for logging. Never use for trading decisions.
        """
        details = []
        for key, p in self._pending.items():
            elapsed = self._bar_index - p.break_bar_index
            details.append({
                "level_price": p.level.price,
                "level_type": p.level.level_type.value,
                "direction": p.direction.value,
                "state": p.state.value,
                "elapsed_bars": elapsed,
                "max_bars": self._max_pb,
                "pulled_back": p.pulled_back,
            })
        return details

    def add_bar(self, bar: Bar, active_levels: List[SwingLevel],
                atr: float) -> List[RetestSignal]:
        """Process a 1-min bar against active levels. Returns retest signals.

        Args:
            bar: A 1-minute bar from the live or backtest stream.
            active_levels: Currently active swing levels (from level detector).
            atr: Current ATR value (for pullback tolerance — unused in base
                 retest logic, but included on the signal for downstream SL/TP).

        Returns:
            List of RetestSignal objects. Usually empty; at most one per bar
            per level in practice.
        """
        self._bar_index += 1
        signals: List[RetestSignal] = []

        # Build set of currently active level keys for cleanup
        active_keys = {_level_key(lv) for lv in active_levels}

        # Remove pending retests for levels no longer active (expired upstream)
        self._pending = {
            k: v for k, v in self._pending.items() if k in active_keys
        }

        # 1. Check for new breaks at levels we're not already tracking
        for lv in active_levels:
            key = _level_key(lv)

            # Skip if already pending
            if key in self._pending:
                continue

            # Skip if in cooldown
            if key in self._cooldowns:
                if self._bar_index - self._cooldowns[key] < self._cooldown:
                    continue
                else:
                    del self._cooldowns[key]

            # Check for initial break
            if lv.level_type == LevelType.RESISTANCE and bar.close > lv.price:
                self._pending[key] = _PendingRetest(
                    level=lv,
                    direction=Direction.LONG,
                    break_bar_index=self._bar_index,
                )
            elif lv.level_type == LevelType.SUPPORT and bar.close < lv.price:
                self._pending[key] = _PendingRetest(
                    level=lv,
                    direction=Direction.SHORT,
                    break_bar_index=self._bar_index,
                )

        # 2. Update pending retests — check pullback and rebreak
        expired_keys: List[Tuple[str, float]] = []
        for key, p in self._pending.items():
            elapsed = self._bar_index - p.break_bar_index

            # Timeout — max pullback window exceeded
            if elapsed > self._max_pb:
                self._cooldowns[key] = self._bar_index
                expired_keys.append(key)
                continue

            # Check pullback (price returns toward the level)
            if not p.pulled_back:
                if p.direction == Direction.LONG and bar.close <= p.level.price:
                    p.pulled_back = True
                    p.state = RetestState.RETESTING
                elif p.direction == Direction.SHORT and bar.close >= p.level.price:
                    p.pulled_back = True
                    p.state = RetestState.RETESTING
                continue

            # Pulled back — check timing constraint
            if elapsed < self._min_pb:
                continue

            # Check rebreak
            if p.direction == Direction.LONG and bar.close > p.level.price:
                signals.append(RetestSignal(
                    timestamp=bar.timestamp,
                    direction=Direction.LONG,
                    level=p.level,
                    breakout_price=bar.close,
                    level_price=p.level.price,
                    atr=atr,
                    break_bar_index=p.break_bar_index,
                    rebreak_bar_index=self._bar_index,
                    pullback_bars=elapsed,
                ))
                self._cooldowns[key] = self._bar_index
                expired_keys.append(key)

            elif p.direction == Direction.SHORT and bar.close < p.level.price:
                signals.append(RetestSignal(
                    timestamp=bar.timestamp,
                    direction=Direction.SHORT,
                    level=p.level,
                    breakout_price=bar.close,
                    level_price=p.level.price,
                    atr=atr,
                    break_bar_index=p.break_bar_index,
                    rebreak_bar_index=self._bar_index,
                    pullback_bars=elapsed,
                ))
                self._cooldowns[key] = self._bar_index
                expired_keys.append(key)

        # Remove completed/expired entries
        for key in expired_keys:
            self._pending.pop(key, None)

        return signals

    def reset(self) -> None:
        """Clear all state."""
        self._bar_index = -1
        self._pending.clear()
        self._cooldowns.clear()
