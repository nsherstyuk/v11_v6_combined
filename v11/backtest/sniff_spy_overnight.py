"""Phase 1 sniff: SPY overnight vs intraday drift.

Hypothesis (Bouchaud, Lou, Polk, Skouras 2019): essentially all of S&P's
positive return comes from the overnight session (close -> next open).
Intraday (open -> close) is roughly flat or negative.

Test:
  - Pull SPY daily OHLC from Stooq (free, no key, ~1993+).
  - overnight_ret_t = open_t / close_{t-1} - 1
  - intraday_ret_t = close_t / open_t - 1
  - Compare means, t-stats, cumulative wealth curves.
  - Stratify by year, by day-of-week.

Sanity per backtest_sanity_check feedback:
  - Walk one row of raw OHLC.
  - Verify ts ordering and that overnight_ret + intraday_ret approximates
    close-to-close return.

Usage:
  python -m v11.backtest.sniff_spy_overnight
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import requests


def main() -> None:
    print(f"Pulling SPY daily from Yahoo Finance chart API ...")
    r = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/SPY",
        params={"period1": 0, "period2": 2000000000, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "date": pd.to_datetime(res["timestamp"], unit="s"),
        "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
    }).dropna(subset=["open", "close"]).reset_index(drop=True)
    df["date"] = df["date"].dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    print(f"  {len(df)} bars  {df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()}")

    df["prev_close"] = df["close"].shift(1)
    df["overnight"] = df["open"] / df["prev_close"] - 1
    df["intraday"] = df["close"] / df["open"] - 1
    df["c2c"] = df["close"] / df["prev_close"] - 1
    df = df.dropna()
    df["year"] = df["date"].dt.year
    df["dow"] = df["date"].dt.dayofweek

    def stats(s: pd.Series, label: str, ann_factor: float = 252) -> None:
        n = len(s)
        m = s.mean()
        sd = s.std(ddof=1)
        t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
        ann_ret = (1 + m) ** ann_factor - 1
        ann_vol = sd * np.sqrt(ann_factor)
        sharpe = ann_ret / ann_vol if ann_vol > 0 else float("nan")
        wr = (s > 0).mean() * 100
        print(f"  {label:12s} n={n:5d}  "
              f"mean={m*1e4:+6.2f} bps/day  "
              f"t={t:+5.2f}  "
              f"ann_ret={ann_ret*100:+5.1f}%  "
              f"ann_vol={ann_vol*100:5.1f}%  "
              f"sharpe={sharpe:+.2f}  "
              f"wr={wr:.1f}%")

    print(f"\n=== Headline ===")
    stats(df["overnight"], "Overnight")
    stats(df["intraday"], "Intraday")
    stats(df["c2c"], "Close-Close")

    print(f"\n=== Diff: overnight - intraday ===")
    diff = df["overnight"] - df["intraday"]
    sd = diff.std(ddof=1)
    t = diff.mean() / (sd / np.sqrt(len(diff)))
    print(f"  mean={diff.mean()*1e4:+.2f} bps/day  t={t:+.2f}  (paired)")

    print(f"\n=== By year ===")
    print(f"  year    n     overnight (bps)   intraday (bps)   diff (bps)")
    for y, g in df.groupby("year"):
        on = g["overnight"].mean() * 1e4
        idy = g["intraday"].mean() * 1e4
        print(f"  {int(y):4d}  {len(g):4d}     {on:+7.2f}            {idy:+7.2f}          {on-idy:+7.2f}")

    print(f"\n=== By day-of-week ===")
    dow_n = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    print(f"  dow      n      overnight (bps)   intraday (bps)")
    for d, g in df.groupby("dow"):
        if d > 4:
            continue
        on = g["overnight"].mean() * 1e4
        idy = g["intraday"].mean() * 1e4
        print(f"  {dow_n[d]}    {len(g):5d}     {on:+7.2f}           {idy:+7.2f}")

    print(f"\n=== Cumulative wealth (log of final / initial, %) ===")
    on_curve = (1 + df["overnight"]).cumprod()
    intra_curve = (1 + df["intraday"]).cumprod()
    bnh_curve = (1 + df["c2c"]).cumprod()
    print(f"  Overnight-only buy/sell:   {(on_curve.iloc[-1]-1)*100:+.1f}%")
    print(f"  Intraday-only:             {(intra_curve.iloc[-1]-1)*100:+.1f}%")
    print(f"  Buy-and-hold (close-close): {(bnh_curve.iloc[-1]-1)*100:+.1f}%")

    print(f"\n=== OOS / IS split (IS = last 5 years) ===")
    cutoff = df["date"].max() - pd.DateOffset(years=5)
    is_ = df[df["date"] >= cutoff]
    oos = df[df["date"] < cutoff]
    stats(oos["overnight"], "OOS o-night")
    stats(is_["overnight"], "IS  o-night")
    stats(oos["intraday"], "OOS intraday")
    stats(is_["intraday"], "IS  intraday")

    print(f"\n=== Sanity: latest 3 rows ===")
    print(df[["date", "open", "close", "prev_close",
             "overnight", "intraday", "c2c"]].tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
