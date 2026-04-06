"""
Tests for Retest Detector -- break -> pullback -> rebreak state machine.

Phase 1 -- Test Specifications (intent + regression for each design decision):

1. Initial break detection: resistance break -> LONG pending, support break -> SHORT pending
   Intent: Only track levels that price has actually broken through.
   Regression: Tracking levels that haven't been broken (price inside level).

2. Pullback detection: price must return to/beyond the level after breaking
   Intent: Confirm the level matters -- price revisits it before continuing.
   Regression: Rebreak signal fires without pullback (just a continuation, not a retest).

3. Rebreak detection: after pullback, price closes beyond level again -> signal
   Intent: The full break -> pullback -> rebreak pattern confirms the level is meaningful.
   Regression: Signal fires on the initial break (no retest), or never fires after valid retest.

4. Min pullback bars: rebreak is invalid before min_pullback_bars elapsed
   Intent: Avoid noise -- immediate bounce back is not a meaningful pullback.
   Regression: Signal fires 1 bar after break (noise, not a real pullback).

5. Max pullback bars (timeout): pending retest expires after max_pullback_bars
   Intent: Stale breaks lose relevance -- don't wait forever for a rebreak.
   Regression: Level stays pending indefinitely, producing signals from ancient breaks.

6. Cooldown: after entry or expiry, level is ignored for cooldown_bars
   Intent: Prevent multiple signals from the same level in quick succession.
   Regression: Same level triggers signal, expires, then triggers again immediately.

7. One pending per level: don't create duplicate tracking for the same level
   Intent: Each level has exactly one state machine at a time.
   Regression: Multiple pending retests for the same price, producing duplicate signals.

8. Upstream expiry cleanup: levels removed from active list are dropped from pending
   Intent: Don't track retests for levels the upstream detector has expired.
   Regression: Orphaned pending retests for expired levels produce stale signals.

9. Direction correctness: LONG rebreak for resistance, SHORT rebreak for support
   Intent: Direction must match the level type through the entire cycle.
   Regression: LONG signal at a support level or SHORT at a resistance level.

10. Signal content: RetestSignal contains correct level, prices, timing, and ATR
    Intent: Downstream trade management has all information needed for SL/TP.
    Regression: Missing or wrong fields in the signal (wrong level price, wrong bar index).

11. Reset clears all state: pending retests, cooldowns, bar index
    Intent: Clean separation between instruments or sessions.
    Regression: State from previous instrument leaks into next one.

Phase 2 -- Test Implementation below.
"""
from datetime import datetime, timezone, timedelta
from typing import List

import pytest

from v11.core.types import (
    Bar, Direction, LevelType, RetestSignal, RetestState, SwingLevel,
)
from v11.core.retest_detector import RetestDetector


# -- Helpers -------------------------------------------------------------------

BASE_TIME = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


def make_bar(close: float, index: int, high: float = None,
             low: float = None) -> Bar:
    """Create a test bar at BASE_TIME + index minutes."""
    if high is None:
        high = close + 0.0001
    if low is None:
        low = close - 0.0001
    return Bar(
        timestamp=BASE_TIME + timedelta(minutes=index),
        open=close,
        high=high,
        low=low,
        close=close,
        tick_count=100,
        buy_volume=50.0,
        sell_volume=50.0,
    )


def make_resistance(price: float) -> SwingLevel:
    """Create a resistance swing level."""
    return SwingLevel(
        price=price,
        level_type=LevelType.RESISTANCE,
        origin_time=BASE_TIME - timedelta(hours=8),
        htf_bar_minutes=240,
    )


def make_support(price: float) -> SwingLevel:
    """Create a support swing level."""
    return SwingLevel(
        price=price,
        level_type=LevelType.SUPPORT,
        origin_time=BASE_TIME - timedelta(hours=8),
        htf_bar_minutes=240,
    )


ATR = 0.0010  # 10 pips for EURUSD-like data


# -- 1. Initial break detection ------------------------------------------------

