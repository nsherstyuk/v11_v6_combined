"""4H Level Retest strategy on ALL available FX pairs.

Tests the best config from deep dive (retest pb=10-30, SMA+CONF, RR=2.0)
plus a few variants on each instrument.

Also tests the direct mode for comparison.
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from datetime import datetime

from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.htf_utils import (
    resample_sessions, compute_sma, build_htf_lookup,
    compute_stats, print_header,
)
from v11.backtest.investigate_htf_levels import build_htf_level_timeline
from v11.backtest.investigate_4h_levels_deep import scan_4h_levels, simulate, filter_period

IS_START, IS_END = datetime(2024, 1, 1), datetime(2026, 12, 31)
OOS_START, OOS_END = datetime(2018, 1, 1), datetime(2023, 12, 31)
OOS_YEARS = 6
IS_YEARS = 2

INSTRUMENTS = ["EURUSD", "XAUUSD", "USDJPY", "GBPUSD", "AUDUSD", "USDCAD", "USDCHF"]

# Instrument-specific spread costs
SPREAD_MAP = {
    "EURUSD": 0.00010, "GBPUSD": 0.00012, "AUDUSD": 0.00010,
    "USDCAD": 0.00012, "USDCHF": 0.00012, "USDJPY": 0.010,
    "XAUUSD": 0.30,
}

# Configs to test per instrument
CONFIGS = [
    ("Direct SMA+CONF RR=2.0",      {"retest_mode": False, "vol_filter": "CONFIRMING"}, 2.0),
    ("Direct SMA+ALL RR=2.0",       {"retest_mode": False, "vol_filter": "ALL"}, 2.0),
    ("Retest pb=10-30 SMA+CONF 2.0", {"retest_mode": True, "min_pullback_bars": 10, "max_pullback_bars": 30, "vol_filter": "CONFIRMING"}, 2.0),
    ("Retest pb=5-30 SMA+CONF 2.0",  {"retest_mode": True, "min_pullback_bars": 5, "max_pullback_bars": 30, "vol_filter": "CONFIRMING"}, 2.0),
    ("Retest pb=5-60 SMA+CONF 2.0",  {"retest_mode": True, "min_pullback_bars": 5, "max_pullback_bars": 60, "vol_filter": "CONFIRMING"}, 2.0),
    ("Retest pb=10-30 SMA+CONF 1.5", {"retest_mode": True, "min_pullback_bars": 10, "max_pullback_bars": 30, "vol_filter": "CONFIRMING"}, 1.5),
]

# Override MAX_HOLD and SPREAD per instrument in the scan function
# We need to monkey-patch the module-level constants
import v11.backtest.investigate_4h_levels_deep as deep_mod


def run_instrument(inst):
    print(f"\n  Loading {inst}...")
    try:
        bars = load_instrument_bars(inst)
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

    print(f"  {len(bars):,} bars ({bars[0].timestamp.date()} to {bars[-1].timestamp.date()})")

    # Set instrument-specific spread
    deep_mod.SPREAD = SPREAD_MAP.get(inst, 0.00012)

    # Build 4H timeline
    # Use instrument-appropriate merge distance
    if inst == "XAUUSD":
        merge = 0.50  # $0.50 for gold
    elif inst == "USDJPY":
        merge = 0.005  # 0.5 pips for JPY pairs
    else:
        merge = 0.0005  # 5 pips for most FX

    timeline = build_htf_level_timeline(bars, 240, 10, 10, 72, merge)

    # Build SMA lookup
    htf_60 = resample_sessions(bars, 60)
    sma_vals = compute_sma(htf_60, 50)
    sma_lookup = build_htf_lookup(sma_vals)

    results = []
    for label, kwargs, rr in CONFIGS:
        raw = scan_4h_levels(bars, timeline, sma_lookup, cooldown_bars=60, **kwargs)
        is_r = filter_period(raw, IS_START, IS_END)
        oos_r = filter_period(raw, OOS_START, OOS_END)
        is_trades = simulate(is_r, rr)
        oos_trades = simulate(oos_r, rr)
        is_s = compute_stats(is_trades)
        oos_s = compute_stats(oos_trades)
        results.append({
            "label": label, "is": is_s, "oos": oos_s,
            "oos_per_year": oos_s["n"] / OOS_YEARS,
        })
    return results


if __name__ == "__main__":
    print_header("4H LEVEL RETEST — ALL INSTRUMENTS", 120)

    all_instrument_results = {}

    for inst in INSTRUMENTS:
        print_header(f"{inst}", 120)
        print(f"  {'Config':40s}  |  "
              f"{'IS_N':>4s} {'IS_WR':>6s} {'IS_AvgR':>7s}  |  "
              f"{'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s}")
        print(f"  {'-' * 95}")

        results = run_instrument(inst)
        if results is None:
            continue

        all_instrument_results[inst] = results

        for r in results:
            is_s = r["is"]
            oos_s = r["oos"]
            is_str = (f"{is_s['n']:4d} {is_s['wr']:6.1f} {is_s['avg_r']:+7.3f}"
                      if is_s['n'] > 0 else "   0   ---     ---")
            oos_str = (f"{oos_s['n']:5d} {r['oos_per_year']:5.1f} {oos_s['wr']:7.1f} {oos_s['avg_r']:+8.3f}"
                       if oos_s['n'] > 0 else "    0   ---    ---      ---")
            flag = " *" if oos_s['n'] < 15 else ""
            print(f"  {r['label']:40s}  |  {is_str}  |  {oos_str}{flag}")

    # ═══════════════════════════════════════════════════════════════════
    # SUMMARY: Best retest config per instrument
    # ═══════════════════════════════════════════════════════════════════
    print_header("SUMMARY: BEST RETEST CONFIG PER INSTRUMENT", 120)
    print(f"  {'Instrument':10s} {'Config':40s}  |  "
          f"{'OOS_N':>5s} {'/yr':>5s} {'OOS_WR':>7s} {'OOS_AvgR':>8s}  |  "
          f"{'IS_N':>4s} {'IS_AvgR':>7s}  |  {'Viable':>6s}")
    print(f"  {'-' * 110}")

    portfolio_retest_trades = 0
    portfolio_retest_r = 0.0

    for inst, results in all_instrument_results.items():
        # Find best retest config by OOS AvgR (with >= 10 trades)
        retest_results = [r for r in results if "Retest" in r["label"] and r["oos"]["n"] >= 10]
        if retest_results:
            best = max(retest_results, key=lambda r: r["oos"]["avg_r"])
        else:
            # Fallback to direct
            best = max(results, key=lambda r: r["oos"]["avg_r"])

        s_oos = best["oos"]
        s_is = best["is"]
        viable = "YES" if s_oos["avg_r"] > 0 and s_oos["n"] >= 15 and s_is["avg_r"] > -0.1 else "maybe" if s_oos["avg_r"] > 0 and s_oos["n"] >= 10 else "NO"

        print(f"  {inst:10s} {best['label']:40s}  |  "
              f"{s_oos['n']:5d} {best['oos_per_year']:5.1f} {s_oos['wr']:7.1f} {s_oos['avg_r']:+8.3f}  |  "
              f"{s_is['n']:4d} {s_is['avg_r']:+7.3f}  |  {viable:>6s}")

        if viable == "YES":
            portfolio_retest_trades += best["oos_per_year"]
            portfolio_retest_r += best["oos_per_year"] * s_oos["avg_r"]

    print(f"\n  Portfolio (viable instruments only):")
    print(f"    Trades/year: {portfolio_retest_trades:.1f}")
    if portfolio_retest_trades > 0:
        print(f"    Avg AvgR: {portfolio_retest_r / portfolio_retest_trades:+.3f}")
        print(f"    Total R/year: {portfolio_retest_r:.1f}")
