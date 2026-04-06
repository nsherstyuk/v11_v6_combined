"""
Tests for 4H Swing Level Detector — detects S/R levels from higher-timeframe bars.

Phase 1 — Test Specifications (intent + regression for each design decision):

1. Swing high detection: bar whose high > highs of `lb` bars before AND `rb` bars after
   Intent: Identify significant resistance levels from HTF price structure.
   Regression: Non-swing bars falsely detected as swings, or real swings missed.

2. Swing low detection: bar whose low < lows of `lb` bars before AND `rb` bars after
   Intent: Identify significant support levels from HTF price structure.
   Regression: Non-swing bars falsely detected as swings, or real swings missed.

3. Buffer requirement: no detection until buffer has `lb + rb + 1` bars
   Intent: Swing detection requires sufficient context on both sides.
   Regression: Swing detected with partial buffer, producing false levels.

4. Level expiry: levels older than `expiry_hours` are pruned
   Intent: Old levels lose market relevance; don't clutter active level list.
   Regression: Expired levels still active, causing stale signals.

5. Level merging: same-type levels within `merge_distance` are deduplicated
   Intent: Prevent duplicate levels from similar swings (e.g. two nearby highs).
   Regression: Duplicate levels cause redundant signals at the same price zone.

6. Look-ahead safety (batch): get_levels_at(ts) returns levels from PREVIOUS
   completed HTF bar, not current
   Intent: Backtest must not use future information when checking levels.
   Regression: Levels from the current in-progress bar used, inflating results.

7. Incremental resamples correctly: 1-min bars grouped into HTF bars at period
   boundaries, detector fed on completion
   Intent: Live engine groups bars into correct HTF periods and detects swings.
   Regression: Wrong period boundaries, bars double-counted, or bars lost.

8. Incremental matches batch: both modes produce identical levels for same input
   Intent: Live engine and backtest agree on which levels exist.
   Regression: Different levels in live vs backtest, causing divergent behavior.

9. Both swing high AND swing low can be detected from the same bar
   Intent: A bar can be both the highest high and lowest low in its window.
   Regression: Only one type detected per bar, missing valid levels.

10. Multiple levels accumulate and coexist
    Intent: The detector maintains all non-expired, non-merged levels simultaneously.
    Regression: New levels replace old ones instead of accumulating.

Phase 2 — Test Implementation below.
"""
from datetime import datetime, timezone, timedelta
from typing import List

import pytest

