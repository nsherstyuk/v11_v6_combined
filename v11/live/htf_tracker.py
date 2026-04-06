"""Real-time HTF tracking for live trading.

Provides incremental (bar-by-bar) versions of the HTF utilities:
    - HTFBarResampler: accumulates 1-min bars into HTF bars (60m, 240m)
    - SMATracker: rolling SMA on HTF bars
    - LiveLevelDetector: 4H swing level detection + retest state machine

These process one bar at a time (no look-ahead, no bulk data).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from ..core.types import Bar, Direction
from ..core.imbalance_classifier import ImbalanceClassifier


# ── Data Structures ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Level:
    price: float
    level_type: str          # "resistance" or "support"
    origin_time: datetime
    htf_minutes: int


@dataclass(frozen=True)
class LevelSignal:
    """Signal from 4H level retest. Compatible with TradeManager."""
    timestamp: datetime
    direction: Direction
    breakout_price: float
    level_price: float
    atr: float


# ── HTF Bar Resampler ──────────────────────────────────────────────────────

class HTFBarResampler:
    """Accumulates 1-min bars into higher-timeframe bars.

    On each add_bar(), returns a completed HTF bar when a new period starts.
    """

    def __init__(self, htf_minutes: int):
        self._htf_minutes = htf_minutes
        self._current_period: Optional[datetime] = None
        self._open = 0.0
        self._high = -1e9
        self._low = 1e9
        self._close = 0.0
        self._tick_count = 0
        self._buy_volume = 0.0
        self._sell_volume = 0.0

    def _floor(self, ts: datetime) -> datetime:
        epoch = ts.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = ts.hour * 60 + ts.minute
        floored = (mins // self._htf_minutes) * self._htf_minutes
        return epoch + timedelta(minutes=floored)

    def add_bar(self, bar: Bar) -> Optional[Bar]:
        """Process a 1-min bar. Returns completed HTF bar if period rolled over."""
        period = self._floor(bar.timestamp)
        completed = None

        if self._current_period is not None and period != self._current_period:
            # Period changed — emit the completed bar
            completed = Bar(
                timestamp=self._current_period,
                open=self._open,
                high=self._high,
                low=self._low,
                close=self._close,
                tick_count=self._tick_count,
                buy_volume=self._buy_volume,
                sell_volume=self._sell_volume,
            )
            # Reset for new period
            self._high = -1e9
            self._low = 1e9
            self._tick_count = 0
            self._buy_volume = 0.0
            self._sell_volume = 0.0
            self._open = bar.open

        if self._current_period is None or period != self._current_period:
            self._open = bar.open
            self._current_period = period

        # Accumulate
        self._high = max(self._high, bar.high)
        self._low = min(self._low, bar.low)
        self._close = bar.close
        self._tick_count += bar.tick_count
        self._buy_volume += bar.buy_volume
        self._sell_volume += bar.sell_volume

        return completed


# ── SMA Tracker ────────────────────────────────────────────────────────────

class SMATracker:
    """Rolling simple moving average on HTF bar closes."""

    def __init__(self, period: int = 50):
        self._period = period
        self._closes: deque = deque(maxlen=period)
        self._sum = 0.0

    def add_bar(self, bar: Bar) -> None:
        """Add an HTF bar."""
        if len(self._closes) == self._period:
            self._sum -= self._closes[0]
        self._closes.append(bar.close)
        self._sum += bar.close

    @property
    def value(self) -> Optional[float]:
        """Current SMA value, or None if not enough data."""
        if len(self._closes) < self._period:
            return None
        return self._sum / self._period

    @property
    def ready(self) -> bool:
        return len(self._closes) >= self._period


# ── Live Level Detector ────────────────────────────────────────────────────

@dataclass
class _PendingRetest:
    level_price: float
    level_type: str
    break_bar: int
    direction: Direction
    pulled_back: bool = False


class LiveLevelDetector:
    """Detects 4H swing levels and tracks retests on 1-min bars.

    Levels persist across sessions (4H levels are real market memory).
    One signal per level (tracked via cooldown).
    """

    def __init__(
        self,
        left_bars: int = 10,
        right_bars: int = 10,
        expiry_hours: int = 72,
        merge_pips: float = 0.0005,
        min_pullback_bars: int = 10,
        max_pullback_bars: int = 30,
        cooldown_bars: int = 60,
        sl_atr_offset: float = 0.3,
        pullback_atr_tol: float = 0.3,
    ):
        self._left = left_bars
        self._right = right_bars
        self._expiry_hours = expiry_hours
        self._merge = merge_pips
        self._min_pb = min_pullback_bars
        self._max_pb = max_pullback_bars
        self._cooldown = cooldown_bars
        self._sl_atr_offset = sl_atr_offset
        self._pb_tol = pullback_atr_tol

        # HTF swing detection
        self._htf_buffer: deque = deque(maxlen=left_bars + right_bars + 1)
        self._levels: List[Level] = []

        # 1-min state
        self._bar_index_1m: int = -1
        self._used_levels: dict = {}  # price -> last signal bar index
        self._pending: List[_PendingRetest] = []

        # ATR (computed on 1-min bars)
        self._atr: float = 0.0
        self._atr_count: int = 0
        self._prev_close: float = 0.0

    def add_htf_bar(self, bar: Bar) -> List[Level]:
        """Process a completed 4H bar. Detects new swing levels."""
        self._htf_buffer.append(bar)
        new_levels = []

        if len(self._htf_buffer) < self._left + self._right + 1:
            return new_levels

        ci = self._left
        candidate = self._htf_buffer[ci]

        # Swing high -> resistance
        left_highs = [self._htf_buffer[j].high for j in range(ci)]
        right_highs = [self._htf_buffer[j].high for j in range(ci + 1, len(self._htf_buffer))]
        if candidate.high > max(left_highs) and candidate.high > max(right_highs):
            lv = Level(price=candidate.high, level_type="resistance",
                       origin_time=candidate.timestamp, htf_minutes=240)
            if self._should_add(lv):
                self._levels.append(lv)
                new_levels.append(lv)

        # Swing low -> support
        left_lows = [self._htf_buffer[j].low for j in range(ci)]
        right_lows = [self._htf_buffer[j].low for j in range(ci + 1, len(self._htf_buffer))]
        if candidate.low < min(left_lows) and candidate.low < min(right_lows):
            lv = Level(price=candidate.low, level_type="support",
                       origin_time=candidate.timestamp, htf_minutes=240)
            if self._should_add(lv):
                self._levels.append(lv)
                new_levels.append(lv)

        # Prune expired
        now = bar.timestamp
        self._levels = [
            lv for lv in self._levels
            if (now - lv.origin_time) < timedelta(hours=self._expiry_hours)
        ]

        return new_levels

    def _should_add(self, new_lv: Level) -> bool:
        for existing in self._levels:
            if existing.level_type == new_lv.level_type:
                if abs(existing.price - new_lv.price) < self._merge:
                    return False
        return True

    def check_bar(self, bar: Bar, classifier: ImbalanceClassifier) -> Optional[LevelSignal]:
        """Process a 1-min bar. Returns a LevelSignal if a retest breakout fires."""
        self._bar_index_1m += 1
        self._update_atr(bar)

        if self._atr <= 0 or self._atr_count < 60:
            return None

        # 1. Check for new breaks -> create pending retests
        for lv in self._levels:
            if lv.price in self._used_levels:
                if self._bar_index_1m - self._used_levels[lv.price] < self._cooldown:
                    continue
            if any(p.level_price == lv.price for p in self._pending):
                continue

            if lv.level_type == "resistance" and bar.close > lv.price:
                self._pending.append(_PendingRetest(
                    level_price=lv.price, level_type=lv.level_type,
                    break_bar=self._bar_index_1m, direction=Direction.LONG,
                ))
            elif lv.level_type == "support" and bar.close < lv.price:
                self._pending.append(_PendingRetest(
                    level_price=lv.price, level_type=lv.level_type,
                    break_bar=self._bar_index_1m, direction=Direction.SHORT,
                ))

        # 2. Process pending retests
        still_pending = []
        for p in self._pending:
            elapsed = self._bar_index_1m - p.break_bar

            if elapsed > self._max_pb:
                self._used_levels[p.level_price] = self._bar_index_1m
                continue

            tol = self._pb_tol * self._atr
            if not p.pulled_back:
                if p.direction == Direction.LONG and bar.low <= p.level_price + tol:
                    p.pulled_back = True
                elif p.direction == Direction.SHORT and bar.high >= p.level_price - tol:
                    p.pulled_back = True
                still_pending.append(p)
                continue

            if elapsed < self._min_pb:
                still_pending.append(p)
                continue

            # Check rebreak + volume
            if p.direction == Direction.LONG and bar.close > p.level_price:
                vol = classifier.classify(Direction.LONG, 3, 0.50)
                if vol.value == "CONFIRMING":
                    self._used_levels[p.level_price] = self._bar_index_1m
                    self._pending = [x for x in still_pending if x.level_price != p.level_price]
                    return LevelSignal(
                        timestamp=bar.timestamp, direction=Direction.LONG,
                        breakout_price=bar.close, level_price=p.level_price,
                        atr=self._atr,
                    )
                still_pending.append(p)
                continue

            elif p.direction == Direction.SHORT and bar.close < p.level_price:
                vol = classifier.classify(Direction.SHORT, 3, 0.50)
                if vol.value == "CONFIRMING":
                    self._used_levels[p.level_price] = self._bar_index_1m
                    self._pending = [x for x in still_pending if x.level_price != p.level_price]
                    return LevelSignal(
                        timestamp=bar.timestamp, direction=Direction.SHORT,
                        breakout_price=bar.close, level_price=p.level_price,
                        atr=self._atr,
                    )
                still_pending.append(p)
                continue

            still_pending.append(p)

        self._pending = still_pending
        return None

    def get_active_levels(self) -> List[Level]:
        return list(self._levels)

    @property
    def current_atr(self) -> float:
        return self._atr

    @property
    def sl_atr_offset(self) -> float:
        return self._sl_atr_offset

    def _update_atr(self, bar: Bar):
        if self._prev_close > 0:
            tr = max(bar.high - bar.low,
                     abs(bar.high - self._prev_close),
                     abs(bar.low - self._prev_close))
        else:
            tr = bar.high - bar.low
        self._prev_close = bar.close
        if self._atr_count < 60:
            self._atr_count += 1
            self._atr += (tr - self._atr) / self._atr_count
        else:
            alpha = 2.0 / 61.0
            self._atr = self._atr * (1 - alpha) + tr * alpha
