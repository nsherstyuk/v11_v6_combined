"""
V11 Strategy Configuration — Pure strategy parameters, environment-agnostic.

No file paths, no API keys, no broker settings.
Frozen dataclass: parameters are immutable after construction.

Darvas parameters confirmed 2026-04-05 (see docs/V11_DESIGN.md §2, §8).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    """Pure strategy parameters for a single instrument.

    Each instrument gets its own StrategyConfig instance since volatility
    profiles differ (XAUUSD vs EURUSD vs USDJPY).
    """
    instrument: str

    # Darvas box formation (confirmed initial values — tunable via backtest)
    top_confirm_bars: int = 15          # bars without new high to confirm box top
    bottom_confirm_bars: int = 15       # bars without new low to confirm box bottom
    min_box_width_atr: float = 0.3      # minimum box width as ATR multiple
    max_box_width_atr: float = 5.0      # maximum box width as ATR multiple
    min_box_duration: int = 20          # minimum bars for box to be valid

    # Breakout confirmation
    breakout_confirm_bars: int = 3      # consecutive bars above/below box to confirm

    # Imbalance classification
    imbalance_window: int = 3           # bars to measure buy_ratio at breakout
    divergence_threshold: float = 0.50  # buy_ratio threshold for confirming/divergent

    # Trade management
    max_hold_bars: int = 120            # time stop in bars (2 hours at 1-min)
    atr_period: int = 60                # bars for ATR computation

    # Volume quality filter
    min_bar_ticks: int = 5              # min tick_count per bar in imbalance window

    # Costs (instrument-specific)
    spread_cost: float = 0.30           # round-trip spread in price units
    tick_size: float = 0.01             # minimum price increment for orders


# ── Pre-built configs for confirmed instruments ─────────────────────────────

XAUUSD_CONFIG = StrategyConfig(
    instrument="XAUUSD",
    spread_cost=0.30,
    tick_size=0.01,
)

EURUSD_CONFIG = StrategyConfig(
    instrument="EURUSD",
    spread_cost=0.00010,
    tick_size=0.00005,
    min_bar_ticks=10,
)

USDJPY_CONFIG = StrategyConfig(
    instrument="USDJPY",
    spread_cost=0.010,
    tick_size=0.005,
    min_bar_ticks=10,
)