class TestInitialBreakDetection:
    """Design decision: resistance break -> LONG pending, support break -> SHORT pending."""

    def test_resistance_break_creates_pending(self):
        """Close above resistance -> level tracked as BROKEN (LONG)."""
        det = RetestDetector(min_pullback_bars=5, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]
        bar = make_bar(1.1010, 0)  # above resistance
        signals = det.add_bar(bar, levels, ATR)
        assert signals == []  # no signal on initial break
        assert det.pending_count == 1

    def test_support_break_creates_pending(self):
        """Close below support -> level tracked as BROKEN (SHORT)."""
        det = RetestDetector(min_pullback_bars=5, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_support(1.1000)]
        bar = make_bar(1.0990, 0)  # below support
        signals = det.add_bar(bar, levels, ATR)
        assert signals == []
        assert det.pending_count == 1

    def test_no_break_no_pending(self):
        """Close at the level (not beyond) -> nothing tracked."""
        det = RetestDetector(min_pullback_bars=5, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]
        bar = make_bar(1.0990, 0)  # below resistance, no break
        signals = det.add_bar(bar, levels, ATR)
        assert signals == []
        assert det.pending_count == 0


# -- 2. Pullback detection -----------------------------------------------------

class TestPullbackDetection:
    """Design decision: price must return to/beyond the level after breaking."""

    def test_long_pullback_detected(self):
        """After resistance break, close back at/below level = pullback."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break above
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        assert det.pending_count == 1

        # Bar 1: price returns to level (pullback)
        det.add_bar(make_bar(1.0995, 1), levels, ATR)
        # Still pending, but now pulled_back=True internally
        assert det.pending_count == 1

    def test_short_pullback_detected(self):
        """After support break, close back at/above level = pullback."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_support(1.1000)]

        # Bar 0: break below
        det.add_bar(make_bar(1.0990, 0), levels, ATR)
        # Bar 1: price returns to level
        det.add_bar(make_bar(1.1005, 1), levels, ATR)
        assert det.pending_count == 1

    def test_no_signal_without_pullback(self):
        """Price breaks and stays beyond -> no signal (continuation, not retest)."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        # Bars 1-15: price stays above, never pulls back
        for i in range(1, 16):
            signals = det.add_bar(make_bar(1.1020 + i * 0.0001, i), levels, ATR)
            assert signals == []


# -- 3. Rebreak detection (full cycle) -----------------------------------------

class TestRebreakDetection:
    """Design decision: break -> pullback -> rebreak = entry signal."""

    def test_long_rebreak_produces_signal(self):
        """Full LONG cycle: break above -> pullback below -> rebreak above."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break above resistance
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        # Bar 1: pullback to level
        det.add_bar(make_bar(1.0995, 1), levels, ATR)
        # Bar 2: still below (waiting for min_pullback_bars)
        det.add_bar(make_bar(1.0998, 2), levels, ATR)
        # Bar 3: rebreak above (elapsed=3, meets min_pullback_bars=3)
        signals = det.add_bar(make_bar(1.1015, 3), levels, ATR)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == Direction.LONG
        assert sig.level_price == 1.1000
        assert sig.breakout_price == 1.1015
        assert sig.pullback_bars == 3
        assert det.pending_count == 0

    def test_short_rebreak_produces_signal(self):
        """Full SHORT cycle: break below -> pullback above -> rebreak below."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_support(1.1000)]

        # Bar 0: break below support
        det.add_bar(make_bar(1.0990, 0), levels, ATR)
        # Bar 1: pullback above level
        det.add_bar(make_bar(1.1005, 1), levels, ATR)
        # Bar 2: still above
        det.add_bar(make_bar(1.1002, 2), levels, ATR)
        # Bar 3: rebreak below
        signals = det.add_bar(make_bar(1.0985, 3), levels, ATR)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.direction == Direction.SHORT
        assert sig.level_price == 1.1000
        assert sig.breakout_price == 1.0985


# -- 4. Min pullback bars ------------------------------------------------------

class TestMinPullbackBars:
    """Design decision: rebreak before min_pullback_bars is invalid."""

    def test_rebreak_too_early_is_ignored(self):
        """Rebreak at elapsed=2 with min_pullback_bars=5 -> no signal."""
        det = RetestDetector(min_pullback_bars=5, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        # Bar 1: pullback
        det.add_bar(make_bar(1.0995, 1), levels, ATR)
        # Bar 2: rebreak attempt (too early, elapsed=2 < min=5)
        signals = det.add_bar(make_bar(1.1010, 2), levels, ATR)
        assert signals == []
        assert det.pending_count == 1  # still tracking

    def test_rebreak_at_exact_min_succeeds(self):
        """Rebreak at exactly min_pullback_bars -> signal fires."""
        det = RetestDetector(min_pullback_bars=5, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        # Bar 1: pullback
        det.add_bar(make_bar(1.0995, 1), levels, ATR)
        # Bars 2-4: stay below level
        for i in range(2, 5):
            det.add_bar(make_bar(1.0990, i), levels, ATR)
        # Bar 5: rebreak (elapsed=5, exactly min_pullback_bars)
        signals = det.add_bar(make_bar(1.1010, 5), levels, ATR)
        assert len(signals) == 1
        assert signals[0].pullback_bars == 5


# -- 5. Max pullback bars (timeout) --------------------------------------------

class TestMaxPullbackBars:
    """Design decision: pending retest expires after max_pullback_bars."""

    def test_timeout_removes_pending(self):
        """Level pending for > max_pullback_bars -> removed, no signal."""
        det = RetestDetector(min_pullback_bars=5, max_pullback_bars=10,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        # Bars 1-10: price stays above, no pullback
        for i in range(1, 11):
            det.add_bar(make_bar(1.1020, i), levels, ATR)
        # Bar 11: elapsed=11 > max=10, should be expired
        signals = det.add_bar(make_bar(1.1020, 11), levels, ATR)
        assert signals == []
        assert det.pending_count == 0

    def test_rebreak_at_exact_max_succeeds(self):
        """Rebreak at exactly max_pullback_bars -> signal still fires."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=10,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        # Bar 1: pullback
        det.add_bar(make_bar(1.0995, 1), levels, ATR)
        # Bars 2-9: stay below
        for i in range(2, 10):
            det.add_bar(make_bar(1.0990, i), levels, ATR)
        # Bar 10: rebreak at exact max (elapsed=10, max=10, NOT > max)
        signals = det.add_bar(make_bar(1.1010, 10), levels, ATR)
        assert len(signals) == 1
        assert signals[0].pullback_bars == 10


