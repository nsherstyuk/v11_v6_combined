"""
HTF SMA Direction Filter — Filters breakout signals by higher-timeframe trend.

Design decision (V11_DESIGN.md §10):
    Only take LONG breakouts when price > 60-min SMA(50).
    Only take SHORT breakouts when price < 60-min SMA(50).

Look-ahead prevention:
    The SMA value used is from the PREVIOUS COMPLETED HTF bar, not the
    current in-progress bar. This is critical — using the current bar's
    SMA would leak future information.

Two usage modes:
    1. Batch (backtest): pre-compute SMA lookup from all bars, then query
       per signal via BatchHTFSMAFilter.
    2. Incremental (live): feed 1-min bars one at a time via
       IncrementalHTFSMAFilter; it resamples internally and maintains
       a rolling SMA.

Both expose the same interface: is_aligned(direction, price, timestamp).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .types import Bar, Direction


# ── Shared alignment check ────────────────────────────────────────────────

def check_sma_alignment(direction: Direction, price: float,
                        sma_value: float) -> bool:
    """Check if a breakout direction aligns with the HTF SMA trend.

    LONG is aligned when price > SMA (uptrend).
    SHORT is aligned when price < SMA (downtrend).

    Args:
        direction: Breakout direction (LONG or SHORT).
        price: Current price at breakout time.
        sma_value: SMA value from the previous completed HTF bar.

    Returns:
        True if aligned, False if counter-trend.
    """
    if direction == Direction.LONG:
        return price > sma_value
    else:
        return price < sma_value


# ── Batch mode (backtest) ─────────────────────────────────────────────────

def _floor_timestamp(ts: datetime, minutes: int) -> datetime:
    """Floor a timestamp to the nearest period boundary."""
    epoch = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    mins_since_midnight = ts.hour * 60 + ts.minute
    floored_mins = (mins_since_midnight // minutes) * minutes
    return epoch + timedelta(minutes=floored_mins)


class BatchHTFSMAFilter:
    """Pre-computed SMA lookup for batch backtesting.

    Resamples all 1-min bars into HTF bars, computes SMA, and builds a
    timestamp-keyed lookup. At query time, returns the SMA from the
    previous completed HTF bar (look-ahead safe).

    This is the same proven approach used in htf_utils.py investigation
    scripts, now packaged as a reusable class.
    """

    def __init__(self, bars: List[Bar], bar_minutes: int, sma_period: int,
                 gap_minutes: int = 30):
        """Build the SMA lookup from historical bars.

        Args:
            bars: All 1-min bars, sorted by time.
            bar_minutes: HTF bar period (e.g. 60 for 60-min bars).
            sma_period: SMA lookback in HTF bars (e.g. 50).
            gap_minutes: Session gap threshold for resampling.
        """
        self._bar_minutes = bar_minutes
        self._sma_period = sma_period
        self._lookup: Dict[datetime, float] = {}

        self._build(bars, gap_minutes)

    def _build(self, bars: List[Bar], gap_minutes: int) -> None:
        """Resample bars and compute SMA lookup."""
        from ..backtest.htf_utils import resample_sessions, compute_sma, build_htf_lookup

        htf_bars = resample_sessions(bars, self._bar_minutes,
                                     gap_minutes=gap_minutes)
        sma_values = compute_sma(htf_bars, self._sma_period)
        self._lookup = build_htf_lookup(sma_values)

    def get_sma_at(self, signal_timestamp: datetime) -> Optional[float]:
        """Get the SMA value for the previous completed HTF bar.

        Returns None if no SMA data available (not enough history).
        """
        floored = _floor_timestamp(signal_timestamp, self._bar_minutes)
        prev_ts = floored - timedelta(minutes=self._bar_minutes)
        return self._lookup.get(prev_ts)

    def is_aligned(self, direction: Direction, price: float,
                   signal_timestamp: datetime) -> bool:
        """Check if direction aligns with SMA trend.

        If SMA data is unavailable (insufficient history), returns True
        (fail-open: don't block signals when we lack data).
        """
        sma = self.get_sma_at(signal_timestamp)
        if sma is None:
            return True  # fail-open
        return check_sma_alignment(direction, price, sma)


# ── Incremental mode (live) ───────────────────────────────────────────────

class IncrementalHTFSMAFilter:
    """Incremental SMA computation from a live 1-min bar stream.

    Accumulates 1-min bars, resamples to HTF bars at period boundaries,
    and maintains a rolling SMA. Designed for the live engine where bars
    arrive one at a time.

    Look-ahead prevention: is_aligned() uses the SMA from the last
    COMPLETED HTF bar, never the in-progress period.
    """

    def __init__(self, bar_minutes: int, sma_period: int):
        """
        Args:
            bar_minutes: HTF bar period (e.g. 60).
            sma_period: SMA lookback in HTF bars (e.g. 50).
        """
        self._bar_minutes = bar_minutes
        self._sma_period = sma_period

        # Accumulator for current HTF bar in progress
        self._current_period_ts: Optional[datetime] = None
        self._current_open: float = 0.0
        self._current_high: float = 0.0
        self._current_low: float = float('inf')
        self._current_close: float = 0.0

        # Completed HTF bar closes for SMA computation
        self._closes: deque[float] = deque(maxlen=sma_period)

        # Running SMA sum for O(1) updates
        self._sma_sum: float = 0.0

        # Total completed HTF bars (not capped by deque maxlen)
        self._total_htf_bars: int = 0

        # Last completed SMA value (look-ahead safe)
        self._last_sma: Optional[float] = None

    @property
    def current_sma(self) -> Optional[float]:
        """SMA from the last completed HTF bar. None if insufficient data."""
        return self._last_sma

    @property
    def htf_bars_count(self) -> int:
        """Number of completed HTF bars seen."""
        return self._total_htf_bars

    def add_bar(self, bar: Bar) -> None:
        """Process a 1-min bar. May complete an HTF bar and update SMA.

        Args:
            bar: A 1-minute bar from the live stream.
        """
        period_ts = _floor_timestamp(bar.timestamp, self._bar_minutes)

        if self._current_period_ts is None:
            # First bar ever
            self._start_new_period(period_ts, bar)
            return

        if period_ts != self._current_period_ts:
            # Period boundary crossed — finalize current HTF bar
            self._finalize_period()
            self._start_new_period(period_ts, bar)
        else:
            # Same period — update running OHLC
            self._current_high = max(self._current_high, bar.high)
            self._current_low = min(self._current_low, bar.low)
            self._current_close = bar.close

    def _start_new_period(self, period_ts: datetime, bar: Bar) -> None:
        """Start accumulating a new HTF bar."""
        self._current_period_ts = period_ts
        self._current_open = bar.open
        self._current_high = bar.high
        self._current_low = bar.low
        self._current_close = bar.close

    def _finalize_period(self) -> None:
        """Finalize the current HTF bar: store close and update SMA."""
        close = self._current_close

        # If deque is at capacity, subtract the value that's about to be evicted
        if len(self._closes) == self._sma_period:
            self._sma_sum -= self._closes[0]

        self._closes.append(close)
        self._sma_sum += close
        self._total_htf_bars += 1

        # Update SMA once we have enough bars
        if len(self._closes) >= self._sma_period:
            self._last_sma = self._sma_sum / self._sma_period

    def is_aligned(self, direction: Direction, price: float) -> bool:
        """Check if direction aligns with SMA trend.

        If SMA data is unavailable (insufficient history), returns True
        (fail-open: don't block signals when we lack data).
        """
        if self._last_sma is None:
            return True  # fail-open
        return check_sma_alignment(direction, price, self._last_sma)

    def seed_bars(self, bars: List[Bar]) -> None:
        """Seed with historical bars to warm up the SMA.

        Call this before live processing to avoid the cold-start period
        where SMA is unavailable.

        Args:
            bars: Historical 1-min bars, sorted by time.
        """
        for bar in bars:
            self.add_bar(bar)
