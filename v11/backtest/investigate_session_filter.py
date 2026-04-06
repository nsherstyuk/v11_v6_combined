"""Investigation 3: Session / Time-of-Day Filter.

Tests whether breakouts during specific trading sessions (by UTC hour)
are more reliable. Applies as a post-filter to existing 1-min signals.

Filters tested:
    - Asian only (00-07 UTC)
    - London only (08-12 UTC)
    - NY only (13-16 UTC)
    - London+NY (08-16 UTC)
    - No Asian (>= 08 UTC)
    - Core hours (08-16 UTC)
    - All (baseline)

Cross with: ALL vs CONFIRMING volume filter.
Tests on: IS (2024-2026) and OOS (2018-2023).
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.htf_utils import (
    collect_signals, simulate_trades, compute_stats,
    print_header, print_row_header, print_row,
)
from v11.config.strategy_config import EURUSD_CONFIG
import pandas as pd

# EURUSD Config B (best IS config)
config = replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
                 max_box_width_atr=3.0, breakout_confirm_bars=2)

RR = 2.0

# Session filters: name -> filter function on signal timestamp hour
SESSION_FILTERS = [
    ("All (baseline)", lambda h: True),
    ("Asian (00-07)",  lambda h: 0 <= h <= 7),
    ("London (08-12)", lambda h: 8 <= h <= 12),
    ("NY (13-16)",     lambda h: 13 <= h <= 16),
    ("London+NY (08-16)", lambda h: 8 <= h <= 16),
    ("No Asian (>=08)", lambda h: h >= 8),
    ("Late NY (17-21)", lambda h: 17 <= h <= 21),
]

VOLUME_FILTERS = [
    ("ALL", lambda vc: True),
    ("CONFIRMING", lambda vc: vc == "CONFIRMING"),
]


def filter_by_period(raw_trades, start, end):
    """Filter raw trades by timestamp range."""
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


if __name__ == "__main__":
    print("Loading all EURUSD data...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"Total: {len(all_bars):,} bars "
          f"({all_bars[0].timestamp.date()} to {all_bars[-1].timestamp.date()})")

    print("Collecting signals...")
    all_signals = collect_signals(all_bars, config)
    print(f"Total signals: {len(all_signals)}")

    # Split into IS and OOS
    is_start, is_end = datetime(2024, 1, 1), datetime(2026, 12, 31)
    oos_start, oos_end = datetime(2018, 1, 1), datetime(2023, 12, 31)

    is_signals = filter_by_period(all_signals, is_start, is_end)
    oos_signals = filter_by_period(all_signals, oos_start, oos_end)

    print(f"IS signals: {len(is_signals)}, OOS signals: {len(oos_signals)}")

    # ── Hour-of-Day Histogram ──────────────────────────────────────────
    print_header("HOUR-OF-DAY HISTOGRAM (ALL signals, CONFIRMING, R:R=2.0, Trail10@60)")
    print(f"  {'Hour':>4s} {'IS_N':>5s} {'IS_WR':>6s} {'IS_AvgR':>7s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s}")
    print(f"  {'-' * 65}")

    for hour in range(24):
        for label, period_signals in [("IS", is_signals), ("OOS", oos_signals)]:
            pass  # handled below

    # Build histogram data
    for hour in range(24):
        is_h = [t for t in is_signals if t["signal"].timestamp.hour == hour
                and t["vol_class"] == "CONFIRMING"]
        oos_h = [t for t in oos_signals if t["signal"].timestamp.hour == hour
                 and t["vol_class"] == "CONFIRMING"]

        is_trades = simulate_trades(is_h, RR, config)
        oos_trades = simulate_trades(oos_h, RR, config)

        is_s = compute_stats(is_trades)
        oos_s = compute_stats(oos_trades)

        is_wr_str = f"{is_s['wr']:6.1f}" if is_s['n'] > 0 else "   ---"
        is_ar_str = f"{is_s['avg_r']:+7.3f}" if is_s['n'] > 0 else "    ---"
        oos_wr_str = f"{oos_s['wr']:7.1f}" if oos_s['n'] > 0 else "    ---"
        oos_ar_str = f"{oos_s['avg_r']:+8.3f}" if oos_s['n'] > 0 else "     ---"

        print(f"  {hour:4d} {is_s['n']:5d} {is_wr_str} {is_ar_str}  |  "
              f"{oos_s['n']:5d} {oos_wr_str} {oos_ar_str}")

    # ── Session × Volume Cross-Tabulation ──────────────────────────────
    print_header("SESSION x VOLUME FILTER — IS (2024-2026) vs OOS (2018-2023)")
    print(f"  {'Session':20s} {'Vol':>10s}  |  "
          f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s} {'IS_PnL':>8s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")
    print(f"  {'-' * 105}")

    for sess_name, sess_fn in SESSION_FILTERS:
        for vol_name, vol_fn in VOLUME_FILTERS:
            def make_filter(sf, vf):
                return lambda t: sf(t["signal"].timestamp.hour) and vf(t["vol_class"])

            filt = make_filter(sess_fn, vol_fn)

            is_trades = simulate_trades(is_signals, RR, config, filter_fn=filt)
            oos_trades = simulate_trades(oos_signals, RR, config, filter_fn=filt)

            is_s = compute_stats(is_trades)
            oos_s = compute_stats(oos_trades)

            is_wr = f"{is_s['wr']:6.1f}" if is_s['n'] > 0 else "   ---"
            is_ar = f"{is_s['avg_r']:+7.3f}" if is_s['n'] > 0 else "    ---"
            is_pnl = f"{is_s['pnl']:+8.4f}" if is_s['n'] > 0 else "     ---"
            oos_wr = f"{oos_s['wr']:7.1f}" if oos_s['n'] > 0 else "    ---"
            oos_ar = f"{oos_s['avg_r']:+8.3f}" if oos_s['n'] > 0 else "     ---"
            oos_pnl = f"{oos_s['pnl']:+9.4f}" if oos_s['n'] > 0 else "      ---"

            flag = " *" if (is_s['n'] < 15 or oos_s['n'] < 15) else ""
            print(f"  {sess_name:20s} {vol_name:>10s}  |  "
                  f"{is_s['n']:4d} {is_wr} {is_ar} {is_pnl}  |  "
                  f"{oos_s['n']:5d} {oos_wr} {oos_ar} {oos_pnl}{flag}")

    print(f"\n  * = fewer than 15 trades in IS or OOS (inconclusive)")
