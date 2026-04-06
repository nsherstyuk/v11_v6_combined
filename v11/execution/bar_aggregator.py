"""
Bar Aggregator — Aggregates streaming price updates into 1-min bars.

Ported from v8 (unchanged logic). Uses price direction (uptick/downtick)
as proxy for buy/sell classification.

Interface:
    on_price(price, now) -> Optional[Bar]
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..core.types import Bar


class BarAggregator:
    """Aggregates streaming price updates into 1-min bars.

    Uses price direction (uptick/downtick) as proxy for buy/sell classification.
    """

    def __init__(self):
        self.current_bar_start: Optional[datetime] = None
        self.bar_open = 0.0
        self.bar_high = 0.0
        self.bar_low = 0.0
        self.bar_close = 0.0
        self.bar_buy_vol = 0.0
        self.bar_sell_vol = 0.0
        self.bar_tick_count = 0
        self.last_price = 0.0

    def on_price(self, price: float, now: datetime) -> Optional[Bar]:
        """Process a price update. Returns completed Bar if minute boundary crossed."""
        bar_start = now.replace(second=0, microsecond=0)
        completed_bar = None

        if self.current_bar_start is not None and bar_start > self.current_bar_start:
            if self.bar_tick_count > 0:
                completed_bar = Bar(
                    timestamp=self.current_bar_start,
                    open=self.bar_open,
                    high=self.bar_high,
                    low=self.bar_low,
                    close=self.bar_close,
                    buy_volume=self.bar_buy_vol,
                    sell_volume=self.bar_sell_vol,
                    tick_count=self.bar_tick_count,
                )
            self.current_bar_start = bar_start
            self.bar_open = price
            self.bar_high = price
            self.bar_low = price
            self.bar_close = price
            self.bar_buy_vol = 0.0
            self.bar_sell_vol = 0.0
            self.bar_tick_count = 0

        elif self.current_bar_start is None:
            self.current_bar_start = bar_start
            self.bar_open = price
            self.bar_high = price
            self.bar_low = price
            self.bar_close = price

        self.bar_high = max(self.bar_high, price)
        self.bar_low = min(self.bar_low, price)
        self.bar_close = price
        self.bar_tick_count += 1

        if self.last_price > 0:
            if price > self.last_price:
                self.bar_buy_vol += 1
            elif price < self.last_price:
                self.bar_sell_vol += 1
            else:
                self.bar_buy_vol += 0.5
                self.bar_sell_vol += 0.5
        self.last_price = price

        return completed_bar
