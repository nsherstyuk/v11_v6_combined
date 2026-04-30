"""AUD/NZD Phase 2 — naive baseline backtest of the 22-06 UTC reversion edge.

Phase 1.5 established that on close-to-close arithmetic, the AUD/NZD overnight
window mean-reverts strongly (corr -0.55, hit rate 66%, +8 pips/night net).
Phase 2 turns that into a single fixed rule and prices it with realized
per-bar spread instead of a flat 4-pip assumption — no grid, no filters.

Rule (no parameters tuned):
  - Compute first-half (22:00-02:00 UTC) midpoint = (max(high)+min(low))/2.
  - At the 02:00 UTC bar (last bar of first half), measure dev = close - mid.
  - If |dev| < 5 pips: skip the night (signal too weak per Phase 1.5 strat).
  - If dev > 0: SELL at bid (mid - avg_spread/2). Direction = revert toward mid.
  - If dev < 0: BUY at ask (mid + avg_spread/2).
  - Exit at the 06:00 UTC bar close, opposite side.
  - Per-trade cost is the realized spread at entry + at exit.

Splits:
  - OOS: 2018-01 to 2023-12
  - IS:  2024-01 to present (was unseen during Phase 1/1.5)

Outputs per split: N, WR, AvgR (in pips), PF, MaxDD (pips), equity curve CSV.

Usage:
  python -m v11.backtest.backtest_audnzd_phase2
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
DEFAULT_CSV = Path(r"C:\nautilus0\data\1m_csv_fresh\audnzd_1m_tick.csv")
OUT_DIR = Path(r"C:\ibkr_grok-_wing_agent\v11\backtest\audnzd_eda_output")
ENTRY_THRESHOLD_PIPS = 5.0
OOS_END = pd.Timestamp("2024-01-01", tz="UTC")


def load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    return df


def build_trades(df: pd.DataFrame) -> pd.DataFrame:
    """For each night, build one candidate trade row.

    Row schema:
      night, entry_ts, exit_ts, side, entry_mid, exit_mid,
      entry_spread, exit_spread, dev_pips, gross_pips, net_pips, traded
    """
    h = df["timestamp"].dt.hour
    first_half = df[(h >= 22) | (h < 2)].copy()
    second_half = df[(h >= 2) & (h < 6)].copy()

    # Offset must be >= 6h so the second-half bars (02:00-05:59 of D+1) tag
    # back to D, matching first-half bars from 22:00 D - 01:59 D+1.
    first_half["night"] = (first_half["timestamp"] - pd.Timedelta(hours=6)).dt.date
    second_half["night"] = (second_half["timestamp"] - pd.Timedelta(hours=6)).dt.date

    g1 = first_half.groupby("night")
    g2 = second_half.groupby("night")

    # Entry bar = last bar of first half (closest to 02:00)
    entry_idx = g1["timestamp"].idxmax()
    exit_idx = g2["timestamp"].idxmax()

    entry_bar = first_half.loc[entry_idx].set_index("night")
    exit_bar = second_half.loc[exit_idx].set_index("night")

    nights = pd.DataFrame({
        "n_bars_h1": g1.size(),
        "n_bars_h2": g2.size(),
        "h1_high": g1["high"].max(),
        "h1_low": g1["low"].min(),
    })
    nights["entry_ts"] = entry_bar["timestamp"]
    nights["entry_mid"] = entry_bar["close"]
    nights["entry_spread"] = entry_bar["avg_spread"]
    nights["exit_ts"] = exit_bar["timestamp"]
    nights["exit_mid"] = exit_bar["close"]
    nights["exit_spread"] = exit_bar["avg_spread"]

    nights = nights.dropna()
    nights = nights[(nights["n_bars_h1"] >= 120) & (nights["n_bars_h2"] >= 120)]

    nights["midpoint"] = (nights["h1_high"] + nights["h1_low"]) / 2.0
    nights["dev_pips"] = (nights["entry_mid"] - nights["midpoint"]) / PIP

    # Side: sell when above mid (dev > 0), buy when below
    nights["side"] = np.where(nights["dev_pips"] > 0, "SELL", "BUY")
    nights["traded"] = nights["dev_pips"].abs() >= ENTRY_THRESHOLD_PIPS

    # Realistic execution: cross the spread at both ends
    # SELL: enter at bid (mid - sp/2), exit (buy back) at ask (mid + sp/2)
    # BUY:  enter at ask (mid + sp/2), exit (sell)   at bid (mid - sp/2)
    half_sp_in = nights["entry_spread"] / 2.0
    half_sp_out = nights["exit_spread"] / 2.0

    sell = nights["side"] == "SELL"
    entry_px = np.where(sell, nights["entry_mid"] - half_sp_in,
                              nights["entry_mid"] + half_sp_in)
    exit_px = np.where(sell, nights["exit_mid"] + half_sp_out,
                             nights["exit_mid"] - half_sp_out)
    nights["entry_px"] = entry_px
    nights["exit_px"] = exit_px

    raw_pips = (nights["exit_mid"] - nights["entry_mid"]) / PIP
    nights["gross_pips"] = np.where(sell, -raw_pips, raw_pips)
    spread_cost_pips = (nights["entry_spread"] + nights["exit_spread"]) / PIP
    nights["spread_cost_pips"] = spread_cost_pips
    nights["net_pips"] = nights["gross_pips"] - spread_cost_pips

    nights.index = pd.to_datetime(nights.index)
    nights["year"] = nights.index.year
    return nights


def metrics(trades: pd.DataFrame) -> dict:
    if len(trades) == 0:
        return {"n": 0, "wr": float("nan"), "avg_r_pips": float("nan"),
                "pf": float("nan"), "max_dd_pips": float("nan"),
                "total_pips": 0.0, "median_spread_pips": float("nan")}
    pnl = trades["net_pips"]
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    pf = wins / losses if losses > 0 else float("inf")
    eq = pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    return {
        "n": int(len(trades)),
        "wr": float((pnl > 0).mean() * 100),
        "avg_r_pips": float(pnl.mean()),
        "pf": float(pf),
        "max_dd_pips": float(dd),
        "total_pips": float(pnl.sum()),
        "median_spread_pips": float(trades["spread_cost_pips"].median()),
    }


def fmt_metrics(name: str, m: dict) -> str:
    return (f"  {name:8s}  n={m['n']:4d}   "
            f"WR={m['wr']:5.1f}%   "
            f"AvgR={m['avg_r_pips']:+6.2f} pips   "
            f"PF={m['pf']:5.2f}   "
            f"MaxDD={m['max_dd_pips']:+7.1f} pips   "
            f"Total={m['total_pips']:+8.1f} pips   "
            f"MedSpread={m['median_spread_pips']:.2f} pips")


def main(csv_path: Path) -> None:
    print(f"Loading {csv_path} ...")
    df = load(csv_path)
    print(f"  {len(df):,} bars, {df['timestamp'].min()} to {df['timestamp'].max()}")

    nights = build_trades(df)
    traded = nights[nights["traded"]].copy()
    print(f"\n  {len(nights)} candidate nights, {len(traded)} trades after "
          f">={ENTRY_THRESHOLD_PIPS:.0f}-pip threshold "
          f"({100*len(traded)/max(len(nights),1):.1f}% participation)")

    oos = traded[traded["entry_ts"] < OOS_END]
    is_ = traded[traded["entry_ts"] >= OOS_END]

    print(f"\nResults (Phase 2 baseline, no filters)")
    print("=" * 60)
    print(fmt_metrics("ALL", metrics(traded)))
    print(fmt_metrics("OOS", metrics(oos)))
    print(fmt_metrics("IS", metrics(is_)))

    print(f"\nBy year")
    print("=" * 60)
    for yr, g in traded.groupby("year"):
        print(fmt_metrics(str(yr), metrics(g)))

    print(f"\nBy side")
    print("=" * 60)
    for side, g in traded.groupby("side"):
        print(fmt_metrics(side, metrics(g)))

    print(f"\nGate check (Phase 3 only if both):")
    oos_m = metrics(oos)
    print(f"  OOS AvgR > 0      : {'PASS' if oos_m['avg_r_pips'] > 0 else 'FAIL'} ({oos_m['avg_r_pips']:+.2f})")
    print(f"  OOS PF   > 1.30   : {'PASS' if oos_m['pf'] > 1.30 else 'FAIL'} ({oos_m['pf']:.2f})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    traded.to_csv(OUT_DIR / "phase2_trades.csv")
    print(f"\nWrote {OUT_DIR / 'phase2_trades.csv'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = p.parse_args()
    main(args.csv)
