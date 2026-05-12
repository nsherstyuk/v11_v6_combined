"""ORB year-by-year breakdown for the US-equities breakout grid.

Companion to `orb_us_equities_grid.py`. Same data, same logic, same costs.
Difference: bucket trades by calendar year instead of IS/OOS split.

Question the breakdown is designed to answer:
  Is the OOS edge on META / AMZN / TSLA structural, or is it concentrated
  in the 2020-2021 COVID/AI-era momentum regime?

If the per-year AvgR_slip is consistently positive across multiple
non-COVID years (2018, 2019, 2022, 2023, 2024, 2025), the edge is
plausibly structural. If it's heavily concentrated in 2020-2021 and
flat-to-negative elsewhere, it's a regime artifact and not tradeable
forward.

Run:
    python -m v11.backtest.orb_us_equities_year_breakdown
"""
from __future__ import annotations

import pandas as pd

from v11.backtest.orb_us_equities_grid import (
    UNIVERSE, COST_PER_SHARE, run_one, metrics,
)


SURVIVORS = ["META", "AMZN", "TSLA"]
ETFS = ["SPY", "QQQ", "IWM", "DIA"]


def by_year(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-year metrics for one ticker's trade set."""
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["year"] = pd.to_datetime(t["date"]).dt.year
    rows = []
    for y in sorted(t["year"].unique()):
        sub = t[t["year"] == y]
        m = metrics(sub)
        ms = metrics(sub, slip=True)
        rows.append({
            "year": int(y),
            "N": m["N"],
            "WR": m["WR"],
            "AvgR_raw": m["AvgR"],
            "AvgR_slip": ms["AvgR"],
            "PF": m["PF"],
        })
    return pd.DataFrame(rows)


def print_table(sym: str, byr: pd.DataFrame):
    print(f"\n  {sym}")
    print(f"  {'Year':<6} {'N':>5} {'WR%':>6} {'AvgR_raw':>9} {'AvgR_slip':>10} {'PF':>6}")
    print(f"  {'-'*5:<6} {'-'*5:>5} {'-'*5:>6} {'-'*8:>9} {'-'*9:>10} {'-'*5:>6}")
    for _, row in byr.iterrows():
        pf = f"{row['PF']:.2f}" if row['PF'] != float('inf') else " inf"
        marker = "  <-- positive after costs" if row['AvgR_slip'] >= 0.05 else ""
        print(f"  {int(row['year']):<6} {int(row['N']):>5} {row['WR']:>6.1f} "
              f"{row['AvgR_raw']:>+9.3f} {row['AvgR_slip']:>+10.3f} {pf:>6}{marker}")


def main():
    print("=" * 100)
    print("  ORB-BREAKOUT year-by-year breakdown — US EQUITIES")
    print(f"  Cost model: ${COST_PER_SHARE}/share round-trip applied to AvgR_slip column")
    print(f"  Hypothesis under test: META/AMZN/TSLA OOS edge — regime artifact or structural?")
    print("=" * 100)

    print("\n=== SURVIVORS from the IS/OOS grid (gated on OOS AvgR_slip ≥ +0.05) ===")
    survivor_trades = {}
    for sym in SURVIVORS:
        trades = run_one(sym)
        if trades.empty:
            print(f"  {sym}: no trades")
            continue
        survivor_trades[sym] = trades
        byr = by_year(trades)
        print_table(sym, byr)

    print("\n\n=== ETF BASELINES (none passed the gate; included for context) ===")
    for sym in ETFS:
        trades = run_one(sym)
        if trades.empty:
            continue
        byr = by_year(trades)
        print_table(sym, byr)

    print("\n\n=== AGGREGATE — all 15 tickers pooled, by year ===")
    all_trades = []
    for sym in UNIVERSE:
        trades = run_one(sym)
        if not trades.empty:
            all_trades.append(trades)
    if all_trades:
        pooled = pd.concat(all_trades, ignore_index=True)
        byr = by_year(pooled)
        print_table("ALL-15-POOLED", byr)

    print("\n\n=== SURVIVORS AGGREGATED (META + AMZN + TSLA pooled, by year) ===")
    if survivor_trades:
        pooled = pd.concat(survivor_trades.values(), ignore_index=True)
        byr = by_year(pooled)
        print_table("META+AMZN+TSLA", byr)

    print("\nDone.")


if __name__ == "__main__":
    main()