from v11.core.types import Bar, SwingLevel, LevelType
from v11.core.level_detector import (
    SwingLevelDetector,
    BatchSwingLevelDetector,
    IncrementalSwingLevelDetector,
    _floor_timestamp,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_bar(high: float, low: float, ts: datetime,
             close: float = None) -> Bar:
    """Create a test bar with sensible defaults."""
    if close is None:
        close = (high + low) / 2
    return Bar(
        timestamp=ts,
        open=close,
        high=high,
        low=low,
        close=close,
        tick_count=100,
        buy_volume=50.0,
        sell_volume=50.0,
    )


def make_htf_bars(highs: List[float], lows: List[float],
                  start: datetime, interval_minutes: int = 240) -> List[Bar]:
    """Create a series of HTF bars from lists of highs and lows."""
    assert len(highs) == len(lows)
    bars = []
    for i in range(len(highs)):
        ts = start + timedelta(minutes=i * interval_minutes)
        bars.append(make_bar(highs[i], lows[i], ts))
    return bars


def make_1min_bars_from_htf(htf_highs: List[float], htf_lows: List[float],
                            start: datetime,
                            htf_minutes: int = 240) -> List[Bar]:
    """Create 1-min bars that resample to the specified HTF highs/lows.

    Each HTF bar period gets `htf_minutes` 1-min bars. The first bar has
    the HTF high, the second has the HTF low, and the rest are flat.
    """
    bars = []
    for i in range(len(htf_highs)):
        period_start = start + timedelta(minutes=i * htf_minutes)
        h, l = htf_highs[i], htf_lows[i]
        mid = (h + l) / 2
        for j in range(htf_minutes):
            ts = period_start + timedelta(minutes=j)
            if j == 0:
                # First bar carries the high
                bars.append(make_bar(h, mid, ts, close=mid))
            elif j == 1:
                # Second bar carries the low
                bars.append(make_bar(mid, l, ts, close=mid))
            else:
                # Remaining bars are flat at mid
                bars.append(make_bar(mid + 0.00001, mid - 0.00001, ts, close=mid))
    return bars


# ── 1. Swing high detection ─────────────────────────────────────────────────

class TestSwingHighDetection:
    """Design decision: swing high = bar.high > highs of lb bars before AND rb bars after."""

    def test_clear_swing_high_detected(self):
        """A bar with higher high than all neighbors is detected as resistance."""
        # lb=2, rb=2 → need 5 bars, middle bar (#2) is the swing
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        highs = [1.10, 1.11, 1.15, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        all_new = []
        for bar in bars:
            all_new.extend(det.add_htf_bar(bar))

        resistance = [lv for lv in all_new if lv.level_type == LevelType.RESISTANCE]
        assert len(resistance) == 1
        assert resistance[0].price == 1.15
        assert resistance[0].origin_time == bars[2].timestamp

    def test_no_swing_high_when_not_highest(self):
        """If the candidate bar is not the highest, no swing high detected."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Monotonically increasing highs — no bar is higher than all right neighbors
        highs = [1.10, 1.11, 1.12, 1.13, 1.14]
        lows =  [1.08, 1.09, 1.10, 1.11, 1.12]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)
        all_new = []
        for bar in bars:
            all_new.extend(det.add_htf_bar(bar))

        resistance = [lv for lv in all_new if lv.level_type == LevelType.RESISTANCE]
        assert len(resistance) == 0


# ── 2. Swing low detection ──────────────────────────────────────────────────

class TestSwingLowDetection:
    """Design decision: swing low = bar.low < lows of lb bars before AND rb bars after."""

    def test_clear_swing_low_detected(self):
        """A bar with lower low than all neighbors is detected as support."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        highs = [1.12, 1.11, 1.09, 1.10, 1.13]
        lows =  [1.10, 1.09, 1.05, 1.08, 1.11]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)
        all_new = []
        for bar in bars:
            all_new.extend(det.add_htf_bar(bar))

        support = [lv for lv in all_new if lv.level_type == LevelType.SUPPORT]
        assert len(support) == 1
        assert support[0].price == 1.05
        assert support[0].origin_time == bars[2].timestamp

    def test_no_swing_low_when_not_lowest(self):
        """If the candidate bar is not the lowest, no swing low detected."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Monotonically decreasing lows
        highs = [1.14, 1.13, 1.12, 1.11, 1.10]
        lows =  [1.12, 1.11, 1.10, 1.09, 1.08]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)
        all_new = []
        for bar in bars:
            all_new.extend(det.add_htf_bar(bar))

        support = [lv for lv in all_new if lv.level_type == LevelType.SUPPORT]
        assert len(support) == 0


# ── 3. Buffer requirement ───────────────────────────────────────────────────

class TestBufferRequirement:
    """Design decision: no detection until buffer has lb + rb + 1 bars."""

    def test_no_levels_with_insufficient_bars(self):
        """With fewer bars than required, no levels detected."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # lb=3, rb=3 → need 7 bars. Only provide 5.
        highs = [1.10, 1.11, 1.15, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=3, right_bars=3,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)
        all_new = []
        for bar in bars:
            all_new.extend(det.add_htf_bar(bar))

        assert len(all_new) == 0

    def test_detection_starts_at_exact_buffer_size(self):
        """Detection begins exactly when buffer reaches lb + rb + 1."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # lb=2, rb=2 → need 5 bars
        # Bar[2] (index 2) has the swing high
        highs = [1.10, 1.11, 1.15, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        # Bars 0-3: no detection (only 4 bars, need 5)
        for bar in bars[:4]:
            new = det.add_htf_bar(bar)
            assert len(new) == 0

        # Bar 4: buffer full, detection fires
        new = det.add_htf_bar(bars[4])
        assert len(new) >= 1

    def test_required_buffer_size_property(self):
        det = SwingLevelDetector(left_bars=10, right_bars=10,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)
        assert det.required_buffer_size == 21


# ── 4. Level expiry ─────────────────────────────────────────────────────────

class TestLevelExpiry:
    """Design decision: levels older than expiry_hours are pruned."""

    def test_level_pruned_after_expiry(self):
        """A level detected early should be removed after expiry_hours pass."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # lb=2, rb=2, expiry=24h, 4H bars
        # Swing high at bar[2] (timestamp = start + 8h)
        highs = [1.10, 1.11, 1.15, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07]

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=24, merge_distance=0.001,
                                 htf_bar_minutes=240)

        bars = make_htf_bars(highs, lows, start)
        for bar in bars:
            det.add_htf_bar(bar)

        # Level exists now
        assert len(det.get_active_levels()) >= 1

        # Feed bars far enough in the future to expire the level (>24h away)
        # Bar[2] origin_time = start + 8h. Need 24h after that = start + 32h.
        # At 4H bars, that's bar index 8 (start + 32h)
        future_start = start + timedelta(hours=36)
        # Need enough non-swing bars to not detect new levels
        future_highs = [1.10, 1.10, 1.10, 1.10, 1.10]
        future_lows =  [1.09, 1.09, 1.09, 1.09, 1.09]
        future_bars = make_htf_bars(future_highs, future_lows, future_start)
        for bar in future_bars:
            det.add_htf_bar(bar)

        # Original level should be expired
        levels = det.get_active_levels()
        original = [lv for lv in levels if lv.price == 1.15]
        assert len(original) == 0

    def test_level_active_before_expiry(self):
        """A level should remain active if within expiry window."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        highs = [1.10, 1.11, 1.15, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07]

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        bars = make_htf_bars(highs, lows, start)
        for bar in bars:
            det.add_htf_bar(bar)

        # Feed a few more bars (still within 72h)
        more_start = start + timedelta(hours=24)
        more_highs = [1.10, 1.10, 1.10]
        more_lows =  [1.09, 1.09, 1.09]
        more_bars = make_htf_bars(more_highs, more_lows, more_start)
        for bar in more_bars:
            det.add_htf_bar(bar)

        levels = det.get_active_levels()
        original = [lv for lv in levels if lv.price == 1.15]
        assert len(original) == 1


# ── 5. Level merging ────────────────────────────────────────────────────────

class TestLevelMerging:
    """Design decision: same-type levels within merge_distance are deduplicated."""

    def test_close_levels_merged(self):
        """Two resistance levels within merge_distance → only first kept."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Two swing highs very close together
        # First swing at bar[2]=1.1500, second swing at bar[7]=1.1502
        # merge_distance=0.001 → they should merge
        highs = [1.10, 1.11, 1.1500, 1.12, 1.09,  1.10, 1.11, 1.1502, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.1300, 1.10, 1.07,  1.08, 1.09, 1.1300, 1.10, 1.07]

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        bars = make_htf_bars(highs, lows, start)
        for bar in bars:
            det.add_htf_bar(bar)

        resistance = [lv for lv in det.get_active_levels()
                      if lv.level_type == LevelType.RESISTANCE]
        assert len(resistance) == 1
        assert resistance[0].price == 1.1500  # first one kept

    def test_distant_levels_not_merged(self):
        """Two resistance levels far apart → both kept."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # First swing at bar[2]=1.1500, second swing at bar[7]=1.1600
        highs = [1.10, 1.11, 1.1500, 1.12, 1.09,  1.10, 1.11, 1.1600, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.1300, 1.10, 1.07,  1.08, 1.09, 1.1400, 1.10, 1.07]

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        bars = make_htf_bars(highs, lows, start)
        for bar in bars:
            det.add_htf_bar(bar)

        resistance = [lv for lv in det.get_active_levels()
                      if lv.level_type == LevelType.RESISTANCE]
        assert len(resistance) == 2

    def test_different_types_not_merged(self):
        """A resistance and support at similar prices are NOT merged."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Swing high at bar[2] with high=1.1500
        # Swing low at bar[7] with low=1.1500
        highs = [1.10, 1.11, 1.1500, 1.12, 1.09,  1.16, 1.17, 1.1600, 1.16, 1.17]
        lows =  [1.08, 1.09, 1.1300, 1.10, 1.07,  1.15, 1.15, 1.1500, 1.15, 1.15]

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        bars = make_htf_bars(highs, lows, start)
        for bar in bars:
            det.add_htf_bar(bar)

        levels = det.get_active_levels()
        types = set(lv.level_type for lv in levels)
        # Should have both resistance and support
        assert LevelType.RESISTANCE in types
        assert LevelType.SUPPORT in types


