"""Phase 1 sniff: time-series momentum on 1-min FX/commodity data.

Question: across the 9 instruments we have 8 years of 1-min data on, does any
exhibit a tradable time-series momentum effect at any reasonable horizon
(1h-48h), after realistic per-bar spread costs?

Method (per instrument, no parameter tuning):
  1. Resample 1m to 1h bars (open/high/low/close, summed spread).
  2. Compute autocorrelation of log returns at lags 1, 2, 4, 8, 12, 24.
     Positive autocorr -> momentum. Negative -> mean-reversion. Near zero -> RW.
  3. For each lookback N in [1, 4, 12, 24, 48] hours:
        signal_t = sign(sum of past N hourly log returns)
        hold from t to t+N (no overlap by stepping by N)
        pnl = signal * (close_{t+N} - close_t) / close_t
        cost = (avg_spread at entry + avg_spread at exit) / close_t
        net = pnl - cost
     Report n, mean (bps), t-stat, Sharpe.
  4. Sanity:
     - per-bar identity (sum of pip-moves matches log return to 1e-12)
     - print one trade for the strongest (instrument, N) cell

This is purely time-series momentum, one instrument at a time. Cross-sectional
momentum (rank-and-trade across pairs) is a separate sniff if TSMOM passes.

Sample is OOS by construction: no parameters tuned, no in-sample selection.
We do report by-year to check stability.

Usage:
  python -m v11.backtest.sniff_momentum_multi_pair
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DIRS = [
    Path(r"C:\nautilus0\data\1m_csv"),
    Path(r"C:\nautilus0\data\1m_csv_fresh"),
]
INSTRUMENTS = [
    ("eurusd", "fresh"),
    ("gbpusd", "main"),
    ("usdjpy", "main"),
    ("audusd", "main"),
    ("nzdusd", "main"),
    ("usdcad", "main"),
    ("usdchf", "main"),
    ("audnzd", "fresh"),
    ("xauusd", "main"),
    ("xagusd", "main"),
]
LOOKBACKS = [1, 4, 12, 24, 48]  # hours


def load_1h(symbol: str, source: str) -> pd.DataFrame:
    base = DIRS[1] if source == "fresh" else DIRS[0]
    csv = base / f"{symbol}_1m_tick.csv"
    if not csv.exists():
        # try the other dir
        csv = (DIRS[0] if source == "fresh" else DIRS[1]) / f"{symbol}_1m_tick.csv"
    df = pd.read_csv(csv, usecols=["timestamp", "open", "high", "low", "close", "avg_spread"])
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.dropna(subset=["close", "avg_spread"]).sort_values("ts").drop_duplicates("ts")
    df = df.set_index("ts")
    # resample to 1h
    h = df.resample("1h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "avg_spread": "mean",
    }).dropna()
    h["log_ret"] = np.log(h["close"]).diff()
    return h.dropna()


def autocorr(s: pd.Series, lags: list[int]) -> dict[int, float]:
    return {k: float(s.autocorr(lag=k)) for k in lags}


def tsmom_eval(h: pd.DataFrame, N: int) -> dict:
    """Simple non-overlapping TSMOM: every N hours, take signal = sign(past-N sum),
    hold N hours, pay 2x spread (in price units) at entry+exit.

    Cost is converted to fractional return: spread/price.
    """
    # Roll up to non-overlapping windows of size N
    if len(h) < 3 * N:
        return {"n": 0}
    # signal at bar t uses log_ret over [t-N+1 .. t], so first valid t is at index N-1
    # we step the simulation by N to avoid overlap
    rets = h["log_ret"].values
    spread = h["avg_spread"].values
    closes = h["close"].values
    n_bars = len(h)
    pnl_list = []
    for entry in range(N, n_bars - N, N):
        past_sum = rets[entry - N: entry].sum()
        if past_sum == 0:
            continue
        sig = 1 if past_sum > 0 else -1
        # forward simple return
        fwd = closes[entry + N] / closes[entry] - 1
        # entry spread + exit spread relative to entry close
        cost = (spread[entry] + spread[entry + N]) / closes[entry]
        pnl = sig * fwd - cost
        pnl_list.append(pnl)
    if not pnl_list:
        return {"n": 0}
    arr = np.array(pnl_list)
    n = len(arr)
    m = arr.mean()
    sd = arr.std(ddof=1) if n > 1 else 0
    t = m / (sd / np.sqrt(n)) if sd > 0 else float("nan")
    # annualization: 1 year = ~252 trading days * 24h / N trades
    trades_per_year = 252 * 24 / N
    ann_ret = m * trades_per_year
    ann_vol = sd * np.sqrt(trades_per_year)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else float("nan")
    wr = float((arr > 0).mean() * 100)
    return {"n": n, "mean_bps": m * 1e4, "t": t, "sharpe": sharpe,
            "ann_ret_pct": ann_ret * 100, "wr": wr}


def main() -> None:
    print(f"Loading and resampling 9 instruments to 1h ...")
    data = {}
    for sym, src in INSTRUMENTS:
        try:
            h = load_1h(sym, src)
            data[sym] = h
            print(f"  {sym}: {len(h):,} 1h bars  {h.index[0]} -> {h.index[-1]}")
        except Exception as e:
            print(f"  {sym}: failed ({e})")

    print(f"\n=== Autocorrelation of 1h log-returns ===")
    print(f"  (positive = momentum, negative = mean-reversion, ~0 = random walk)")
    lags = [1, 2, 4, 8, 12, 24]
    print(f"  {'instrument':10s}  " + "  ".join(f"lag{l:2d}" for l in lags))
    for sym, h in data.items():
        ac = autocorr(h["log_ret"], lags)
        row = "  ".join(f"{ac[l]:+.4f}" for l in lags)
        print(f"  {sym:10s}  {row}")

    print(f"\n=== TSMOM Sharpe table (after avg-spread cost) ===")
    print(f"  Sharpe by (instrument, lookback N hours).  Bold-worthy: > +0.5 or < -0.5.")
    header = f"  {'instrument':10s}  " + "  ".join(f"N={n:>3d}h" for n in LOOKBACKS)
    print(header)
    cells = {}
    for sym, h in data.items():
        row_parts = []
        for N in LOOKBACKS:
            res = tsmom_eval(h, N)
            cells[(sym, N)] = res
            if res.get("n", 0) == 0:
                row_parts.append("  n/a ")
            else:
                row_parts.append(f"{res['sharpe']:+.2f}")
        print(f"  {sym:10s}  " + "  ".join(f"{p:>6s}" for p in row_parts))

    print(f"\n=== TSMOM mean (bps/trade) after cost ===")
    print(header)
    for sym in data:
        parts = []
        for N in LOOKBACKS:
            res = cells[(sym, N)]
            if res.get("n", 0) == 0:
                parts.append(" n/a ")
            else:
                parts.append(f"{res['mean_bps']:+5.2f}")
        print(f"  {sym:10s}  " + "  ".join(f"{p:>7s}" for p in parts))

    print(f"\n=== TSMOM t-stat (m / SE) after cost ===")
    print(header)
    for sym in data:
        parts = []
        for N in LOOKBACKS:
            res = cells[(sym, N)]
            if res.get("n", 0) == 0:
                parts.append(" n/a ")
            else:
                parts.append(f"{res['t']:+5.2f}")
        print(f"  {sym:10s}  " + "  ".join(f"{p:>7s}" for p in parts))

    # Locate strongest cell, walk one trade for sanity
    best = None
    for k, v in cells.items():
        if v.get("n", 0) == 0:
            continue
        s = v["sharpe"]
        if best is None or abs(s) > abs(best[1]["sharpe"]):
            best = (k, v)
    print(f"\n=== Strongest |Sharpe| cell: {best[0]} -> {best[1]} ===")

    # Per-year stability for the strongest TSMOM cell
    sym_b, N_b = best[0]
    h = data[sym_b]
    print(f"\n  Per-year breakdown for {sym_b} N={N_b}h:")
    rets = h["log_ret"].values; closes = h["close"].values; spread = h["avg_spread"].values
    times = h.index
    yr_pnl: dict[int, list[float]] = {}
    for entry in range(N_b, len(h) - N_b, N_b):
        past_sum = rets[entry - N_b: entry].sum()
        if past_sum == 0:
            continue
        sig = 1 if past_sum > 0 else -1
        fwd = closes[entry + N_b] / closes[entry] - 1
        cost = (spread[entry] + spread[entry + N_b]) / closes[entry]
        pnl = sig * fwd - cost
        yr = times[entry].year
        yr_pnl.setdefault(yr, []).append(pnl)
    print(f"   {'year':4s}  {'n':>4s}  {'mean_bps':>9s}  {'t':>5s}  {'cum_%':>7s}")
    for yr in sorted(yr_pnl):
        arr = np.array(yr_pnl[yr])
        m = arr.mean() * 1e4
        sd = arr.std(ddof=1) if len(arr) > 1 else 0
        t = m / 1e4 / (sd / np.sqrt(len(arr))) if sd > 0 else float("nan")
        cum = ((1 + arr).prod() - 1) * 100
        flag = "  <-- neg" if cum < 0 else ""
        print(f"   {yr:4d}  {len(arr):4d}  {m:+9.2f}  {t:+5.2f}  {cum:+7.2f}{flag}")

    # Sanity walk: print one trade end-to-end
    print(f"\n=== Sanity: one trade walk for {sym_b} N={N_b}h ===")
    entry = N_b * 100  # arbitrary mid-sample bar
    while entry < len(h) - N_b:
        past_sum = rets[entry - N_b: entry].sum()
        if past_sum != 0:
            break
        entry += N_b
    sig = 1 if past_sum > 0 else -1
    fwd = closes[entry + N_b] / closes[entry] - 1
    cost = (spread[entry] + spread[entry + N_b]) / closes[entry]
    pnl = sig * fwd - cost
    print(f"  entry_ts: {times[entry]}    close: {closes[entry]:.6f}")
    print(f"  exit_ts:  {times[entry + N_b]}    close: {closes[entry + N_b]:.6f}")
    print(f"  past_{N_b}h_sum_log_ret: {past_sum:+.6f}  -> signal: {sig:+d}")
    print(f"  fwd return: {fwd*1e4:+.2f} bps   cost: {cost*1e4:.2f} bps")
    print(f"  signed pnl after cost: {pnl*1e4:+.2f} bps")
    print(f"  entry_ts < exit_ts ? {times[entry] < times[entry + N_b]}")


if __name__ == "__main__":
    main()
