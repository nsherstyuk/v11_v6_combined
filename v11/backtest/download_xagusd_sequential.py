"""Sequential XAGUSD tick downloader — one hour at a time.

The proven parallel downloaders (tick_vault, download_eurusd_full) trigger
Dukascopy's rate limit by firing 168 hour-chunks of a week in parallel.
This script fetches ONE URL at a time with a small delay, and sleeps
longer whenever Dukascopy returns 503. Slower but consistent.

Output: C:\\nautilus0\\data\\1m_csv\\xagusd_1m_tick.csv (resumable — starts
from the max timestamp already in the file).

Run:
    python -m v11.backtest.download_xagusd_sequential
    python -m v11.backtest.download_xagusd_sequential --delay 1.0
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import argparse
import asyncio
import gc
import lzma
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd


SYMBOL = "XAGUSD"
OUTPUT_DIR = Path(r"C:\nautilus0\data\1m_csv")
OUTPUT_FILE = OUTPUT_DIR / f"{SYMBOL.lower()}_1m_tick.csv"
BASE_URL = "https://datafeed.dukascopy.com/datafeed"
PIPET = 1e-3  # Silver scale: 20500 -> 20.500

# Tuning knobs
DEFAULT_DELAY = 0.5          # seconds between successful requests
LONG_BACKOFF_503 = 120.0     # sleep this long on 503/rate limit (2 min)
MAX_CONSEC_503 = 3           # after this many 503s, long-backoff
CHECKPOINT_HOURS = 168       # save CSV every N hours (1 week)
TIMEOUT_S = 30.0


def decode_bi5(data: bytes, hour_dt: datetime) -> pd.DataFrame:
    """Decode one hour of Dukascopy .bi5 ticks into a DataFrame.
    Returns empty DataFrame if file is empty or undecodable."""
    if not data:
        return pd.DataFrame()
    try:
        raw = lzma.decompress(data)
    except lzma.LZMAError:
        return pd.DataFrame()
    n = len(raw) // 20
    if n == 0:
        return pd.DataFrame()
    arr = np.frombuffer(raw, dtype=np.dtype([
        ("time", ">u4"), ("ask", ">u4"), ("bid", ">u4"),
        ("ask_volume", ">f4"), ("bid_volume", ">f4"),
    ]))
    out = np.empty(len(arr), dtype=[
        ("time", "datetime64[ms]"),
        ("ask", "f8"), ("bid", "f8"),
        ("ask_volume", "i8"), ("bid_volume", "i8"),
    ])
    start = np.datetime64(hour_dt, "ms")
    out["time"] = start + arr["time"].astype(np.int64).astype("timedelta64[ms]")
    out["ask"] = arr["ask"].astype(np.float64) * PIPET
    out["bid"] = arr["bid"].astype(np.float64) * PIPET
    out["ask_volume"] = np.round(arr["ask_volume"].astype(np.float64) * 1e6).astype(np.int64)
    out["bid_volume"] = np.round(arr["bid_volume"].astype(np.float64) * 1e6).astype(np.int64)
    return pd.DataFrame(out)


def ticks_to_1m_bars(ticks_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate ticks into 1-minute OHLCV bars (matches download_fx_universal.py)."""
    if ticks_df.empty:
        return pd.DataFrame()
    df = ticks_df.copy()
    df["mid"] = (df["ask"] + df["bid"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    df["imbalance"] = df["ask_volume"] - df["bid_volume"]
    df["minute"] = df["time"].dt.floor("1min")
    bars = df.groupby("minute").agg(
        open=("mid", "first"), high=("mid", "max"),
        low=("mid", "min"), close=("mid", "last"),
        tick_count=("mid", "count"),
        avg_spread=("spread", "mean"), max_spread=("spread", "max"),
        vol_imbalance=("imbalance", "sum"),
        buy_volume=("ask_volume", "sum"),
        sell_volume=("bid_volume", "sum"),
    )
    bars.index.name = "timestamp"
    bars = bars.reset_index()
    bars["total_volume"] = bars["buy_volume"] + bars["sell_volume"]
    bars["buy_ratio"] = bars["buy_volume"] / (bars["total_volume"] + 1e-9)
    return bars


def save_bars(all_bars: list, output_file: Path) -> pd.DataFrame:
    if not all_bars:
        return pd.DataFrame()
    combined = pd.concat(all_bars, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"])
    # Drop any phantom index columns if CSV was previously corrupted
    for col in list(combined.columns):
        if col.startswith("Unnamed"):
            combined = combined.drop(columns=[col])
    combined = (combined
                .sort_values("timestamp")
                .drop_duplicates(subset=["timestamp"])
                .reset_index(drop=True))
    combined.to_csv(output_file, index=False)
    return combined


def url_for(dt: datetime) -> str:
    # Dukascopy uses 0-indexed months in the path
    return (f"{BASE_URL}/{SYMBOL}/{dt.year}/{dt.month - 1:02d}/"
            f"{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


async def fetch_hour(client: httpx.AsyncClient, dt: datetime,
                     consec_503: int, delay: float) -> tuple[pd.DataFrame, int]:
    """Fetch one hour. Returns (bars_df, updated_consec_503)."""
    url = url_for(dt)
    for attempt in range(5):
        try:
            resp = await client.get(url, timeout=TIMEOUT_S)
            if resp.status_code == 200 and resp.content:
                ticks = decode_bi5(resp.content, dt)
                bars = ticks_to_1m_bars(ticks)
                return bars, 0  # success → reset counter
            elif resp.status_code == 404:
                return pd.DataFrame(), 0  # no data (weekend/holiday) is fine
            elif resp.status_code in (429, 503):
                consec_503 += 1
                if consec_503 >= MAX_CONSEC_503:
                    print(f"    [!] {consec_503} consecutive 503s — "
                          f"sleeping {LONG_BACKOFF_503}s to let Dukascopy cool off",
                          flush=True)
                    await asyncio.sleep(LONG_BACKOFF_503)
                    consec_503 = 0
                else:
                    await asyncio.sleep(5 * (attempt + 1))
                continue
            else:
                return pd.DataFrame(), 0  # other HTTP error — skip this hour
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
            await asyncio.sleep(3 * (attempt + 1))
    # Exhausted retries
    return pd.DataFrame(), consec_503


async def main(start_year: int, end_year: int, delay: float):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Resume
    existing_bars: list = []
    last_date = None
    if OUTPUT_FILE.exists():
        df = pd.read_csv(OUTPUT_FILE)
        for col in list(df.columns):
            if col.startswith("Unnamed"):
                df = df.drop(columns=[col])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        if not df.empty:
            last_date = df["timestamp"].max()
            existing_bars.append(df)
            print(f"  RESUMING: {len(df):,} existing bars, last = {last_date}")

    if last_date:
        start_dt = last_date.to_pydatetime().replace(
            minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(hours=1)
    else:
        start_dt = datetime(start_year, 1, 1, 22)

    end_limit = min(datetime(end_year + 1, 1, 1), datetime.now())

    total_hours = int((end_limit - start_dt).total_seconds() / 3600)
    print("=" * 66)
    print(f"  {SYMBOL} Sequential Tick Download")
    print(f"  From:  {start_dt}")
    print(f"  To:    {end_limit}")
    print(f"  Hours: {total_hours:,}")
    print(f"  Delay: {delay}s between requests")
    print(f"  ETA:   {total_hours * (delay + 0.7) / 3600:.1f}h "
          f"(best case; 503 backoffs add to this)")
    print("=" * 66)

    all_bars = existing_bars.copy()
    new_bar_count = 0
    consec_503 = 0
    hours_done = 0
    errors = 0
    start_wall = time.time()
    current = start_dt

    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (compatible; research)"},
        follow_redirects=True,
        http2=False,
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
    ) as client:
        while current < end_limit:
            bars, consec_503 = await fetch_hour(client, current, consec_503, delay)
            if not bars.empty:
                all_bars.append(bars)
                new_bar_count += len(bars)
            else:
                errors += 1

            hours_done += 1

            # Checkpoint every N hours
            if hours_done % CHECKPOINT_HOURS == 0:
                combined = save_bars(all_bars, OUTPUT_FILE)
                all_bars = [combined]
                gc.collect()
                elapsed = time.time() - start_wall
                rate = hours_done / max(elapsed, 1)
                eta_min = (total_hours - hours_done) / max(rate, 0.001) / 60
                pct = hours_done / total_hours * 100
                print(f"  {current.strftime('%Y-%m-%d')} | {pct:5.1f}% | "
                      f"{new_bar_count:,} new bars | "
                      f"{rate * 3600:.0f} hrs/h | ETA {eta_min:.0f}min | "
                      f"empty_hours={errors}", flush=True)

            # Polite delay
            await asyncio.sleep(delay)
            current += timedelta(hours=1)

    # Final save
    combined = save_bars(all_bars, OUTPUT_FILE)
    elapsed = time.time() - start_wall
    print()
    print("=" * 66)
    print(f"  DONE: {SYMBOL}")
    print(f"  Total bars: {len(combined):,}  (added {new_bar_count:,} this run)")
    print(f"  Range:      {combined['timestamp'].min()} -> {combined['timestamp'].max()}")
    print(f"  Wall time:  {elapsed / 60:.1f} min")
    print(f"  File:       {OUTPUT_FILE}")
    print("=" * 66)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=2018)
    parser.add_argument("--end", type=int, default=2026)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help="Seconds between sequential requests (default 0.5)")
    args = parser.parse_args()

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main(args.start, args.end, args.delay))
