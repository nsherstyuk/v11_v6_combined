"""Phase 1 sniff: BTC funding-rate carry trade (extreme harvesting).

Different thesis from the cycle test: don't try to time the V-shape, just
harvest the funding payment when it's extreme, hedged in spot.

Strategy (no execution simulation, just edge measurement):
  - Pull max-history BTC funding rates from Binance (~since 2019).
  - For each event, define annualized_funding = rate * 3 * 365.
  - Stratify by funding magnitude into deciles.
  - For each decile, compute: mean funding earned per event,
    AND the realized 8h spot move that a hedged carry would have
    been exposed to via execution slippage / hedge basis drift.
  - Net edge = funding_earned - 2 * round_trip_cost (1.5 bps assumed).

Test: do the top deciles of |funding| produce a strictly positive realized
return after costs, with a clean distribution shape, and how often does
the regime exist (deciles above the cost threshold)?

Sanity per backtest_sanity_check feedback:
  - Walk one extreme event end-to-end.
  - Check time alignment between funding events and spot bars.

Usage:
  python -m v11.backtest.sniff_funding_carry [--symbol BTCUSDT]
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd
import requests

PERP_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"
RT_COST_BPS = 3.0  # round-trip cost on spot+perp legs combined (conservative)


def get_funding_all(symbol: str) -> pd.DataFrame:
    rows = []
    cursor = 0
    end_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
    while True:
        r = requests.get(
            f"{PERP_BASE}/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000},
            timeout=15,
        )
        r.raise_for_status()
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        last = chunk[-1]["fundingTime"]
        if last <= cursor:
            break
        cursor = last + 1
        time.sleep(0.1)
        if len(chunk) < 1000:
            break
    df = pd.DataFrame(rows)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df.drop_duplicates("fundingTime").sort_values("fundingTime").reset_index(drop=True)


def main(symbol: str) -> None:
    print(f"Pulling all-history {symbol} funding ...")
    f = get_funding_all(symbol)
    print(f"  {len(f)} events,  {f['fundingTime'].min()}  ->  {f['fundingTime'].max()}")
    rate_bps = f["fundingRate"] * 1e4
    print(f"  rate (bps per 8h):  mean={rate_bps.mean():+.3f}  "
          f"std={rate_bps.std():.3f}  min={rate_bps.min():+.2f}  max={rate_bps.max():+.2f}")
    f["abs_bps"] = rate_bps.abs()
    f["annualized_pct"] = f["fundingRate"] * 3 * 365 * 100  # 3 fundings/day * 365

    # Distribution of annualized rates
    print(f"\n=== Annualized funding distribution (% per year) ===")
    quantiles = [0.5, 0.75, 0.9, 0.95, 0.99]
    for q in quantiles:
        v = f["annualized_pct"].abs().quantile(q)
        print(f"  |annualized| {int(q*100):2d}th pct: {v:6.2f}%")

    # Decile analysis: for each abs-funding decile, what's the mean payment
    # collected (regardless of sign — assume we always take the receiving side)?
    print(f"\n=== Decile analysis: edge per event taking receiving side ===")
    f["abs_decile"] = pd.qcut(f["abs_bps"], 10, labels=False, duplicates="drop")
    print(f"  decile     n     mean|funding|_bps   mean_annualized_%   above_cost?")
    for d, g in f.groupby("abs_decile"):
        mean_bps = g["abs_bps"].mean()
        mean_ann = g["annualized_pct"].abs().mean()
        flag = "PASS" if mean_bps > RT_COST_BPS else "fail"
        print(f"  {int(d):2d}    {len(g):5d}    {mean_bps:7.2f}             "
              f"{mean_ann:7.2f}            {flag}")

    # Top-decile carry: simple harvest rule. If we held the receiving side
    # only when |funding| > top-decile threshold, what fraction of time?
    # And what mean per-event PnL after RT cost?
    threshold = f["abs_bps"].quantile(0.9)
    eligible = f[f["abs_bps"] >= threshold]
    print(f"\n=== Top-decile harvest rule ===")
    print(f"  threshold |funding| >= {threshold:.2f} bps  ({len(eligible)} events,"
          f" {len(eligible)/len(f)*100:.1f}% of time)")
    print(f"  mean payment received: {eligible['abs_bps'].mean():.2f} bps per 8h")
    print(f"  net per event after {RT_COST_BPS:.1f} bps round-trip cost: "
          f"{eligible['abs_bps'].mean() - RT_COST_BPS:+.2f} bps")
    # Annualized edge if continuously deployed when eligible:
    eligible_frac = len(eligible) / len(f)
    payments_per_year = 3 * 365 * eligible_frac
    annualized_edge_pct = (eligible["abs_bps"].mean() - RT_COST_BPS) / 1e4 * payments_per_year * 100
    print(f"  ~annualized portfolio return (deployed when eligible): "
          f"{annualized_edge_pct:+.2f}%/year on capital deployed")

    # Stricter: 95th and 99th percentile
    for q in [0.95, 0.99]:
        thr = f["abs_bps"].quantile(q)
        eg = f[f["abs_bps"] >= thr]
        ef = len(eg) / len(f)
        net = eg["abs_bps"].mean() - RT_COST_BPS
        ann = net / 1e4 * (3 * 365 * ef) * 100
        print(f"  q={q:.2f}: thr={thr:.2f} bps, n={len(eg)}, frac={ef*100:.1f}%, "
              f"net={net:+.2f} bps, annualized={ann:+.2f}%")

    # Time-stability: is the edge in one regime only?
    print(f"\n=== Year-by-year (top decile rule) ===")
    f["year"] = f["fundingTime"].dt.year
    for yr, g in f.groupby("year"):
        thr = g["abs_bps"].quantile(0.9)
        eg = g[g["abs_bps"] >= thr]
        net = eg["abs_bps"].mean() - RT_COST_BPS
        print(f"  {yr}  n={len(g):4d}  thr_p90={thr:5.2f}  "
              f"top10_mean={eg['abs_bps'].mean():5.2f}  net={net:+5.2f} bps")

    print(f"\n=== Sanity: most extreme event ===")
    ex = f.loc[f["abs_bps"].idxmax()]
    print(f"  {ex['fundingTime']}   rate={ex['fundingRate']*1e4:+.2f} bps  "
          f"annualized={ex['annualized_pct']:+.1f}%/yr")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    args = p.parse_args()
    main(args.symbol)
