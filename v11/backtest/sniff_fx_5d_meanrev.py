"""FX 5-day mean-reversion fade.

Original sniff_momentum_daily.py found significant NEGATIVE momentum at N=5d
on multiple FX pairs (t-stats with negative sign, |t|>1.9):
  AUDUSD t=-2.76, USDCAD t=-2.24, EURUSD t=-2.31, USDCHF t=-1.94

Negative TSMOM = positive fade. So flip the signal: when 5-day return is
positive, go SHORT for 5 days; when negative, go LONG. Same costs.

Tests per pair:
  1. Fade signal at N=5d (and N=3, 10 for sensitivity)
  2. OOS/IS split: 2018-2022 vs 2023-2026
  3. Year-by-year stability
  4. Cross-sectional fade: rank by past-N return, long bottom-2, short top-2
  5. Cost sensitivity: did the apparent edge survive the per-bar avg_spread?

If the fade is real:
  - sign t-stat positive across most pairs at N=5
  - OOS and IS both positive (not just one regime)
  - cross-sectional version (no instrument-pick bias) also profitable
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

DIRS = [Path(r"C:\nautilus0\data\1m_csv"), Path(r"C:\nautilus0\data\1m_csv_fresh")]
INSTRUMENTS = [
    ("eurusd", "main"), ("gbpusd", "main"), ("usdjpy", "main"),
    ("audusd", "main"), ("nzdusd", "main"), ("usdcad", "main"),
    ("usdchf", "main"), ("audnzd", "fresh"),
]
LOOKBACKS = [3, 5, 10]


def load_1d(symbol: str, source: str) -> pd.DataFrame:
    base = DIRS[1] if source == "fresh" else DIRS[0]
    csv = base / f"{symbol}_1m_tick.csv"
    if not csv.exists():
        csv = (DIRS[0] if source == "fresh" else DIRS[1]) / f"{symbol}_1m_tick.csv"
    df = pd.read_csv(csv, usecols=["timestamp", "close", "avg_spread"])
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.dropna().sort_values("ts").drop_duplicates("ts").set_index("ts")
    d = df.resample("1D").agg({"close": "last", "avg_spread": "mean"}).dropna()
    d["log_ret"] = np.log(d["close"]).diff()
    return d.dropna()


def fade(d: pd.DataFrame, N: int, year_split: int | None = None) -> dict:
    """Fade: signal = -sign(past N-day log return). Hold N days."""
    rets = d["log_ret"].values
    closes = d["close"].values
    spread = d["avg_spread"].values
    times = d.index
    pnl = []; yrs = []
    for i in range(N, len(d) - N, N):
        past = rets[i - N: i].sum()
        if past == 0:
            continue
        sig = -1 if past > 0 else 1   # FADE
        fwd = closes[i + N] / closes[i] - 1
        cost = (spread[i] + spread[i + N]) / closes[i]
        p = sig * fwd - cost
        pnl.append(p); yrs.append(times[i].year)
    arr = np.array(pnl); yrs_arr = np.array(yrs); n = len(arr)
    if n < 2:
        return {"n": n}
    m = arr.mean(); sd = arr.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    tpy = 252 / N
    sh = (m * tpy) / (sd * np.sqrt(tpy)) if sd > 0 else float("nan")
    cum = (1 + arr).prod() - 1
    out = {"n": n, "mean_bps": m * 1e4, "t": t, "sharpe": sh,
           "ann_pct": m * tpy * 100, "cum_pct": cum * 100,
           "wr": float((arr > 0).mean() * 100),
           "pnl": arr, "yrs": yrs_arr}
    if year_split is not None:
        oos_mask = yrs_arr < year_split
        is_mask = yrs_arr >= year_split
        for label, mask in [("oos", oos_mask), ("is", is_mask)]:
            a = arr[mask]
            if len(a) < 2:
                out[label] = {"n": len(a)}; continue
            mm = a.mean(); ss = a.std(ddof=1)
            tt = mm / (ss / np.sqrt(len(a))) if ss > 0 else float("nan")
            shh = (mm * tpy) / (ss * np.sqrt(tpy)) if ss > 0 else float("nan")
            out[label] = {"n": len(a), "mean_bps": mm * 1e4, "t": tt,
                          "sharpe": shh, "cum_pct": ((1 + a).prod() - 1) * 100,
                          "ann_pct": mm * tpy * 100}
    return out


def xs_fade(panel: pd.DataFrame, spreads: pd.DataFrame, N: int, k: int = 2) -> dict:
    """Cross-sectional FADE: long bottom-k (worst past), short top-k (best past)."""
    panel = panel.dropna(how="any")
    spreads = spreads.reindex(panel.index)
    log_ret = np.log(panel).diff().fillna(0)
    closes = panel.values; spr = spreads.values
    n_days, n_inst = panel.shape
    if n_days < 3 * N:
        return {"n": 0}
    pnl = []
    for i in range(N, n_days - N, N):
        past = log_ret.iloc[i - N: i].sum().values
        order = np.argsort(past)
        longs = order[:k]   # FADE: long the losers
        shorts = order[-k:]  # FADE: short the winners
        fwd = closes[i + N] / closes[i] - 1
        cost = (spr[i] + spr[i + N]) / closes[i]
        leg = 0.0
        for j in longs:
            leg += (fwd[j] - cost[j]) / k
        for j in shorts:
            leg += (-fwd[j] - cost[j]) / k
        pnl.append(leg)
    arr = np.array(pnl); n = len(arr)
    if n < 2:
        return {"n": n}
    m = arr.mean(); sd = arr.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    tpy = 252 / N
    sh = (m * tpy) / (sd * np.sqrt(tpy)) if sd > 0 else float("nan")
    return {"n": n, "mean_bps": m * 1e4, "t": t, "sharpe": sh,
            "ann_pct": m * tpy * 100, "cum_pct": ((1 + arr).prod() - 1) * 100,
            "wr": float((arr > 0).mean() * 100)}


def main() -> None:
    print("Loading 8 FX pairs daily ...")
    data = {}
    for sym, src in INSTRUMENTS:
        try:
            d = load_1d(sym, src)
            data[sym] = d
            print(f"  {sym}: {len(d)} bars  {d.index[0].date()} -> {d.index[-1].date()}")
        except Exception as e:
            print(f"  {sym}: failed ({e})")

    print(f"\n=== FADE strategy: signal = -sign(past N-day return), hold N days ===")
    for N in LOOKBACKS:
        print(f"\n--- N = {N} days ---")
        print(f"  {'pair':8s}  {'n':>3s}  {'mean_bps':>9s}  {'t':>5s}  "
              f"{'Sharpe':>6s}  {'ann_%':>6s}  {'cum_%':>7s}  {'WR':>5s}  "
              f"OOS_Sh    IS_Sh")
        for sym, d in data.items():
            r = fade(d, N, year_split=2023)
            if r.get("n", 0) < 2:
                continue
            oos_sh = r.get("oos", {}).get("sharpe", float("nan"))
            is_sh = r.get("is", {}).get("sharpe", float("nan"))
            print(f"  {sym:8s}  {r['n']:3d}  {r['mean_bps']:+9.2f}  "
                  f"{r['t']:+5.2f}  {r['sharpe']:+6.2f}  "
                  f"{r['ann_pct']:+6.1f}  {r['cum_pct']:+7.1f}  "
                  f"{r['wr']:5.1f}  {oos_sh:+6.2f}  {is_sh:+6.2f}")

    # Cross-sectional fade
    print(f"\n=== Cross-sectional FADE (long bottom-2, short top-2) ===")
    closes = pd.DataFrame({s: data[s]["close"] for s in data})
    spreads = pd.DataFrame({s: data[s]["avg_spread"] for s in data})
    closes = closes.dropna(how="any")
    spreads = spreads.reindex(closes.index)
    print(f"  panel: {closes.shape[0]} days x {closes.shape[1]} pairs")
    for N in LOOKBACKS:
        r = xs_fade(closes, spreads, N, k=2)
        if r.get("n", 0) < 2:
            print(f"  N={N}d  insufficient"); continue
        print(f"  N={N:3d}d  n={r['n']:3d}  Sh={r['sharpe']:+5.2f}  "
              f"t={r['t']:+5.2f}  mean={r['mean_bps']:+6.2f}bps  "
              f"ann={r['ann_pct']:+6.1f}%  cum={r['cum_pct']:+7.1f}%  WR={r['wr']:.1f}%")

    # Year-by-year for the strongest single-pair fade at N=5d
    print(f"\n=== Per-year breakdown for each pair at N=5d ===")
    for sym, d in data.items():
        r = fade(d, 5)
        if r.get("n", 0) < 2:
            continue
        print(f"\n  {sym}:")
        years = sorted(set(r["yrs"]))
        print(f"   {'yr':4s}  {'n':>3s}  {'mean_bps':>9s}  {'t':>5s}  {'cum_%':>7s}")
        for yr in years:
            mask = r["yrs"] == yr
            a = r["pnl"][mask]
            if len(a) < 2: continue
            mm = a.mean() * 1e4
            ss = a.std(ddof=1)
            tt = mm/1e4/(ss/np.sqrt(len(a))) if ss > 0 else float("nan")
            cum = ((1 + a).prod() - 1) * 100
            flag = "  <-- neg" if cum < 0 else ""
            print(f"   {yr:4d}  {len(a):3d}  {mm:+9.2f}  {tt:+5.2f}  {cum:+7.2f}{flag}")


if __name__ == "__main__":
    main()