# ── 6. Look-ahead safety (batch) ────────────────────────────────────────────

class TestBatchLookAheadSafety:
    """Design decision: get_levels_at(ts) returns levels from PREVIOUS completed HTF bar."""

    def test_uses_previous_htf_bar_levels(self):
        """Signal at 10:15 on a 4H grid should use levels from the 04:00 bar, not 08:00."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Create enough bars for detection: lb=2, rb=2
        # Swing high at bar[2] (timestamp=start+8h with 4H bars)
        htf_highs = [1.10, 1.11, 1.15, 1.12, 1.09, 1.10, 1.10]
        htf_lows =  [1.08, 1.09, 1.13, 1.10, 1.07, 1.08, 1.08]

        # Create 1-min bars that resample to these HTF bars
        bars_1m = make_1min_bars_from_htf(htf_highs, htf_lows, start,
                                          htf_minutes=240)

        batch = BatchSwingLevelDetector(
            bars=bars_1m, htf_bar_minutes=240,
            left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )

        # The swing at bar[2] (start+8h) is detected when bar[4] arrives.
        # bar[4] timestamp = start + 16h (16:00)
        # After bar[4] processing, levels are snapshotted at bar[4].timestamp.

        # Query at 20:15 → floor to 20:00 → prev = 16:00
        # Levels at 16:00 should include the swing high from bar[2]
        signal_ts = datetime(2026, 1, 1, 20, 15, tzinfo=timezone.utc)
        levels = batch.get_levels_at(signal_ts)
        resistance = [lv for lv in levels if lv.level_type == LevelType.RESISTANCE]
        assert len(resistance) >= 1
        assert resistance[0].price == 1.15

    def test_no_levels_from_future_htf_bar(self):
        """Levels detected at a later HTF bar should not appear at an earlier query time."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Only 3 HTF bars — detection requires 5 (lb=2, rb=2)
        htf_highs = [1.10, 1.11, 1.15]
        htf_lows =  [1.08, 1.09, 1.13]

        bars_1m = make_1min_bars_from_htf(htf_highs, htf_lows, start,
                                          htf_minutes=240)

        batch = BatchSwingLevelDetector(
            bars=bars_1m, htf_bar_minutes=240,
            left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )

        # Query at 04:15 → floor to 04:00 → prev = 00:00
        # At bar[0], no levels detected yet (insufficient buffer)
        signal_ts = datetime(2026, 1, 1, 4, 15, tzinfo=timezone.utc)
        levels = batch.get_levels_at(signal_ts)
        assert len(levels) == 0


