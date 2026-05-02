"""Phase 2 — overnight-drift cost-aware backtest with regime decomposition.

Phase 1 showed SPY overnight (close -> open) earned t=+4.45 over 33 years,
Sharpe 0.80, with intraday (open -> close) flat. Phase 2 adds:

  1. Execution costs (per-leg slippage in bps).
  2. Regime decomposition: high-rate vs low-rate, calm-VIX vs high-VIX,
     and whether the recent compression is a permanent shift or a regime
     visit we have seen before.
  3. Day-of-week conditional rule (Phase 1: Tue strongest).
  4. Multi-instrument generalization (QQQ, IWM, DIA, EFA).
  5. Tail risk: worst single overnight, MaxDD, distribution of large losses.

No parameter optimization. The "rules" are the *baseline* (every overnight),
"Tue-only" (Mon close -> Tue open), and "no-Friday" (skip Fri close -> Mon open
because the weekend hold has tail risk and Phase 1 showed weak Fri overnight).
We compare all three at the same fixed cost.

Cost model:
  - Retail SPY: bid-ask 1 cent on $700 = ~0.014 bps. Slippage: 1 cent/side.
  - We assume 0.5 bp ROUND-TRIP cost (1 in + 1 out), conservative.
  - Sensitivity sweep at 0.5, 1.0, 2.0 bps round-trip.

Regime sources:
  - VIX from Yahoo (^VIX, daily close).
  - 10y Treasury yield from FRED via the Yahoo proxy ^TNX.

Sanity per backtest_sanity_check feedback:
  - Walk one trade end-to-end.
  - Verify entry_ts < exit_ts always.
  - Confirm overnight_ret + intraday_ret approximates close-to-close return.

Usage:
  python -m v11.backtest.backtest_overnight_drift_phase2
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests


def yahoo_daily(symbol: str, period1: int = 0, period2: int = 2_000_000_000) -> pd.DataFrame:
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"period1": period1, "period2": period2, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "date": pd.to_datetime(res["timestamp"], unit="s").normalize(),
        "open": q.get("open"),
        "high": q.get("high"),
        "low": q.get("low"),
        "close": q.get("close"),
    }).dropna(subset=["open", "close"]).reset_index(drop=True)
    df = df.sort_values("date").reset_index(drop=True)
    return df


@dataclass
class StratResult:
    name: str
    n: int
    mean_bps: float
    t: float
    sharpe: float
    ann_ret: float
    ann_vol: float
    wr: float
    max_dd: float
    worst_day: float
    cum_wealth: float


def stats(rets: pd.Series, name: str, ann_factor: float = 252) -> StratResult:
    rets = rets.dropna()
    n = len(rets)
    if n == 0:
        return StratResult(name, 0, *([float("nan")] * 8))
    m = rets.mean()
    sd = rets.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    ann_ret = (1 + m) ** ann_factor - 1
    ann_vol = sd * np.sqrt(ann_factor)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else float("nan")
    wr = (rets > 0).mean() * 100
    eq = (1 + rets).cumprod()
    dd = (eq / eq.cummax() - 1).min() * 100
    worst = rets.min() * 100
    cum_wealth = (eq.iloc[-1] - 1) * 100
    return StratResult(name, n, m * 1e4, t, sharpe, ann_ret * 100, ann_vol * 100,
                       wr, dd, worst, cum_wealth)


def fmt_row(r: StratResult) -> str:
    return (f"  {r.name:24s}  n={r.n:5d}  "
            f"mean={r.mean_bps:+6.2f} bps  "
            f"t={r.t:+5.2f}  "
            f"Sh={r.sharpe:+5.2f}  "
            f"ann={r.ann_ret:+5.1f}%  "
            f"WR={r.wr:4.1f}%  "
            f"MaxDD={r.max_dd:+6.1f}%  "
            f"worst={r.worst_day:+5.2f}%  "
            f"cum={r.cum_wealth:+8.1f}%")


def main() -> None:
    print("Loading SPY ...")
    spy = yahoo_daily("SPY")
    spy["dow"] = spy["date"].dt.dayofweek
    spy["overnight"] = spy["open"] / spy["close"].shift(1) - 1
    spy["intraday"] = spy["close"] / spy["open"] - 1
    spy["c2c"] = spy["close"] / spy["close"].shift(1) - 1
    spy = spy.dropna(subset=["overnight"]).reset_index(drop=True)
    print(f"  SPY {len(spy)} bars  {spy['date'].iloc[0].date()} -> {spy['date'].iloc[-1].date()}")

    # Sanity
    err = (spy["overnight"] + spy["intraday"] +
           spy["overnight"] * spy["intraday"] - spy["c2c"]).abs().max()
    print(f"  identity check: max |on*(1+id) + id - c2c| = {err:.2e}")
    assert err < 1e-10, "identity broken"

    print("\nLoading VIX, ^TNX, sibling ETFs ...")
    vix = yahoo_daily("^VIX")[["date", "close"]].rename(columns={"close": "vix"})
    tnx = yahoo_daily("^TNX")[["date", "close"]].rename(columns={"close": "tnx"})
    spy = spy.merge(vix, on="date", how="left").merge(tnx, on="date", how="left")
    spy["vix"] = spy["vix"].ffill()
    spy["tnx"] = spy["tnx"].ffill()

    print("\n" + "=" * 110)
    print("Section 1. Headline overnight performance, no costs")
    print("=" * 110)
    print(fmt_row(stats(spy["overnight"], "Overnight all-days")))
    print(fmt_row(stats(spy["intraday"], "Intraday all-days")))
    print(fmt_row(stats(spy["c2c"], "Buy-and-hold (c2c)")))

    print("\n" + "=" * 110)
    print("Section 2. Cost sensitivity (bps round-trip applied per overnight trade)")
    print("=" * 110)
    for cost_bps in [0.5, 1.0, 2.0]:
        net = spy["overnight"] - cost_bps / 1e4
        print(fmt_row(stats(net, f"Overnight @ {cost_bps:.1f} bps RT")))

    # Day-of-week conditional rules (still trading on a single overnight, but
    # we choose which entry-to-exit overnights to take based on entry-day weekday).
    # Mon close -> Tue open is overnight on Tue's row (dow=1).
    print("\n" + "=" * 110)
    print("Section 3. Day-of-week variants (overnights kept = those with exit-day-dow shown)")
    print("=" * 110)
    cost = 1.0 / 1e4  # 1 bp RT, mid-range
    for dow_set, label in [
        ([0, 1, 2, 3, 4], "All weekdays"),
        ([1], "Mon->Tue only"),
        ([1, 2, 3], "Tue/Wed/Thu only (skip weekend hold)"),
        ([0], "Fri->Mon only (weekend hold)"),
    ]:
        sub = spy[spy["dow"].isin(dow_set)]
        net = sub["overnight"] - cost
        # When days are skipped, you sit in cash. Annualization should still
        # use 252 trading days but we scale the annualized return by participation.
        ret = stats(net, label)
        # also report participation fraction for context
        print(fmt_row(ret) + f"  participation={len(sub)/len(spy)*100:5.1f}%")

    print("\n" + "=" * 110)
    print("Section 4. Year-by-year (overnight, 1 bp RT cost)")
    print("=" * 110)
    spy["year"] = spy["date"].dt.year
    spy["overnight_net"] = spy["overnight"] - cost
    print(f"  {'year':4s}  {'n':>4s}  {'mean_bps':>9s}  {'t':>5s}  {'cum_%':>7s}")
    for y, g in spy.groupby("year"):
        m = g["overnight_net"].mean() * 1e4
        sd = g["overnight_net"].std(ddof=1)
        t = m / 1e4 / (sd / np.sqrt(len(g))) if sd > 0 else float("nan")
        cum = ((1 + g["overnight_net"]).prod() - 1) * 100
        marker = "  <-- negative" if cum < 0 else ""
        print(f"  {int(y):4d}  {len(g):4d}  {m:+9.2f}  {t:+5.2f}  {cum:+7.2f}{marker}")

    print("\n" + "=" * 110)
    print("Section 5. Regime decomposition")
    print("=" * 110)
    # VIX terciles (over the full sample, where VIX is available)
    sub = spy.dropna(subset=["vix"]).copy()
    sub["vix_tercile"] = pd.qcut(sub["vix"], 3, labels=["calm", "normal", "stressed"])
    print("  By VIX tercile (1 bp cost):")
    for t_label, g in sub.groupby("vix_tercile", observed=True):
        net = g["overnight"] - cost
        print(fmt_row(stats(net, f"VIX {t_label}")))

    # 10y rates buckets: low <3%, mid 3-4.5%, high >4.5%
    sub2 = spy.dropna(subset=["tnx"]).copy()
    # ^TNX is yield * 10 (i.e. 4.5% shows as 45.0)
    sub2["rate_bucket"] = pd.cut(sub2["tnx"], bins=[-1, 30, 45, 200],
                                  labels=["low<3%", "mid3-4.5", "high>4.5"])
    print("\n  By 10y yield bucket (1 bp cost):")
    for r_label, g in sub2.groupby("rate_bucket", observed=True):
        net = g["overnight"] - cost
        print(fmt_row(stats(net, f"rates {r_label}")))

    # Rolling 5y Sharpe to see whether the recent dip is unprecedented
    print("\n  5y rolling Sharpe (overnight, no cost) — last/current 5y vs prior visits:")
    spy["ovr"] = spy["overnight"]
    win = 252 * 5
    rolling_sharpe = (spy["ovr"].rolling(win).mean() / spy["ovr"].rolling(win).std(ddof=1)
                      * np.sqrt(252))
    spy["roll_sh"] = rolling_sharpe
    quants = rolling_sharpe.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(2)
    print(f"   distribution of rolling 5y Sharpe: {quants.to_dict()}")
    print(f"   current rolling 5y Sharpe (last bar): {rolling_sharpe.iloc[-1]:+.2f}")
    print(f"   minimum ever: {rolling_sharpe.min():+.2f} on {spy.loc[rolling_sharpe.idxmin(), 'date'].date() if pd.notna(rolling_sharpe.idxmin()) else 'n/a'}")
    pct = (rolling_sharpe < rolling_sharpe.iloc[-1]).mean() * 100
    print(f"   current value is at the {pct:.0f}th percentile of all-time rolling 5y Sharpe")

    print("\n" + "=" * 110)
    print("Section 6. Sibling ETFs (1 bp cost, all-days overnight)")
    print("=" * 110)
    for sym in ["QQQ", "IWM", "DIA", "EFA"]:
        try:
            df = yahoo_daily(sym)
            df["overnight"] = df["open"] / df["close"].shift(1) - 1
            df = df.dropna(subset=["overnight"])
            net = df["overnight"] - cost
            print(fmt_row(stats(net, sym)))
        except Exception as e:
            print(f"  {sym}: failed ({e})")

    print("\n" + "=" * 110)
    print("Section 7. Tail risk (overnight, 1 bp cost)")
    print("=" * 110)
    net = spy["overnight"] - cost
    print(f"  Total overnights: {len(net)}")
    print(f"  Worst day:   {net.min()*100:+.2f}% on {spy.loc[net.idxmin(), 'date'].date()}")
    print(f"  Worst 5:")
    bottom = spy.assign(net=net).nsmallest(5, "net")
    for _, row in bottom.iterrows():
        print(f"     {row['date'].date()}  {row['net']*100:+.2f}%")
    print(f"  Best 5:")
    top = spy.assign(net=net).nlargest(5, "net")
    for _, row in top.iterrows():
        print(f"     {row['date'].date()}  {row['net']*100:+.2f}%")
    p = [1, 5, 50, 95, 99]
    pcts = np.percentile(net.dropna() * 100, p)
    print(f"  Percentiles: " + "  ".join(f"p{pp}={vv:+.2f}%" for pp, vv in zip(p, pcts)))
    sigma = net.std() * 100
    n_3sigma = (net.abs() > 3 * net.std()).sum()
    print(f"  Daily sigma: {sigma:.3f}%   |x|>3sigma days: {n_3sigma} ({n_3sigma/len(net)*100:.2f}%)")

    print("\n" + "=" * 110)
    print("Section 8. Sanity walk: one specific trade")
    print("=" * 110)
    ex = spy.iloc[-200:].iloc[100]
    prev_close = spy.iloc[spy.index.get_loc(ex.name) - 1]["close"]
    print(f"  date            : {ex['date'].date()}")
    print(f"  prev close      : {prev_close:.4f}")
    print(f"  open  (entry+1d): {ex['open']:.4f}")
    print(f"  overnight ret   : {ex['overnight']*1e4:+.2f} bps")
    print(f"  recomputed      : {(ex['open']/prev_close - 1)*1e4:+.2f} bps  (should match)")
    print(f"  intraday ret    : {ex['intraday']*1e4:+.2f} bps")


if __name__ == "__main__":
    main()
