"""
V6 market event types — copied from C:\\nautilus0\\v6_orb_refactor\\core\\market_event.py
DO NOT MODIFY — frozen V6 code.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass(frozen=True)
class MarketEvent:
    timestamp: datetime

@dataclass(frozen=True)
class Tick(MarketEvent):
    """A raw price event from the market."""
    bid: float
    ask: float
    
    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2
        
    @property
    def spread(self) -> float:
        return self.ask - self.bid

@dataclass(frozen=True)
class Bar(MarketEvent):
    """A time-aggregated price bar."""
    open: float
    high: float
    low: float
    close: float
    tick_count: int
    avg_spread: float

@dataclass(frozen=True)
class Fill:
    """An execution event."""
    timestamp: datetime
    price: float
    direction: str  # "LONG" or "SHORT"
    reason: str     # "ENTRY", "SL", "TP", "MARKET"

@dataclass(frozen=True)
class GapMetrics:
    """Pre-trade gap period (e.g. 06:00-08:00 UTC) analysis results."""
    gap_volatility: float       # std of 1-min log returns during gap
    gap_range: float            # (high - low) / overnight_range during gap
    vol_passes: bool            # True if gap_volatility >= rolling percentile
    range_passes: bool          # True if gap_range >= rolling percentile


@dataclass(frozen=True)
class RangeInfo:
    """Calculated Asian Range metrics."""
    high: float
    low: float
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    
    @property
    def size(self) -> float:
        return self.high - self.low
        
    def is_valid(self, min_range: float, max_range: float) -> bool:
        """Validates if the range size is within acceptable bounds."""
        return min_range <= self.size <= max_range
