"""Phase 1 sniff (daily horizons): time-series momentum and cross-sectional rank.

Intraday TSMOM (1h-48h) failed on all 9 instruments after costs — see
`sniff_momentum_multi_pair.py`. The classical academic finding is that FX
momentum lives at multi-day to multi-month horizons, not intraday.

This sniff:
  - Resamples 1m to DAILY bars.
  - Tests TSMOM at lookbacks 5d, 10d, 20d, 60d, 120d (hold = lookback).
  - Tests cross-sectional momentum: rank instruments by past N-day return,
    long top-2 / short bottom-2, rebalance every N days.
  - Costs: avg_spread per bar carried through to daily as the daily mean,
    so 1-day RT cost = 2 * mean_spread / close.

No tuning. Sample is full 2018-01 to 2026 across all 9 instruments.

Sanity:
  - n_bars per instrument ~2000 daily bars
  - per-instrument cumulative TSMOM should equal sum of per-trade pnls
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
    ("xauusd", "main"), ("xagusd", "main"),
]
LOOKBACKS = [5, 10, 20, 60, 120]


def load_1d(symbol: str, source: str) -> pd.DataFrame:
    base = DIRS[1] if source == "fresh" else DIRS[0]
    csv = base / f"{symbol}_1m_tick.csv"
    if not csv.exists():
        csv = (DIRS[0] if source == "fresh" else DIRS[1]) / f"{symbol}_1m_tick.csv"
    df = pd.read_csv(csv, usecols=["timestamp", "open", "high", "low", "close", "avg_spread"])
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.dropna().sort_values("ts").drop_duplicates("ts").set_index("ts")
    d = df.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                               "close": "last", "avg_spread": "mean"}).dropna()
    d["log_ret"] = np.log(d["close"]).diff()
    return d.dropna()


def tsmom(d: pd.DataFrame, N: int) -> dict:
    if len(d) < 3 * N:
        return {"n": 0}
    rets = d["log_ret"].values; closes = d["close"].values
    spread = d["avg_spread"].values
    pnl = []
    for i in range(N, len(d) - N, N):
        sig_sum = rets[i - N: i].sum()
        if sig_sum == 0:
            continue
        sig = 1 if sig_sum > 0 else -1
        fwd = closes[i + N] / closes[i] - 1
        cost = (spread[i] + spread[i + N]) / closes[i]
        pnl.append(sig * fwd - cost)
    arr = np.array(pnl)
    n = len(arr)
    if n < 2:
        return {"n": n}
    m = arr.mean(); sd = arr.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    trades_per_year = 252 / N
    ann = m * trades_per_year
    sh = ann / (sd * np.sqrt(trades_per_year)) if sd > 0 else float("nan")
    cum = (1 + arr).prod() - 1
    return {"n": n, "mean_bps": m * 1e4, "t": t, "sharpe": sh,
            "ann_pct": ann * 100, "cum_pct": cum * 100,
            "wr": float((arr > 0).mean() * 100)}


def xs_momentum(panel: pd.DataFrame, spreads: pd.DataFrame, N: int, k: int = 2) -> dict:
    """Cross-sectional momentum: every N days, rank by past N-day log return.
    Long top-k, short bottom-k, equal-weight, hold N days. Cost = mean spread per leg.
    """
    panel = panel.dropna(how="any")  # only days where all instruments have data
    spreads = spreads.reindex(panel.index)
    log_ret = np.log(panel).diff()
    log_ret = log_ret.fillna(0)
    closes = panel.values
    spr = spreads.values
    n_days, n_inst = panel.shape
    if n_days < 3 * N:
        return {"n": 0}
    pnl = []
    for i in range(N, n_days - N, N):
        past = log_ret.iloc[i - N: i].sum().values  # per-instrument past return
        order = np.argsort(past)
        shorts = order[:k]; longs = order[-k:]
        # forward return per instrument
        fwd = closes[i + N] / closes[i] - 1
        # cost: spread/close at entry + at exit, one leg each side
        cost = (spr[i] + spr[i + N]) / closes[i]
        leg_pnl = 0.0
        for j in longs:
            leg_pnl += (fwd[j] - cost[j]) / k
        for j in shorts:
            leg_pnl += (-fwd[j] - cost[j]) / k
        pnl.append(leg_pnl)
    arr = np.array(pnl); n = len(arr)
    if n < 2:
        return {"n": n}
    m = arr.mean(); sd = arr.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    tpy = 252 / N
    sh = (m * tpy) / (sd * np.sqrt(tpy)) if sd > 0 else float("nan")
    cum = (1 + arr).prod() - 1
    return {"n": n, "mean_bps": m * 1e4, "t": t, "sharpe": sh,
            "ann_pct": m * tpy * 100, "cum_pct": cum * 100,
            "wr": float((arr > 0).mean() * 100)}


def main() -> None:
    print("Loading 1m -> 1d for 9 instruments ...")
    data = {}
    for sym, src in INSTRUMENTS:
        try:
            d = load_1d(sym, src)
            data[sym] = d
            print(f"  {sym}: {len(d)} daily bars  {d.index[0].date()} -> {d.index[-1].date()}")
        except Exception as e:
            print(f"  {sym}: failed ({e})")

    print(f"\n=== TSMOM Sharpe (after spread cost) by lookback (days) ===")
    print(f"  {'instr':10s}  " + "  ".join(f"N={n:>3d}d" for n in LOOKBACKS))
    cells: dict = {}
    for sym, d in data.items():
        parts = []
        for N in LOOKBACKS:
            res = tsmom(d, N)
            cells[(sym, N)] = res
            parts.append(f"{res.get('sharpe', float('nan')):+.2f}" if res.get("n", 0) >= 2 else " n/a ")
        print(f"  {sym:10s}  " + "  ".join(f"{p:>6s}" for p in parts))

    print(f"\n=== TSMOM mean (bps/trade) after cost ===")
    print(f"  {'instr':10s}  " + "  ".join(f"N={n:>3d}d" for n in LOOKBACKS))
    for sym in data:
        parts = []
        for N in LOOKBACKS:
            res = cells[(sym, N)]
            parts.append(f"{res.get('mean_bps', float('nan')):+6.1f}" if res.get("n", 0) >= 2 else " n/a ")
        print(f"  {sym:10s}  " + "  ".join(f"{p:>7s}" for p in parts))

    print(f"\n=== TSMOM t-stat ===")
    print(f"  {'instr':10s}  " + "  ".join(f"N={n:>3d}d" for n in LOOKBACKS))
    for sym in data:
        parts = []
        for N in LOOKBACKS:
            res = cells[(sym, N)]
            parts.append(f"{res.get('t', float('nan')):+5.2f}" if res.get("n", 0) >= 2 else " n/a ")
        print(f"  {sym:10s}  " + "  ".join(f"{p:>6s}" for p in parts))

    print(f"\n=== TSMOM annualized return % ===")
    print(f"  {'instr':10s}  " + "  ".join(f"N={n:>3d}d" for n in LOOKBACKS))
    for sym in data:
        parts = []
        for N in LOOKBACKS:
            res = cells[(sym, N)]
            parts.append(f"{res.get('ann_pct', float('nan')):+6.1f}" if res.get("n", 0) >= 2 else " n/a ")
        print(f"  {sym:10s}  " + "  ".join(f"{p:>7s}" for p in parts))

    # Highlight passing cells
    print(f"\n=== Pass gate (Sharpe > 0.5 AND |t| > 1.5) ===")
    for (sym, N), res in cells.items():
        if res.get("n", 0) < 2: continue
        if res["sharpe"] > 0.5 and abs(res["t"]) > 1.5:
            print(f"   PASS: {sym} N={N}d  Sh={res['sharpe']:+.2f}  t={res['t']:+.2f}  "
                  f"mean={res['mean_bps']:+.1f}bps  ann={res['ann_pct']:+.1f}%")

    # Cross-sectional momentum panel (FX majors + AUDNZD only — same units)
    print(f"\n=== Cross-sectional momentum (long top-2 / short bottom-2 by past N-day return) ===")
    fx_syms = ["eurusd", "gbpusd", "usdjpy", "audusd", "nzdusd", "usdcad", "usdchf", "audnzd"]
    closes = pd.DataFrame({s: data[s]["close"] for s in fx_syms if s in data})
    spreads = pd.DataFrame({s: data[s]["avg_spread"] for s in fx_syms if s in data})
    closes = closes.dropna(how="any")
    spreads = spreads.reindex(closes.index)
    print(f"  panel: {closes.shape[0]} days x {closes.shape[1]} instruments  "
          f"{closes.index[0].date()} -> {closes.index[-1].date()}")
    for N in LOOKBACKS:
        res = xs_momentum(closes, spreads, N, k=2)
        if res.get("n", 0) < 2:
            print(f"  N={N:3d}d  (insufficient)")
            continue
        print(f"  N={N:3d}d  n={res['n']:3d}  Sh={res['sharpe']:+5.2f}  "
              f"t={res['t']:+5.2f}  mean={res['mean_bps']:+6.1f}bps  "
              f"ann={res['ann_pct']:+6.1f}%  cum={res['cum_pct']:+6.1f}%  WR={res['wr']:.1f}%")

    # Per-year for the strongest TSMOM cell
    best = None
    for k, v in cells.items():
        if v.get("n", 0) < 2: continue
        s = v["sharpe"]
        if best is None or s > best[1]["sharpe"]:
            best = (k, v)
    if best is not None:
        sym_b, N_b = best[0]
        print(f"\n=== Per-year for best TSMOM cell: {sym_b} N={N_b}d ===")
        d = data[sym_b]
        rets = d["log_ret"].values; closes_arr = d["close"].values
        spr = d["avg_spread"].values; times = d.index
        yr_pnl: dict[int, list[float]] = {}
        for i in range(N_b, len(d) - N_b, N_b):
            ss = rets[i - N_b: i].sum()
            if ss == 0: continue
            sig = 1 if ss > 0 else -1
            fwd = closes_arr[i + N_b] / closes_arr[i] - 1
            cost = (spr[i] + spr[i + N_b]) / closes_arr[i]
            yr_pnl.setdefault(times[i].year, []).append(sig * fwd - cost)
        print(f"   {'year':4s}  {'n':>3s}  {'mean_bps':>9s}  {'t':>5s}  {'cum_%':>7s}")
        for yr in sorted(yr_pnl):
            arr = np.array(yr_pnl[yr])
            m = arr.mean() * 1e4
            sd = arr.std(ddof=1) if len(arr) > 1 else 0
            t = m/1e4/(sd/np.sqrt(len(arr))) if sd > 0 else float("nan")
            cum = ((1 + arr).prod() - 1) * 100
            flag = "  <-- neg" if cum < 0 else ""
            print(f"   {yr:4d}  {len(arr):3d}  {m:+9.2f}  {t:+5.2f}  {cum:+7.2f}{flag}")


if __name__ == "__main__":
    main()
