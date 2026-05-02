"""Phase 1 sniff: BTC/ETH perpetual funding-rate cycle.

Hypothesis: Binance perp funding pays every 8h (00/08/16 UTC). Basis (perp - spot)
should systematically rise into a positive-funding event and snap back after,
producing a periodic, predictive intraday cycle.

What we test (read-only, public Binance endpoints, no API key):
  1. Sample mean basis path in the 8 hours surrounding each funding event,
     stratified by sign(funding_rate). Should look V-shaped for positive funding.
  2. corr(basis at T-1h before funding, basis change [T-1h -> T+1h]).
     Negative correlation => snapback is predictive.
  3. Sign-test: when funding > 0, fraction of cycles where
     basis(T+1h) < basis(T-1h). Should be > 55% if cycle is tradable.
  4. Mean reversion magnitude vs the funding payment itself
     (does the snapback exceed the cost of crossing spread twice?).

Sanity (per backtest_sanity_check feedback):
  - Verify event timestamps cluster on 00/08/16 UTC.
  - Print one cycle of raw data so eyeballs can confirm the V-shape isn't an artifact.

Usage:
  python -m v11.backtest.sniff_perp_funding_cycle [--symbol BTCUSDT] [--days 90]
"""
from __future__ import annotations

import argparse
import time
from typing import Iterable

import numpy as np
import pandas as pd
import requests

PERP_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"


def get_funding(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
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
        last_t = chunk[-1]["fundingTime"]
        if last_t <= cursor:
            break
        cursor = last_t + 1
        time.sleep(0.1)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df.drop_duplicates("fundingTime").sort_values("fundingTime").reset_index(drop=True)


def get_klines(base: str, path: str, symbol: str, interval: str,
               start_ms: int, end_ms: int) -> pd.DataFrame:
    rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        r = requests.get(
            f"{base}{path}",
            params={"symbol": symbol, "interval": interval,
                    "startTime": cursor, "endTime": end_ms, "limit": 1000},
            timeout=15,
        )
        r.raise_for_status()
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        last_open = chunk[-1][0]
        if last_open <= cursor:
            break
        cursor = last_open + 1
        time.sleep(0.1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "tb_base", "tb_quote", "ignore",
    ])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    return df[["ts", "open", "high", "low", "close"]].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def event_study(basis: pd.DataFrame, events: pd.DataFrame,
                hours_before: int = 4, hours_after: int = 4) -> pd.DataFrame:
    """For each funding event, snapshot basis at T-h .. T+h (1h-bar resolution)."""
    rows = []
    basis_idx = basis.set_index("ts")["basis_bps"]
    for _, ev in events.iterrows():
        t = ev["fundingTime"].floor("h")
        offsets = list(range(-hours_before, hours_after + 1))
        snap = {}
        for off in offsets:
            ts = t + pd.Timedelta(hours=off)
            if ts in basis_idx.index:
                snap[off] = basis_idx.loc[ts]
            else:
                snap[off] = np.nan
        snap["funding_bps"] = ev["fundingRate"] * 1e4
        snap["t"] = t
        rows.append(snap)
    return pd.DataFrame(rows)


