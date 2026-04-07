"""
V6 StrategyConfig — copied from C:\\nautilus0\\v6_orb_refactor\\config\\config.py
DO NOT MODIFY — frozen V6 code. Only StrategyConfig kept; yaml/load removed.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    """Pure strategy parameters.
    Environment-agnostic: no file paths, API keys, or broker settings.
    """
    instrument: str

    # Time Windows (in UTC)
    range_start_hour: int = 0
    range_end_hour: int = 6
    trade_start_hour: int = 8
    trade_end_hour: int = 16

    # Days to avoid trading (0 = Monday, 6 = Sunday)
    skip_weekdays: tuple = ()

    # Velocity Filter
    velocity_filter_enabled: bool = True
    velocity_lookback_minutes: int = 3
    velocity_threshold: float = 200.0

    # Trade Management
    rr_ratio: float = 2.5

    # Range validity (absolute price units, e.g. dollars for XAUUSD)
    min_range_size: float = 1.0
    max_range_size: float = 15.0

    # Range validity as % of price (used if > 0, overrides absolute)
    min_range_pct: float = 0.05
    max_range_pct: float = 2.0

    # Breakeven: move SL to entry + offset after N hours (999 = disabled)
    be_hours: float = 999
    be_offset: float = 2.0

    # Cancel unfilled entries after N hours (0 = full window)
    max_pending_hours: int = 4

    # Close at market after N minutes in trade (0 = disabled)
    time_exit_minutes: int = 0

    # Gap Filter (06:00-08:00 UTC pre-trade window analysis)
    # Rolling gap volatility: skip day if gap vol < trailing percentile
    gap_filter_enabled: bool = False
    gap_vol_percentile: float = 50.0      # Rolling percentile threshold (P50)
    gap_range_filter_enabled: bool = False # Optional second filter
    gap_range_percentile: float = 40.0    # Rolling percentile for gap range
    gap_rolling_days: int = 60            # Trailing window for percentile calc
    gap_start_hour: int = 6              # Gap period start (UTC)
    gap_end_hour: int = 8                # Gap period end (UTC)

    # Position sizing
    qty: int = 1
    point_value: float = 1.0

    # Display
    price_decimals: int = 2
