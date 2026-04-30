"""AUD/NZD Phase 3 step 1 — per-trade MAE/MFE and stop-loss sweep.

Phase 2 reported a 70% win rate / PF 3.5 OOS *with no stop-loss*. The
MaxDD figure was cumulative equity, not worst single-trade adverse
excursion. Before adding any filters, we need to know:

  1. How bad does a losing trade get before it (often) reverts? (MAE)
  2. How good do winners get before they pull back? (MFE)
  3. If we'd applied a fixed stop of N pips, would we have:
       - cut losers and improved PF, or
       - stopped out winners-in-disguise and killed the edge?

For each trade in phase2_trades.csv, walk the 1-min bars between the
entry timestamp (02:00 UTC bar close) and the exit timestamp (06:00 UTC
bar close). Track high/low against the trade direction.

  - SELL: MAE driven by max(high), MFE driven by min(low)
  - BUY : MAE driven by min(low),  MFE driven by max(high)

MAE/MFE are quoted in pips, signed positive (excursion magnitude).

Then sweep stops at 10/20/30/50/100/150 pips and replay the equity:
each trade either stops out at its MAE level (if MAE >= stop) or runs
to the original close-to-close net P&L. Cost model unchanged.

Usage:
  python -m v11.backtest.backtest_audnzd_phase3_mae_mfe
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
DEFAULT_CSV = Path(r"C:\nautilus0\data\1m_csv_fresh\audnzd_1m_tick.csv")
TRADES_CSV = Path(r"C:\ibkr_grok-_wing_agent\v11\backtest\audnzd_eda_output\phase2_trades.csv")
OUT_DIR = Path(r"C:\ibkr_grok-_wing_agent\v11\backtest\audnzd_eda_output")
STOP_LEVELS_PIPS = [10, 15, 20, 30, 50, 75, 100, 150]


def load_bars(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    df = df.set_index("timestamp")
    return df


def load_trades(trades_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(trades_csv, parse_dates=["entry_ts", "exit_ts"])
    return df


def compute_mae_mfe(bars: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """For each trade, find max adverse / favorable excursion in pips.

    Excursion is measured against entry_px (the actual fill price including
    half-spread). We use the bars *strictly after* the entry bar and *up to
    and including* the exit bar — those are the bars where intra-night
    movement could have hit a stop.
    """
    mae_pips = np.zeros(len(trades))
    mfe_pips = np.zeros(len(trades))
    bars_in_trade = np.zeros(len(trades), dtype=int)

    for i, row in enumerate(trades.itertuples(index=False)):
        # Bars strictly after the entry bar's timestamp, up to and including exit
        window = bars.loc[
            (bars.index > row.entry_ts) & (bars.index <= row.exit_ts)
        ]
        if len(window) == 0:
            mae_pips[i] = 0.0
            mfe_pips[i] = 0.0
            continue

        bars_in_trade[i] = len(window)
        hi = window["high"].max()
        lo = window["low"].min()

        if row.side == "SELL":
            adverse = (hi - row.entry_px) / PIP   # price up = loss for short
            favorable = (row.entry_px - lo) / PIP  # price down = gain for short
        else:  # BUY
            adverse = (row.entry_px - lo) / PIP
            favorable = (hi - row.entry_px) / PIP

        mae_pips[i] = max(adverse, 0.0)
        mfe_pips[i] = max(favorable, 0.0)

    out = trades.copy()
    out["mae_pips"] = mae_pips
    out["mfe_pips"] = mfe_pips
    out["bars_in_trade"] = bars_in_trade
    return out


def replay_with_stop(trades: pd.DataFrame, stop_pips: float) -> pd.DataFrame:
    """If MAE >= stop_pips, the trade stops out at -stop_pips gross.
    Otherwise it runs to its original close-to-close gross_pips.
    Spread cost is unchanged (round-trip, paid either way).
    """
    out = trades.copy()
    stopped = out["mae_pips"] >= stop_pips
    gross = np.where(stopped, -stop_pips, out["gross_pips"])
    out["stopped"] = stopped
    out["gross_with_stop"] = gross
    out["net_with_stop"] = gross - out["spread_cost_pips"]
    return out


def metrics(pnl: pd.Series, n_stopped: int = 0) -> dict:
    if len(pnl) == 0:
        return {"n": 0, "n_stopped": 0, "wr": float("nan"),
                "avg_r": float("nan"), "pf": float("nan"),
                "max_dd": float("nan"), "total": 0.0}
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    pf = wins / losses if losses > 0 else float("inf")
    eq = pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    return {
        "n": int(len(pnl)),
        "n_stopped": int(n_stopped),
        "wr": float((pnl > 0).mean() * 100),
        "avg_r": float(pnl.mean()),
        "pf": float(pf),
        "max_dd": float(dd),
        "total": float(pnl.sum()),
    }


def fmt(name: str, m: dict) -> str:
    return (f"  {name:>14s}   "
            f"n={m['n']:4d}   stopped={m['n_stopped']:3d}   "
            f"WR={m['wr']:5.1f}%   "
            f"AvgR={m['avg_r']:+6.2f}p   "
            f"PF={m['pf']:5.2f}   "
            f"MaxDD={m['max_dd']:+8.1f}p   "
            f"Total={m['total']:+8.1f}p")


def percentile_table(s: pd.Series, name: str) -> str:
    pct = [50, 75, 90, 95, 99, 100]
    vals = [s.quantile(p / 100) if p < 100 else s.max() for p in pct]
    parts = "  ".join(f"p{p}={v:7.1f}" for p, v in zip(pct, vals))
    return f"  {name:6s} mean={s.mean():.1f}  {parts}"


def main(csv_path: Path, trades_csv: Path) -> None:
    print(f"Loading bars from {csv_path} ...")
    bars = load_bars(csv_path)
    print(f"  {len(bars):,} bars indexed")

    print(f"Loading trades from {trades_csv} ...")
    trades = load_trades(trades_csv)
    print(f"  {len(trades)} trades")

    print("\nComputing MAE/MFE per trade ...")
    enriched = compute_mae_mfe(bars, trades)
    print(f"  median bars_in_trade = {int(enriched['bars_in_trade'].median())}")

    # Sanity: max bars_in_trade should be ~240 (4 hours of 1-min bars)
    print(f"  max bars_in_trade    = {int(enriched['bars_in_trade'].max())}")

    print("\nMAE / MFE distribution (pips):")
    print(percentile_table(enriched["mae_pips"], "MAE"))
    print(percentile_table(enriched["mfe_pips"], "MFE"))

    # Conditional: among losers (close-to-close net < 0), what was MAE?
    losers = enriched[enriched["net_pips"] < 0]
    winners = enriched[enriched["net_pips"] >= 0]
    print(f"\nMAE among CLOSE-TO-CLOSE LOSERS (n={len(losers)}):")
    print(percentile_table(losers["mae_pips"], "MAE"))
    print(f"MAE among CLOSE-TO-CLOSE WINNERS (n={len(winners)}):")
    print(percentile_table(winners["mae_pips"], "MAE"))

    print("\nStop-loss sweep")
    print("=" * 100)
    print(fmt("NO STOP", metrics(enriched["net_pips"], 0)))
    for stop in STOP_LEVELS_PIPS:
        replayed = replay_with_stop(enriched, stop)
        m = metrics(replayed["net_with_stop"], int(replayed["stopped"].sum()))
        print(fmt(f"stop={stop}p", m))

    print("\nStop-loss sweep, OOS only (entry < 2024)")
    print("=" * 100)
    oos = enriched[enriched["entry_ts"] < pd.Timestamp("2024-01-01", tz="UTC")]
    print(fmt("OOS NO STOP", metrics(oos["net_pips"], 0)))
    for stop in STOP_LEVELS_PIPS:
        rep = replay_with_stop(oos, stop)
        m = metrics(rep["net_with_stop"], int(rep["stopped"].sum()))
        print(fmt(f"OOS stop={stop}p", m))

    print("\nStop-loss sweep, IS only (entry >= 2024)")
    print("=" * 100)
    is_ = enriched[enriched["entry_ts"] >= pd.Timestamp("2024-01-01", tz="UTC")]
    print(fmt("IS NO STOP", metrics(is_["net_pips"], 0)))
    for stop in STOP_LEVELS_PIPS:
        rep = replay_with_stop(is_, stop)
        m = metrics(rep["net_with_stop"], int(rep["stopped"].sum()))
        print(fmt(f"IS stop={stop}p", m))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(OUT_DIR / "phase3_mae_mfe.csv", index=False)
    print(f"\nWrote {OUT_DIR / 'phase3_mae_mfe.csv'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--trades", type=Path, default=TRADES_CSV)
    args = p.parse_args()
    main(args.csv, args.trades)
