"""GBPCHF SMA-cross — sub-hypothesis (b): trend-flip filter.

Pre-registration:
    Hypothesis: cross events where bars_since_last_cross >= 24
    (~2h of one-side dwelling) isolate genuine trend reversals from
    chop-zone re-crossings. The filtered set should differ from the
    unconditional ~46% continuation rate.

    Two-tailed: we report whether the filtered rate is meaningfully
    different (either direction). No PnL simulation in this phase —
    descriptive only.

    Threshold 24 bars is chosen ONCE, in advance, as a round 2-hour
    window. NOT tuned by trying other values.

Run:
    python -m v11.backtest.gbpchf_sma_cross_trendflip
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
CROSSES_CSV = ROOT / "v11" / "backtest" / "results" / "gbpchf_sma_crosses.csv"

TREND_FLIP_BARS = 24  # ~2h of dwelling on one side before the cross


def _pip(x):
    return f"{x * 10000:.2f}"


def _q(s, q):
    return s.quantile(q)


def _compare(unfiltered: pd.DataFrame, filtered: pd.DataFrame) -> None:
    """Side-by-side: unconditional vs trend-flip-filtered."""
    print("=" * 78)
    print(f"  GBPCHF 5-min SMA50 cross — sub-hypothesis (b)")
    print(f"  Trend-flip filter: bars_since_last_cross >= "
          f"{TREND_FLIP_BARS} (~2h)")
    print("=" * 78)

    print(f"  Unconditional set:  N = {len(unfiltered):>6,}")
    print(f"  Trend-flip subset:  N = {len(filtered):>6,}  "
          f"({len(filtered)/len(unfiltered):.1%} of unconditional)")
    print()

    def row(label, sub):
        cm = sub["cont_match"].mean()
        cm_up = sub[sub["direction"] == 1]["cont_match"].mean() if (sub["direction"] == 1).any() else np.nan
        cm_dn = sub[sub["direction"] == -1]["cont_match"].mean() if (sub["direction"] == -1).any() else np.nan
        mfe = _q(sub["mfe_with_thesis"], 0.5)
        mae = _q(sub["mae_with_thesis"], 0.5)
        cr = _q(sub["cross_range"], 0.5)
        print(f"    {label:<26} cont={cm:.1%}  (up={cm_up:.1%}, "
              f"dn={cm_dn:.1%})  MFE_p50={_pip(mfe)}p  MAE_p50={_pip(mae)}p "
              f"cross_range_p50={_pip(cr)}p")

    print("  Headline continuation + excursion stats:")
    row("unconditional:", unfiltered)
    row("trend-flip filter:", filtered)
    print()

    # By session
    print("  By session (trend-flip subset only):")
    print(f"    {'session':<10} {'N':>5}  {'cont%':>6}  "
          f"{'cont_up':>7}  {'cont_dn':>7}  {'MFE_p50':>8}  "
          f"{'MAE_p50':>8}  {'cross_range_p50':>15}")
    for sess in ["Sydney", "Asia", "London", "Overlap", "NY"]:
        sub = filtered[filtered["session"] == sess]
        if len(sub) == 0:
            continue
        cm_up = sub[sub["direction"] == 1]["cont_match"].mean() if (sub["direction"] == 1).any() else np.nan
        cm_dn = sub[sub["direction"] == -1]["cont_match"].mean() if (sub["direction"] == -1).any() else np.nan
        print(f"    {sess:<10} {len(sub):>5,}  "
              f"{sub['cont_match'].mean():>6.1%}  "
              f"{cm_up:>7.1%}  {cm_dn:>7.1%}  "
              f"{_pip(_q(sub['mfe_with_thesis'],0.5)):>8}  "
              f"{_pip(_q(sub['mae_with_thesis'],0.5)):>8}  "
              f"{_pip(_q(sub['cross_range'],0.5)):>15}")
    print()

    # By year, with explicit comparison to unconditional same-year
    print("  By year (trend-flip subset vs unconditional same year):")
    print(f"    {'year':<6} {'N_tf':>5}  {'cont%_tf':>9}  "
          f"{'N_uc':>5}  {'cont%_uc':>9}  {'delta':>7}  {'MFE_p50_tf':>11}")
    yrs = sorted(filtered.index.year.unique())
    for yr in yrs:
        tf = filtered[filtered.index.year == yr]
        uc = unfiltered[unfiltered.index.year == yr]
        delta = tf["cont_match"].mean() - uc["cont_match"].mean()
        print(f"    {yr:<6} {len(tf):>5,}  "
              f"{tf['cont_match'].mean():>9.1%}  "
              f"{len(uc):>5,}  "
              f"{uc['cont_match'].mean():>9.1%}  "
              f"{delta:>+7.1%}  "
              f"{_pip(_q(tf['mfe_with_thesis'],0.5)):>11}p")
    print()

    # MFE vs MAE asymmetry (the thing that would matter for tradeability)
    mfe_tf = filtered["mfe_with_thesis"]
    mae_tf = filtered["mae_with_thesis"]
    mfe_uc = unfiltered["mfe_with_thesis"]
    mae_uc = unfiltered["mae_with_thesis"]
    print("  MFE / MAE asymmetry (next-bar):")
    print(f"    unconditional:  MFE_p50={_pip(_q(mfe_uc,0.5))}p, "
          f"MAE_p50={_pip(_q(mae_uc,0.5))}p, ratio={(_q(mfe_uc,0.5)/_q(mae_uc,0.5)):.2f}")
    print(f"    trend-flip:     MFE_p50={_pip(_q(mfe_tf,0.5))}p, "
          f"MAE_p50={_pip(_q(mae_tf,0.5))}p, ratio={(_q(mfe_tf,0.5)/_q(mae_tf,0.5)):.2f}")
    print()

    # Trend-flip-filtered correlations
    signed = (filtered["next_close"] - filtered["next_open"]) * filtered["direction"]
    corr_cr = filtered[["cross_range"]].assign(s=signed).corr().iloc[0, 1]
    corr_pr = filtered[["prev_range"]].assign(s=signed).corr().iloc[0, 1]
    corr_bslc = filtered[["bars_since_last_cross"]].assign(s=signed).corr().iloc[0, 1]
    print("  Correlations within trend-flip subset:")
    print(f"    cross_range × signed_followthru:           {corr_cr:+.3f}")
    print(f"    prev_range  × signed_followthru:           {corr_pr:+.3f}")
    print(f"    bars_since_last_cross × signed_followthru: {corr_bslc:+.3f}")
    print()
    print("=" * 78)


def main():
    crosses = pd.read_csv(CROSSES_CSV, parse_dates=["ts"]).set_index("ts")
    print(f"Loaded {len(crosses):,} cross events from "
          f"{CROSSES_CSV.relative_to(ROOT)}\n")

    # Drop NaN bars_since_last_cross (the very first event of dataset)
    crosses = crosses.dropna(subset=["bars_since_last_cross"]).copy()

    filtered = crosses[crosses["bars_since_last_cross"] >= TREND_FLIP_BARS].copy()
    _compare(crosses, filtered)


if __name__ == "__main__":
    main()