# -- 6. Cooldown ---------------------------------------------------------------

class TestCooldown:
    """Design decision: after signal or expiry, level ignored for cooldown_bars."""

    def test_cooldown_after_signal(self):
        """After a rebreak signal, same level can't trigger again for cooldown_bars."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=10)
        levels = [make_resistance(1.1000)]

        # Full cycle: break -> pullback -> rebreak -> signal at bar 3
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        det.add_bar(make_bar(1.0995, 1), levels, ATR)
        det.add_bar(make_bar(1.0990, 2), levels, ATR)
        signals = det.add_bar(make_bar(1.1010, 3), levels, ATR)
        assert len(signals) == 1

        # Bar 4: try to break again immediately (cooldown active)
        det.add_bar(make_bar(1.0990, 4), levels, ATR)  # below level
        det.add_bar(make_bar(1.1010, 5), levels, ATR)  # break again
        assert det.pending_count == 0  # blocked by cooldown

    def test_cooldown_expires(self):
        """After cooldown_bars, level can be tracked again."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=10)
        levels = [make_resistance(1.1000)]

        # Full cycle ending at bar 3
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        det.add_bar(make_bar(1.0995, 1), levels, ATR)
        det.add_bar(make_bar(1.0990, 2), levels, ATR)
        det.add_bar(make_bar(1.1010, 3), levels, ATR)

        # Bars 4-12: cooldown period (bars below level)
        for i in range(4, 13):
            det.add_bar(make_bar(1.0990, i), levels, ATR)
        assert det.pending_count == 0

        # Bar 13: cooldown_bars=10, last signal at bar 3, so 13-3=10 -> expired
        # Now a new break should be tracked
        det.add_bar(make_bar(1.1010, 13), levels, ATR)
        assert det.pending_count == 1

    def test_cooldown_after_timeout(self):
        """After timeout expiry, cooldown prevents immediate re-tracking."""
        det = RetestDetector(min_pullback_bars=5, max_pullback_bars=10,
                             cooldown_bars=20)
        levels = [make_resistance(1.1000)]

        # Bar 0: break
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        # Bars 1-10: no pullback, timeout at bar 11
        for i in range(1, 12):
            det.add_bar(make_bar(1.1020, i), levels, ATR)
        assert det.pending_count == 0  # timed out

        # Bar 12: try to break again (cooldown active, set at bar 11)
        det.add_bar(make_bar(1.0990, 12), levels, ATR)
        det.add_bar(make_bar(1.1010, 13), levels, ATR)
        assert det.pending_count == 0  # still in cooldown


