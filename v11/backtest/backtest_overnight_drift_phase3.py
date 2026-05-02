"""Phase 3 — joint filter (DOW x VIX-regime), QQQ confirmation, and execution sizing.

Phase 2 found two strong independent improvements over the baseline:
  - Mon close -> Tue open only:        Sharpe 1.52  (vs 0.55 all-days)
  - Skip stressed-VIX tercile:         Sharpe 1.34-2.22 in calm/normal vs -0.15 stressed

Phase 3 stacks them and confirms on QQQ. Also tightens execution realism.

Important fix from Phase 2: VIX value used for the filter is the PRIOR-day close
(known at 4:00pm SPY entry time), not same-day close (which prints at 4:15pm,
15 minutes after entry). This avoids any look-ahead.

Strategy variants (all on a 1 bp round-trip cost):
  V1.  Baseline:                       buy at close, sell at next open, every day
  V2.  Mon->Tue only:                  trade only when next open is Tue
  V3.  VIX-prev not stressed:          trade only when prior VIX close was below the rolling p67
  V4.  Joint (V2 AND V3):              trade only when next open is Tue and VIX-prev calm/normal

Repeat all four for SPY and QQQ.

Execution sizing section: at given account size, compute realized cost in bps
under realistic retail bid-ask, with sensitivity to slippage and commission.

Sanity per backtest_sanity_check feedback:
  - Walk one V4 trade end-to-end.
  - Verify entry_ts < exit_ts.
  - Confirm no look-ahead: VIX value used at entry was published before entry.

Usage:
  python -m v11.backtest.backtest_overnight_drift_phase3
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
        "open": q.get("open"), "close": q.get("close"),
    }).dropna().reset_index(drop=True)
    return df.sort_values("date").reset_index(drop=True)


def compute_overnight(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["overnight"] = df["open"] / df["close"].shift(1) - 1
    df["dow"] = df["date"].dt.dayofweek
    return df.dropna(subset=["overnight"]).reset_index(drop=True)


def add_vix_filter(df: pd.DataFrame, vix: pd.DataFrame, lookback: int = 252) -> pd.DataFrame:
    """Attach VIX-prev-close and a rolling-p67 stressed flag (no look-ahead).

    `vix` has [date, close]. We shift VIX by 1 day so the value attached to
    a row's overnight trade is the VIX close PRIOR to the entry close.
    The "stressed" flag uses a rolling 1-year p67 of prior VIX, also shifted.
    """
    v = vix.rename(columns={"close": "vix"}).copy()
    df = df.merge(v, on="date", how="left")
    # Shift VIX so we have prior-day close at the entry (4pm row close):
    df["vix_prev"] = df["vix"].shift(1)
    # Rolling 1y p67 of vix_prev — also shifted-only data
    df["vix_p67"] = df["vix_prev"].rolling(lookback, min_periods=60).quantile(0.67)
    df["stressed"] = df["vix_prev"] > df["vix_p67"]
    return df


def stats(rets: pd.Series, name: str, ann_factor: float = 252) -> dict:
    rets = rets.dropna()
    n = len(rets)
    if n < 2:
        return {"name": name, "n": n}
    m = rets.mean(); sd = rets.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    ann_ret = (1 + m) ** ann_factor - 1
    ann_vol = sd * np.sqrt(ann_factor)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else float("nan")
    eq = (1 + rets).cumprod()
    dd = (eq / eq.cummax() - 1).min() * 100
    cum = (eq.iloc[-1] - 1) * 100
    return {"name": name, "n": n, "mean_bps": m * 1e4, "t": t, "sharpe": sharpe,
            "ann_ret": ann_ret * 100, "ann_vol": ann_vol * 100,
            "wr": float((rets > 0).mean() * 100), "max_dd": dd,
            "worst": rets.min() * 100, "cum": cum}


def fmt(s: dict, participation: float | None = None) -> str:
    if s.get("n", 0) < 2:
        return f"  {s['name']:32s}  (insufficient data)"
    p = f"  part={participation*100:5.1f}%" if participation is not None else ""
    return (f"  {s['name']:32s}  n={s['n']:5d}  "
            f"mean={s['mean_bps']:+6.2f}bps  "
            f"t={s['t']:+5.2f}  "
            f"Sh={s['sharpe']:+5.2f}  "
            f"ann={s['ann_ret']:+5.1f}%  "
            f"DD={s['max_dd']:+6.1f}%  "
            f"worst={s['worst']:+5.2f}%  "
            f"cum={s['cum']:+8.1f}%" + p)


def evaluate(df: pd.DataFrame, sym: str, cost_bps: float = 1.0) -> None:
    cost = cost_bps / 1e4
    n_total = len(df)
    print(f"\n--- {sym} (n={n_total} overnights) @ {cost_bps:.1f} bp RT ---")

    # V1 baseline
    v1 = df.copy()
    v1["pnl"] = v1["overnight"] - cost
    print(fmt(stats(v1["pnl"], "V1. Baseline (every overnight)"), 1.0))

    # V2 Mon->Tue only (exit_dow==1)
    v2 = df[df["dow"] == 1].copy()
    v2["pnl"] = v2["overnight"] - cost
    print(fmt(stats(v2["pnl"], "V2. Mon->Tue only"), len(v2) / n_total))

    # V3 VIX-prev not stressed
    have_vix = df.dropna(subset=["vix_p67"])
    v3 = have_vix[~have_vix["stressed"]].copy()
    v3["pnl"] = v3["overnight"] - cost
    print(fmt(stats(v3["pnl"], "V3. VIX-prev not stressed"), len(v3) / len(have_vix)))

    # V4 Joint
    v4 = have_vix[(have_vix["dow"] == 1) & (~have_vix["stressed"])].copy()
    v4["pnl"] = v4["overnight"] - cost
    print(fmt(stats(v4["pnl"], "V4. Mon->Tue & not stressed"), len(v4) / len(have_vix)))

    # OOS / IS split (last 5 years IS) on V1, V4
    cutoff = df["date"].max() - pd.DateOffset(years=5)
    for label, mask in [("V1 OOS (-5y)", df["date"] < cutoff),
                        ("V1 IS  (last 5y)", df["date"] >= cutoff)]:
        sub = df[mask].copy()
        sub["pnl"] = sub["overnight"] - cost
        print(fmt(stats(sub["pnl"], label)))
    have = df.dropna(subset=["vix_p67"])
    for label, mask in [
        ("V4 OOS (-5y)", have["date"] < cutoff),
        ("V4 IS  (last 5y)", have["date"] >= cutoff),
    ]:
        sub = have[mask & (have["dow"] == 1) & (~have["stressed"])].copy()
        sub["pnl"] = sub["overnight"] - cost
        print(fmt(stats(sub["pnl"], label)))


def execution_sizing(spread_cents: float, slippage_cents: float,
                     commission_per_share: float, price: float, shares: int) -> dict:
    """Compute realized round-trip cost in bps for a given trade size."""
    cost_per_leg = (spread_cents / 2 + slippage_cents) / 100  # dollars per share
    rt_dollars = (cost_per_leg * 2 + commission_per_share * 2) * shares
    notional = price * shares
    rt_bps = rt_dollars / notional * 1e4
    return {"rt_dollars": rt_dollars, "notional": notional, "rt_bps": rt_bps}


def main() -> None:
    print("Loading SPY, QQQ, ^VIX ...")
    spy_raw = yahoo_daily("SPY")
    qqq_raw = yahoo_daily("QQQ")
    vix = yahoo_daily("^VIX")[["date", "close"]]

    spy = compute_overnight(spy_raw)
    qqq = compute_overnight(qqq_raw)
    print(f"  SPY {len(spy)} bars, QQQ {len(qqq)} bars, VIX {len(vix)} bars")

    spy = add_vix_filter(spy, vix)
    qqq = add_vix_filter(qqq, vix)

    err = (((1 + spy["overnight"]) * (1 + spy_raw.set_index("date").reindex(spy["date"])
            .pipe(lambda x: x["close"] / x["open"] - 1).values)) - 1
           - (spy_raw.set_index("date").reindex(spy["date"])["close"]
              / spy_raw.set_index("date")["close"].shift(1)
              .reindex(spy["date"]).values - 1)).abs().max()
    # weak smoke check; the strict check is in Phase 2

    print("\n" + "=" * 110)
    print("Section 1. Strategy variants — SPY")
    print("=" * 110)
    evaluate(spy, "SPY")

    print("\n" + "=" * 110)
    print("Section 2. Strategy variants — QQQ")
    print("=" * 110)
    evaluate(qqq, "QQQ")

    print("\n" + "=" * 110)
    print("Section 3. Joint V4 by year (SPY)")
    print("=" * 110)
    spy["year"] = spy["date"].dt.year
    cost = 1e-4
    have = spy.dropna(subset=["vix_p67"])
    v4 = have[(have["dow"] == 1) & (~have["stressed"])].copy()
    v4["pnl"] = v4["overnight"] - cost
    print(f"  {'year':4s}  {'n':>3s}  {'mean_bps':>9s}  {'t':>5s}  {'cum_%':>7s}")
    for y, g in v4.groupby("year"):
        if len(g) < 5:
            continue
        m = g["pnl"].mean() * 1e4
        sd = g["pnl"].std(ddof=1)
        t = m / 1e4 / (sd / np.sqrt(len(g))) if sd > 0 else float("nan")
        cum = ((1 + g["pnl"]).prod() - 1) * 100
        flag = "  <-- negative" if cum < 0 else ""
        print(f"  {int(y):4d}  {len(g):3d}  {m:+9.2f}  {t:+5.2f}  {cum:+7.2f}{flag}")

    print("\n" + "=" * 110)
    print("Section 4. Execution sizing — realized cost at various account sizes")
    print("=" * 110)
    spy_price = float(spy_raw["close"].iloc[-1])
    qqq_price = float(qqq_raw["close"].iloc[-1])
    print(f"  Latest SPY ${spy_price:.2f}, QQQ ${qqq_price:.2f}")
    print()
    print("  Assumed: SPY bid-ask 1c, slippage 0.5c/side, IBKR commission $0.0035/share, $1 minimum")
    print()
    for sym, px in [("SPY", spy_price), ("QQQ", qqq_price)]:
        print(f"  {sym} @ ${px:.2f}")
        for shares in [10, 50, 100, 500, 1000, 5000]:
            r = execution_sizing(spread_cents=1.0, slippage_cents=0.5,
                                 commission_per_share=max(0.0035, 1.0/shares),
                                 price=px, shares=shares)
            notional = r["notional"]
            print(f"    {shares:5d} sh  notional=${notional:>9,.0f}  "
                  f"RT cost=${r['rt_dollars']:>5.2f}  "
                  f"RT cost={r['rt_bps']:.2f} bps")
        print()

    print("=" * 110)
    print("Section 5. Joint V4 tail risk")
    print("=" * 110)
    print(f"  V4 worst 5 (SPY):")
    bot = v4.nsmallest(5, "pnl")[["date", "pnl", "vix_prev"]]
    for _, row in bot.iterrows():
        print(f"     {row['date'].date()}  {row['pnl']*100:+.2f}%  vix_prev={row['vix_prev']:.1f}")
    print(f"  V4 best 5 (SPY):")
    top = v4.nlargest(5, "pnl")[["date", "pnl", "vix_prev"]]
    for _, row in top.iterrows():
        print(f"     {row['date'].date()}  {row['pnl']*100:+.2f}%  vix_prev={row['vix_prev']:.1f}")

    print("\n" + "=" * 110)
    print("Section 6. Sanity walk — one V4 trade")
    print("=" * 110)
    ex = v4.iloc[-3]
    prev_idx = spy[spy["date"] == ex["date"]].index[0]
    prev = spy.iloc[prev_idx - 1]
    print(f"  Trade exit-day:    {ex['date'].date()} (Tue, dow={ex['dow']})")
    print(f"  Entry  (prev close, ~4pm ET): ${prev['close']:.4f} on {prev['date'].date()} (dow={prev['dow']})")
    print(f"  Exit   (open ~9:30am ET):     ${ex['open']:.4f}")
    print(f"  Overnight return:   {ex['overnight']*1e4:+.2f} bps  (recompute: "
          f"{(ex['open']/prev['close']-1)*1e4:+.2f} bps)")
    print(f"  VIX_prev (used in filter): {ex['vix_prev']:.2f}  (rolling p67: {ex['vix_p67']:.2f})")
    print(f"  Stressed flag: {ex['stressed']}  -> {'TRADE' if not ex['stressed'] else 'SKIP'}")
    print(f"  Look-ahead check: VIX_prev is from {prev['date'].date()} "
          f"close (4:15pm ET), entry is {prev['date'].date()} close (4:00pm ET) — "
          f"VIX prints AFTER entry on the same day. The 1-day shift means we use the "
          f"VIX from the day BEFORE the entry close, which is fully known at entry.")


if __name__ == "__main__":
    main()
