"""Re-download EURUSD 1-min bars using the PROVEN download_fx_universal.py logic.

Writes to a SEPARATE folder (C:\\nautilus0\\data\\1m_csv_fresh\\) so the
corrupted file at C:\\nautilus0\\data\\1m_csv\\eurusd_1m_tick.csv is NOT touched.

Uses tick_vault's cached .bi5 files (already on disk) — just re-aggregates
using the same logic that produced the clean XAUUSD/GBPUSD CSVs.

Safe in two ways:
  1. Output folder is fresh. Existing 1m_csv/ is untouched.
  2. If an eurusd_1m_tick.csv exists in the fresh folder (prior partial run),
     we resume from it — but never touch other pairs.

Usage:
  python -m v11.backtest.redownload_eurusd_fresh
  python -m v11.backtest.redownload_eurusd_fresh --start 2018 --end 2026
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import asyncio
import argparse
import gc
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from tick_vault import download_range, read_tick_data, reload_config


# ── Config ──────────────────────────────────────────────────────────────────

SYMBOL = "EURUSD"
# FRESH folder — existing corrupted file at C:\nautilus0\data\1m_csv\ stays intact
OUTPUT_DIR = Path(r"C:\nautilus0\data\1m_csv_fresh")
TICK_VAULT_DIR = r"C:\nautilus0\tick_vault_data"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / f"{SYMBOL.lower()}_1m_tick.csv"

# Mirror download_fx_universal.py constants exactly
PAUSE_BETWEEN_WEEKS = 15       # seconds between weekly chunks
CHECKPOINT_INTERVAL = 4        # save every N weeks
MAX_RETRIES = 3


# ── Aggregation (IDENTICAL to download_fx_universal.py) ─────────────────────

def ticks_to_1m_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate raw tick data into 1-minute OHLCV bars with microstructure."""
    if df is None or df.empty:
        return pd.DataFrame()
    df["mid"] = (df["ask"] + df["bid"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    df["imbalance"] = df["ask_volume"] - df["bid_volume"]
    df["minute"] = df["time"].dt.floor("1min")
    bars = df.groupby("minute").agg(
        open=("mid", "first"),
        high=("mid", "max"),
        low=("mid", "min"),
        close=("mid", "last"),
        tick_count=("mid", "count"),
        avg_spread=("spread", "mean"),
        max_spread=("spread", "max"),
        vol_imbalance=("imbalance", "sum"),
        buy_volume=("ask_volume", "sum"),
        sell_volume=("bid_volume", "sum"),
    )
    bars.index.name = "timestamp"
    bars = bars.reset_index()
    bars["total_volume"] = bars["buy_volume"] + bars["sell_volume"]
    bars["buy_ratio"] = bars["buy_volume"] / (bars["total_volume"] + 1e-9)
    return bars


async def process_week(symbol: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """Download one week of data with retry logic."""
    tag = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
    for attempt in range(MAX_RETRIES):
        try:
            sys.stdout.write(f"  {tag}: downloading (attempt {attempt+1}/{MAX_RETRIES})... ")
            sys.stdout.flush()
            await download_range(symbol=symbol, start=start_date, end=end_date)
            sys.stdout.write("reading... ")
            sys.stdout.flush()
            df_raw = read_tick_data(symbol=symbol, start=start_date, end=end_date)
            if df_raw is None or df_raw.empty:
                print("NO DATA (weekend/holiday)")
                return pd.DataFrame()
            raw_n = len(df_raw)
            sys.stdout.write(f"{raw_n:,} ticks -> ")
            sys.stdout.flush()
            bars = ticks_to_1m_bars(df_raw)
            print(f"{len(bars):,} bars")
            del df_raw
            gc.collect()
            return bars
        except Exception as e:
            print(f"ERROR: {e}")
            if attempt < MAX_RETRIES - 1:
                wait_time = 30 * (attempt + 1)
                print(f"  Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                print(f"  Failed after {MAX_RETRIES} attempts, skipping week")
                return pd.DataFrame()
    return pd.DataFrame()


def save_checkpoint(all_bars: list, output_file: Path) -> pd.DataFrame:
    """Merge all accumulated bars and save to disk — with explicit index=False.

    Safety: drops any 'Unnamed: *' columns that might have snuck in from prior
    corrupted reads (belt-and-suspenders).
    """
    if not all_bars:
        return pd.DataFrame()
    combined = pd.concat(all_bars, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"])
    combined = (combined
                .sort_values("timestamp")
                .drop_duplicates(subset=["timestamp"])
                .reset_index(drop=True))
    # Drop any phantom index columns before saving
    for col in list(combined.columns):
        if col.startswith("Unnamed"):
            combined = combined.drop(columns=[col])
    combined.to_csv(output_file, index=False)
    return combined


async def main(start_year: int, end_year: int, pause: int):
    global PAUSE_BETWEEN_WEEKS
    PAUSE_BETWEEN_WEEKS = pause

    # Configure tick_vault (same settings as download_fx_universal.py)
    reload_config(
        base_directory=TICK_VAULT_DIR,
        worker_per_proxy=1,
        fetch_max_retry_attempts=5,
        fetch_base_retry_delay=5.0,
        worker_queue_timeout=120.0,
    )

    print("=" * 66)
    print(f"  {SYMBOL} 1-Min Bar RE-DOWNLOAD ({start_year}-{end_year})")
    print(f"  Output:  {OUTPUT_FILE}")
    print(f"  Safety:  fresh folder — will NOT touch existing CSVs")
    print(f"  Pause:   {PAUSE_BETWEEN_WEEKS}s between weeks")
    print("=" * 66)
    print()

    # Resume from fresh folder's file only (never reads the corrupted one)
    existing_bars = []
    last_date = None
    if OUTPUT_FILE.exists():
        try:
            existing_df = pd.read_csv(OUTPUT_FILE)
            # Drop any phantom index columns that snuck in before resuming
            for col in list(existing_df.columns):
                if col.startswith("Unnamed"):
                    existing_df = existing_df.drop(columns=[col])
            existing_df["timestamp"] = pd.to_datetime(existing_df["timestamp"])
            n_existing = len(existing_df)
            last_date = existing_df["timestamp"].max()
            existing_bars.append(existing_df)
            print(f"  RESUMING: {n_existing:,} existing bars in fresh folder")
            print(f"  Last:     {last_date}")
            print()
        except Exception as e:
            print(f"  Could not load existing fresh file: {e}")
            print(f"  Starting fresh.")
            print()

    all_bars = existing_bars.copy()

    current_date = datetime(start_year, 1, 1)
    if last_date:
        current_date = last_date.to_pydatetime().replace(tzinfo=None) + timedelta(days=1)

    end_date_limit = min(datetime(end_year + 1, 1, 1), datetime.now())
    if current_date >= end_date_limit:
        print(f"  Already up to date. Last: {last_date}")
        return

    total_weeks = int((end_date_limit - current_date).days / 7) + 1
    print(f"  ~{total_weeks} weeks to process")
    est_min = total_weeks * (PAUSE_BETWEEN_WEEKS + 5) / 60
    print(f"  Estimated max time: {est_min:.0f} minutes (faster if cache hits)")
    print()

    start_time = time.time()
    week_count = 0

    while current_date < end_date_limit:
        week_end = min(current_date + timedelta(days=7), end_date_limit)
        bars = await process_week(SYMBOL, current_date, week_end)
        if not bars.empty:
            all_bars.append(bars)
        week_count += 1

        if week_count % CHECKPOINT_INTERVAL == 0 and all_bars:
            combined = save_checkpoint(all_bars, OUTPUT_FILE)
            elapsed = time.time() - start_time
            weeks_remaining = total_weeks - week_count
            eta_min = (elapsed / max(week_count, 1)) * weeks_remaining / 60
            print(f"  -> Checkpoint: {len(combined):,} total bars. "
                  f"Week {week_count}/{total_weeks}, ETA {eta_min:.0f}min")
            print()
            all_bars = [combined]
            gc.collect()

        current_date = week_end
        if current_date < end_date_limit:
            await asyncio.sleep(PAUSE_BETWEEN_WEEKS)

    if all_bars:
        combined = save_checkpoint(all_bars, OUTPUT_FILE)
        elapsed = time.time() - start_time
        print()
        print("=" * 66)
        print(f"  DONE: {SYMBOL}")
        print(f"  Bars:  {len(combined):,}")
        print(f"  Size:  {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")
        print(f"  Range: {combined['timestamp'].iloc[0]} to {combined['timestamp'].iloc[-1]}")
        print(f"  Time:  {elapsed/60:.1f} min")
        print(f"  File:  {OUTPUT_FILE}")
        print("=" * 66)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-download EURUSD 1-min bars (writes to fresh folder only).",
    )
    parser.add_argument("--start", type=int, default=2018)
    parser.add_argument("--end", type=int, default=2026)
    parser.add_argument("--pause", type=int, default=15,
                        help="Seconds between weekly chunks (default: 15)")
    args = parser.parse_args()

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main(args.start, args.end, args.pause))
