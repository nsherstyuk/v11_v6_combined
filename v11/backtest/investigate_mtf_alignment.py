"""Investigation 5: Multi-Timeframe Box Alignment.

Runs DarvasDetector on both 1-min (micro) and a higher timeframe (macro).
Only takes micro breakouts when the breakout price is near a macro box boundary.

The idea: micro breakouts near macro structural levels are more significant
because they represent breaks of consolidation at multiple scales.

Parameter sweep:
    Macro bar period: 15, 60 min
    Macro top_confirm: 8, 12, 15
    Macro bottom_confirm: 8, 12, 15
    Macro max_box_width_atr: 3.0, 5.0
    Proximity threshold (ATR multiples): 0.5, 1.0, 1.5, 2.0
    Direction alignment: Yes (micro direction matches macro boundary side), No

= 288 combinations, tested IS + OOS. Micro params fixed at Config B.
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime, timedelta
from dataclasses import replace
from itertools import product
from typing import Dict, List, Optional, Tuple

from v11.backtest.data_loader import load_instrument_bars, split_by_sessions
from v11.backtest.htf_utils import (
    resample_bars, collect_signals, simulate_trades, compute_stats,
    print_header, _floor_timestamp,
)
from v11.core.darvas_detector import DarvasDetector
from v11.core.types import Bar, DarvasBox, Direction
from v11.config.strategy_config import EURUSD_CONFIG, StrategyConfig


# Micro config: EURUSD Config B (fixed)
MICRO_CONFIG = replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
                       max_box_width_atr=3.0, breakout_confirm_bars=2)

RR = 2.0

# Macro parameter sweep
MACRO_PERIODS = [15, 60]  # minutes
MACRO_GRID = {
    "top_confirm_bars":     [8, 12, 15],
    "bottom_confirm_bars":  [8, 12, 15],
    "max_box_width_atr":    [3.0, 5.0],
}
PROXIMITY_THRESHOLDS = [0.5, 1.0, 1.5, 2.0]  # ATR multiples
DIRECTION_ALIGNMENT = [False, True]


# Rescale time-based params for macro bars
MACRO_RESCALE = {
    15: {"max_hold_bars": 8,  "atr_period": 4,  "min_box_duration": 2,
         "min_box_width_atr": 0.3, "breakout_confirm_bars": 2},
    60: {"max_hold_bars": 2,  "atr_period": 4,  "min_box_duration": 2,
         "min_box_width_atr": 0.3, "breakout_confirm_bars": 1},
}


def build_macro_configs(macro_period: int) -> List[StrategyConfig]:
    """Build all macro StrategyConfig variants."""
    rescale = MACRO_RESCALE[macro_period]
    keys = list(MACRO_GRID.keys())
    combos = list(product(*[MACRO_GRID[k] for k in keys]))

    configs = []
    for combo in combos:
        overrides = dict(zip(keys, combo))
        overrides.update(rescale)
        cfg = replace(EURUSD_CONFIG, **overrides)
        configs.append(cfg)
    return configs


def build_macro_box_timeline(bars: List[Bar], config: StrategyConfig,
                             gap_minutes: int = 30
                             ) -> Dict[datetime, Optional[DarvasBox]]:
    """Run DarvasDetector on bars and record active_box at each timestamp.

    Resets detector between sessions (same as the 1-min pipeline).

    Returns dict mapping bar.timestamp -> active_box (or None).
    """
    sessions = split_by_sessions(bars, gap_minutes=gap_minutes)
    timeline = {}

    for session_bars in sessions:
        det = DarvasDetector(config)
        for bar in session_bars:
            det.add_bar(bar)
            timeline[bar.timestamp] = det.active_box

    return timeline


def get_macro_box_at(timeline: Dict[datetime, Optional[DarvasBox]],
                     signal_timestamp: datetime,
                     macro_minutes: int) -> Optional[DarvasBox]:
    """Look up the macro box active at the previous completed macro bar.

    Uses the previous HTF bar's state to avoid look-ahead bias.
    """
    floored = _floor_timestamp(signal_timestamp, macro_minutes)
    prev_ts = floored - timedelta(minutes=macro_minutes)
    return timeline.get(prev_ts)


def check_proximity(signal, macro_box: DarvasBox, threshold_atr: float
                    ) -> Tuple[bool, str]:
    """Check if micro breakout price is near a macro box boundary.

    Uses micro ATR for normalization (signal's native scale).

    Returns (passes_filter, nearest_boundary: "top"/"bottom"/"none").
    """
    price = signal.breakout_price
    atr = signal.atr

    if atr <= 0:
        return False, "none"

    dist_to_top = abs(price - macro_box.top)
    dist_to_bottom = abs(price - macro_box.bottom)

    min_dist = min(dist_to_top, dist_to_bottom)
    proximity = min_dist / atr

    if proximity <= threshold_atr:
        boundary = "top" if dist_to_top <= dist_to_bottom else "bottom"
        return True, boundary
    return False, "none"


def direction_matches_boundary(is_long: bool, boundary: str) -> bool:
    """Check if micro direction aligns with macro boundary.

    LONG near macro top = aligned (breaking out of macro box upward).
    SHORT near macro bottom = aligned (breaking down from macro box).
    """
    if is_long and boundary == "top":
        return True
    if not is_long and boundary == "bottom":
        return True
    return False


def filter_by_period(raw_trades, start, end):
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


if __name__ == "__main__":
    print("Loading all EURUSD data...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"Total 1-min bars: {len(all_bars):,}")

    # Collect micro signals
    print("Collecting micro (1-min) signals...")
    all_signals = collect_signals(all_bars, MICRO_CONFIG)
    print(f"Total micro signals: {len(all_signals)}")

    is_start, is_end = datetime(2024, 1, 1), datetime(2026, 12, 31)
    oos_start, oos_end = datetime(2018, 1, 1), datetime(2023, 12, 31)

    is_signals = filter_by_period(all_signals, is_start, is_end)
    oos_signals = filter_by_period(all_signals, oos_start, oos_end)

    print(f"IS signals: {len(is_signals)}, OOS signals: {len(oos_signals)}")

    # ── Baseline ───────────────────────────────────────────────────────
    print_header("MTF BOX ALIGNMENT — IS (2024-2026) vs OOS (2018-2023)")

    print(f"\n  Baseline (no macro filter, CONFIRMING volume):")
    for label, sigs in [("IS", is_signals), ("OOS", oos_signals)]:
        filt = lambda t: t["vol_class"] == "CONFIRMING"
        s = compute_stats(simulate_trades(sigs, RR, MICRO_CONFIG, filter_fn=filt))
        print(f"    {label}: N={s['n']} WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f} "
              f"PnL={s['pnl']:+.4f}")

    # ── Main sweep ─────────────────────────────────────────────────────
    print(f"\n  {'MacP':>4s} {'tc':>3s} {'bc':>3s} {'mxW':>4s} {'Prox':>5s} "
          f"{'DirAl':>5s}  |  "
          f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s}")
    print(f"  {'-' * 85}")

    best_oos_avg_r = -999.0
    best_oos_combo = None

    combo_count = 0
    total_combos = (len(MACRO_PERIODS) * len(build_macro_configs(15))
                    * len(PROXIMITY_THRESHOLDS) * len(DIRECTION_ALIGNMENT))

    for macro_period in MACRO_PERIODS:
        # Resample all bars to macro timeframe
        macro_bars = resample_bars(all_bars, macro_period)
        print(f"\n  --- Macro period: {macro_period}-min ({len(macro_bars):,} bars) ---")

        macro_configs = build_macro_configs(macro_period)

        for macro_cfg in macro_configs:
            # Build macro box timeline for this config
            timeline = build_macro_box_timeline(macro_bars, macro_cfg)

            for prox_thresh in PROXIMITY_THRESHOLDS:
                for require_dir_align in DIRECTION_ALIGNMENT:
                    combo_count += 1
                    if combo_count % 50 == 0:
                        print(f"  ... {combo_count}/{total_combos} combos...")

                    # Build filter
                    def make_filter(tl, mp, pt, rda):
                        def f(t):
                            if t["vol_class"] != "CONFIRMING":
                                return False
                            macro_box = get_macro_box_at(tl, t["signal"].timestamp, mp)
                            if macro_box is None:
                                return False
                            passes, boundary = check_proximity(t["signal"], macro_box, pt)
                            if not passes:
                                return False
                            if rda and not direction_matches_boundary(t["is_long"], boundary):
                                return False
                            return True
                        return f

                    filt = make_filter(timeline, macro_period, prox_thresh,
                                       require_dir_align)

                    is_trades = simulate_trades(is_signals, RR, MICRO_CONFIG, filter_fn=filt)
                    oos_trades = simulate_trades(oos_signals, RR, MICRO_CONFIG, filter_fn=filt)

                    is_s = compute_stats(is_trades)
                    oos_s = compute_stats(oos_trades)

                    # Track best OOS
                    if oos_s['n'] >= 15 and oos_s['avg_r'] > best_oos_avg_r:
                        best_oos_avg_r = oos_s['avg_r']
                        best_oos_combo = {
                            "macro_period": macro_period,
                            "tc": macro_cfg.top_confirm_bars,
                            "bc": macro_cfg.bottom_confirm_bars,
                            "mxW": macro_cfg.max_box_width_atr,
                            "prox": prox_thresh,
                            "dir_align": require_dir_align,
                            "is": is_s,
                            "oos": oos_s,
                        }

                    # Only print if OOS has some trades
                    if oos_s['n'] > 0 or is_s['n'] > 0:
                        is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
                                  if is_s['n'] > 0 else "   0   ---     ---")
                        oos_str = (f"{oos_s['n']:5d} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f}"
                                   if oos_s['n'] > 0 else "    0    ---      ---")

                        da_str = "Yes" if require_dir_align else "No"
                        flag = " *" if (is_s['n'] < 15 or oos_s['n'] < 15) else ""
                        print(f"  {macro_period:4d} {macro_cfg.top_confirm_bars:3d} "
                              f"{macro_cfg.bottom_confirm_bars:3d} "
                              f"{macro_cfg.max_box_width_atr:4.1f} "
                              f"{prox_thresh:5.1f} {da_str:>5s}  |  "
                              f"{is_str}  |  {oos_str}{flag}")

    # ── Best OOS Result ────────────────────────────────────────────────
    if best_oos_combo:
        print(f"\n{'=' * 85}")
        print(f"  BEST OOS RESULT (>= 15 trades)")
        print(f"{'=' * 85}")
        b = best_oos_combo
        print(f"  Macro: {b['macro_period']}-min, tc={b['tc']}, bc={b['bc']}, "
              f"mxW={b['mxW']}, proximity={b['prox']} ATR, "
              f"dir_align={'Yes' if b['dir_align'] else 'No'}")
        print(f"  IS:  N={b['is']['n']} WR={b['is']['wr']:.1f}% "
              f"AvgR={b['is']['avg_r']:+.3f} PnL={b['is']['pnl']:+.4f}")
        print(f"  OOS: N={b['oos']['n']} WR={b['oos']['wr']:.1f}% "
              f"AvgR={b['oos']['avg_r']:+.3f} PnL={b['oos']['pnl']:+.4f}")
    else:
        print(f"\n  No OOS result with >= 15 trades found.")

    print(f"\n  Proximity: min(|price - box.top|, |price - box.bottom|) / micro_ATR")
    print(f"  Dir alignment: LONG near macro top, SHORT near macro bottom")
    print(f"  Volume filter: CONFIRMING only (hardcoded based on prior findings)")
    print(f"  Look-ahead prevention: macro box from previous completed macro bar")
