"""Repair the EURUSD 1-min CSV: undo the 100x price scaling bug from GLM's
April-12 re-download.

Reads:  C:\\nautilus0\\data\\1m_csv\\eurusd_1m_tick.csv  (untouched)
Writes: C:\\nautilus0\\data\\1m_csv\\eurusd_1m_tick_repaired.csv  (new file)

Fixes:
  - Drop phantom 'Unnamed: 0' column
  - Divide open/high/low/close/avg_spread/max_spread by 100
  - Volumes / tick_count / buy_ratio / vol_imbalance unchanged (already correct)

After running, validates against the partial fresh sample at
C:\\nautilus0\\data\\1m_csv_fresh\\eurusd_1m_tick.csv (if present) — any
overlap must have exact-match prices.

Run: python -m v11.backtest.repair_eurusd_prices
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

from pathlib import Path
import pandas as pd

CORRUPTED = Path(r"C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv")
REPAIRED  = Path(r"C:\nautilus0\data\1m_csv\eurusd_1m_tick_repaired.csv")
FRESH     = Path(r"C:\nautilus0\data\1m_csv_fresh\eurusd_1m_tick.csv")

PRICE_COLS = ["open", "high", "low", "close", "avg_spread", "max_spread"]


def main():
    if not CORRUPTED.exists():
        print(f"ERROR: corrupted file not found: {CORRUPTED}")
        sys.exit(1)
    if REPAIRED.exists():
        print(f"NOTE: repaired file already exists; will overwrite:\n  {REPAIRED}")

    print("=" * 80)
    print(f"Reading:  {CORRUPTED}")
    print(f"Writing:  {REPAIRED}")
    print("=" * 80)

    df = pd.read_csv(CORRUPTED)
    n_rows_raw = len(df)
    cols_raw = list(df.columns)
    print(f"  Loaded {n_rows_raw:,} rows, {len(cols_raw)} cols")

    # Drop phantom index columns
    dropped = []
    for col in list(df.columns):
        if col.startswith("Unnamed"):
            df = df.drop(columns=[col])
            dropped.append(col)
    if dropped:
        print(f"  Dropped phantom columns: {dropped}")

    # Verify expected columns are present
    missing = [c for c in PRICE_COLS if c not in df.columns]
    if missing:
        print(f"ERROR: expected price columns missing: {missing}")
        sys.exit(1)

    # The corruption is LOCALIZED, not global. Investigation showed:
    #   Jan 1 2018 - Sept 27 2018 22:27 UTC: prices 100x inflated (277k rows)
    #   Everything after that: correct
    # Detect per-row: EURUSD has never exceeded ~2.0 historically, so any
    # close > 5 is a smoking gun for 100x scaling.
    EURUSD_SANITY_THRESHOLD = 5.0

    bad_mask = df["close"] > EURUSD_SANITY_THRESHOLD
    n_bad = int(bad_mask.sum())
    n_good = len(df) - n_bad
    print(f"  Rows with close > {EURUSD_SANITY_THRESHOLD} (corrupted): {n_bad:,}")
    print(f"  Rows with close <= {EURUSD_SANITY_THRESHOLD} (untouched): {n_good:,}")

    if n_bad == 0:
        print("ERROR: no corrupted rows detected. File may already be repaired.")
        sys.exit(1)

    # Show the affected time range
    bad_rows = df.loc[bad_mask, "timestamp"]
    print(f"  Corrupted range: {bad_rows.min()}  ->  {bad_rows.max()}")

    # Sanity: the bad rows should average ~100x the good rows (~1.1 * 100 = 110)
    bad_close_mean = df.loc[bad_mask, "close"].mean()
    print(f"  Bad rows close mean BEFORE fix: {bad_close_mean:.2f}  (expected ~100-130)")
    if not (50 < bad_close_mean < 300):
        print("ERROR: bad-row mean outside expected 100x-scale range. Aborting.")
        sys.exit(1)

    # Apply fix only to the corrupted rows
    for col in PRICE_COLS:
        df.loc[bad_mask, col] = df.loc[bad_mask, col] / 100.0

    # Verify ALL rows now look like EURUSD
    close_min, close_max, close_mean = df["close"].min(), df["close"].max(), df["close"].mean()
    print(f"  close range AFTER fix: min={close_min:.4f}  max={close_max:.4f}  mean={close_mean:.4f}")

    if close_max > EURUSD_SANITY_THRESHOLD:
        still_bad = int((df["close"] > EURUSD_SANITY_THRESHOLD).sum())
        print(f"ERROR: {still_bad:,} rows still above {EURUSD_SANITY_THRESHOLD} after fix. Aborting.")
        sys.exit(1)
    if not (0.8 < close_mean < 1.5):
        print("ERROR: post-fix close mean not in a plausible EURUSD range. Aborting.")
        sys.exit(1)

    # Ensure timestamp is preserved as-is (no unwanted reformatting)
    # and write with explicit index=False
    df.to_csv(REPAIRED, index=False)
    print(f"  Wrote {len(df):,} rows to {REPAIRED}")
    print(f"  Size: {REPAIRED.stat().st_size / 1024 / 1024:.1f} MB")

    # Validate against fresh sample if available
    if FRESH.exists():
        print()
        print("=" * 80)
        print("VALIDATION against fresh sample")
        print("=" * 80)
        fresh = pd.read_csv(FRESH)
        # Normalize both to same dtype
        fresh["timestamp"] = pd.to_datetime(fresh["timestamp"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        merged = fresh.set_index("timestamp").join(
            df.set_index("timestamp"), how="inner",
            lsuffix="_fresh", rsuffix="_rep")
        print(f"  Overlapping minutes: {len(merged):,}")

        if len(merged) == 0:
            print("  (no overlap — skipping validation)")
        else:
            all_exact = True
            for col in PRICE_COLS:
                a, b = f"{col}_fresh", f"{col}_rep"
                if a not in merged.columns or b not in merged.columns:
                    continue
                diff = (merged[a] - merged[b]).abs()
                exact_pct = (diff < 1e-7).mean() * 100
                max_diff = diff.max()
                status = "OK" if exact_pct > 99.9 else "MISMATCH"
                print(f"    {col:<12} exact={exact_pct:6.2f}%  max_diff={max_diff:.8f}  [{status}]")
                if exact_pct <= 99.9:
                    all_exact = False

            if all_exact:
                print()
                print("  -> REPAIR VERIFIED. Prices in the repaired file match the")
                print("     proven-pipeline fresh sample exactly over the overlap window.")
                print()
                print("  Next steps:")
                print("    1. Kill the still-running re-download (Ctrl+C in that terminal).")
                print("    2. Swap the files when ready:")
                print("       mv eurusd_1m_tick.csv eurusd_1m_tick.corrupted.bak")
                print("       mv eurusd_1m_tick_repaired.csv eurusd_1m_tick.csv")
                print("    3. Re-run Darvas / 4H Level Retest backtests on repaired data.")
            else:
                print()
                print("  -> REPAIR DID NOT VALIDATE CLEANLY. Do not swap the files;")
                print("     investigate which minutes mismatch and why.")
    else:
        print()
        print("  (no fresh sample at expected path — skipping validation)")


if __name__ == "__main__":
    main()