# -- 7. One pending per level ---------------------------------------------------

class TestOnePendingPerLevel:
    """Design decision: each level has exactly one state machine at a time."""

    def test_no_duplicate_pending(self):
        """Breaking the same level twice doesn't create two pending entries."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Bar 0: break above
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        assert det.pending_count == 1

        # Bar 1: still above (would be another "break" but already pending)
        det.add_bar(make_bar(1.1020, 1), levels, ATR)
        assert det.pending_count == 1


# -- 8. Upstream expiry cleanup -------------------------------------------------

class TestUpstreamExpiryCleanup:
    """Design decision: levels removed from active list are dropped from pending."""

    def test_expired_level_removed_from_pending(self):
        """If level disappears from active_levels, pending retest is dropped."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=60)
        resistance = make_resistance(1.1000)

        # Bar 0: break with level active
        det.add_bar(make_bar(1.1010, 0), [resistance], ATR)
        assert det.pending_count == 1

        # Bar 1: level no longer in active list (expired upstream)
        det.add_bar(make_bar(1.0990, 1), [], ATR)
        assert det.pending_count == 0

    def test_other_levels_unaffected_by_expiry(self):
        """Only the expired level is removed; other pending retests survive."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=60)
        r1 = make_resistance(1.1000)
        r2 = make_resistance(1.1100)

        # Bar 0: break both levels
        det.add_bar(make_bar(1.1110, 0), [r1, r2], ATR)
        assert det.pending_count == 2

        # Bar 1: only r1 still active
        det.add_bar(make_bar(1.1110, 1), [r1], ATR)
        assert det.pending_count == 1


# -- 9. Direction correctness --------------------------------------------------

class TestDirectionCorrectness:
    """Design decision: LONG for resistance, SHORT for support, through full cycle."""

    def test_resistance_retest_is_long(self):
        """Resistance break -> pullback -> rebreak produces LONG signal."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        det.add_bar(make_bar(1.0990, 1), levels, ATR)
        signals = det.add_bar(make_bar(1.1010, 2), levels, ATR)
        assert len(signals) == 1
        assert signals[0].direction == Direction.LONG

    def test_support_retest_is_short(self):
        """Support break -> pullback -> rebreak produces SHORT signal."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_support(1.1000)]

        det.add_bar(make_bar(1.0990, 0), levels, ATR)
        det.add_bar(make_bar(1.1010, 1), levels, ATR)
        signals = det.add_bar(make_bar(1.0990, 2), levels, ATR)
        assert len(signals) == 1
        assert signals[0].direction == Direction.SHORT


# -- 10. Signal content ---------------------------------------------------------

class TestSignalContent:
    """Design decision: RetestSignal contains all info for downstream SL/TP."""

    def test_signal_fields_are_correct(self):
        """Verify all fields of the emitted RetestSignal."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=60)
        resistance = make_resistance(1.1000)

        det.add_bar(make_bar(1.1010, 0), [resistance], ATR)
        det.add_bar(make_bar(1.0995, 1), [resistance], ATR)
        det.add_bar(make_bar(1.0990, 2), [resistance], ATR)
        signals = det.add_bar(make_bar(1.1020, 3), [resistance], ATR)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.timestamp == BASE_TIME + timedelta(minutes=3)
        assert sig.direction == Direction.LONG
        assert sig.level is resistance
        assert sig.breakout_price == 1.1020
        assert sig.level_price == 1.1000
        assert sig.atr == ATR
        assert sig.break_bar_index == 0
        assert sig.rebreak_bar_index == 3
        assert sig.pullback_bars == 3

    def test_signal_for_support_has_correct_level(self):
        """SHORT signal references the correct support level object."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        support = make_support(1.0900)

        det.add_bar(make_bar(1.0890, 0), [support], ATR)
        det.add_bar(make_bar(1.0910, 1), [support], ATR)
        signals = det.add_bar(make_bar(1.0880, 2), [support], ATR)

        assert len(signals) == 1
        sig = signals[0]
        assert sig.level is support
        assert sig.level_price == 1.0900


# -- 11. Reset clears all state -------------------------------------------------

class TestReset:
    """Design decision: reset() provides clean separation between sessions."""

    def test_reset_clears_pending(self):
        """After reset, no pending retests remain."""
        det = RetestDetector(min_pullback_bars=3, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        assert det.pending_count == 1

        det.reset()
        assert det.pending_count == 0

    def test_reset_clears_cooldowns(self):
        """After reset, cooldowns don't carry over."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=100)
        levels = [make_resistance(1.1000)]

        # Full cycle -> signal -> cooldown active
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        det.add_bar(make_bar(1.0990, 1), levels, ATR)
        det.add_bar(make_bar(1.1010, 2), levels, ATR)

        det.reset()

        # After reset, level should be trackable again
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        assert det.pending_count == 1

    def test_reset_allows_fresh_bar_indexing(self):
        """After reset, bar index restarts from -1."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        levels = [make_resistance(1.1000)]

        # Feed some bars
        for i in range(10):
            det.add_bar(make_bar(1.0990, i), levels, ATR)

        det.reset()

        # New cycle should work with fresh indexing
        det.add_bar(make_bar(1.1010, 0), levels, ATR)
        det.add_bar(make_bar(1.0990, 1), levels, ATR)
        signals = det.add_bar(make_bar(1.1010, 2), levels, ATR)
        assert len(signals) == 1
        assert signals[0].break_bar_index == 0
        assert signals[0].rebreak_bar_index == 2


# -- 12. Multiple levels simultaneously ----------------------------------------

class TestMultipleLevels:
    """Multiple levels can be tracked independently at the same time."""

    def test_two_levels_independent_signals(self):
        """Two resistance levels at different prices produce independent signals."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        r1 = make_resistance(1.1000)
        r2 = make_resistance(1.1100)

        # Bar 0: break both
        det.add_bar(make_bar(1.1110, 0), [r1, r2], ATR)
        assert det.pending_count == 2

        # Bar 1: pullback to both levels
        det.add_bar(make_bar(1.0990, 1), [r1, r2], ATR)

        # Bar 2: rebreak both
        signals = det.add_bar(make_bar(1.1110, 2), [r1, r2], ATR)
        assert len(signals) == 2
        prices = {s.level_price for s in signals}
        assert prices == {1.1000, 1.1100}

    def test_resistance_and_support_independent(self):
        """Resistance and support levels tracked independently."""
        det = RetestDetector(min_pullback_bars=2, max_pullback_bars=30,
                             cooldown_bars=60)
        resistance = make_resistance(1.1100)
        support = make_support(1.0900)

        # Bar 0: break resistance (close above 1.1100)
        det.add_bar(make_bar(1.1110, 0), [resistance, support], ATR)
        assert det.pending_count == 1  # only resistance broken

        # Bar 1: pullback below resistance
        det.add_bar(make_bar(1.1090, 1), [resistance, support], ATR)

        # Bar 2: rebreak resistance
        signals = det.add_bar(make_bar(1.1110, 2), [resistance, support], ATR)
        assert len(signals) == 1
        assert signals[0].direction == Direction.LONG