def main(symbol: str, days: int) -> None:
    end_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    start_ms = end_ms - days * 86_400_000

    print(f"Pulling {symbol} funding history, {days} days ...")
    fund = get_funding(symbol, start_ms, end_ms)
    print(f"  {len(fund)} funding events")
    if fund.empty:
        return
    print(f"  span: {fund['fundingTime'].min()} -> {fund['fundingTime'].max()}")
    print(f"  funding rate stats (bps): "
          f"mean={fund['fundingRate'].mean()*1e4:+.2f}  "
          f"std={fund['fundingRate'].std()*1e4:.2f}  "
          f"frac>0={(fund['fundingRate']>0).mean()*100:.1f}%")
    hours = fund["fundingTime"].dt.hour.value_counts().sort_index()
    print(f"  funding-time UTC hour bins: {hours.to_dict()}  (expect {{0, 8, 16}})")

    print(f"\nPulling {symbol} perp 1h klines ...")
    perp = get_klines(PERP_BASE, "/fapi/v1/klines", symbol, "1h", start_ms, end_ms)
    print(f"  {len(perp)} bars")

    print(f"Pulling {symbol} spot 1h klines ...")
    spot = get_klines(SPOT_BASE, "/api/v3/klines", symbol, "1h", start_ms, end_ms)
    print(f"  {len(spot)} bars")

    merged = perp.merge(spot[["ts", "close"]].rename(columns={"close": "spot_close"}),
                        on="ts", how="inner")
    merged["basis"] = merged["close"] - merged["spot_close"]
    merged["basis_bps"] = (merged["basis"] / merged["spot_close"]) * 1e4
    print(f"\nMerged perp/spot bars: {len(merged)}")
    print(f"  basis_bps stats: mean={merged['basis_bps'].mean():+.2f}  "
          f"std={merged['basis_bps'].std():.2f}")

    es = event_study(merged, fund)
    pos = es[es["funding_bps"] > 0]
    neg = es[es["funding_bps"] < 0]

    print(f"\n=== Test 1. Mean basis path around funding event (bps from spot) ===")
    print(f"   offset_h     pos_funding (n={len(pos)})    neg_funding (n={len(neg)})")
    for off in range(-4, 5):
        pm = pos[off].mean() if off in pos.columns else float("nan")
        nm = neg[off].mean() if off in neg.columns else float("nan")
        marker = "  <-- funding" if off == 0 else ""
        print(f"   T{off:+d}h          {pm:+7.2f}                  {nm:+7.2f}{marker}")

    print(f"\n=== Test 2. Predictive correlation ===")
    es["pre"] = es[-1]
    es["post"] = es[1]
    es["snap"] = es["post"] - es["pre"]
    valid = es.dropna(subset=["pre", "snap"])
    corr_all = valid["pre"].corr(valid["snap"])
    print(f"   corr(basis_T-1h, basis_T+1h - basis_T-1h)  all events: "
          f"{corr_all:+.4f}  (n={len(valid)})")
    pv = valid[valid["funding_bps"] > 0]
    nv = valid[valid["funding_bps"] < 0]
    print(f"     subset funding > 0: {pv['pre'].corr(pv['snap']):+.4f}  (n={len(pv)})")
    print(f"     subset funding < 0: {nv['pre'].corr(nv['snap']):+.4f}  (n={len(nv)})")
    print(f"   (negative => higher pre-basis predicts a snap-down. Need < -0.20)")

    print(f"\n=== Test 3. Sign-test snapback ===")
    valid["snapped_toward_zero"] = (
        ((valid["pre"] > 0) & (valid["snap"] < 0)) |
        ((valid["pre"] < 0) & (valid["snap"] > 0))
    )
    rate = valid["snapped_toward_zero"].mean() * 100
    print(f"   % of events where basis snapped toward zero: {rate:.1f}%  (need > 55%)")

    print(f"\n=== Test 4. Snapback magnitude vs funding payment ===")
    valid["abs_pre"] = valid["pre"].abs()
    valid["toward_mag"] = -np.sign(valid["pre"]) * valid["snap"]
    print(f"   mean snapback toward zero: {valid['toward_mag'].mean():+.2f} bps")
    print(f"   mean |funding payment|:    {valid['funding_bps'].abs().mean():.2f} bps")
    print(f"   (cash-and-carry edge ~ snapback - 2*spread_bps, est spread ~1-2 bps)")

    print(f"\n=== Sanity: one strong-funding cycle (raw bars) ===")
    if len(pv) > 0:
        ex = pv.sort_values("funding_bps", ascending=False).iloc[0]
        t0 = ex["t"]
        win = merged[(merged["ts"] >= t0 - pd.Timedelta(hours=4)) &
                     (merged["ts"] <= t0 + pd.Timedelta(hours=4))]
        print(f"   funding event: {t0}  rate={ex['funding_bps']:+.2f} bps")
        print(win[["ts", "close", "spot_close", "basis_bps"]].to_string(index=False))

    print(f"\n=== Verdict gate ===")
    print(f"   [{'PASS' if corr_all < -0.20 else 'FAIL'}]  predictive corr < -0.20  ({corr_all:+.4f})")
    print(f"   [{'PASS' if rate > 55 else 'FAIL'}]  snapback rate > 55%      ({rate:.1f}%)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--days", type=int, default=90)
    args = p.parse_args()
    main(args.symbol, args.days)
