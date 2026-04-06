"""Trade Frequency Investigation — Multiple angles to increase trade count.

Part A: Test 60-min SMA(50) filter on ALL 3 instruments (EURUSD, XAUUSD, USDJPY)
Part B: Loosen 1-min Darvas params with SMA as safety net (wider boxes, fewer confirm bars)
Part C: 5-min Darvas + SMA filter combination
Part D: All additional FX pairs (GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF)

Goal: Find a portfolio-level trade count of 50+ trades/year with positive OOS AvgR.
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
from v11.backtest.simulator import run_backtest
from v11.backtest.metrics import compute_metrics
from v11.config.strategy_config import (
    StrategyConfig, EURUSD_CONFIG, XAUUSD_CONFIG, USDJPY_CONFIG,
)

IS_START, IS_END = datetime(2024, 1, 1), datetime(2026, 12, 31)
OOS_START, OOS_END = datetime(2018, 1, 1), datetime(2023, 12, 31)

HTF_MIN = 60
SMA_PERIOD = 50
RR = 2.0


def build_sma_lookup(bars):
    """Build 60-min SMA(50) lookup from 1-min bars."""
    htf_bars = resample_sessions(bars, HTF_MIN)
    sma_values = compute_sma(htf_bars, SMA_PERIOD)
    return build_htf_lookup(sma_values)


def sma_filter(lookup):
    """Create a filter function that checks SMA alignment."""
    def f(t):
        if t["vol_class"] != "CONFIRMING":
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


def sma_filter_all_vol(lookup):
    """SMA filter without volume requirement."""
    def f(t):
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


def print_result_row(label, is_s, oos_s):
    is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f} "
              f"{is_s['pnl']:+8.4f}") if is_s['n'] > 0 else "   0   ---     ---      ---"
    oos_str = (f"{oos_s['n']:5d} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} "
               f"{oos_s['pnl']:+9.4f}") if oos_s['n'] > 0 else "    0    ---      ---       ---"
    flag = " *" if (is_s['n'] < 15 or oos_s['n'] < 15) else ""
    print(f"  {label:40s}  |  {is_str}  |  {oos_str}{flag}")


if __name__ == "__main__":

    # ═══════════════════════════════════════════════════════════════════
    # PART A: SMA filter on all 3 instruments
    # ═══════════════════════════════════════════════════════════════════
    print_header("PART A: 60-min SMA(50) + CONFIRMING on ALL INSTRUMENTS", 115)
    print(f"  {'Config':40s}  |  "
          f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s} {'IS_PnL':>8s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}")
    print(f"  {'-' * 110}")

    portfolio_is = []
    portfolio_oos = []

    instrument_configs = {
        "EURUSD": replace(EURUSD_CONFIG, top_confirm_bars=20, bottom_confirm_bars=12,
                          max_box_width_atr=3.0, breakout_confirm_bars=2),
        "XAUUSD": XAUUSD_CONFIG,
        "USDJPY": USDJPY_CONFIG,
    }

    for inst, cfg in instrument_configs.items():
        print(f"\n  Loading {inst}...")
        bars = load_instrument_bars(inst)
        print(f"  {inst}: {len(bars):,} bars")

        lookup = build_sma_lookup(bars)
        signals = collect_signals(bars, cfg)

        is_sigs = filter_by_period(signals, IS_START, IS_END)
        oos_sigs = filter_by_period(signals, OOS_START, OOS_END)

        # Baseline (CONFIRMING only, no SMA)
        conf_filter = lambda t: t["vol_class"] == "CONFIRMING"
        is_base = simulate_trades(is_sigs, RR, cfg, filter_fn=conf_filter)
        oos_base = simulate_trades(oos_sigs, RR, cfg, filter_fn=conf_filter)
        print_result_row(f"{inst} baseline (CONF only)", compute_stats(is_base), compute_stats(oos_base))

        # SMA + CONFIRMING
        filt = sma_filter(lookup)
        is_trades = simulate_trades(is_sigs, RR, cfg, filter_fn=filt)
        oos_trades = simulate_trades(oos_sigs, RR, cfg, filter_fn=filt)
        is_s = compute_stats(is_trades)
        oos_s = compute_stats(oos_trades)
        print_result_row(f"{inst} + SMA(50) + CONF", is_s, oos_s)
        portfolio_is.extend(is_trades)
        portfolio_oos.extend(oos_trades)

        # SMA + ALL volume (more trades?)
        filt_all = sma_filter_all_vol(lookup)
        is_trades_all = simulate_trades(is_sigs, RR, cfg, filter_fn=filt_all)
        oos_trades_all = simulate_trades(oos_sigs, RR, cfg, filter_fn=filt_all)
        print_result_row(f"{inst} + SMA(50) + ALL vol", compute_stats(is_trades_all), compute_stats(oos_trades_all))

    # Portfolio totals
    print(f"\n  {'-' * 110}")
    port_is_s = compute_stats(portfolio_is)
    port_oos_s = compute_stats(portfolio_oos)
    print_result_row("PORTFOLIO (3 inst, SMA+CONF)", port_is_s, port_oos_s)
    oos_years = 6
    is_years = 2
    print(f"\n  Portfolio trades/year: IS={port_is_s['n']/is_years:.0f}, OOS={port_oos_s['n']/oos_years:.1f}")

    # ═══════════════════════════════════════════════════════════════════
    # PART B: Loosen 1-min params with SMA safety net (EURUSD only)
    # ═══════════════════════════════════════════════════════════════════
    print_header("PART B: LOOSENED 1-min PARAMS + SMA(50) — EURUSD", 115)
    print(f"  Testing whether SMA allows wider boxes / fewer confirm bars without degrading OOS\n")

    eurusd_bars = load_instrument_bars("EURUSD")
    eurusd_lookup = build_sma_lookup(eurusd_bars)

    loosen_grid = {
        "top_confirm_bars":     [10, 15, 20],
        "bottom_confirm_bars":  [8, 10, 12, 15],
        "max_box_width_atr":    [3.0, 4.0, 5.0],
        "breakout_confirm_bars": [1, 2, 3],
    }

    keys = list(loosen_grid.keys())
    combos = list(product(*[loosen_grid[k] for k in keys]))
    print(f"  Grid: {len(combos)} combos")

    print(f"\n  {'tc':>3s} {'bc':>3s} {'mxW':>4s} {'brk':>3s}  |  "
          f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}  |  "
          f"{'OOS/yr':>6s}")
    print(f"  {'-' * 95}")

    best_oos = []

    for combo in combos:
        overrides = dict(zip(keys, combo))
        cfg = replace(EURUSD_CONFIG, **overrides)

        signals = collect_signals(eurusd_bars, cfg)
        is_sigs = filter_by_period(signals, IS_START, IS_END)
        oos_sigs = filter_by_period(signals, OOS_START, OOS_END)

        filt = sma_filter(eurusd_lookup)
        is_trades = simulate_trades(is_sigs, RR, cfg, filter_fn=filt)
        oos_trades = simulate_trades(oos_sigs, RR, cfg, filter_fn=filt)

        is_s = compute_stats(is_trades)
        oos_s = compute_stats(oos_trades)

        # Store for ranking
        best_oos.append({
            "params": overrides, "is": is_s, "oos": oos_s,
            "oos_per_year": oos_s['n'] / oos_years,
        })

        # Only print if meaningful OOS trades
        if oos_s['n'] >= 10:
            is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
                      if is_s['n'] > 0 else "   0   ---     ---")
            oos_str = (f"{oos_s['n']:5d} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} "
                       f"{oos_s['pnl']:+9.4f}")
            flag = " *" if oos_s['n'] < 15 else ""
            print(f"  {overrides['top_confirm_bars']:3d} {overrides['bottom_confirm_bars']:3d} "
                  f"{overrides['max_box_width_atr']:4.1f} {overrides['breakout_confirm_bars']:3d}  |  "
                  f"{is_str}  |  {oos_str}  |  {oos_s['n']/oos_years:6.1f}{flag}")

    # Top 10 by OOS trades/year with positive AvgR
    print(f"\n  --- Top 10 by OOS trades/year (AvgR > 0, N >= 15) ---")
    positive = [r for r in best_oos if r['oos']['avg_r'] > 0 and r['oos']['n'] >= 15]
    positive.sort(key=lambda r: r['oos_per_year'], reverse=True)
    for i, r in enumerate(positive[:10]):
        p = r['params']
        s = r['oos']
        print(f"  {i+1:2d}. tc={p['top_confirm_bars']:2d} bc={p['bottom_confirm_bars']:2d} "
              f"mxW={p['max_box_width_atr']:.1f} brk={p['breakout_confirm_bars']} "
              f"-> OOS: N={s['n']} ({r['oos_per_year']:.1f}/yr) "
              f"WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f} PnL={s['pnl']:+.4f}")

    # Top 10 by OOS AvgR with decent trade count
    print(f"\n  --- Top 10 by OOS AvgR (N >= 15) ---")
    decent = [r for r in best_oos if r['oos']['n'] >= 15]
    decent.sort(key=lambda r: r['oos']['avg_r'], reverse=True)
    for i, r in enumerate(decent[:10]):
        p = r['params']
        s = r['oos']
        print(f"  {i+1:2d}. tc={p['top_confirm_bars']:2d} bc={p['bottom_confirm_bars']:2d} "
              f"mxW={p['max_box_width_atr']:.1f} brk={p['breakout_confirm_bars']} "
              f"-> OOS: N={s['n']} ({r['oos_per_year']:.1f}/yr) "
              f"WR={s['wr']:.1f}% AvgR={s['avg_r']:+.3f} PnL={s['pnl']:+.4f}")

    # ═══════════════════════════════════════════════════════════════════
    # PART C: 5-min Darvas + SMA filter
    # ═══════════════════════════════════════════════════════════════════
    print_header("PART C: 5-MIN DARVAS + SMA(50) — EURUSD", 115)

    from v11.backtest.htf_utils import resample_sessions as rs

    eurusd_5m_all = rs(eurusd_bars, 5)
    eurusd_5m_is = [b for b in eurusd_5m_all if IS_START <= b.timestamp <= IS_END]
    eurusd_5m_oos = [b for b in eurusd_5m_all if OOS_START <= b.timestamp <= OOS_END]

    # Build SMA lookup on 60-min bars (same as before — works on resampled too)
    # But we need to build it from the 5-min bars for correct timestamps
    htf_from_5m = rs(eurusd_bars, HTF_MIN)  # 60-min from 1-min (same as before)
    sma_vals = compute_sma(htf_from_5m, SMA_PERIOD)
    lookup_5m = build_htf_lookup(sma_vals)

    # 5-min grid
    grid_5m = {
        "top_confirm_bars":     [8, 12, 15, 20],
        "bottom_confirm_bars":  [8, 12, 15, 20],
        "max_box_width_atr":    [2.0, 3.0, 5.0],
        "breakout_confirm_bars": [1, 2, 3],
    }
    RESCALE_5M = {"max_hold_bars": 24, "atr_period": 12, "min_box_duration": 4,
                  "min_box_width_atr": 0.3}

    keys_5m = list(grid_5m.keys())
    combos_5m = list(product(*[grid_5m[k] for k in keys_5m]))
    print(f"  Grid: {len(combos_5m)} combos on 5-min bars")
    print(f"  5-min IS bars: {len(eurusd_5m_is):,}, OOS bars: {len(eurusd_5m_oos):,}")

    results_5m = []

    for idx, combo in enumerate(combos_5m):
        if (idx + 1) % 50 == 0:
            print(f"    {idx+1}/{len(combos_5m)}...")
        overrides = dict(zip(keys_5m, combo))
        overrides.update(RESCALE_5M)
        cfg = replace(EURUSD_CONFIG, **overrides)

        # Collect signals on 5-min bars
        is_sigs = collect_signals(eurusd_5m_is, cfg)
        oos_sigs = collect_signals(eurusd_5m_oos, cfg)

        # SMA filter (same 60-min SMA lookup)
        def make_sma_filt(lk):
            def f(t):
                sma_val = get_htf_value_at(lk, t["signal"].timestamp, HTF_MIN)
                if sma_val is None:
                    return False
                price = t["signal"].breakout_price
                if t["is_long"]:
                    return price > sma_val
                else:
                    return price < sma_val
            return f

        filt = make_sma_filt(lookup_5m)

        is_trades = simulate_trades(is_sigs, RR, cfg, filter_fn=filt)
        oos_trades = simulate_trades(oos_sigs, RR, cfg, filter_fn=filt)

        # Also without SMA for comparison
        is_trades_nosma = simulate_trades(is_sigs, RR, cfg)
        oos_trades_nosma = simulate_trades(oos_sigs, RR, cfg)

        is_s = compute_stats(is_trades)
        oos_s = compute_stats(oos_trades)
        oos_nosma = compute_stats(oos_trades_nosma)

        results_5m.append({
            "params": overrides, "is": is_s, "oos": oos_s,
            "oos_nosma": oos_nosma,
            "oos_per_year": oos_s['n'] / oos_years,
        })

    # Top 10 by OOS trades/year with positive AvgR
    print(f"\n  --- Top 10 by OOS trades/year (AvgR > 0, N >= 10) + SMA filter ---")
    print(f"  {'#':>3s} {'tc':>3s} {'bc':>3s} {'mxW':>4s} {'brk':>3s} "
          f"{'OOS_N':>5s} {'/yr':>5s} {'WR%':>6s} {'AvgR':>7s} {'PnL':>8s}  |  "
          f"{'noSMA_N':>7s} {'noSMA_WR':>8s} {'noSMA_AvgR':>10s}")
    print(f"  {'-' * 95}")

    positive_5m = [r for r in results_5m if r['oos']['avg_r'] > 0 and r['oos']['n'] >= 10]
    positive_5m.sort(key=lambda r: r['oos_per_year'], reverse=True)
    for i, r in enumerate(positive_5m[:10]):
        p = r['params']
        s = r['oos']
        ns = r['oos_nosma']
        print(f"  {i+1:3d} {p['top_confirm_bars']:3d} {p['bottom_confirm_bars']:3d} "
              f"{p['max_box_width_atr']:4.1f} {p['breakout_confirm_bars']:3d} "
              f"{s['n']:5d} {r['oos_per_year']:5.1f} {s['wr']:6.1f} "
              f"{s['avg_r']:+7.3f} {s['pnl']:+8.4f}  |  "
              f"{ns['n']:7d} {ns['wr']:8.1f} {ns['avg_r']:+10.3f}")

    # Top 10 by OOS AvgR
    print(f"\n  --- Top 10 by OOS AvgR (N >= 10) + SMA filter ---")
    decent_5m = [r for r in results_5m if r['oos']['n'] >= 10]
    decent_5m.sort(key=lambda r: r['oos']['avg_r'], reverse=True)
    for i, r in enumerate(decent_5m[:10]):
        p = r['params']
        s = r['oos']
        ns = r['oos_nosma']
        print(f"  {i+1:3d} {p['top_confirm_bars']:3d} {p['bottom_confirm_bars']:3d} "
              f"{p['max_box_width_atr']:4.1f} {p['breakout_confirm_bars']:3d} "
              f"{s['n']:5d} {r['oos_per_year']:5.1f} {s['wr']:6.1f} "
              f"{s['avg_r']:+7.3f} {s['pnl']:+8.4f}  |  "
              f"{ns['n']:7d} {ns['wr']:8.1f} {ns['avg_r']:+10.3f}")

    # ═══════════════════════════════════════════════════════════════════
    # PART D: Additional FX pairs with SMA filter
    # ═══════════════════════════════════════════════════════════════════
    print_header("PART D: SMA(50) + DEFAULT PARAMS on ADDITIONAL FX PAIRS", 115)
    print(f"  Using default StrategyConfig params per instrument + SMA filter\n")

    extra_pairs = ["GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF"]

    print(f"  {'Instrument':12s}  |  "
          f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}  |  "
          f"{'OOS_N':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s} {'OOS_PnL':>9s}  |  "
          f"{'OOS/yr':>6s}")
    print(f"  {'-' * 90}")

    all_portfolio_is = list(portfolio_is)  # start with the 3 main instruments
    all_portfolio_oos = list(portfolio_oos)

    for inst in extra_pairs:
        try:
            bars = load_instrument_bars(inst)
        except Exception as e:
            print(f"  {inst:12s}  |  ERROR: {e}")
            continue

        # Use EURUSD config as base with instrument-specific spread
        # Approximate spreads for extra pairs
        spread_map = {
            "GBPUSD": 0.00012, "AUDUSD": 0.00010, "NZDUSD": 0.00015,
            "USDCAD": 0.00012, "USDCHF": 0.00012,
        }
        cfg = replace(EURUSD_CONFIG,
                      instrument=inst,
                      spread_cost=spread_map.get(inst, 0.00012))

        lookup = build_sma_lookup(bars)
        signals = collect_signals(bars, cfg)
        is_sigs = filter_by_period(signals, IS_START, IS_END)
        oos_sigs = filter_by_period(signals, OOS_START, OOS_END)

        filt = sma_filter(lookup)
        is_trades = simulate_trades(is_sigs, RR, cfg, filter_fn=filt)
        oos_trades = simulate_trades(oos_sigs, RR, cfg, filter_fn=filt)

        is_s = compute_stats(is_trades)
        oos_s = compute_stats(oos_trades)

        is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
                  if is_s['n'] > 0 else "   0   ---     ---")
        oos_str = (f"{oos_s['n']:5d} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f} "
                   f"{oos_s['pnl']:+9.4f}") if oos_s['n'] > 0 else "    0    ---      ---       ---"
        flag = " *" if oos_s['n'] < 15 else ""

        print(f"  {inst:12s}  |  {is_str}  |  {oos_str}  |  "
              f"{oos_s['n']/oos_years:6.1f}{flag}")

        all_portfolio_is.extend(is_trades)
        all_portfolio_oos.extend(oos_trades)

    # Full portfolio totals
    print(f"\n  {'-' * 90}")
    full_is = compute_stats(all_portfolio_is)
    full_oos = compute_stats(all_portfolio_oos)
    print(f"  {'FULL PORTFOLIO (8 pairs)':12s}  |  "
          f"{full_is['n']:4d} {full_is['wr']:6.1f} {full_is['avg_r']:+7.3f}  |  "
          f"{full_oos['n']:5d} {full_oos['wr']:7.1f} {full_oos['avg_r']:+8.3f} "
          f"{full_oos['pnl']:+9.4f}  |  {full_oos['n']/oos_years:6.1f}")

    print(f"\n  Portfolio trades/year: IS={full_is['n']/is_years:.0f}, "
          f"OOS={full_oos['n']/oos_years:.1f}")
