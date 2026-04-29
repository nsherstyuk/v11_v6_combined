"""AUD/NZD Phase 1 EDA — does the regime split the hypothesis requires actually exist?

Hypothesis (from docs/journal/2026-04-29_audnzd_periodic_reversion_idea.md):
  During 22:00–06:00 UTC, AUDNZD shows lower drift and higher mean-reversion
  than during 06:00–22:00 UTC, with spread cost that does not eat the edge.

This script does NOT trade anything. It only describes the data:
  1. Hourly UTC profile of mean |return| (proxy for activity)
  2. Hourly UTC profile of mean signed return (proxy for drift)
  3. 22:00–06:00 UTC range distribution (per night) — year and DOW breakdowns
  4. Hourly UTC profile of realized spread (in pips)
  5. Coverage check — number of trading nights, skipped weeks

Pip = 0.0001 for AUDNZD (price quoted to 5 decimals; 5th is a pipette).

Usage:
  python -m v11.backtest.investigate_audnzd_eda
  python -m v11.backtest.investigate_audnzd_eda --csv <path>
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


PIP = 1e-4
DEFAULT_CSV = Path(r"C:\nautilus0\data\1m_csv_fresh\audnzd_1m_tick.csv")


def load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    df["hour_utc"] = df["timestamp"].dt.hour
    df["date_utc"] = df["timestamp"].dt.date
    df["dow"] = df["timestamp"].dt.dayofweek  # 0=Mon..6=Sun
    df["year"] = df["timestamp"].dt.year
    df["ret"] = df["close"] - df["open"]              # price units
    df["abs_ret"] = df["ret"].abs()
    df["spread_pips"] = df["avg_spread"] / PIP
    return df


def hourly_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Mean |return|, mean signed return, mean spread, by UTC hour."""
    g = df.groupby("hour_utc")
    out = pd.DataFrame({
        "n_bars": g.size(),
        "abs_ret_pips": g["abs_ret"].mean() / PIP,
        "signed_ret_pips": g["ret"].mean() / PIP,
        "spread_pips_mean": g["spread_pips"].mean(),
        "spread_pips_p50": g["spread_pips"].median(),
        "spread_pips_p95": g["spread_pips"].quantile(0.95),
    })
    return out.round(3)


