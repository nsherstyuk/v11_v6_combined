"""
Tests for ImbalanceClassifier — ported from v8, extended for v11.

Test Specifications:

1. Buy ratio computation
   Intent: buy_ratio = sum(buy_volume) / sum(buy_volume + sell_volume) over window.
   Regression: Ratio computed over wrong window, or division by zero crash.

2. NaN on insufficient data
   Intent: Returns NaN when fewer bars available than requested window.
   Regression: Returns 0.5 or crashes instead of NaN.

3. NaN on low tick quality
   Intent: Returns NaN when any bar in window has tick_count < min_bar_ticks.
   Regression: Low-quality bars produce confident ratios that mislead the LLM.

4. Classification: CONFIRMING for long
   Intent: buy_ratio >= threshold for long direction → CONFIRMING.
   Regression: Long breakout classified as DIVERGENT when buyers dominate.

5. Classification: DIVERGENT for long
   Intent: buy_ratio < threshold for long direction → DIVERGENT.
   Regression: Seller-dominated long breakout classified as CONFIRMING.

6. Classification: CONFIRMING for short
   Intent: buy_ratio <= (1-threshold) for short direction → CONFIRMING.
   Regression: Short breakout classified as DIVERGENT when sellers dominate.

7. Classification: INDETERMINATE on bad data
   Intent: Returns INDETERMINATE when data quality is insufficient.
   Regression: Makes confident classification on garbage data.

8. Trend detection
   Intent: Compares short-window to long-window buy_ratio to detect trend.
   Regression: Trend always "flat" or wrong direction.
"""
import math
from datetime import datetime, timezone

import pytest

from v11.core.imbalance_classifier import ImbalanceClassifier
from v11.core.types import Bar, Direction, ImbalanceClassification


def make_bar(buy_vol: float, sell_vol: float, tick_count: int = 100) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=100.0, high=100.0, low=100.0, close=100.0,
        tick_count=tick_count,
        buy_volume=buy_vol,
        sell_volume=sell_vol,
    )


class TestBuyRatio:
    """Intent: Correct buy_ratio over the requested window."""

    def test_all_buyers(self):
        ic = ImbalanceClassifier()
        for _ in range(5):
            ic.add_bar(make_bar(100.0, 0.0))
        assert ic.get_buy_ratio(3) == pytest.approx(1.0)

    def test_all_sellers(self):
        ic = ImbalanceClassifier()
        for _ in range(5):
            ic.add_bar(make_bar(0.0, 100.0))
        assert ic.get_buy_ratio(3) == pytest.approx(0.0)

    def test_balanced(self):
        ic = ImbalanceClassifier()
        for _ in range(5):
            ic.add_bar(make_bar(50.0, 50.0))
        assert ic.get_buy_ratio(3) == pytest.approx(0.5)

    def test_uses_correct_window(self):
        """Last 2 bars are buy-heavy, first 3 are sell-heavy."""
        ic = ImbalanceClassifier()
        for _ in range(3):
            ic.add_bar(make_bar(10.0, 90.0))  # sell-heavy
        for _ in range(2):
            ic.add_bar(make_bar(90.0, 10.0))  # buy-heavy
        # Window=2 should only see buy-heavy bars
        assert ic.get_buy_ratio(2) == pytest.approx(0.9)

    def test_zero_volume_returns_half(self):
        ic = ImbalanceClassifier()
        for _ in range(3):
            ic.add_bar(make_bar(0.0, 0.0))
        assert ic.get_buy_ratio(3) == pytest.approx(0.5)


class TestNaN:
    """Intent: NaN when data is insufficient or low quality."""

    def test_nan_insufficient_bars(self):
        ic = ImbalanceClassifier()
        ic.add_bar(make_bar(50.0, 50.0))
        assert math.isnan(ic.get_buy_ratio(5))

    def test_nan_low_ticks(self):
        ic = ImbalanceClassifier(min_bar_ticks=50)
        for _ in range(5):
            ic.add_bar(make_bar(50.0, 50.0, tick_count=10))  # below threshold
        assert math.isnan(ic.get_buy_ratio(3))

    def test_ok_when_ticks_sufficient(self):
        ic = ImbalanceClassifier(min_bar_ticks=50)
        for _ in range(5):
            ic.add_bar(make_bar(50.0, 50.0, tick_count=100))
        assert not math.isnan(ic.get_buy_ratio(3))


class TestClassification:
    """Intent: Correct directional classification."""

    def test_confirming_long(self):
        ic = ImbalanceClassifier()
        for _ in range(5):
            ic.add_bar(make_bar(80.0, 20.0))  # buy_ratio = 0.8
        result = ic.classify(Direction.LONG, window=3, threshold=0.5)
        assert result == ImbalanceClassification.CONFIRMING

    def test_divergent_long(self):
        ic = ImbalanceClassifier()
        for _ in range(5):
            ic.add_bar(make_bar(20.0, 80.0))  # buy_ratio = 0.2
        result = ic.classify(Direction.LONG, window=3, threshold=0.5)
        assert result == ImbalanceClassification.DIVERGENT

    def test_confirming_short(self):
        ic = ImbalanceClassifier()
        for _ in range(5):
            ic.add_bar(make_bar(20.0, 80.0))  # buy_ratio = 0.2
        result = ic.classify(Direction.SHORT, window=3, threshold=0.5)
        assert result == ImbalanceClassification.CONFIRMING

    def test_divergent_short(self):
        ic = ImbalanceClassifier()
        for _ in range(5):
            ic.add_bar(make_bar(80.0, 20.0))  # buy_ratio = 0.8
        result = ic.classify(Direction.SHORT, window=3, threshold=0.5)
        assert result == ImbalanceClassification.DIVERGENT

    def test_indeterminate_on_bad_data(self):
        ic = ImbalanceClassifier(min_bar_ticks=50)
        for _ in range(5):
            ic.add_bar(make_bar(50.0, 50.0, tick_count=10))
        result = ic.classify(Direction.LONG, window=3)
        assert result == ImbalanceClassification.INDETERMINATE


class TestTrend:
    """Intent: Trend detection compares short vs long window."""

    def test_increasing_trend(self):
        ic = ImbalanceClassifier(max_lookback=30)
        # Long window: balanced
        for _ in range(15):
            ic.add_bar(make_bar(50.0, 50.0))
        # Short window: buy-heavy
        for _ in range(10):
            ic.add_bar(make_bar(90.0, 10.0))
        assert ic.get_trend(short_window=5, long_window=20) == "increasing"

    def test_decreasing_trend(self):
        ic = ImbalanceClassifier(max_lookback=30)
        for _ in range(15):
            ic.add_bar(make_bar(50.0, 50.0))
        for _ in range(10):
            ic.add_bar(make_bar(10.0, 90.0))
        assert ic.get_trend(short_window=5, long_window=20) == "decreasing"

    def test_flat_trend(self):
        ic = ImbalanceClassifier(max_lookback=30)
        for _ in range(25):
            ic.add_bar(make_bar(50.0, 50.0))
        assert ic.get_trend(short_window=5, long_window=20) == "flat"
