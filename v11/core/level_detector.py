"""
4H Swing Level Detector — Detects significant S/R levels from higher-timeframe bars.

Design decisions (V11_DESIGN.md §12):
    - Swing high: bar whose high > highs of `lb` bars before AND `rb` bars after
    - Swing low: bar whose low < lows of `lb` bars before AND `rb` bars after
    - Levels expire after `expiry_hours` (default 72h = 3 days)
    - Levels of same type within `merge_distance` are deduplicated
    - Detection is delayed by `rb` bars (inherent look-ahead safety)

Two usage modes:
    1. Batch (backtest): pre-compute level timeline from all bars, then query
       per signal via BatchSwingLevelDetector.
    2. Incremental (live): feed 1-min bars one at a time via
       IncrementalSwingLevelDetector; it resamples internally and feeds
       completed HTF bars to the swing detector.

Both expose: get_levels_at(timestamp) or get_active_levels().
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .types import Bar, SwingLevel, LevelType


# ── Shared timestamp floor (same as htf_sma_filter.py) ──────────────────

def _floor_timestamp(ts: datetime, minutes: int) -> datetime:
    """Floor a timestamp to the nearest period boundary."""
    epoch = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    mins_since_midnight = ts.hour * 60 + ts.minute
    floored_mins = (mins_since_midnight // minutes) * minutes
    return epoch + timedelta(minutes=floored_mins)


# ── Core Swing Level Detector (operates on HTF bars) ────────────────────

class SwingLevelDetector:
    """Detects swing highs/lows from a stream of HTF bars.

    Maintains a sliding buffer of `left_bars + right_bars + 1` bars.
    When the buffer is full, checks the middle bar for swing high/low.
    Detected levels are stored with expiry and merge logic.

    Interface (narrow):
        add_htf_bar(bar) -> List[SwingLevel]   (newly detected levels)
        get_active_levels() -> List[SwingLevel] (all non-expired levels)
        reset()
    """

    def __init__(self, left_bars: int, right_bars: int,
                 expiry_hours: int, merge_distance: float,
                 htf_bar_minutes: int):
        """
        Args:
            left_bars: Number of bars required to the left of a swing point.
            right_bars: Number of bars required to the right of a swing point.
            expiry_hours: Hours after which a level expires and is pruned.
            merge_distance: Price distance below which same-type levels merge.
            htf_bar_minutes: Timeframe of the input bars (e.g. 240 for 4H).
        """
        self._left = left_bars
        self._right = right_bars
        self._expiry_hours = expiry_hours
        self._merge_distance = merge_distance
        self._htf_bar_minutes = htf_bar_minutes

        self._buffer: deque[Bar] = deque(maxlen=left_bars + right_bars + 1)
        self._levels: List[SwingLevel] = []

    @property
    def buffer_size(self) -> int:
        """Current number of bars in the sliding buffer."""
        return len(self._buffer)

    @property
    def required_buffer_size(self) -> int:
        """Number of bars needed before swing detection can begin."""
        return self._left + self._right + 1

    def add_htf_bar(self, bar: Bar) -> List[SwingLevel]:
        """Process an HTF bar. Returns newly detected levels (if any).

        Args:
            bar: A higher-timeframe bar (e.g. 4H).

        Returns:
            List of newly detected SwingLevel objects. Empty if no new levels.
        """
        self._buffer.append(bar)

        if len(self._buffer) < self.required_buffer_size:
            return []

        new_levels = self._check_swing()

        # Prune expired levels based on the latest bar's timestamp
        self._prune_expired(bar.timestamp)

        return new_levels

    def get_active_levels(self) -> List[SwingLevel]:
        """Return all currently active (non-expired) levels."""
        return list(self._levels)

    def reset(self) -> None:
        """Clear all state."""
        self._buffer.clear()
        self._levels.clear()

    def _check_swing(self) -> List[SwingLevel]:
        """Check the candidate bar (center of buffer) for swing high/low."""
        candidate_idx = self._left
        candidate = self._buffer[candidate_idx]
        new_levels = []

        left_bars = [self._buffer[j] for j in range(candidate_idx)]
        right_bars = [self._buffer[j]
                      for j in range(candidate_idx + 1, len(self._buffer))]

        # Swing high: candidate high > all left highs AND all right highs
        left_highs = [b.high for b in left_bars]
        right_highs = [b.high for b in right_bars]
        if candidate.high > max(left_highs) and candidate.high > max(right_highs):
            level = SwingLevel(
                price=candidate.high,
                level_type=LevelType.RESISTANCE,
                origin_time=candidate.timestamp,
                htf_bar_minutes=self._htf_bar_minutes,
            )
            if self._should_add(level):
                self._levels.append(level)
                new_levels.append(level)

        # Swing low: candidate low < all left lows AND all right lows
        left_lows = [b.low for b in left_bars]
        right_lows = [b.low for b in right_bars]
        if candidate.low < min(left_lows) and candidate.low < min(right_lows):
            level = SwingLevel(
                price=candidate.low,
                level_type=LevelType.SUPPORT,
                origin_time=candidate.timestamp,
                htf_bar_minutes=self._htf_bar_minutes,
            )
            if self._should_add(level):
                self._levels.append(level)
                new_levels.append(level)

        return new_levels

    def _should_add(self, new_level: SwingLevel) -> bool:
        """Check if a new level should be added (not too close to existing same-type)."""
        for existing in self._levels:
            if existing.level_type == new_level.level_type:
                if abs(existing.price - new_level.price) < self._merge_distance:
                    return False
        return True

    def _prune_expired(self, now: datetime) -> None:
        """Remove levels older than expiry_hours."""
        cutoff = now - timedelta(hours=self._expiry_hours)
        self._levels = [
            lv for lv in self._levels
            if lv.origin_time >= cutoff
        ]


# ── Batch mode (backtest) ───────────────────────────────────────────────

class BatchSwingLevelDetector:
    """Pre-computed level timeline for batch backtesting.

    Resamples all 1-min bars into HTF bars, runs the swing detector,
    and builds a timestamp-keyed lookup. At query time, returns levels
    from the previous completed HTF bar (look-ahead safe).

    Uses resample_bars (NOT session-split) because levels persist across
    sessions — a 4H swing high from yesterday is still relevant today.
    """

    def __init__(self, bars: List[Bar], htf_bar_minutes: int,
                 left_bars: int, right_bars: int,
                 expiry_hours: int, merge_distance: float):
        """Build the level timeline from historical bars.

        Args:
            bars: All 1-min bars, sorted by time.
            htf_bar_minutes: HTF bar period (e.g. 240 for 4H).
            left_bars: Swing detection left lookback.
            right_bars: Swing detection right lookback.
            expiry_hours: Level expiry in hours.
            merge_distance: Merge threshold in price units.
        """
        self._htf_bar_minutes = htf_bar_minutes
        self._timeline: Dict[datetime, List[SwingLevel]] = {}

        self._build(bars, htf_bar_minutes, left_bars, right_bars,
                    expiry_hours, merge_distance)

    def _build(self, bars: List[Bar], htf_bar_minutes: int,
               left_bars: int, right_bars: int,
               expiry_hours: int, merge_distance: float) -> None:
        """Resample bars, run swing detector, and record levels at each HTF bar."""
        from ..backtest.htf_utils import resample_bars

        htf_bars = resample_bars(bars, htf_bar_minutes)
        detector = SwingLevelDetector(
            left_bars=left_bars,
            right_bars=right_bars,
            expiry_hours=expiry_hours,
            merge_distance=merge_distance,
            htf_bar_minutes=htf_bar_minutes,
        )

        for bar in htf_bars:
            detector.add_htf_bar(bar)
            # Snapshot the active levels at this HTF bar's timestamp
            self._timeline[bar.timestamp] = detector.get_active_levels()

    def get_levels_at(self, signal_timestamp: datetime) -> List[SwingLevel]:
        """Get active levels at a signal timestamp (look-ahead safe).

        Returns levels from the PREVIOUS completed HTF bar — not the
        current in-progress bar. This prevents look-ahead bias.

        Returns empty list if no level data is available.
        """
        floored = _floor_timestamp(signal_timestamp, self._htf_bar_minutes)
        prev_ts = floored - timedelta(minutes=self._htf_bar_minutes)
        return self._timeline.get(prev_ts, [])

    @property
    def timeline_size(self) -> int:
        """Number of HTF bars in the timeline."""
        return len(self._timeline)


# ── Incremental mode (live) ─────────────────────────────────────────────

class IncrementalSwingLevelDetector:
    """Incremental level detection from a live 1-min bar stream.

    Accumulates 1-min bars, resamples to HTF bars at period boundaries,
    and feeds completed HTF bars to the SwingLevelDetector.

    Look-ahead prevention: get_active_levels() returns levels based on
    completed HTF bars only. The current in-progress period is never used
    for detection.
    """

    def __init__(self, htf_bar_minutes: int, left_bars: int,
                 right_bars: int, expiry_hours: int,
                 merge_distance: float):
        """
        Args:
            htf_bar_minutes: HTF bar period (e.g. 240 for 4H).
            left_bars: Swing detection left lookback.
            right_bars: Swing detection right lookback.
            expiry_hours: Level expiry in hours.
            merge_distance: Merge threshold in price units.
        """
        self._htf_bar_minutes = htf_bar_minutes
        self._detector = SwingLevelDetector(
            left_bars=left_bars,
            right_bars=right_bars,
            expiry_hours=expiry_hours,
            merge_distance=merge_distance,
            htf_bar_minutes=htf_bar_minutes,
        )

        # Accumulator for current HTF bar in progress
        self._current_period_ts: Optional[datetime] = None
        self._current_open: float = 0.0
        self._current_high: float = 0.0
        self._current_low: float = float('inf')
        self._current_close: float = 0.0
        self._current_tick_count: int = 0
        self._current_buy_volume: float = 0.0
        self._current_sell_volume: float = 0.0

        # Total completed HTF bars fed to detector
        self._total_htf_bars: int = 0

    @property
    def htf_bars_count(self) -> int:
        """Number of completed HTF bars processed."""
        return self._total_htf_bars

    @property
    def buffer_fill(self) -> str:
        """Diagnostic: current swing detector buffer fill (e.g. '18/21')."""
        return f"{self._detector.buffer_size}/{self._detector.required_buffer_size}"

    @property
    def levels_ready(self) -> bool:
        """Whether enough HTF bars exist for the swing detector to produce levels."""
        return self._detector.buffer_size >= self._detector.required_buffer_size

    def add_bar(self, bar: Bar) -> List[SwingLevel]:
        """Process a 1-min bar. Returns newly detected levels (if any).

        A new level can only be detected when an HTF bar completes and
        the swing detector's buffer is full.

        Args:
            bar: A 1-minute bar from the live stream.

        Returns:
            List of newly detected SwingLevel objects. Usually empty.
        """
        period_ts = _floor_timestamp(bar.timestamp, self._htf_bar_minutes)

        if self._current_period_ts is None:
            # First bar ever
            self._start_new_period(period_ts, bar)
            return []

        if period_ts != self._current_period_ts:
            # Period boundary crossed — finalize current HTF bar
            new_levels = self._finalize_period()
            self._start_new_period(period_ts, bar)
            return new_levels
        else:
            # Same period — update running OHLCV
            self._current_high = max(self._current_high, bar.high)
            self._current_low = min(self._current_low, bar.low)
            self._current_close = bar.close
            self._current_tick_count += bar.tick_count
            self._current_buy_volume += bar.buy_volume
            self._current_sell_volume += bar.sell_volume
            return []

    def get_active_levels(self) -> List[SwingLevel]:
        """Return currently active levels from completed HTF bars."""
        return self._detector.get_active_levels()

    def seed_bars(self, bars: List[Bar]) -> None:
        """Seed with historical bars to warm up the detector.

        Call this before live processing to avoid the cold-start period
        where the buffer has insufficient bars for swing detection.

        Args:
            bars: Historical 1-min bars, sorted by time.
        """
        for bar in bars:
            self.add_bar(bar)

    def reset(self) -> None:
        """Clear all state."""
        self._detector.reset()
        self._current_period_ts = None
        self._current_open = 0.0
        self._current_high = 0.0
        self._current_low = float('inf')
        self._current_close = 0.0
        self._current_tick_count = 0
        self._current_buy_volume = 0.0
        self._current_sell_volume = 0.0
        self._total_htf_bars = 0

    def _start_new_period(self, period_ts: datetime, bar: Bar) -> None:
        """Start accumulating a new HTF bar."""
        self._current_period_ts = period_ts
        self._current_open = bar.open
        self._current_high = bar.high
        self._current_low = bar.low
        self._current_close = bar.close
        self._current_tick_count = bar.tick_count
        self._current_buy_volume = bar.buy_volume
        self._current_sell_volume = bar.sell_volume

    def _finalize_period(self) -> List[SwingLevel]:
        """Finalize the current HTF bar and feed it to the swing detector."""
        htf_bar = Bar(
            timestamp=self._current_period_ts,
            open=self._current_open,
            high=self._current_high,
            low=self._current_low,
            close=self._current_close,
            tick_count=self._current_tick_count,
            buy_volume=self._current_buy_volume,
            sell_volume=self._current_sell_volume,
        )
        self._total_htf_bars += 1
        return self._detector.add_htf_bar(htf_bar)
