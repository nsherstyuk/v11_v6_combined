"""
Tests for BarAggregator.

Test Specifications:

1. Bar completion at minute boundary
   Intent: When a new price arrives in a different minute than the current bar,
   the current bar is completed and returned.
   Regression: Bars never complete, or complete on every tick.

2. OHLC tracking within a bar
   Intent: open = first price, high = max price, low = min price, close = last price.
   Regression: OHLC values wrong, e.g. high not updated on new maximum.

3. Buy/sell volume classification
   Intent: Uptick → buy, downtick → sell, unchanged → split equally.
   Regression: All volume classified as buy or all as sell.

4. No bar returned on first tick
   Intent: The very first tick initializes the bar but doesn't complete one.
   Regression: First tick returns a bar with no data.

5. Tick count
   Intent: tick_count increments with each price update within the bar.
   Regression: tick_count always 0 or 1.
"""
from datetime import datetime, timezone

import pytest

from v11.execution.bar_aggregator import BarAggregator
from v11.core.types import Bar


class TestBarCompletion:
    """Intent: Bar completes when minute boundary crosses."""

    def test_bar_completes_on_new_minute(self):
        agg = BarAggregator()
        t0 = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 1, 5, tzinfo=timezone.utc)

        result = agg.on_price(100.0, t0)
        assert result is None  # First tick, no completed bar

        result = agg.on_price(101.0, t1)  # New minute
        assert result is not None
        assert isinstance(result, Bar)
        assert result.close == 100.0  # Previous bar's close
        assert result.timestamp.minute == 0

    def test_no_completion_within_same_minute(self):
        agg = BarAggregator()
        t0 = datetime(2026, 1, 1, 12, 0, 10, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 12, 0, 50, tzinfo=timezone.utc)

        agg.on_price(100.0, t0)
        result = agg.on_price(101.0, t1)
        assert result is None
        result = agg.on_price(99.0, t2)
        assert result is None


class TestOHLC:
    """Intent: Correct OHLC tracking."""

    def test_ohlc_values(self):
        agg = BarAggregator()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

        agg.on_price(100.0, t0)
        agg.on_price(105.0, t0)  # new high
        agg.on_price(98.0, t0)   # new low
        agg.on_price(102.0, t0)  # close

        bar = agg.on_price(101.0, t1)
        assert bar is not None
        assert bar.open == 100.0
        assert bar.high == 105.0
        assert bar.low == 98.0
        assert bar.close == 102.0


class TestBuySellClassification:
    """Intent: Uptick=buy, downtick=sell, flat=split."""

    def test_uptick_is_buy(self):
        agg = BarAggregator()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

        agg.on_price(100.0, t0)  # first tick (no last_price to compare)
        agg.on_price(101.0, t0)  # uptick → buy
        agg.on_price(102.0, t0)  # uptick → buy

        bar = agg.on_price(100.0, t1)
        assert bar.buy_volume == 2.0
        assert bar.sell_volume == 0.0

    def test_downtick_is_sell(self):
        agg = BarAggregator()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

        agg.on_price(100.0, t0)
        agg.on_price(99.0, t0)   # downtick → sell
        agg.on_price(98.0, t0)   # downtick → sell

        bar = agg.on_price(100.0, t1)
        assert bar.buy_volume == 0.0
        assert bar.sell_volume == 2.0

    def test_flat_splits(self):
        agg = BarAggregator()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

        agg.on_price(100.0, t0)
        agg.on_price(100.0, t0)  # flat → split
        agg.on_price(100.0, t0)  # flat → split

        bar = agg.on_price(100.0, t1)
        assert bar.buy_volume == pytest.approx(1.0)
        assert bar.sell_volume == pytest.approx(1.0)


class TestTickCount:
    """Intent: tick_count increments per price update."""

    def test_tick_count(self):
        agg = BarAggregator()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 1, 0, tzinfo=timezone.utc)

        for _ in range(10):
            agg.on_price(100.0, t0)

        bar = agg.on_price(100.0, t1)
        assert bar.tick_count == 10
