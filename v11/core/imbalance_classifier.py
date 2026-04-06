"""
Deep module: Computes and classifies volume imbalance from bar data.

Ported from v8 (unchanged logic). Maintains a rolling buffer of recent bars'
buy/sell volumes. Provides buy_ratio over a configurable lookback window and
classifies whether the imbalance diverges from or matches a given price direction.

Interface:
    add_bar(bar)
    get_buy_ratio(window) -> float
    classify(direction, window, threshold) -> ImbalanceClassification
    get_trend(short_window, long_window) -> str

Design decision hidden: How buy/sell classification maps to directional confirmation.
Why it might change: Different instruments may need different thresholds or classification logic.
Interface is narrower than implementation: consumers call classify() and get a label.
"""
import math
from collections import deque

from .types import Bar, Direction, ImbalanceClassification, TickQuality


class ImbalanceClassifier:

    def __init__(self, max_lookback: int = 20, min_bar_ticks: int = 0):
        self._buy_vols: deque = deque(maxlen=max_lookback)
        self._sell_vols: deque = deque(maxlen=max_lookback)
        self._tick_counts: deque = deque(maxlen=max_lookback)
        self._min_bar_ticks = min_bar_ticks

    def add_bar(self, bar: Bar) -> None:
        self._buy_vols.append(bar.buy_volume)
        self._sell_vols.append(bar.sell_volume)
        self._tick_counts.append(bar.tick_count)

    def get_buy_ratio(self, window: int) -> float:
        """Buy ratio over the last `window` bars.

        Returns NaN if any bar in the window has fewer than min_bar_ticks.
        Returns 0.5 if total volume is zero.
        """
        if len(self._buy_vols) < window:
            return float("nan")
        ticks = list(self._tick_counts)[-window:]
        if self._min_bar_ticks > 0 and any(t < self._min_bar_ticks for t in ticks):
            return float("nan")
        bv = sum(list(self._buy_vols)[-window:])
        sv = sum(list(self._sell_vols)[-window:])
        total = bv + sv
        if total == 0:
            return 0.5
        return bv / total

    def classify(self, direction: Direction, window: int,
                 threshold: float = 0.5) -> ImbalanceClassification:
        """Classify imbalance relative to breakout direction.

        For long breakouts: buy_ratio >= threshold → CONFIRMING
        For short breakouts: buy_ratio <= (1 - threshold) → CONFIRMING
        Returns INDETERMINATE if insufficient data quality.
        """
        br = self.get_buy_ratio(window)
        if math.isnan(br):
            return ImbalanceClassification.INDETERMINATE

        if direction == Direction.LONG:
            if br >= threshold:
                return ImbalanceClassification.CONFIRMING
            return ImbalanceClassification.DIVERGENT
        else:
            if br <= (1.0 - threshold):
                return ImbalanceClassification.CONFIRMING
            return ImbalanceClassification.DIVERGENT

    def get_tick_quality(self, window: int, high_threshold: int = 50) -> TickQuality:
        """Assess tick count quality over the window."""
        if len(self._tick_counts) < window:
            return TickQuality.INSUFFICIENT
        ticks = list(self._tick_counts)[-window:]
        avg_ticks = sum(ticks) / len(ticks)
        if avg_ticks >= high_threshold:
            return TickQuality.HIGH
        return TickQuality.LOW

    def get_trend(self, short_window: int = 5, long_window: int = 20) -> str:
        """Buy ratio trend: compare short-term to long-term ratio.

        Returns "increasing", "decreasing", or "flat".
        """
        short_br = self.get_buy_ratio(short_window)
        long_br = self.get_buy_ratio(long_window)
        if math.isnan(short_br) or math.isnan(long_br):
            return "flat"
        diff = short_br - long_br
        if diff > 0.05:
            return "increasing"
        elif diff < -0.05:
            return "decreasing"
        return "flat"

    def has_quality_data(self, window: int) -> bool:
        """True if the last `window` bars all meet the min_bar_ticks threshold."""
        return not math.isnan(self.get_buy_ratio(window))

    def reset(self) -> None:
        self._buy_vols.clear()
        self._sell_vols.clear()
        self._tick_counts.clear()
