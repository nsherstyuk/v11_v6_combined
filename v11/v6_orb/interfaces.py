"""
V6 interfaces — copied from C:\\nautilus0\\v6_orb_refactor\\core\\interfaces.py
DO NOT MODIFY — frozen V6 code. Only import paths changed.
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from .market_event import RangeInfo, GapMetrics


class MarketContext(ABC):
    """
    Deep module: Hides data history, tick buffering, and complex aggregations.
    The strategy interacts with this to understand the market state without
    managing data structures.
    """

    @abstractmethod
    def get_velocity(self, lookback_minutes: int, current_time: datetime) -> float:
        """Calculate tick velocity (ticks per minute) over the lookback window."""
        pass

    @abstractmethod
    def get_asian_range(self, start_hour: int, end_hour: int,
                        current_time: datetime) -> Optional[RangeInfo]:
        """Return the session range. None if unavailable."""
        pass

    @abstractmethod
    def time_is_in_trade_window(self, current_time: datetime,
                                start_hour: int, end_hour: int) -> bool:
        """Check if current time falls within the trading window."""
        pass

    @abstractmethod
    def get_current_price(self, current_time: datetime) -> Optional[float]:
        """Return current mid price. Used for breakeven guard checks."""
        pass

    @abstractmethod
    def get_gap_metrics(self, current_time: datetime,
                        gap_start_hour: int, gap_end_hour: int,
                        vol_percentile: float, range_percentile: float,
                        rolling_days: int) -> Optional[GapMetrics]:
        """Compute gap period metrics and compare to rolling thresholds.
        Returns None if insufficient data. Strategy doesn't know how
        percentiles are calculated or where the bar data comes from."""
        pass


class ExecutionEngine(ABC):
    """
    Deep module: Hides broker order IDs, bracket leg tracking, slippage,
    and fill simulation. The strategy calls these methods and doesn't know
    if it's hitting IBKR or a backtest simulator.
    """

    @abstractmethod
    def set_orb_brackets(self, range_info: RangeInfo, rr_ratio: float):
        """Place OCA entry stop brackets for the ORB breakout strategy."""
        pass

    @abstractmethod
    def cancel_orb_brackets(self):
        """Cancel resting entry brackets (not SL/TP if position active)."""
        pass

    @abstractmethod
    def close_at_market(self):
        """Close any open position immediately at market price."""
        pass

    @abstractmethod
    def modify_sl(self, new_sl_price: float):
        """Modify the stop-loss price on the active position's SL order."""
        pass

    @abstractmethod
    def has_position(self) -> bool:
        """Return True if there is an active position."""
        pass

    @abstractmethod
    def has_resting_entries(self) -> bool:
        """Return True if entry brackets are currently resting."""
        pass
