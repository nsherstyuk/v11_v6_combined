"""Investigation 2: Run Darvas on 5-min / 15-min Resampled Bars.

Resamples 1-min bars to longer timeframes and runs DarvasDetector directly
on them. Tests whether Darvas boxes on longer bars produce a more durable,
regime-independent edge.

Critical rescaling:
    max_hold_bars:   120 (1-min) -> 24 (5-min) -> 8 (15-min)  [2 hours]
    atr_period:       60 (1-min) -> 12 (5-min) -> 4 (15-min)  [1 hour]
    min_box_duration:  20 (1-min) ->  4 (5-min) -> 2 (15-min) [~20 min]

Grid sweep per bar period:
    top_confirm_bars: 8, 12, 15, 20
    bottom_confirm_bars: 8, 12, 15, 20
    max_box_width_atr: 2.0, 3.0, 5.0
    breakout_confirm_bars: 1, 2, 3

= 144 combos x 2 bar periods x 2 R:R = 576 combos

Reports top 10 by Sharpe for IS and OOS separately.
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from itertools import product
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.htf_utils import resample_sessions
from v11.backtest.simulator import run_backtest
from v11.backtest.metrics import compute_metrics, MetricsReport
from v11.config.strategy_config import EURUSD_CONFIG
import pandas as pd

# Time-based parameter rescaling
RESCALE = {
    5:  {"max_hold_bars": 24, "atr_period": 12, "min_box_duration": 4},
    15: {"max_hold_bars": 8,  "atr_period": 4,  "min_box_duration": 2},
}

# Grid parameters to sweep
PARAM_GRID = {
    "top_confirm_bars":     [8, 12, 15, 20],
    "bottom_confirm_bars":  [8, 12, 15, 20],
    "max_box_width_atr":    [2.0, 3.0, 5.0],
    "breakout_confirm_bars": [1, 2, 3],
}

RR_VALUES = [1.5, 2.0]
BAR_PERIODS = [5, 15]  # minutes
MIN_TRADES = 5


def build_configs(bar_period: int):
    """Build all StrategyConfig variants for a given bar period."""
    rescale = RESCALE[bar_period]
    keys = list(PARAM_GRID.keys())
    combos = list(product(*[PARAM_GRID[k] for k in keys]))

    configs = []
    for combo in combos:
        overrides = dict(zip(keys, combo))
        overrides.update(rescale)
        overrides["min_box_width_atr"] = 0.3  # keep default
        cfg = replace(EURUSD_CONFIG, **overrides)
        configs.append(cfg)

    return configs


def run_grid(bars, configs, rr, label):
    """Run backtest for all configs, return sorted results."""
    results = []
    total = len(configs)
    for idx, cfg in enumerate(configs):
        if (idx + 1) % 50 == 0:
            print(f"    {label}: {idx + 1}/{total}...")
        bt_result = run_backtest(bars, cfg, rr_ratio=rr)
        if len(bt_result.trades) >= MIN_TRADES:
            metrics = compute_metrics(bt_result)
            results.append(metrics)

    # Sort by Sharpe
    results.sort(key=lambda m: m.sharpe_ratio, reverse=True)
    return results


def print_top(results, n=10, label=""):
    """Print top N results."""
    print(f"\n  Top {n} by Sharpe — {label}")
    print(f"  {'#':>3s} {'tc':>3s} {'bc':>3s} {'mxW':>4s} {'brk':>3s} "
          f"{'N':>4s} {'WR%':>6s} {'AvgR':>7s} {'PF':>5s} {'Sharpe':>7s} "
          f"{'PnL':>8s} {'MaxDD':>8s}")
    print(f"  {'-' * 75}")

    for i, m in enumerate(results[:n]):
        p = m.config_params
        print(f"  {i+1:3d} {p.get('top_confirm_bars', 0):3d} "
              f"{p.get('bottom_confirm_bars', 0):3d} "
              f"{p.get('max_box_width_atr', 0):4.1f} "
              f"{p.get('breakout_confirm_bars', 0):3d} "
              f"{m.trades_taken:4d} {m.win_rate:6.1f} {m.avg_pnl_r:+7.3f} "
              f"{m.profit_factor:5.2f} {m.sharpe_ratio:7.3f} "
              f"{m.total_pnl:+8.4f} {m.max_drawdown:8.4f}")


if __name__ == "__main__":
    print("Loading all EURUSD data...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"Total 1-min bars: {len(all_bars):,}")

    is_start, is_end = datetime(2024, 1, 1), datetime(2026, 12, 31)
    oos_start, oos_end = datetime(2018, 1, 1), datetime(2023, 12, 31)

    is_bars_1m = [b for b in all_bars if is_start <= b.timestamp <= is_end]
    oos_bars_1m = [b for b in all_bars if oos_start <= b.timestamp <= oos_end]

    print(f"IS bars: {len(is_bars_1m):,}, OOS bars: {len(oos_bars_1m):,}")

    for bar_period in BAR_PERIODS:
        print(f"\n{'=' * 80}")
        print(f"  DARVAS ON {bar_period}-MIN BARS")
        print(f"  Rescaled: hold={RESCALE[bar_period]['max_hold_bars']}, "
              f"atr_period={RESCALE[bar_period]['atr_period']}, "
              f"min_duration={RESCALE[bar_period]['min_box_duration']}")
        print(f"{'=' * 80}")

        # Resample
        is_bars = resample_sessions(is_bars_1m, bar_period)
        oos_bars = resample_sessions(oos_bars_1m, bar_period)
        print(f"  IS {bar_period}-min bars: {len(is_bars):,}")
        print(f"  OOS {bar_period}-min bars: {len(oos_bars):,}")

        configs = build_configs(bar_period)
        print(f"  Grid: {len(configs)} parameter combinations")

        for rr in RR_VALUES:
            print(f"\n  --- R:R = {rr} ---")

            print(f"  Running IS grid...")
            is_results = run_grid(is_bars, configs, rr, f"IS {bar_period}m RR={rr}")
            print(f"  IS: {len(is_results)} configs with >= {MIN_TRADES} trades")
            print_top(is_results, 10, f"IS {bar_period}-min R:R={rr}")

            print(f"  Running OOS grid...")
            oos_results = run_grid(oos_bars, configs, rr, f"OOS {bar_period}m RR={rr}")
            print(f"  OOS: {len(oos_results)} configs with >= {MIN_TRADES} trades")
            print_top(oos_results, 10, f"OOS {bar_period}-min R:R={rr}")

            # Cross-check: run IS top config on OOS
            if is_results:
                best_is = is_results[0]
                best_params = best_is.config_params
                cross_cfg = replace(EURUSD_CONFIG,
                                    top_confirm_bars=best_params.get("top_confirm_bars", 15),
                                    bottom_confirm_bars=best_params.get("bottom_confirm_bars", 15),
                                    max_box_width_atr=best_params.get("max_box_width_atr", 3.0),
                                    breakout_confirm_bars=best_params.get("breakout_confirm_bars", 2),
                                    min_box_width_atr=0.3,
                                    **RESCALE[bar_period])

                cross_bt = run_backtest(oos_bars, cross_cfg, rr_ratio=rr)
                if cross_bt.trades:
                    cross_m = compute_metrics(cross_bt)
                    print(f"\n  >>> CROSS-CHECK: Best IS config on OOS data:")
                    print(f"      Params: tc={cross_cfg.top_confirm_bars} "
                          f"bc={cross_cfg.bottom_confirm_bars} "
                          f"mxW={cross_cfg.max_box_width_atr} "
                          f"brk={cross_cfg.breakout_confirm_bars}")
                    print(f"      N={cross_m.trades_taken} WR={cross_m.win_rate:.1f}% "
                          f"AvgR={cross_m.avg_pnl_r:+.3f} "
                          f"Sharpe={cross_m.sharpe_ratio:.3f} "
                          f"PnL={cross_m.total_pnl:+.4f}")
                else:
                    print(f"\n  >>> CROSS-CHECK: Best IS config → 0 trades on OOS")
