"""AUD/NZD Phase 1.5 — direct mean-reversion test.

Phase 1 tested raw activity (does the quiet zone exist?) — answer was weak.
Phase 1.5 tests the *actual* hypothesis: does price systematically return
toward the session midpoint?

Test design:
  - First half:  22:00 UTC to 02:00 UTC (4 hours)
  - Midpoint:    (max(high) + min(low)) / 2 over the first half
  - p02:         close at 02:00 UTC (last bar of first half)
  - p06:         close at 06:00 UTC (last bar of second half)
  - dev_02:      p02 - midpoint           (how far the rubber band is stretched)
  - move_06:     p06 - p02                 (what happens next)

If mean reversion exists:
  - Correlation(dev_02, move_06) should be strongly NEGATIVE
  - When dev_02 > 0, mean(move_06) should be < 0
  - When dev_02 < 0, mean(move_06) should be > 0
  - % of nights where price moves *toward* midpoint > 55% (after spread cost)

Usage:
  python -m v11.backtest.investigate_audnzd_reversion
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PIP = 1e-4
DEFAULT_CSV = Path(r"C:\nautilus0\data\1m_csv_fresh\audnzd_1m_tick.csv")
SPREAD_COST_PIPS = 4.0  # round-trip: ~2 pip spread × 2 (entry + exit)


def load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")
    return df


def build_nights(df: pd.DataFrame) -> pd.DataFrame:
    """For each night, compute first-half stats and second-half outcome."""
    ts = df["timestamp"]
    h = ts.dt.hour

    first_half = df[(h >= 22) | (h < 2)].copy()
    second_half = df[(h >= 2) & (h < 6)].copy()

    # Anchor each bar to the night's start date (the 22:00 anchor day)
    first_half["night"] = (first_half["timestamp"] - pd.Timedelta(hours=2)).dt.date
    second_half["night"] = (second_half["timestamp"] - pd.Timedelta(hours=2)).dt.date

    g1 = first_half.groupby("night")
    g2 = second_half.groupby("night")

    nights = pd.DataFrame({
        "n_bars_h1": g1.size(),
        "h1_high": g1["high"].max(),
        "h1_low": g1["low"].min(),
        "h1_open": g1["open"].first(),
        "p02": g1["close"].last(),  # last bar of first half = closest to 02:00 UTC
        "h1_spread_p50": g1["avg_spread"].median(),
    })
    nights["n_bars_h2"] = g2.size()
    nights["p06"] = g2["close"].last()
    nights["h2_spread_p50"] = g2["avg_spread"].median()

    nights = nights.dropna()  # need both halves
    nights = nights[(nights["n_bars_h1"] >= 120) & (nights["n_bars_h2"] >= 120)]

    nights["midpoint"] = (nights["h1_high"] + nights["h1_low"]) / 2.0
    nights["range_h1_pips"] = (nights["h1_high"] - nights["h1_low"]) / PIP
    nights["dev_02_pips"] = (nights["p02"] - nights["midpoint"]) / PIP
    nights["move_06_pips"] = (nights["p06"] - nights["p02"]) / PIP

    nights.index = pd.to_datetime(nights.index)
    nights["year"] = nights.index.year
    return nights


def test_reversion(nights: pd.DataFrame) -> None:
    """Run the mean-reversion tests and print results."""

    # 1. Overall correlation between deviation and subsequent move
    corr = nights["dev_02_pips"].corr(nights["move_06_pips"])
    n = len(nights)
    print(f"\n1. Correlation test (n={n})")
    print(f"   Pearson(dev_02, move_06) = {corr:+.4f}")
    print(f"   (negative = reversion. Need < -0.10 for tradeable, < -0.20 strong)")

    # 2. Conditional means by deviation sign
    pos = nights[nights["dev_02_pips"] > 0]
    neg = nights[nights["dev_02_pips"] < 0]
    print(f"\n2. Conditional means")
    print(f"   When dev_02 > 0 (price above mid):  n={len(pos):4d}   mean(move_06) = {pos['move_06_pips'].mean():+.2f} pips")
    print(f"   When dev_02 < 0 (price below mid):  n={len(neg):4d}   mean(move_06) = {neg['move_06_pips'].mean():+.2f} pips")
    print(f"   (above-mid should be NEGATIVE, below-mid POSITIVE if reversion)")

    # 3. Hit rate: how often does price move toward the midpoint?
    nights = nights.copy()
    nights["moved_toward"] = (
        ((nights["dev_02_pips"] > 0) & (nights["move_06_pips"] < 0)) |
        ((nights["dev_02_pips"] < 0) & (nights["move_06_pips"] > 0))
    )
    nights["moved_toward_strict"] = nights["moved_toward"].astype(int)
    hit_rate = nights["moved_toward_strict"].mean()
    print(f"\n3. Hit rate")
    print(f"   % of nights price moved toward midpoint: {hit_rate*100:.1f}%")
    print(f"   (need > 55% to be tradeable after cost; > 50% is just noise)")

    # 4. Stratify by deviation magnitude — does reversion strengthen with stretch?
    nights["abs_dev"] = nights["dev_02_pips"].abs()
    bins = [0, 5, 10, 15, 20, 30, 50, np.inf]
    labels = ["0-5", "5-10", "10-15", "15-20", "20-30", "30-50", "50+"]
    nights["dev_bin"] = pd.cut(nights["abs_dev"], bins=bins, labels=labels)
    by_bin = nights.groupby("dev_bin", observed=True).apply(
        lambda g: pd.Series({
            "n": len(g),
            "mean_move_pips": g["move_06_pips"].mean(),
            # signed expected reversion: how much, on average, did we move toward mid?
            # toward-mid is move_06 with sign opposite to dev_02
            "mean_revert_pips": (-np.sign(g["dev_02_pips"]) * g["move_06_pips"]).mean(),
            "hit_rate": g["moved_toward_strict"].mean() * 100,
        })
    )
    print(f"\n4. Reversion stratified by stretch magnitude")
    print(by_bin.round(2).to_string())
    print(f"   mean_revert_pips = expected pips of reversion (positive = good)")
    print(f"   For tradability: mean_revert_pips > {SPREAD_COST_PIPS:.0f} (round-trip spread)")

    # 5. Per-year stability — is the (if any) edge consistent?
    print(f"\n5. Year-by-year")
    yearly = nights.groupby("year").apply(
        lambda g: pd.Series({
            "n": len(g),
            "corr": g["dev_02_pips"].corr(g["move_06_pips"]),
            "hit_rate": g["moved_toward_strict"].mean() * 100,
            "mean_revert_pips": (-np.sign(g["dev_02_pips"]) * g["move_06_pips"]).mean(),
        })
    ).round(3)
    print(yearly.to_string())

    # 6. Net-of-cost hit rate — strict version
    nights["net_revert_pips"] = (-np.sign(nights["dev_02_pips"]) * nights["move_06_pips"]) - SPREAD_COST_PIPS
    profitable = (nights["net_revert_pips"] > 0).mean() * 100
    mean_net = nights["net_revert_pips"].mean()
    print(f"\n6. After {SPREAD_COST_PIPS:.0f}-pip round-trip cost")
    print(f"   % of nights net-profitable: {profitable:.1f}%")
    print(f"   Mean net result per night:  {mean_net:+.2f} pips")

    # 7. Final verdict
    print(f"\n7. Verdict")
    print(f"   Pass criteria (all required):")
    print(f"     [{'PASS' if corr < -0.10 else 'FAIL'}]  corr < -0.10                  (got {corr:+.4f})")
    print(f"     [{'PASS' if hit_rate > 0.55 else 'FAIL'}]  hit_rate > 55%                (got {hit_rate*100:.1f}%)")
    print(f"     [{'PASS' if mean_net > 0 else 'FAIL'}]  mean_net > 0 after cost       (got {mean_net:+.2f} pips)")


def main(csv_path: Path) -> None:
    print(f"Loading {csv_path} ...")
    df = load(csv_path)
    print(f"  {len(df):,} bars, {df['timestamp'].min()} to {df['timestamp'].max()}")
    nights = build_nights(df)
    print(f"  {len(nights)} nights with sufficient coverage")
    test_reversion(nights)

    out_dir = Path(r"C:\ibkr_grok-_wing_agent\v11\backtest\audnzd_eda_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    nights.to_csv(out_dir / "reversion_nights.csv")
    print(f"\nWrote {out_dir / 'reversion_nights.csv'}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = p.parse_args()
    main(args.csv)
