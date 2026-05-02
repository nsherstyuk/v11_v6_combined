"""Decompose XAUUSD daily TSMOM result: is it momentum or just long-the-bull-market?

Tests:
  1. TSMOM (long when past N>0, short when past N<0) at N=20, 60, 120d
  2. Always-long (buy-and-hold every N-day window, pay same costs)
  3. Always-short (mirror)
  4. Signal composition: how many longs vs shorts did TSMOM take?
  5. Per-year breakdown of TSMOM signal direction vs realized return — did
     the signal correctly flip short when gold fell?
  6. Detrend test: subtract rolling mean of past-N return from signal — does
     the Sharpe survive when we strip out the unconditional drift?

If TSMOM ~ always-long, the "edge" is bull-market direction, not momentum.
If TSMOM clearly beats always-long (esp. in down years), it's a real signal.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

CSV = Path(r"C:\nautilus0\data\1m_csv\xauusd_1m_tick.csv")
LOOKBACKS = [20, 60, 120]


def load_1d() -> pd.DataFrame:
    df = pd.read_csv(CSV, usecols=["timestamp", "close", "avg_spread"])
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.dropna().sort_values("ts").drop_duplicates("ts").set_index("ts")
    d = df.resample("1D").agg({"close": "last", "avg_spread": "mean"}).dropna()
    d["log_ret"] = np.log(d["close"]).diff()
    return d.dropna()


def run_strategy(d: pd.DataFrame, N: int, mode: str) -> dict:
    """mode: 'tsmom' | 'long' | 'short' | 'detrend'"""
    rets = d["log_ret"].values
    closes = d["close"].values
    spread = d["avg_spread"].values
    times = d.index
    pnl = []; sigs = []; yrs = []; fwds = []
    for i in range(N, len(d) - N, N):
        past = rets[i - N: i].sum()
        if mode == "tsmom":
            sig = 1 if past > 0 else (-1 if past < 0 else 0)
        elif mode == "long":
            sig = 1
        elif mode == "short":
            sig = -1
        elif mode == "detrend":
            # subtract rolling mean of past-N sums (lookback over prior 4*N windows)
            window = 4 * N
            if i < N + window:
                continue
            past_history = [rets[j - N: j].sum() for j in range(i - window, i, N)]
            mu = np.mean(past_history)
            adj = past - mu
            sig = 1 if adj > 0 else (-1 if adj < 0 else 0)
        else:
            raise ValueError(mode)
        if sig == 0:
            continue
        fwd = closes[i + N] / closes[i] - 1
        cost = (spread[i] + spread[i + N]) / closes[i]
        p = sig * fwd - cost
        pnl.append(p); sigs.append(sig); yrs.append(times[i].year); fwds.append(fwd)
    arr = np.array(pnl); n = len(arr)
    if n < 2:
        return {"n": n}
    m = arr.mean(); sd = arr.std(ddof=1)
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    tpy = 252 / N
    sh = (m * tpy) / (sd * np.sqrt(tpy)) if sd > 0 else float("nan")
    cum = (1 + arr).prod() - 1
    return {
        "n": n, "mean_bps": m * 1e4, "t": t, "sharpe": sh,
        "ann_pct": m * tpy * 100, "cum_pct": cum * 100,
        "wr": float((arr > 0).mean() * 100),
        "n_long": int(sum(1 for s in sigs if s > 0)),
        "n_short": int(sum(1 for s in sigs if s < 0)),
        "pnl": arr, "sigs": np.array(sigs), "yrs": np.array(yrs),
        "fwds": np.array(fwds),
    }


def main() -> None:
    print(f"Loading XAUUSD daily ...")
    d = load_1d()
    print(f"  {len(d)} daily bars  {d.index[0].date()} -> {d.index[-1].date()}")
    print(f"  spot: {d['close'].iloc[0]:.0f} -> {d['close'].iloc[-1]:.0f}  "
          f"({(d['close'].iloc[-1] / d['close'].iloc[0] - 1) * 100:+.0f}% over sample)")

    for N in LOOKBACKS:
        print(f"\n{'='*70}\n  N = {N} days\n{'='*70}")
        ts = run_strategy(d, N, "tsmom")
        lg = run_strategy(d, N, "long")
        sh = run_strategy(d, N, "short")
        dt = run_strategy(d, N, "detrend")
        print(f"  {'strategy':12s}  {'n':>3s}  {'mean_bps':>9s}  {'t':>5s}  "
              f"{'Sharpe':>6s}  {'ann_%':>6s}  {'cum_%':>7s}  {'WR':>5s}")
        for label, r in [("TSMOM", ts), ("Long-only", lg),
                          ("Short-only", sh), ("Detrend", dt)]:
            if r.get("n", 0) < 2:
                print(f"  {label:12s}  insufficient")
                continue
            print(f"  {label:12s}  {r['n']:3d}  {r['mean_bps']:+9.2f}  "
                  f"{r['t']:+5.2f}  {r['sharpe']:+6.2f}  "
                  f"{r['ann_pct']:+6.1f}  {r['cum_pct']:+7.1f}  {r['wr']:5.1f}")

        # signal composition
        print(f"\n  Signal composition (TSMOM):  "
              f"{ts['n_long']} long / {ts['n_short']} short   "
              f"(long-bias = {ts['n_long']/ts['n']*100:.0f}%)")

        # per-year: did TSMOM flip correctly in down years?
        print(f"\n  Per-year TSMOM vs Long-only (did momentum catch the regime?):")
        print(f"   {'year':4s}  {'n':>3s}  {'sigs':>10s}  {'gold_%':>7s}  "
              f"{'tsmom_%':>8s}  {'long_%':>7s}  edge")
        years = sorted(set(ts["yrs"]))
        for yr in years:
            mask_t = ts["yrs"] == yr
            mask_l = lg["yrs"] == yr
            if mask_t.sum() == 0:
                continue
            longs = int((ts["sigs"][mask_t] > 0).sum())
            shorts = int((ts["sigs"][mask_t] < 0).sum())
            gold_ret = (1 + ts["fwds"][mask_t]).prod() - 1  # what gold did
            tsmom_ret = (1 + ts["pnl"][mask_t]).prod() - 1
            long_ret = (1 + lg["pnl"][mask_l]).prod() - 1
            edge = (tsmom_ret - long_ret) * 100
            print(f"   {yr:4d}  {mask_t.sum():3d}  "
                  f"{longs:>4d}L/{shorts:>2d}S  "
                  f"{gold_ret*100:+7.1f}  {tsmom_ret*100:+8.1f}  "
                  f"{long_ret*100:+7.1f}  {edge:+5.1f}")


if __name__ == "__main__":
    main()