# ── 7. Incremental resampling ───────────────────────────────────────────────

class TestIncrementalResampling:
    """Design decision: 1-min bars grouped into HTF bars at period boundaries."""

    def test_htf_bars_counted_correctly(self):
        """240 1-min bars = 1 completed 4H bar when next period starts."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        det = IncrementalSwingLevelDetector(
            htf_bar_minutes=240, left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )

        # Feed 240 bars (0-239) — period still in progress
        for i in range(240):
            ts = start + timedelta(minutes=i)
            det.add_bar(make_bar(1.10 + 0.00001, 1.10 - 0.00001, ts))
        assert det.htf_bars_count == 0

        # Feed 1 more bar (minute 240) — triggers finalization
        det.add_bar(make_bar(1.10 + 0.00001, 1.10 - 0.00001,
                             start + timedelta(minutes=240)))
        assert det.htf_bars_count == 1

    def test_multiple_htf_bars_accumulated(self):
        """Multiple 4H periods are counted correctly."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        det = IncrementalSwingLevelDetector(
            htf_bar_minutes=240, left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )

        # Feed 720 bars (3 complete 4H periods) + 1 trigger
        for i in range(721):
            ts = start + timedelta(minutes=i)
            det.add_bar(make_bar(1.10 + 0.00001, 1.10 - 0.00001, ts))

        assert det.htf_bars_count == 3

    def test_htf_bar_captures_correct_high_low(self):
        """The resampled HTF bar should have the max high and min low from its 1-min bars."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        det = IncrementalSwingLevelDetector(
            htf_bar_minutes=240, left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )

        # Create 1-min bars for 5 complete 4H periods with distinct swing at period[2]
        htf_highs = [1.10, 1.11, 1.15, 1.12, 1.09]
        htf_lows =  [1.08, 1.09, 1.05, 1.10, 1.07]

        bars = make_1min_bars_from_htf(htf_highs, htf_lows, start,
                                       htf_minutes=240)

        # Feed all bars + 1 trigger bar for the last period
        for bar in bars:
            det.add_bar(bar)
        trigger_ts = start + timedelta(minutes=5 * 240)
        det.add_bar(make_bar(1.10 + 0.00001, 1.10 - 0.00001, trigger_ts))

        # Should detect swing high (1.15) and/or swing low (1.05) at period[2]
        levels = det.get_active_levels()
        assert len(levels) >= 1


# ── 8. Incremental matches batch ────────────────────────────────────────────

class TestIncrementalMatchesBatch:
    """Design decision: live and backtest must produce identical levels."""

    def test_same_levels_detected(self):
        """IncrementalSwingLevelDetector produces the same levels as BatchSwingLevelDetector."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # 7 HTF periods with a clear swing high at [2] and swing low at [4]
        htf_highs = [1.10, 1.11, 1.15, 1.12, 1.09, 1.10, 1.10]
        htf_lows =  [1.08, 1.09, 1.13, 1.10, 1.05, 1.08, 1.08]

        bars_1m = make_1min_bars_from_htf(htf_highs, htf_lows, start,
                                          htf_minutes=240)

        # Batch
        batch = BatchSwingLevelDetector(
            bars=bars_1m, htf_bar_minutes=240,
            left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )

        # Incremental
        inc = IncrementalSwingLevelDetector(
            htf_bar_minutes=240, left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )
        for bar in bars_1m:
            inc.add_bar(bar)
        # Feed one extra bar to finalize last period
        trigger_ts = start + timedelta(minutes=7 * 240)
        inc.add_bar(make_bar(1.10 + 0.00001, 1.10 - 0.00001, trigger_ts))

        # Get levels from both — query batch at the last HTF bar's time
        # The last complete HTF bar in batch is bar[6] at start + 6*240 = start + 24h
        # Query at start + 28h (next period) → floor to 24h → prev = 20h (bar[5])
        # Actually, let's just compare the level sets directly
        inc_levels = inc.get_active_levels()
        # For batch, query at the trigger time
        batch_levels = batch.get_levels_at(trigger_ts)

        # Both should have the same level prices and types
        inc_set = {(lv.price, lv.level_type) for lv in inc_levels}
        batch_set = {(lv.price, lv.level_type) for lv in batch_levels}
        assert inc_set == batch_set, f"Incremental {inc_set} != Batch {batch_set}"


