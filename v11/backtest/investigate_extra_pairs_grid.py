"""Per-instrument grid search + SMA(50) on additional FX pairs.

Tests GBPUSD, AUDUSD, USDCAD, USDCHF with instrument-specific param optimization.
(NZDUSD excluded — lowest liquidity, worst default results.)

Each pair gets its own grid search to find Darvas params that work WITH the SMA filter.
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
from v11.config.strategy_config import StrategyConfig, EURUSD_CONFIG

IS_START, IS_END = datetime(2024, 1, 1), datetime(2026, 12, 31)
OOS_START, OOS_END = datetime(2018, 1, 1), datetime(2023, 12, 31)
OOS_YEARS = 6
IS_YEARS = 2

HTF_MIN = 60
SMA_PERIOD = 50

# Instrument-specific base configs (spread + tick size)
PAIR_CONFIGS = {
    "GBPUSD": replace(EURUSD_CONFIG, instrument="GBPUSD", spread_cost=0.00012, tick_size=0.00005),
    "AUDUSD": replace(EURUSD_CONFIG, instrument="AUDUSD", spread_cost=0.00010, tick_size=0.00005),
    "USDCAD": replace(EURUSD_CONFIG, instrument="USDCAD", spread_cost=0.00012, tick_size=0.00005),
    "USDCHF": replace(EURUSD_CONFIG, instrument="USDCHF", spread_cost=0.00012, tick_size=0.00005),
}

# Focused grid — based on what worked on EURUSD/USDJPY
PARAM_GRID = {
    "top_confirm_bars":     [15, 20, 25, 30],
    "bottom_confirm_bars":  [10, 12, 15, 20],
    "max_box_width_atr":    [2.0, 3.0, 4.0],
    "breakout_confirm_bars": [1, 2, 3],
}

RR_VALUES = [1.5, 2.0]


def build_sma_lookup(bars):
    htf_bars = resample_sessions(bars, HTF_MIN)
    sma_values = compute_sma(htf_bars, SMA_PERIOD)
    return build_htf_lookup(sma_values)


def make_sma_conf_filter(lookup):
    def f(t):
        if t["vol_class"] != "CONFIRMING":
            return False
        sma_val = get_htf_value_at(lookup, t["signal"].timestamp, HTF_MIN)
        if sma_val is None:
            return False
        price = t["signal"].breakout_price
        return (price > sma_val) if t["is_long"] else (price < sma_val)
    return f


def make_sma_all_filter(lookup):
    def f(t):
        sma_val = get_htf_value_at(lookup, t["signal"].timestamp, HTF_MIN)
        if sma_val is None:
            return False
        price = t["signal"].breakout_price
        return (price > sma_val) if t["is_long"] else (price < sma_val)
    return f


def filter_by_period(raw_trades, start, end):
    return [t for t in raw_trades if start <= t["signal"].timestamp <= end]


if __name__ == "__main__":
    keys = list(PARAM_GRID.keys())
    combos = list(product(*[PARAM_GRID[k] for k in keys]))

    for inst, base_cfg in PAIR_CONFIGS.items():
        print_header(f"{inst} -- PER-INSTRUMENT GRID SEARCH + SMA(50)", 110)

        print(f"Loading {inst}...")
        try:
            bars = load_instrument_bars(inst)
        except Exception as e:
            print(f"  ERROR loading {inst}: {e}")
            continue

        print(f"{inst}: {len(bars):,} bars ({bars[0].timestamp.date()} to {bars[-1].timestamp.date()})")

        lookup = build_sma_lookup(bars)
        total = len(combos)
        print(f"Grid: {total} param combos x 2 R:R x 2 vol = {total * 4}")

        all_results = []

        for combo_idx, combo in enumerate(combos):
            if (combo_idx + 1) % 50 == 0:
                print(f"  {combo_idx + 1}/{total}...")

            overrides = dict(zip(keys, combo))
            cfg = replace(base_cfg, **overrides)

            all_sigs = collect_signals(bars, cfg)
            is_sigs = filter_by_period(all_sigs, IS_START, IS_END)
            oos_sigs = filter_by_period(all_sigs, OOS_START, OOS_END)

            for rr in RR_VALUES:
                for vol_name, filt_fn in [("SMA+CONF", make_sma_conf_filter(lookup)),
                                           ("SMA+ALL", make_sma_all_filter(lookup))]:
                    is_trades = simulate_trades(is_sigs, rr, cfg, filter_fn=filt_fn)
                    oos_trades = simulate_trades(oos_sigs, rr, cfg, filter_fn=filt_fn)
                    is_s = compute_stats(is_trades)
                    oos_s = compute_stats(oos_trades)

                    all_results.append({
                        "params": overrides, "rr": rr, "vol": vol_name,
                        "is": is_s, "oos": oos_s,
                        "oos_per_year": oos_s["n"] / OOS_YEARS,
                    })

        # Print top results
        for sort_label, sort_fn, min_n in [
            ("Top 10 by OOS AvgR", lambda r: r["oos"]["avg_r"], 15),
            ("Top 10 by OOS trades/yr (AvgR>0)", lambda r: r["oos_per_year"], 10),
        ]:
            if sort_label.startswith("Top 10 by OOS trades"):
                filtered = [r for r in all_results if r["oos"]["avg_r"] > 0 and r["oos"]["n"] >= min_n]
            else:
                filtered = [r for r in all_results if r["oos"]["n"] >= min_n]
            filtered.sort(key=sort_fn, reverse=True)

            print(f"\n  --- {inst} {sort_label} (N >= {min_n}) ---")
            print(f"  {'#':>3s} {'tc':>3s} {'bc':>3s} {'mxW':>4s} {'brk':>3s} {'RR':>3s} {'Vol':>8s}"
                  f"  |  {'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}"
                  f"  |  {'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")
            print(f"  {'-' * 100}")

            for i, r in enumerate(filtered[:10]):
                p = r["params"]
                si = r["is"]
                so = r["oos"]
                is_str = (f"{si['n']:4d} {si['wr']:6.1f} {si['avg_r']:+7.3f}"
                          if si['n'] > 0 else "   0   ---     ---")
                flag = " *" if so['n'] < 15 else ""
                print(f"  {i+1:3d} {p['top_confirm_bars']:3d} {p['bottom_confirm_bars']:3d} "
                      f"{p['max_box_width_atr']:4.1f} {p['breakout_confirm_bars']:3d} "
                      f"{r['rr']:3.1f} {r['vol']:>8s}"
                      f"  |  {is_str}"
                      f"  |  {so['n']:5d} {r['oos_per_year']:5.1f} {so['wr']:7.1f} "
                      f"{so['avg_r']:+8.3f} {so['pnl']:+9.4f}{flag}")

        total_pos = sum(1 for r in all_results if r["oos"]["avg_r"] > 0 and r["oos"]["n"] >= 15)
        total_tested = sum(1 for r in all_results if r["oos"]["n"] >= 15)
        print(f"\n  SUMMARY: {total_pos}/{total_tested} configs with positive OOS AvgR (>= 15 trades)")
