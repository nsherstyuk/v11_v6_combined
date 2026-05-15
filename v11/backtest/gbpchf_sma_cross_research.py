"""GBPCHF SMA-cross descriptive research — Phase 1.

Pre-registration:
    Hypothesis: when GBPCHF 5-min close crosses SMA50, the next bar's
    directional follow-through correlates with the crossing bar's
    range. Possible conditional variables: session, prior-bar range,
    bars-since-last-cross.

    This Phase 1 script identifies cross events and reports DESCRIPTIVE
    statistics only — NO trading simulation, NO R/R numbers, NO PnL.
    Decision on whether to formalize into a tradeable rule happens
    AFTER reading the descriptive output.

    Date range: 2019-01-01 → 2026-05-13 (the GBPCHF MID dataset).
    IS / OOS split (for later Phase 3, not used here):
        IS: 2023-2025
        OOS: 2019-2022

Data:
    tick_vault_data/fx/GBPCHF_MIDPOINT.csv (1-min, ~1.21M rows)
    Resampled to 5-min OHLC.

Output:
    v11/backtest/results/gbpchf_sma_crosses.csv  (one row per cross event)
    Plus printed summary stats.

Run:
    python -m v11.backtest.gbpchf_sma_cross_research
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


# ── Paths ───────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[2]
MID_CSV = ROOT / "tick_vault_data" / "fx" / "GBPCHF_MIDPOINT.csv"
OUT_DIR = ROOT / "v11" / "backtest" / "results"
OUT_CSV = OUT_DIR / "gbpchf_sma_crosses.csv"


# ── Pre-registered parameters ───────────────────────────────────────────────

SMA_PERIOD = 50
BAR_INTERVAL = "5min"


# ── Session label (UTC) ─────────────────────────────────────────────────────

def _session(hour_utc: int) -> str:
    """Coarse FX session labels in UTC.

    Sydney : 22-00 UTC
    Asia   : 00-07 UTC  (Tokyo + Singapore + HK)
    London : 07-12 UTC  (pre-NY-overlap)
    Overlap: 12-16 UTC  (London-NY high-liquidity)
    NY     : 16-22 UTC  (post-London-close)
    """
    if hour_utc >= 22:
        return "Sydney"
    if hour_utc < 7:
        return "Asia"
    if hour_utc < 12:
        return "London"
    if hour_utc < 16:
        return "Overlap"
    return "NY"


# ── Data load + resample ────────────────────────────────────────────────────

def load_5min(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["date"])
    df = df.set_index("date").sort_index()
    # 1-min OHLC → 5-min OHLC. Closed='left' label='left' gives clock-aligned
    # buckets [00:00, 00:05) labeled 00:00 — matches what TradingView shows.
    bars = df.resample(
        BAR_INTERVAL, closed="left", label="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    bars = bars.dropna(how="any")
    return bars


# ── Cross identification ────────────────────────────────────────────────────

def identify_crosses(bars: pd.DataFrame) -> pd.DataFrame:
    """Return one row per cross event with descriptive features.

    Definition of "cross at bar i":
        bar(i-1).close was on one side of sma(i-1),
        bar(i).close is on the other side of sma(i).
        (Both SMAs computed from closes ending at i-1 and i respectively.)
        Strict inequalities; bars exactly at the SMA are skipped.

    For each cross event we record:
        - ts          (close time of the crossing bar)
        - direction   (+1 up-cross, -1 down-cross)
        - cross_close, cross_open, cross_high, cross_low, cross_range
        - prev_close, prev_range
        - sma_at_cross, dist_close_minus_sma
        - next_open, next_close, next_high, next_low, next_range
        - next_dir    (sign of (next_close - next_open))
        - cont_match  (1 if next_dir matches cross direction, else 0; 0 if flat)
        - mfe_with_thesis  (next bar's max favorable excursion in price-units)
        - mae_with_thesis  (next bar's max adverse excursion in price-units)
        - hour_utc, session, weekday
        - bars_since_last_cross
    """
    df = bars.copy()
    df["sma"] = df["close"].rolling(SMA_PERIOD).mean()
    df = df.dropna(subset=["sma"]).copy()

    df["above"] = df["close"] > df["sma"]
    df["below"] = df["close"] < df["sma"]
    df["prev_above"] = df["above"].shift(1)
    df["prev_below"] = df["below"].shift(1)

    up_cross = df["above"] & df["prev_below"]
    down_cross = df["below"] & df["prev_above"]
    df["is_cross"] = up_cross | down_cross
    df["direction"] = np.where(up_cross, 1, np.where(down_cross, -1, 0))

    # Prior + next bar context, computed against the whole resampled series
    # (i.e. previous/next bar in the 5-min stream, not "previous cross").
    df["prev_close"] = df["close"].shift(1)
    df["prev_range"] = (df["high"] - df["low"]).shift(1)
    df["range"] = df["high"] - df["low"]
    df["next_open"] = df["open"].shift(-1)
    df["next_close"] = df["close"].shift(-1)
    df["next_high"] = df["high"].shift(-1)
    df["next_low"] = df["low"].shift(-1)

    crosses = df[df["is_cross"]].copy()

    if crosses.empty:
        return crosses

    crosses["cross_close"] = crosses["close"]
    crosses["cross_open"] = crosses["open"]
    crosses["cross_high"] = crosses["high"]
    crosses["cross_low"] = crosses["low"]
    crosses["cross_range"] = crosses["range"]
    crosses["sma_at_cross"] = crosses["sma"]
    crosses["dist_close_minus_sma"] = crosses["close"] - crosses["sma"]
    crosses["next_range"] = crosses["next_high"] - crosses["next_low"]
    crosses["next_dir"] = np.sign(crosses["next_close"] - crosses["next_open"])
    crosses["cont_match"] = (
        crosses["next_dir"] == crosses["direction"]
    ).astype(int)

    # Max favorable / adverse excursion of the NEXT bar, in the direction
    # of the cross thesis. For up-cross: favorable = next_high - next_open,
    # adverse = next_open - next_low. For down-cross: flipped.
    up_mask = crosses["direction"] == 1
    crosses["mfe_with_thesis"] = np.where(
        up_mask,
        crosses["next_high"] - crosses["next_open"],
        crosses["next_open"] - crosses["next_low"],
    )
    crosses["mae_with_thesis"] = np.where(
        up_mask,
        crosses["next_open"] - crosses["next_low"],
        crosses["next_high"] - crosses["next_open"],
    )

    crosses["hour_utc"] = crosses.index.hour
    crosses["session"] = crosses["hour_utc"].apply(_session)
    crosses["weekday"] = crosses.index.dayofweek  # 0=Mon

    # Bars since last cross (in 5-min units).
    crosses["bars_since_last_cross"] = (
        pd.Series(crosses.index, index=crosses.index)
        .diff().dt.total_seconds() / 300
    ).fillna(np.nan)

    # Tidy
    cols = [
        "direction",
        "cross_open", "cross_high", "cross_low", "cross_close", "cross_range",
        "prev_close", "prev_range",
        "sma_at_cross", "dist_close_minus_sma",
        "next_open", "next_high", "next_low", "next_close", "next_range",
        "next_dir", "cont_match",
        "mfe_with_thesis", "mae_with_thesis",
        "hour_utc", "session", "weekday",
        "bars_since_last_cross",
    ]
    crosses = crosses[cols].copy()
    crosses.index.name = "ts"
    return crosses


# ── Descriptive output ──────────────────────────────────────────────────────

def _pip(x):
    """GBPCHF is quoted with 4 decimal pips at 0.0001; print in pip units."""
    return f"{x * 10000:.2f}"


def _q(series, q):
    return series.quantile(q)


def report(crosses: pd.DataFrame) -> None:
    print("=" * 78)
    print(f"  GBPCHF 5-min SMA{SMA_PERIOD} cross — Phase 1 descriptive")
    print("=" * 78)
    print(f"  Total events:        {len(crosses):,}")
    yrs = crosses.index.year.unique()
    print(f"  Year span:           {yrs.min()} → {yrs.max()}")
    print(f"  Avg events/year:     {len(crosses) / len(yrs):.0f}")
    print()

    # By direction
    n_up = (crosses["direction"] == 1).sum()
    n_dn = (crosses["direction"] == -1).sum()
    print(f"  Up-crosses:   {n_up:>6,}  ({n_up/len(crosses):.1%})")
    print(f"  Down-crosses: {n_dn:>6,}  ({n_dn/len(crosses):.1%})")
    print()

    # Continuation match rate (does next bar's open-to-close match cross dir?)
    cm_all = crosses["cont_match"].mean()
    cm_up = crosses[crosses["direction"] == 1]["cont_match"].mean()
    cm_dn = crosses[crosses["direction"] == -1]["cont_match"].mean()
    print(f"  Next-bar same-direction rate (continuation):")
    print(f"    All:    {cm_all:.1%}   "
          f"(neutral baseline 50% — flat bars don't match)")
    print(f"    Up:     {cm_up:.1%}")
    print(f"    Down:   {cm_dn:.1%}")
    print()

    # Cross range distribution
    cr = crosses["cross_range"]
    print(f"  Crossing bar range (pips):")
    print(f"    p25={_pip(_q(cr,0.25))}  p50={_pip(_q(cr,0.5))}  "
          f"p75={_pip(_q(cr,0.75))}  p95={_pip(_q(cr,0.95))}")
    print()

    # Next bar follow-through stats — descriptive only
    print(f"  Next-bar MFE-with-thesis (pips, favorable excursion):")
    mfe = crosses["mfe_with_thesis"]
    print(f"    p25={_pip(_q(mfe,0.25))}  p50={_pip(_q(mfe,0.5))}  "
          f"p75={_pip(_q(mfe,0.75))}  p95={_pip(_q(mfe,0.95))}")
    mae = crosses["mae_with_thesis"]
    print(f"  Next-bar MAE-with-thesis (pips, adverse excursion):")
    print(f"    p25={_pip(_q(mae,0.25))}  p50={_pip(_q(mae,0.5))}  "
          f"p75={_pip(_q(mae,0.75))}  p95={_pip(_q(mae,0.95))}")
    print()

    # Conditional: split by crossing bar range — does follow-through differ?
    median_cr = cr.median()
    big = crosses[cr >= median_cr]
    small = crosses[cr < median_cr]
    print(f"  Continuation rate by crossing-bar range:")
    print(f"    cross_range  < median ({_pip(median_cr)}p):  "
          f"cont_match={small['cont_match'].mean():.1%}  N={len(small):,}")
    print(f"    cross_range >= median ({_pip(median_cr)}p):  "
          f"cont_match={big['cont_match'].mean():.1%}  N={len(big):,}")
    print()

    # By session
    print(f"  By session (continuation rate, mean MFE pips, mean MAE pips):")
    print(f"    {'session':<10} {'N':>6}  {'cont%':>6}  "
          f"{'MFE_p50':>8}  {'MAE_p50':>8}  {'cross_range_p50':>15}")
    for sess in ["Sydney", "Asia", "London", "Overlap", "NY"]:
        sub = crosses[crosses["session"] == sess]
        if len(sub) == 0:
            continue
        print(f"    {sess:<10} {len(sub):>6,}  "
              f"{sub['cont_match'].mean():>6.1%}  "
              f"{_pip(sub['mfe_with_thesis'].median()):>8}  "
              f"{_pip(sub['mae_with_thesis'].median()):>8}  "
              f"{_pip(sub['cross_range'].median()):>15}")
    print()

    # By year
    print(f"  By year (continuation rate, N):")
    print(f"    {'year':<6} {'N':>6}  {'cont%':>6}  "
          f"{'MFE_p50':>8}  {'cross_range_p50':>15}")
    for yr in sorted(yrs):
        sub = crosses[crosses.index.year == yr]
        print(f"    {yr:<6} {len(sub):>6,}  "
              f"{sub['cont_match'].mean():>6.1%}  "
              f"{_pip(sub['mfe_with_thesis'].median()):>8}  "
              f"{_pip(sub['cross_range'].median()):>15}")
    print()

    # Bars since last cross
    bslc = crosses["bars_since_last_cross"].dropna()
    print(f"  Bars-since-last-cross (5-min bars):")
    print(f"    p25={_q(bslc,0.25):.0f}  p50={_q(bslc,0.5):.0f}  "
          f"p75={_q(bslc,0.75):.0f}  p95={_q(bslc,0.95):.0f}")
    print()

    # Correlation: crossing bar range vs next bar follow-through (in same direction)
    # For up-crosses: next_close - next_open should be positive if continuation
    crosses_with_signed = crosses.copy()
    crosses_with_signed["signed_followthru"] = (
        (crosses_with_signed["next_close"] - crosses_with_signed["next_open"])
        * crosses_with_signed["direction"]
    )
    corr = crosses_with_signed[
        ["cross_range", "signed_followthru"]
    ].corr().iloc[0, 1]
    corr_prev = crosses_with_signed[
        ["prev_range", "signed_followthru"]
    ].corr().iloc[0, 1]
    print(f"  Correlations (Pearson):")
    print(f"    cross_range × signed_followthru:  {corr:+.3f}")
    print(f"    prev_range  × signed_followthru:  {corr_prev:+.3f}")
    print()
    print("=" * 78)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"Loading {MID_CSV} ...")
    bars = load_5min(MID_CSV)
    print(f"  {len(bars):,} 5-min bars, "
          f"{bars.index.min()} → {bars.index.max()}")

    crosses = identify_crosses(bars)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    crosses.to_csv(OUT_CSV)
    print(f"  cross events written to {OUT_CSV.relative_to(ROOT)} "
          f"({len(crosses):,} rows)\n")

    report(crosses)


if __name__ == "__main__":
    main()
