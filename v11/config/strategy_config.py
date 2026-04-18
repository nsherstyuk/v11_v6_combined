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

    # HTF SMA direction filter (see V11_DESIGN.md §10)
    htf_sma_enabled: bool = True        # enable 60-min SMA direction filter
    htf_sma_bar_minutes: int = 60       # HTF bar period for SMA computation
    htf_sma_period: int = 50            # SMA lookback in HTF bars

    # 4H Swing Level Detector (see V11_DESIGN.md §12)
    level_detector_enabled: bool = True # enable 4H swing level detection
    level_htf_bar_minutes: int = 240    # HTF bar period (240 = 4H)
    level_left_bars: int = 3            # swing detection: bars required on left (3×4H=12h is sufficient)
    level_right_bars: int = 3           # swing detection: bars required on right
    level_expiry_hours: int = 72        # levels expire after 72h (3 days)
    level_merge_distance: float = 0.00005  # merge levels within 0.5 pips (EURUSD)

    # Retest detector (see V11_DESIGN.md §12)
    retest_min_pullback_bars: int = 10    # min bars after break before rebreak is valid
    retest_max_pullback_bars: int = 30    # max bars for full break → rebreak cycle
    retest_cooldown_bars: int = 60        # ignore level for N bars after entry/expiry
    retest_sl_atr_offset: float = 0.3     # SL placed this many ATR beyond the level
    retest_rr_ratio: float = 2.0          # TP at entry + risk × rr_ratio

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