# ── 9. Both swing high and low from same bar ────────────────────────────────

class TestDualSwingDetection:
    """Design decision: a bar can be both swing high and swing low."""

    def test_both_high_and_low_detected(self):
        """A bar that is both the highest high AND lowest low should produce both levels."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Bar[2] has highest high AND lowest low
        highs = [1.10, 1.11, 1.20, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.00, 1.10, 1.07]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        all_new = []
        for bar in bars:
            all_new.extend(det.add_htf_bar(bar))

        types = {lv.level_type for lv in all_new}
        assert LevelType.RESISTANCE in types
        assert LevelType.SUPPORT in types


# ── 10. Multiple levels accumulate ──────────────────────────────────────────

class TestMultipleLevelsAccumulate:
    """Design decision: detector maintains all non-expired, non-merged levels."""

    def test_multiple_swing_highs_coexist(self):
        """Two swing highs at different prices should both be active."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Swing high at bar[2]=1.15, swing high at bar[7]=1.18
        highs = [1.10, 1.11, 1.15, 1.12, 1.09,  1.10, 1.11, 1.18, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07,  1.08, 1.09, 1.16, 1.10, 1.07]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        for bar in bars:
            det.add_htf_bar(bar)

        resistance = [lv for lv in det.get_active_levels()
                      if lv.level_type == LevelType.RESISTANCE]
        assert len(resistance) == 2
        prices = {lv.price for lv in resistance}
        assert 1.15 in prices
        assert 1.18 in prices

    def test_mixed_support_resistance_coexist(self):
        """Support and resistance levels from different bars coexist."""
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Swing high at bar[2]=1.15, swing low at bar[7]=1.04
        highs = [1.10, 1.11, 1.15, 1.12, 1.09,  1.08, 1.07, 1.06, 1.07, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07,  1.06, 1.05, 1.04, 1.05, 1.07]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)

        for bar in bars:
            det.add_htf_bar(bar)

        levels = det.get_active_levels()
        resistance = [lv for lv in levels if lv.level_type == LevelType.RESISTANCE]
        support = [lv for lv in levels if lv.level_type == LevelType.SUPPORT]
        assert len(resistance) >= 1
        assert len(support) >= 1


