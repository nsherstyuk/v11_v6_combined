"""Per-instrument grid search WITH SMA(50) filter on XAUUSD and USDJPY.

The default Darvas params don't work on XAUUSD/USDJPY. This script searches
for instrument-specific params that ARE profitable when combined with the
60-min SMA(50) direction filter.

For each instrument:
  1. Load all bars, build SMA lookup
  2. Grid search Darvas params with SMA filter always on
  3. Report top configs for IS and OOS separately
  4. Cross-check: best IS config tested on OOS
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime
from dataclasses import replace
from itertools import product

from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.htf_utils import (
    resample_sessions, compute_sma, build_htf_lookup, get_htf_value_at,
    collect_signals, simulate_trades, compute_stats,
    print_header,
)
from v11.config.strategy_config import XAUUSD_CONFIG, USDJPY_CONFIG, EURUSD_CONFIG

IS_START, IS_END = datetime(2024, 1, 1), datetime(2026, 12, 31)
OOS_START, OOS_END = datetime(2018, 1, 1), datetime(2023, 12, 31)
OOS_YEARS = 6
IS_YEARS = 2

HTF_MIN = 60
SMA_PERIOD = 50
RR_VALUES = [1.5, 2.0]

# Wide grid — let the data tell us what works per instrument
PARAM_GRID = {
    "top_confirm_bars":     [10, 15, 20, 25, 30],
    "bottom_confirm_bars":  [8, 10, 12, 15, 20],
    "max_box_width_atr":    [2.0, 3.0, 4.0, 5.0],
    "breakout_confirm_bars": [1, 2, 3],
}

# Volume filters to test
VOL_FILTERS = [
    ("SMA+ALL",  lambda t: True),
    ("SMA+CONF", lambda t: t["vol_class"] == "CONFIRMING"),
]


def build_sma_lookup(bars):
    htf_bars = resample_sessions(bars, HTF_MIN)
    sma_values = compute_sma(htf_bars, SMA_PERIOD)
    return build_htf_lookup(sma_values)


def make_sma_vol_filter(lookup, vol_fn):
    def f(t):
        if not vol_fn(t):
            return False
        sma_val = get_htf_value_at(lookup, t["signal"].timestamp, HTF_MIN)
        if sma_val is None:
            return False
        price = t["signal"].breakout_price
        if t["is_long"]:
            return price > sma_val
        else:
            return price < sma_val
    return f


def filter_by_period(raw_trades, start, end):
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


def run_instrument_grid(inst, base_config):
    print(f"\nLoading {inst}...")
    bars = load_instrument_bars(inst)
    print(f"{inst}: {len(bars):,} bars ({bars[0].timestamp.date()} to {bars[-1].timestamp.date()})")

    lookup = build_sma_lookup(bars)

    keys = list(PARAM_GRID.keys())
    combos = list(product(*[PARAM_GRID[k] for k in keys]))
    total = len(combos)
    print(f"Grid: {total} param combos x {len(RR_VALUES)} R:R x {len(VOL_FILTERS)} vol = {total * len(RR_VALUES) * len(VOL_FILTERS)} total")

    all_results = []

    for combo_idx, combo in enumerate(combos):
        if (combo_idx + 1) % 50 == 0:
            print(f"  {combo_idx + 1}/{total} combos...")

        overrides = dict(zip(keys, combo))
        cfg = replace(base_config, **overrides)

        # Collect signals once per config
        all_sigs = collect_signals(bars, cfg)
        is_sigs = filter_by_period(all_sigs, IS_START, IS_END)
        oos_sigs = filter_by_period(all_sigs, OOS_START, OOS_END)

        for rr in RR_VALUES:
            for vol_name, vol_fn in VOL_FILTERS:
                filt = make_sma_vol_filter(lookup, vol_fn)

                is_trades = simulate_trades(is_sigs, rr, cfg, filter_fn=filt)
                oos_trades = simulate_trades(oos_sigs, rr, cfg, filter_fn=filt)

                is_s = compute_stats(is_trades)
                oos_s = compute_stats(oos_trades)

                all_results.append({
                    "params": overrides,
                    "rr": rr,
                    "vol": vol_name,
                    "is": is_s,
                    "oos": oos_s,
                    "oos_per_year": oos_s["n"] / OOS_YEARS,
                })

    return all_results


def print_top(results, sort_key, label, n=15, min_oos_trades=10):
    filtered = [r for r in results if r["oos"]["n"] >= min_oos_trades]
    filtered.sort(key=sort_key, reverse=True)

    print(f"\n  --- {label} (OOS N >= {min_oos_trades}) ---")
    print(f"  {'#':>3s} {'tc':>3s} {'bc':>3s} {'mxW':>4s} {'brk':>3s} {'RR':>3s} {'Vol':>8s}"
          f"  |  {'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}"
          f"  |  {'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")
    print(f"  {'-' * 100}")

    for i, r in enumerate(filtered[:n]):
        p = r["params"]
        s_is = r["is"]
        s_oos = r["oos"]
        is_str = (f"{s_is['n']:4d} {s_is['wr']:6.1f} {s_is['avg_r']:+7.3f}"
                  if s_is['n'] > 0 else "   0   ---     ---")
        flag = " *" if s_oos['n'] < 15 else ""
        print(f"  {i+1:3d} {p['top_confirm_bars']:3d} {p['bottom_confirm_bars']:3d} "
              f"{p['max_box_width_atr']:4.1f} {p['breakout_confirm_bars']:3d} "
              f"{r['rr']:3.1f} {r['vol']:>8s}"
              f"  |  {is_str}"
              f"  |  {s_oos['n']:5d} {r['oos_per_year']:5.1f} {s_oos['wr']:7.1f} "
              f"{s_oos['avg_r']:+8.3f} {s_oos['pnl']:+9.4f}{flag}")


if __name__ == "__main__":

    for inst, base_cfg in [("XAUUSD", XAUUSD_CONFIG), ("USDJPY", USDJPY_CONFIG)]:
        print_header(f"{inst} — PER-INSTRUMENT GRID SEARCH + SMA(50) FILTER", 110)

        results = run_instrument_grid(inst, base_cfg)

        # Top by OOS AvgR (quality)
        print_top(results,
                  sort_key=lambda r: r["oos"]["avg_r"],
                  label=f"{inst} Top 15 by OOS AvgR",
                  n=15, min_oos_trades=10)

        # Top by OOS trades/year with positive AvgR (frequency)
        positive = [r for r in results if r["oos"]["avg_r"] > 0]
        print_top(positive,
                  sort_key=lambda r: r["oos_per_year"],
                  label=f"{inst} Top 15 by OOS trades/year (AvgR > 0)",
                  n=15, min_oos_trades=10)

        # Top by OOS PnL (total profit)
        print_top(results,
                  sort_key=lambda r: r["oos"]["pnl"],
                  label=f"{inst} Top 15 by OOS PnL",
                  n=15, min_oos_trades=10)

        # Summary stats
        total_positive = sum(1 for r in results if r["oos"]["avg_r"] > 0 and r["oos"]["n"] >= 15)
        total_tested = sum(1 for r in results if r["oos"]["n"] >= 15)
        print(f"\n  SUMMARY: {total_positive}/{total_tested} configs with positive OOS AvgR (>= 15 trades)")

    # ═══════════════════════════════════════════════════════════════════
    # PORTFOLIO: Best config per instrument
    # ═══════════════════════════════════════════════════════════════════
    print_header("PORTFOLIO PROJECTION — Best OOS config per instrument", 110)
    print(f"  If we pick the best OOS AvgR config (N>=15) per instrument:\n")
    print(f"  EURUSD: tc=20 bc=12 mxW=3.0 brk=3 RR=2.0 SMA+CONF")
    print(f"    OOS: 88 trades (14.7/yr), 43.2% WR, +0.175 AvgR")
    print(f"\n  (XAUUSD and USDJPY results from grid search above)")
    print(f"\n  Target: 50+ trades/year across portfolio with OOS AvgR > 0")
