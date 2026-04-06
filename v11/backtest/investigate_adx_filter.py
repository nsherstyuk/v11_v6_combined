"""Investigation 4: ADX Trend Strength Filter.

Only takes breakouts when ADX exceeds a threshold (market is trending).
Optionally requires directional alignment (+DI > -DI for longs).

Parameter sweep:
    HTF bar period: 60, 240 min (1h, 4h)
    ADX threshold: 15, 20, 25, 30
    Directional filter: None, aligned (+DI/-DI must match direction)
    Volume filter: ALL, CONFIRMING

Tests on: IS (2024-2026) and OOS (2018-2023).
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.htf_utils import (
    resample_sessions, compute_adx, build_htf_lookup, get_htf_value_at,
    collect_signals, simulate_trades, compute_stats,
    print_header, _floor_timestamp,
)
from v11.config.strategy_config import EURUSD_CONFIG
from datetime import timedelta

# EURUSD Config B
config = replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
                 max_box_width_atr=3.0, breakout_confirm_bars=2)

RR = 2.0

HTF_PERIODS = [60, 240]          # minutes
ADX_THRESHOLDS = [15, 20, 25, 30]
ADX_PERIOD = 14                   # standard


def filter_by_period(raw_trades, start, end):
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


def get_adx_at(lookup, signal_timestamp, htf_minutes):
    """Look up previous completed HTF bar's ADX values (adx, plus_di, minus_di).

    Returns None if not available.
    """
    floored = _floor_timestamp(signal_timestamp, htf_minutes)
    prev_ts = floored - timedelta(minutes=htf_minutes)
    return lookup.get(prev_ts)


if __name__ == "__main__":
    print("Loading all EURUSD data...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"Total: {len(all_bars):,} bars "
          f"({all_bars[0].timestamp.date()} to {all_bars[-1].timestamp.date()})")

    # Pre-compute ADX lookups
    print("Computing ADX lookups...")
    adx_lookups = {}  # htf_min -> lookup dict of (adx, plus_di, minus_di)
    for htf_min in HTF_PERIODS:
        htf_bars = resample_sessions(all_bars, htf_min)
        print(f"  {htf_min}-min bars: {len(htf_bars):,}")
        adx_values = compute_adx(htf_bars, ADX_PERIOD)
        adx_lookups[htf_min] = build_htf_lookup(adx_values)
        print(f"    ADX({ADX_PERIOD}): {len(adx_values):,} values")

    # Collect signals
    print("Collecting signals...")
    all_signals = collect_signals(all_bars, config)
    print(f"Total signals: {len(all_signals)}")

    is_start, is_end = datetime(2024, 1, 1), datetime(2026, 12, 31)
    oos_start, oos_end = datetime(2018, 1, 1), datetime(2023, 12, 31)

    is_signals = filter_by_period(all_signals, is_start, is_end)
    oos_signals = filter_by_period(all_signals, oos_start, oos_end)

    print(f"IS signals: {len(is_signals)}, OOS signals: {len(oos_signals)}")

    # ── Results Table ──────────────────────────────────────────────────
    print_header("ADX TREND STRENGTH FILTER — IS (2024-2026) vs OOS (2018-2023)")
    print(f"  {'HTF':>4s} {'ADX>':>5s} {'DirFlt':>7s} {'Vol':>10s}  |  "
          f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s} {'IS_PnL':>8s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")
    print(f"  {'-' * 105}")

    # Baseline
    for vol_name, vol_fn in [("ALL", lambda vc: True), ("CONFIRMING", lambda vc: vc == "CONFIRMING")]:
        filt = lambda t, vf=vol_fn: vf(t["vol_class"])
        is_s = compute_stats(simulate_trades(is_signals, RR, config, filter_fn=filt))
        oos_s = compute_stats(simulate_trades(oos_signals, RR, config, filter_fn=filt))

        is_str = f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f} {is_s['pnl']:+8.4f}" if is_s['n'] > 0 else "   0   ---     ---      ---"
        oos_str = f"{oos_s['n']:5d} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} {oos_s['pnl']:+9.4f}" if oos_s['n'] > 0 else "    0    ---      ---       ---"

        print(f"  {'---':>4s} {'---':>5s} {'---':>7s} {vol_name:>10s}  |  "
              f"{is_str}  |  {oos_str}  (baseline)")

    print(f"  {'-' * 105}")

    # ADX filter combos
    for htf_min in HTF_PERIODS:
        lookup = adx_lookups[htf_min]

        for adx_thresh in ADX_THRESHOLDS:
            for dir_filter_name, use_dir_filter in [("None", False), ("Aligned", True)]:
                for vol_name, vol_fn in [("ALL", lambda vc: True),
                                          ("CONFIRMING", lambda vc: vc == "CONFIRMING")]:

                    def make_filter(lk, hm, thresh, use_dir, vf):
                        def f(t):
                            if not vf(t["vol_class"]):
                                return False
                            adx_data = get_adx_at(lk, t["signal"].timestamp, hm)
                            if adx_data is None:
                                return False
                            adx_val, plus_di, minus_di = adx_data
                            if adx_val < thresh:
                                return False
                            if use_dir:
                                if t["is_long"] and plus_di <= minus_di:
                                    return False
                                if not t["is_long"] and minus_di <= plus_di:
                                    return False
                            return True
                        return f

                    filt = make_filter(lookup, htf_min, adx_thresh,
                                       use_dir_filter, vol_fn)

                    is_trades = simulate_trades(is_signals, RR, config, filter_fn=filt)
                    oos_trades = simulate_trades(oos_signals, RR, config, filter_fn=filt)

                    is_s = compute_stats(is_trades)
                    oos_s = compute_stats(oos_trades)

                    is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f} "
                              f"{is_s['pnl']:+8.4f}") if is_s['n'] > 0 else "   0   ---     ---      ---"
                    oos_str = (f"{oos_s['n']:5d} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} "
                               f"{oos_s['pnl']:+9.4f}") if oos_s['n'] > 0 else "    0    ---      ---       ---"

                    flag = " *" if (is_s['n'] < 15 or oos_s['n'] < 15) else ""
                    print(f"  {htf_min:4d} {adx_thresh:5d} {dir_filter_name:>7s} "
                          f"{vol_name:>10s}  |  {is_str}  |  {oos_str}{flag}")

    print(f"\n  * = fewer than 15 trades in IS or OOS (inconclusive)")
    print(f"  ADX filter: only trade when ADX > threshold (market trending)")
    print(f"  Aligned: +DI > -DI for longs, -DI > +DI for shorts")
    print(f"  Look-ahead prevention: uses previous completed HTF bar's ADX")
