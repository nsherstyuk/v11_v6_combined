"""V2 (Mon->Tue overnight) OOS/IS confirmation on SPY and QQQ.

Phase 3 reported V1 and V4 OOS/IS splits but not V2. The gating question:
  - Does the Mon->Tue Sharpe ~1.5 hold in 2021-2026 specifically?
  - Or has it weakened the same way the all-days baseline has?

Splits used:
  - OOS: 1993-01 (or QQQ inception 1999) to 2021-04-30 (~5 years before today)
  - IS:  last 5 calendar years (2021-04-30 -> 2026-04-29)
  - Plus rolling 5y Sharpe of V2 to see how the recent half-decade ranks.

No filters beyond exit_dow == 1 (Tue open). 1 bp round-trip cost.

Sanity per backtest_sanity_check feedback:
  - Total trade count must equal V1 SPY 1719, V1 QQQ 1401 (sum of OOS + IS).
  - OOS + IS cumulative wealth must compose back to V2-full cumulative wealth.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import requests


def yahoo_daily(symbol: str) -> pd.DataFrame:
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"period1": 0, "period2": 2_000_000_000, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=20,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "date": pd.to_datetime(res["timestamp"], unit="s").normalize(),
        "open": q["open"], "close": q["close"],
    }).dropna().reset_index(drop=True)
    return df.sort_values("date").reset_index(drop=True)


def stats(rets: pd.Series, label: str) -> str:
    rets = rets.dropna()
    n = len(rets)
    if n < 2:
        return f"  {label:24s}  (n<2)"
    m = rets.mean()
    sd = rets.std(ddof=1)
    t = m / (sd / np.sqrt(n))
    ann = (1 + m) ** 252 - 1
    sharpe = ann / (sd * np.sqrt(252))
    eq = (1 + rets).cumprod()
    dd = (eq / eq.cummax() - 1).min() * 100
    cum = (eq.iloc[-1] - 1) * 100
    wr = (rets > 0).mean() * 100
    return (f"  {label:24s}  n={n:5d}  "
            f"mean={m*1e4:+6.2f}bps  t={t:+5.2f}  "
            f"Sh={sharpe:+5.2f}  ann={ann*100:+5.1f}%  "
            f"WR={wr:4.1f}%  DD={dd:+6.1f}%  worst={rets.min()*100:+5.2f}%  "
            f"cum={cum:+8.1f}%")


def evaluate(sym: str, cost_bps: float = 1.0) -> None:
    print(f"\n=== {sym} V2 (Mon->Tue overnight only) — OOS/IS split @ {cost_bps:.1f} bp RT ===")
    df = yahoo_daily(sym)
    df["overnight"] = df["open"] / df["close"].shift(1) - 1
    df["dow"] = df["date"].dt.dayofweek
    df = df.dropna(subset=["overnight"])
    v2 = df[df["dow"] == 1].copy()
    v2["pnl"] = v2["overnight"] - cost_bps / 1e4

    cutoff = v2["date"].max() - pd.DateOffset(years=5)
    oos = v2[v2["date"] < cutoff]["pnl"]
    is_ = v2[v2["date"] >= cutoff]["pnl"]

    print(stats(v2["pnl"], "V2 full sample"))
    print(stats(oos, f"V2 OOS (<{cutoff.date()})"))
    print(stats(is_, f"V2 IS  (last 5y)"))

    # Year-by-year so we see exactly when the 5y window weakened
    print(f"\n  Year-by-year V2 ({sym}):")
    v2["year"] = v2["date"].dt.year
    print(f"   year   n   mean_bps    t     cum_%")
    for y, g in v2.groupby("year"):
        if len(g) < 5:
            continue
        m = g["pnl"].mean() * 1e4
        sd = g["pnl"].std(ddof=1)
        t = m / 1e4 / (sd / np.sqrt(len(g))) if sd > 0 else float("nan")
        cum = ((1 + g["pnl"]).prod() - 1) * 100
        flag = "  <-- negative" if cum < 0 else ""
        print(f"   {int(y):4d}  {len(g):3d}  {m:+8.2f}  {t:+5.2f}  {cum:+7.2f}{flag}")

    # Rolling 5y Sharpe of V2
    if len(v2) > 250:
        roll = (v2["pnl"].rolling(250).mean() / v2["pnl"].rolling(250).std(ddof=1) * np.sqrt(52))
        # 52 Tuesdays/yr → annualization factor
        v2["roll_sh"] = roll
        latest = roll.iloc[-1]
        ranks = (roll < latest).mean() * 100
        q = roll.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(2)
        print(f"\n  Rolling 5y (250-trade) Sharpe distribution: {q.to_dict()}")
        print(f"  Current rolling 5y Sharpe: {latest:+.2f} ({ranks:.0f}th percentile)")
        idx_min = roll.idxmin()
        if pd.notna(idx_min):
            print(f"  Min ever: {roll.min():+.2f} on {v2.loc[idx_min, 'date'].date()}")

    # Sanity: cumulative composition
    full_cum = (1 + v2["pnl"]).prod() - 1
    oos_cum = (1 + oos).prod() - 1
    is_cum = (1 + is_).prod() - 1
    composed = (1 + oos_cum) * (1 + is_cum) - 1
    print(f"\n  Sanity: full_cum={full_cum*100:+.2f}%  composed={composed*100:+.2f}%  "
          f"diff={(full_cum-composed)*100:+.4f}%")


if __name__ == "__main__":
    for sym in ["SPY", "QQQ"]:
        evaluate(sym)