def overnight_session(df: pd.DataFrame, start_h: int = 22, end_h: int = 6) -> pd.DataFrame:
    """Per-night stats for the 22:00 UTC → 06:00 UTC window.

    Each "night" is anchored to the date at 22:00 UTC start.
    Wraps midnight, so the window is [22:00 of day D, 06:00 of day D+1).
    """
    ts = df["timestamp"]
    in_window = (ts.dt.hour >= start_h) | (ts.dt.hour < end_h)
    sub = df[in_window].copy()
    # Anchor each bar to the night's start date
    sub["night"] = (sub["timestamp"] - pd.Timedelta(hours=end_h)).dt.date
    g = sub.groupby("night")
    nights = pd.DataFrame({
        "n_bars": g.size(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "open": g["open"].first(),
        "close": g["close"].last(),
        "spread_p50_pips": g["spread_pips"].median(),
        "spread_p95_pips": g["spread_pips"].quantile(0.95),
    })
    nights["range_pips"] = (nights["high"] - nights["low"]) / PIP
    nights["session_ret_pips"] = (nights["close"] - nights["open"]) / PIP
    nights["session_ret_abs_pips"] = nights["session_ret_pips"].abs()
    nights = nights[nights["n_bars"] >= 60]  # need at least 1h of data to count
    nights.index = pd.to_datetime(nights.index)
    nights["dow"] = nights.index.dayofweek
    nights["year"] = nights.index.year
    return nights


def by_year(nights: pd.DataFrame) -> pd.DataFrame:
    g = nights.groupby("year")
    out = pd.DataFrame({
        "n_nights": g.size(),
        "range_pips_mean": g["range_pips"].mean(),
        "range_pips_p25": g["range_pips"].quantile(0.25),
        "range_pips_p50": g["range_pips"].median(),
        "range_pips_p75": g["range_pips"].quantile(0.75),
        "range_pips_p95": g["range_pips"].quantile(0.95),
        "ret_mean_pips": g["session_ret_pips"].mean(),
        "ret_abs_mean_pips": g["session_ret_abs_pips"].mean(),
    })
    return out.round(2)


def by_dow(nights: pd.DataFrame) -> pd.DataFrame:
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    g = nights.groupby("dow")
    out = pd.DataFrame({
        "n_nights": g.size(),
        "range_pips_mean": g["range_pips"].mean(),
        "range_pips_p50": g["range_pips"].median(),
        "ret_mean_pips": g["session_ret_pips"].mean(),
        "ret_abs_mean_pips": g["session_ret_abs_pips"].mean(),
    })
    out.index = [dow_names[i] for i in out.index]
    return out.round(2)


def coverage(df: pd.DataFrame, nights: pd.DataFrame) -> dict:
    first = df["timestamp"].min()
    last = df["timestamp"].max()
    expected_weekdays = pd.bdate_range(first.date(), last.date())
    night_dates = pd.DatetimeIndex(nights.index)
    missing = sorted(set(expected_weekdays.date) - set(night_dates.date))
    return {
        "first_bar": first,
        "last_bar": last,
        "n_bars": len(df),
        "n_nights": len(nights),
        "expected_weekdays": len(expected_weekdays),
        "missing_count": len(missing),
        "missing_pct": 100 * len(missing) / max(len(expected_weekdays), 1),
        "first_missing": missing[:5],
        "last_missing": missing[-5:],
    }


def fmt_table(df: pd.DataFrame, title: str) -> str:
    return f"\n{title}\n{'=' * len(title)}\n{df.to_string()}\n"


def main(csv_path: Path) -> None:
    print(f"Loading {csv_path} ...")
    df = load(csv_path)
    print(f"  {len(df):,} bars, {df['timestamp'].min()} to {df['timestamp'].max()}")

    hp = hourly_profile(df)
    nights = overnight_session(df, start_h=22, end_h=6)
    yr = by_year(nights)
    dow = by_dow(nights)
    cov = coverage(df, nights)

    print(fmt_table(hp, "1. Hourly UTC profile (all bars)"))
    print(fmt_table(yr, "2. Overnight 22-06 UTC range, per year"))
    print(fmt_table(dow, "3. Overnight 22-06 UTC range, per day-of-week (anchor=start date)"))

    print("\n4. Coverage check")
    print("=" * 17)
    for k, v in cov.items():
        print(f"  {k}: {v}")

    # Persist to disk for follow-up
    out_dir = Path(r"C:\ibkr_grok-_wing_agent\v11\backtest\audnzd_eda_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    hp.to_csv(out_dir / "hourly_profile.csv")
    yr.to_csv(out_dir / "by_year.csv")
    dow.to_csv(out_dir / "by_dow.csv")
    nights.to_csv(out_dir / "nights.csv")
    print(f"\nWrote tables to {out_dir}")

    # Quick pass/fail flag against the hypothesis
    print("\n5. Hypothesis quick-check")
    print("=" * 25)
    quiet = hp.loc[[22, 23, 0, 1, 2, 3, 4, 5], "abs_ret_pips"].mean()
    busy = hp.loc[[7, 8, 9, 13, 14, 15], "abs_ret_pips"].mean()
    print(f"  Mean |ret| 22-06 UTC (quiet):   {quiet:.3f} pips/min")
    print(f"  Mean |ret| London+NY open:      {busy:.3f} pips/min")
    print(f"  Ratio quiet/busy:               {quiet / busy:.2f}  (lower = more compression)")
    spread_quiet = hp.loc[[22, 23, 0, 1, 2, 3, 4, 5], "spread_pips_p50"].mean()
    print(f"  Median spread 22-06 UTC:        {spread_quiet:.2f} pips")
    print(f"  Verdict: if quiet/busy < 0.7 AND spread < 2 pips, hypothesis Phase 1 PASSES")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = p.parse_args()
    main(args.csv)
