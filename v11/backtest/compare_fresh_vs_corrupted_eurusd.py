"""Compare fresh EURUSD data (partial, Jan-Mar 2018) vs the corrupted CSV
over the overlapping date range.

Tests the hypothesis: GLM's April-12 re-download used a different aggregation
code path than the proven download_fx_universal.py, producing structurally
different bars (lower tick_count, more gaps, extra Unnamed: 0 column).

If the fresh aggregation matches the corrupted one within noise, we don't
have a bug — the Darvas/4H edge evaporation is about something else.
If they differ systematically, the bug is confirmed and the full
re-download is worth completing.

Run: python -m v11.backtest.compare_fresh_vs_corrupted_eurusd
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from pathlib import Path
import pandas as pd
import numpy as np

FRESH     = Path(r"C:\nautilus0\data\1m_csv_fresh\eurusd_1m_tick.csv")
CORRUPTED = Path(r"C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv")


def load(path: Path, label: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    print(f"\n[{label}] raw columns: {list(df.columns)}")
    # Normalize: drop any phantom index column
    for col in list(df.columns):
        if col.startswith("Unnamed"):
            print(f"[{label}] DROPPING phantom column '{col}'")
            df = df.drop(columns=[col])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def summarize(df: pd.DataFrame, label: str) -> dict:
    tc = df["tick_count"]
    ts = df["timestamp"].sort_values().reset_index(drop=True)
    deltas = ts.diff().dt.total_seconds() / 60
    intraday = deltas[(deltas > 1) & (deltas < 100)]
    return {
        "label": label,
        "rows": len(df),
        "start": str(ts.min()),
        "end":   str(ts.max()),
        "tc_min": int(tc.min()), "tc_max": int(tc.max()),
        "tc_mean": float(tc.mean()),
        "tc_p50": int(tc.quantile(0.50)),
        "tc_p95": int(tc.quantile(0.95)),
        "intraday_gaps": int(len(intraday)),
        "gap_mean_min": float(intraday.mean()) if len(intraday) else 0.0,
        "zero_tc_pct": float((tc == 0).mean() * 100),
    }


def main():
    print("=" * 88)
    print("EURUSD FRESH vs CORRUPTED — Q1 2018 OVERLAP COMPARISON")
    print("=" * 88)

    if not FRESH.exists():
        print(f"ERROR: fresh file not found at {FRESH}")
        sys.exit(1)
    if not CORRUPTED.exists():
        print(f"ERROR: corrupted file not found at {CORRUPTED}")
        sys.exit(1)

    fresh_full = load(FRESH, "FRESH")
    corrupted_full = load(CORRUPTED, "CORRUPTED")

    # Find the overlap range (limited by fresh's partial download)
    fresh_end = fresh_full["timestamp"].max()
    overlap_start = fresh_full["timestamp"].min()
    overlap_end = min(fresh_end, corrupted_full["timestamp"].max())

    print(f"\nOverlap window: {overlap_start} -> {overlap_end}")

    fresh_ov = fresh_full[fresh_full["timestamp"] <= overlap_end].copy()
    corrupted_ov = corrupted_full[
        (corrupted_full["timestamp"] >= overlap_start) &
        (corrupted_full["timestamp"] <= overlap_end)
    ].copy()

    # Summary stats side by side
    f = summarize(fresh_ov, "FRESH")
    c = summarize(corrupted_ov, "CORRUPTED")

    print("\n" + "=" * 88)
    print("STRUCTURAL COMPARISON")
    print("=" * 88)
    print(f"  {'Metric':<22} {'FRESH':>18} {'CORRUPTED':>18} {'DIFF':>18}")
    print("  " + "-" * 78)
    for key in ("rows", "tc_min", "tc_max", "tc_mean", "tc_p50", "tc_p95",
                "intraday_gaps", "gap_mean_min", "zero_tc_pct"):
        fv, cv = f[key], c[key]
        if isinstance(fv, float):
            diff = f"{fv - cv:+.2f}"
            print(f"  {key:<22} {fv:>18.2f} {cv:>18.2f} {diff:>18}")
        else:
            diff = f"{fv - cv:+d}"
            print(f"  {key:<22} {fv:>18} {cv:>18} {diff:>18}")

    # Per-minute merge — the smoking gun
    print("\n" + "=" * 88)
    print("PER-MINUTE DIRECT COMPARISON (inner join on timestamp)")
    print("=" * 88)
    f_idx = fresh_ov.set_index("timestamp")
    c_idx = corrupted_ov.set_index("timestamp")
    merged = f_idx.join(c_idx, how="inner", lsuffix="_fresh", rsuffix="_corr")
    print(f"  Matching timestamps: {len(merged):,}")
    print(f"  Fresh-only timestamps: {len(f_idx) - len(merged):,}")
    print(f"  Corrupted-only timestamps: {len(c_idx) - len(merged):,}")

    if len(merged) == 0:
        print("  NO OVERLAP — can't do per-minute comparison.")
        return

    # Price agreement
    price_cols = [("open_fresh", "open_corr"),
                  ("high_fresh", "high_corr"),
                  ("low_fresh", "low_corr"),
                  ("close_fresh", "close_corr")]
    print("\n  Price agreement on matching timestamps:")
    for fc, cc in price_cols:
        diff = (merged[fc] - merged[cc]).abs()
        exact = (diff < 1e-7).mean() * 100
        mean_abs = diff.mean()
        max_abs = diff.max()
        print(f"    {fc.split('_')[0]:<6} exact={exact:>6.2f}%  "
              f"mean_abs_diff={mean_abs:.7f}  max_abs_diff={max_abs:.7f}")

    # Tick count divergence — the key finding
    print("\n  tick_count comparison on matching timestamps:")
    tc_diff = merged["tick_count_fresh"] - merged["tick_count_corr"]
    print(f"    mean(fresh - corrupted): {tc_diff.mean():+.2f}")
    print(f"    median                 : {tc_diff.median():+.2f}")
    print(f"    std                    : {tc_diff.std():.2f}")
    print(f"    exact match rate       : {(tc_diff == 0).mean() * 100:.2f}%")
    print(f"    fresh > corrupted      : {(tc_diff > 0).mean() * 100:.2f}%")
    print(f"    fresh < corrupted      : {(tc_diff < 0).mean() * 100:.2f}%")
    print(f"    fresh mean tc          : {merged['tick_count_fresh'].mean():.1f}")
    print(f"    corrupt mean tc        : {merged['tick_count_corr'].mean():.1f}")
    print(f"    relative diff          : "
          f"{(merged['tick_count_fresh'].mean() / merged['tick_count_corr'].mean() - 1) * 100:+.2f}%")

    # Volume (buy_volume + sell_volume) comparison
    if "buy_volume_fresh" in merged.columns:
        f_vol = merged["buy_volume_fresh"] + merged["sell_volume_fresh"]
        c_vol = merged["buy_volume_corr"] + merged["sell_volume_corr"]
        v_diff = f_vol - c_vol
        print(f"\n  total_volume comparison on matching timestamps:")
        print(f"    mean(fresh - corrupted): {v_diff.mean():+.2f}")
        print(f"    exact match rate       : {(v_diff == 0).mean() * 100:.2f}%")
        print(f"    fresh mean vol         : {f_vol.mean():.1f}")
        print(f"    corrupt mean vol       : {c_vol.mean():.1f}")

    # Verdict
    print("\n" + "=" * 88)
    print("VERDICT")
    print("=" * 88)
    rel = (merged['tick_count_fresh'].mean() / merged['tick_count_corr'].mean() - 1) * 100
    exact_price = ((merged["close_fresh"] - merged["close_corr"]).abs() < 1e-7).mean() * 100
    if exact_price > 95 and abs(rel) < 2:
        print("  -> Fresh and corrupted agree on prices AND tick_counts.")
        print("     The corrupted file is NOT structurally wrong. The strategy")
        print("     edge evaporation is caused by something else (regime change,")
        print("     or the corruption is localized to a date range outside Q1 2018).")
        print("  -> Full re-download is likely NOT necessary.")
    elif exact_price > 95 and abs(rel) > 5:
        print(f"  -> Prices match but tick_counts differ by {rel:+.1f}% systematically.")
        print("     This confirms the aggregation bug: same tick stream, different")
        print("     tick_count output. Full re-download IS worth completing.")
    elif exact_price < 95:
        print(f"  -> Prices differ in {100-exact_price:.1f}% of minutes.")
        print("     Different underlying tick streams — Dukascopy may have changed")
        print("     historical data between downloads. Investigate which file is trustworthy.")
    else:
        print("  -> Mixed signals. Inspect the numbers above manually.")


if __name__ == "__main__":
    main()
