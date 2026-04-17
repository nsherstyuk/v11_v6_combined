"""Data integrity comparison — EURUSD (suspect, modified 2026-04-13) vs
XAUUSD (clean reference, untouched since 2026-03-10) vs GBPUSD (control).

Reports structure, date range, column stats, distributions, continuity.
Run: python -m v11.backtest.investigate_data_integrity
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from pathlib import Path
import pandas as pd
import numpy as np

DATA = Path(r"C:\nautilus0\data\1m_csv")
PAIRS = ["xauusd", "gbpusd", "eurusd"]  # clean, control, suspect


def load(pair: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"{pair}_1m_tick.csv")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def basic_stats(df: pd.DataFrame) -> dict:
    ts = df["timestamp"]
    return {
        "rows": len(df),
        "start": str(ts.min()),
        "end": str(ts.max()),
        "cols": list(df.columns),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
    }


def tick_count_dist(df: pd.DataFrame) -> dict:
    tc = df["tick_count"]
    return {
        "min": int(tc.min()),
        "p5": int(tc.quantile(0.05)),
        "p50": int(tc.quantile(0.50)),
        "p95": int(tc.quantile(0.95)),
        "max": int(tc.max()),
        "mean": float(tc.mean()),
        "std": float(tc.std()),
        "zero_pct": float((tc == 0).mean() * 100),
        "above_168": float((tc > 168).mean() * 100),
    }


def gap_analysis(df: pd.DataFrame) -> dict:
    """Find gaps in 1-min timestamps (excluding weekends)."""
    ts = df["timestamp"].sort_values().reset_index(drop=True)
    deltas = ts.diff().dt.total_seconds() / 60  # minutes
    # Ignore normal weekend gaps (fri close → sun open, ~49 hours = 2940 min)
    # Focus on unexpected intraday gaps (> 1 minute, < 100 minutes)
    intraday_gaps = deltas[(deltas > 1) & (deltas < 100)]
    weekend_gaps = deltas[deltas >= 100]
    return {
        "total_gaps_over_1min": int((deltas > 1).sum()),
        "intraday_gaps_count": int(len(intraday_gaps)),
        "intraday_gaps_max_minutes": float(intraday_gaps.max()) if len(intraday_gaps) else 0.0,
        "intraday_gaps_mean_minutes": float(intraday_gaps.mean()) if len(intraday_gaps) else 0.0,
        "weekend_gaps_count": int(len(weekend_gaps)),
        "duplicate_timestamps": int(ts.duplicated().sum()),
    }


def price_sanity(df: pd.DataFrame) -> dict:
    """Look for weird OHLC values."""
    ohlc_broken = (
        (df["high"] < df["low"]).sum() +
        (df["high"] < df["open"]).sum() +
        (df["high"] < df["close"]).sum() +
        (df["low"] > df["open"]).sum() +
        (df["low"] > df["close"]).sum()
    )
    # Large jumps (open-to-close pct change > 1%)
    pct = (df["close"] - df["open"]).abs() / df["open"]
    return {
        "ohlc_violations": int(ohlc_broken),
        "nan_count": int(df[["open", "high", "low", "close"]].isna().sum().sum()),
        "zero_prices": int((df[["open", "high", "low", "close"]] == 0).sum().sum()),
        "max_bar_pct_move": float(pct.max() * 100),
        "p99_bar_pct_move": float(pct.quantile(0.99) * 100),
        "mean_spread_col_exists": "avg_spread" in df.columns,
    }


def year_density(df: pd.DataFrame) -> dict:
    """Rows per year — should be roughly constant for consistent FX data."""
    by_year = df.groupby(df["timestamp"].dt.year).size()
    return {int(y): int(n) for y, n in by_year.items()}


def session_density(df: pd.DataFrame) -> dict:
    """Tick activity by UTC hour — should show London/NY peaks."""
    by_hour = df.groupby(df["timestamp"].dt.hour)["tick_count"].mean()
    return {int(h): float(v) for h, v in by_hour.items()}


def volume_structure(df: pd.DataFrame) -> dict:
    """Check buy/sell volume patterns."""
    if "buy_volume" not in df.columns:
        return {"buy_volume_col": False}
    bv = df["buy_volume"]
    sv = df["sell_volume"]
    tc = df["tick_count"]
    # Ratio = buy / (buy + sell). Should cluster around 0.5 for healthy data.
    total = bv + sv
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(total > 0, bv / total, 0.5)
    # Correlation between buy_volume+sell_volume and tick_count
    corr = np.corrcoef(total, tc)[0, 1] if len(df) > 10 else float("nan")
    return {
        "buy_volume_col": True,
        "buy_ratio_mean": float(ratio.mean()),
        "buy_ratio_std": float(ratio.std()),
        "volume_tick_correlation": float(corr),
        "rows_with_zero_volume": int((total == 0).sum()),
        "zero_volume_pct": float((total == 0).mean() * 100),
    }


def main():
    print("=" * 90)
    print("DATA INTEGRITY COMPARISON")
    print("  XAUUSD = clean reference (file unchanged since 2026-03-10)")
    print("  GBPUSD = control (file unchanged since 2026-03-23)")
    print("  EURUSD = SUSPECT (file modified 2026-04-13 — overwritten during tick vault re-download)")
    print("=" * 90)

    for pair in PAIRS:
        print(f"\n{'=' * 90}\n  {pair.upper()}\n{'=' * 90}")
        df = load(pair)

        print("\n-- Structure --")
        s = basic_stats(df)
        print(f"  rows:  {s['rows']:,}")
        print(f"  range: {s['start']} -> {s['end']}")
        print(f"  cols:  {s['cols']}")

        print("\n-- tick_count distribution --")
        tc = tick_count_dist(df)
        print(f"  min={tc['min']}  p5={tc['p5']}  p50={tc['p50']}  p95={tc['p95']}  max={tc['max']}")
        print(f"  mean={tc['mean']:.1f}  std={tc['std']:.1f}")
        print(f"  zero_pct={tc['zero_pct']:.2f}%  above_168={tc['above_168']:.2f}%")

        print("\n-- Timestamp continuity --")
        g = gap_analysis(df)
        print(f"  total_gaps>1min:        {g['total_gaps_over_1min']:,}")
        print(f"  intraday_gaps (1-100m): {g['intraday_gaps_count']:,}  max={g['intraday_gaps_max_minutes']:.1f}m  mean={g['intraday_gaps_mean_minutes']:.2f}m")
        print(f"  weekend_gaps (>100m):   {g['weekend_gaps_count']:,}")
        print(f"  duplicate_timestamps:   {g['duplicate_timestamps']:,}")

        print("\n-- Price sanity --")
        p = price_sanity(df)
        print(f"  ohlc_violations: {p['ohlc_violations']}")
        print(f"  nan_count:       {p['nan_count']}")
        print(f"  zero_prices:     {p['zero_prices']}")
        print(f"  max_bar_move:    {p['max_bar_pct_move']:.3f}%")
        print(f"  p99_bar_move:    {p['p99_bar_pct_move']:.4f}%")

        print("\n-- Volume structure --")
        v = volume_structure(df)
        if v["buy_volume_col"]:
            print(f"  buy_ratio: mean={v['buy_ratio_mean']:.3f}  std={v['buy_ratio_std']:.3f}  (healthy: mean~0.5)")
            print(f"  volume/tick_count correlation: {v['volume_tick_correlation']:.3f}  (should be ~1.0 for consistent data)")
            print(f"  zero_volume_rows: {v['rows_with_zero_volume']:,}  ({v['zero_volume_pct']:.2f}%)")

        print("\n-- Rows per year --")
        for y, n in sorted(year_density(df).items()):
            print(f"  {y}: {n:,}")

        print("\n-- Mean tick_count by UTC hour (session signature) --")
        hours = session_density(df)
        for h in sorted(hours.keys()):
            bar = "#" * min(60, int(hours[h] / max(hours.values()) * 60))
            print(f"  {h:>2}h: {hours[h]:>7.1f}  {bar}")


if __name__ == "__main__":
    main()
