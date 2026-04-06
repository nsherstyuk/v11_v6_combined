"""Investigation 1: HTF SMA Direction Filter.

Only takes LONG breakouts when price > HTF SMA, SHORT when price < HTF SMA.
Tests multiple HTF periods and SMA lengths.

Parameter sweep:
    HTF bar period: 15, 30, 60 min
    SMA period: 10, 20, 50 bars
    Volume filter: ALL, CONFIRMING

Uses the previous completed HTF bar's SMA to avoid look-ahead bias.
Tests on: IS (2024-2026) and OOS (2018-2023).
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.htf_utils import (
    resample_sessions, compute_sma, build_htf_lookup, get_htf_value_at,
    collect_signals, simulate_trades, compute_stats,
    print_header, print_row_header, print_row,
)
from v11.config.strategy_config import EURUSD_CONFIG

# EURUSD Config B
config = replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
                 max_box_width_atr=3.0, breakout_confirm_bars=2)

RR = 2.0

HTF_PERIODS = [15, 30, 60]     # minutes
SMA_PERIODS = [10, 20, 50]     # bars at HTF resolution

VOLUME_FILTERS = [
    ("ALL", lambda vc: True),
    ("CONFIRMING", lambda vc: vc == "CONFIRMING"),
]


def filter_by_period(raw_trades, start, end):
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


if __name__ == "__main__":
    print("Loading all EURUSD data...")
    all_bars = load_instrument_bars("EURUSD")
    print(f"Total: {len(all_bars):,} bars "
          f"({all_bars[0].timestamp.date()} to {all_bars[-1].timestamp.date()})")

    # Pre-compute all HTF SMA lookups
    print("Computing HTF SMA lookups...")
    sma_lookups = {}  # (htf_min, sma_period) -> lookup dict
    for htf_min in HTF_PERIODS:
        htf_bars = resample_sessions(all_bars, htf_min)
        print(f"  {htf_min}-min bars: {len(htf_bars):,}")
        for sma_p in SMA_PERIODS:
            sma_values = compute_sma(htf_bars, sma_p)
            sma_lookups[(htf_min, sma_p)] = build_htf_lookup(sma_values)
            print(f"    SMA({sma_p}): {len(sma_values):,} values")

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
    print_header("HTF SMA DIRECTION FILTER — IS (2024-2026) vs OOS (2018-2023)")
    print(f"  {'HTF':>4s} {'SMA':>4s} {'Vol':>10s}  |  "
          f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s} {'IS_PnL':>8s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")
    print(f"  {'-' * 100}")

    # Baseline (no SMA filter)
    for vol_name, vol_fn in VOLUME_FILTERS:
        filt = lambda t, vf=vol_fn: vf(t["vol_class"])
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

        print(f"  {'---':>4s} {'---':>4s} {vol_name:>10s}  |  "
              f"{is_s['n']:4d} {is_wr} {is_ar} {is_pnl}  |  "
              f"{oos_s['n']:5d} {oos_wr} {oos_ar} {oos_pnl}  (baseline)")

    print(f"  {'-' * 100}")

    # SMA filter combos
    for htf_min in HTF_PERIODS:
        for sma_p in SMA_PERIODS:
            lookup = sma_lookups[(htf_min, sma_p)]

            for vol_name, vol_fn in VOLUME_FILTERS:
                def make_filter(lk, hm, vf):
                    def f(t):
                        if not vf(t["vol_class"]):
                            return False
                        sma_val = get_htf_value_at(lk, t["signal"].timestamp, hm)
                        if sma_val is None:
                            return False
                        price = t["signal"].breakout_price
                        if t["is_long"]:
                            return price > sma_val
                        else:
                            return price < sma_val
                    return f

                filt = make_filter(lookup, htf_min, vol_fn)

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
                print(f"  {htf_min:4d} {sma_p:4d} {vol_name:>10s}  |  "
                      f"{is_s['n']:4d} {is_wr} {is_ar} {is_pnl}  |  "
                      f"{oos_s['n']:5d} {oos_wr} {oos_ar} {oos_pnl}{flag}")

    print(f"\n  * = fewer than 15 trades in IS or OOS (inconclusive)")
    print(f"  SMA filter: LONG requires price > SMA, SHORT requires price < SMA")
    print(f"  Look-ahead prevention: uses previous completed HTF bar's SMA value")