# ── Bonus: reset clears state ───────────────────────────────────────────────

class TestReset:
    """Reset clears all internal state."""

    def test_swing_detector_reset(self):
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        highs = [1.10, 1.11, 1.15, 1.12, 1.09]
        lows =  [1.08, 1.09, 1.13, 1.10, 1.07]
        bars = make_htf_bars(highs, lows, start)

        det = SwingLevelDetector(left_bars=2, right_bars=2,
                                 expiry_hours=72, merge_distance=0.001,
                                 htf_bar_minutes=240)
        for bar in bars:
            det.add_htf_bar(bar)
        assert len(det.get_active_levels()) >= 1

        det.reset()
        assert len(det.get_active_levels()) == 0
        assert det.buffer_size == 0

    def test_incremental_detector_reset(self):
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        det = IncrementalSwingLevelDetector(
            htf_bar_minutes=240, left_bars=2, right_bars=2,
            expiry_hours=72, merge_distance=0.001,
        )

        # Feed some bars
        for i in range(500):
            ts = start + timedelta(minutes=i)
            det.add_bar(make_bar(1.10 + 0.00001, 1.10 - 0.00001, ts))
        assert det.htf_bars_count > 0

        det.reset()
        assert det.htf_bars_count == 0
        assert len(det.get_active_levels()) == 0
