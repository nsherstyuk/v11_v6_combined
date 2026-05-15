"""Sequential GBPCHF 1-min bar downloader for MID, BID, ASK.

Mirrors `download_us_equities_sequential.py` but adapted for FX:
  - Forex contract on IDEALPRO (no SMART routing for cash forex)
  - Three data types: MIDPOINT, BID, ASK — separate CSVs each
  - Sequential per-data-type, resumable from existing CSV
  - clientId=3 (US-equities used 2; v11 live uses 11; no collision)

Per Nick 2026-05-14: research GBPCHF cross-pair. Empirical BID/ASK
gives session-by-hour spread distribution from IBKR's actual quotes,
which is more authoritative than any third-party estimate for cost
modeling. MID is the standard backtest input.

Output layout:
    tick_vault_data/fx/GBPCHF_MIDPOINT.csv
    tick_vault_data/fx/GBPCHF_BID.csv
    tick_vault_data/fx/GBPCHF_ASK.csv

Each CSV: date, open, high, low, close (FX has no volume from IBKR).

Run:
    # Canonical run (~7 years, 2019-01-01 → today, 1m bars, three data types)
    python -m v11.backtest.download_gbpchf_sequential

    # Smoke test (1 week of MIDPOINT only, sandbox output)
    python -m v11.backtest.download_gbpchf_sequential --smoke
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

from ib_insync import IB, Forex, util


# ── Project root / .env ─────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


# ── Pre-registered parameters ───────────────────────────────────────────────

PAIR = "GBPCHF"
DATA_TYPES = ("MIDPOINT", "BID", "ASK")  # IBKR whatToShow values

START_DATE = datetime(2019, 1, 1)  # ~7.4 years to today
# END_DATE is dynamic = "today" at run time.

BAR_SIZE = "1 min"
USE_RTH = False  # FX trades 24/5; useRTH irrelevant but pass False

# Chunk size in days. FX `download_1m_bars.py` in nautilus0 used 10 D
# successfully across thousands of requests. Equities had to drop to 5 D
# because the TRADES computation timed out on older dates; FX MIDPOINT/
# BID/ASK do not have that problem.
CHUNK_DAYS = 10

# Pacing — same as proven values from the equities downloader.
PACE_DELAY_S = 12.0
PACING_BACKOFF_S = 60.0
MAX_CONSEC_PACING = 3
REQ_TIMEOUT_S = 30.0

# After N empty chunks, skip a week. FX rarely has gaps but the
# weekend-spanning chunks can be empty if endDateTime falls on a Saturday.
EMPTY_STREAK_LIMIT = 10
EMPTY_SKIP_DAYS = 7

# Survive Gateway daily restart at 01:15 EDT.
RECONNECT_MAX_ATTEMPTS = 10
RECONNECT_BASE_DELAY_S = 10.0
RECONNECT_MAX_DELAY_S = 60.0
MAX_CONSEC_DISCONNECTS = 8


# ── Output paths ────────────────────────────────────────────────────────────

OUTPUT_DIR = ROOT / "tick_vault_data" / "fx"
SMOKE_DIR = OUTPUT_DIR / "_smoke"


# ── Connection ──────────────────────────────────────────────────────────────

IBKR_HOST = os.getenv("IB_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IB_PORT", "4002"))
# clientId=3: us-equities used 2 (idle now), v11 live uses 11, ibkr-health
# probe uses 99. 3 keeps us isolated from anything else and reproducible.
IBKR_CLIENT_ID = 3


# ── Helpers (identical to us-equities downloader) ───────────────────────────

def _today_utc() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _fmt_end(dt: datetime) -> str:
    """IBKR endDateTime: space + ' UTC' (the form that works reliably)."""
    return dt.strftime("%Y%m%d %H:%M:%S") + " UTC"


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    return df


def _read_existing(csv_path: Path) -> Optional[pd.DataFrame]:
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    if df.empty:
        return None
    return _normalize_dates(df)


def _last_bar_ts(df: Optional[pd.DataFrame]) -> Optional[datetime]:
    if df is None or df.empty:
        return None
    return df["date"].max().to_pydatetime()


def _save(df: pd.DataFrame, csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df = (df.sort_values("date")
            .drop_duplicates(subset=["date"])
            .reset_index(drop=True))
    df.to_csv(csv_path, index=False)


def _reconnect(ib: IB) -> bool:
    if ib.isConnected():
        return True
    print(f"    [!] connection lost — attempting reconnect to "
          f"{IBKR_HOST}:{IBKR_PORT} clientId={IBKR_CLIENT_ID}")
    for attempt in range(1, RECONNECT_MAX_ATTEMPTS + 1):
        try:
            try:
                ib.disconnect()
            except Exception:
                pass
            ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=20)
            if ib.isConnected():
                print(f"    [!] reconnected on attempt {attempt}")
                return True
        except Exception as e:
            print(f"    [!] reconnect {attempt}/{RECONNECT_MAX_ATTEMPTS} "
                  f"failed: {type(e).__name__}: {e}")
        delay = min(RECONNECT_BASE_DELAY_S * attempt, RECONNECT_MAX_DELAY_S)
        time.sleep(delay)
    print(f"    [!!] reconnect failed after {RECONNECT_MAX_ATTEMPTS} attempts")
    return False


def _is_disconnect_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("not connected" in msg
            or "isconnected=false" in msg
            or ("connection" in msg and "lost" in msg))


def _trading_day_fraction(df: pd.DataFrame, start: datetime,
                          end: datetime) -> float:
    """Fraction of weekdays present. FX trades Sun 22:00 UTC → Fri 22:00 UTC,
    so weekday coverage is the right denominator (no exchange holiday
    subtraction needed; FX trades through most US holidays)."""
    if df.empty:
        return 0.0
    bd = pd.bdate_range(start.date(), end.date())
    if len(bd) == 0:
        return 0.0
    have_dates = pd.to_datetime(df["date"]).dt.normalize().unique()
    have_set = set(pd.Timestamp(d) for d in have_dates)
    expected = set(bd)
    return len(have_set & expected) / len(expected)


# ── Per-data-type download ──────────────────────────────────────────────────

def download_one_type(
    ib: IB,
    contract,
    what_to_show: str,
    start: datetime,
    end: datetime,
    csv_path: Path,
) -> dict:
    """Download bars of one `whatToShow` type (MIDPOINT / BID / ASK)."""
    print(f"\n  {PAIR} {what_to_show}:")

    if not ib.isConnected():
        if not _reconnect(ib):
            print(f"    [SKIP] reconnect failed before {what_to_show} — "
                  f"abandoning remaining data types")
            raise ConnectionError("Gateway unreachable, giving up")

    existing = _read_existing(csv_path)
    last_ts = _last_bar_ts(existing)

    if last_ts is not None:
        resume_from = last_ts + timedelta(minutes=1)
        if resume_from >= end:
            print(f"    [DONE-ON-DISK] last bar {last_ts}, "
                  f"already covers requested range")
            df_full = existing.copy()
            return {
                "what": what_to_show, "status": "already_complete",
                "rows": len(df_full),
                "first": df_full["date"].min(),
                "last": df_full["date"].max(),
                "td_frac": _trading_day_fraction(df_full, start, end),
            }
        print(f"    RESUMING from {resume_from} "
              f"(existing rows={len(existing):,})")
        cursor_start = resume_from
        accumulated = [existing]
    else:
        cursor_start = start
        accumulated = []

    chunk_end = cursor_start + timedelta(days=CHUNK_DAYS)
    consec_pacing = 0
    consec_empty = 0
    consec_disconnect = 0
    chunks_ok = 0
    chunks_empty = 0
    chunks_failed = 0
    new_rows = 0
    t_start = time.time()

    while cursor_start < end:
        if chunk_end > end:
            chunk_end = end

        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=_fmt_end(chunk_end),
                durationStr=f"{CHUNK_DAYS} D",
                barSizeSetting=BAR_SIZE,
                whatToShow=what_to_show,
                useRTH=USE_RTH,
                formatDate=1,
                timeout=REQ_TIMEOUT_S,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if _is_disconnect_error(exc):
                consec_disconnect += 1
                if consec_disconnect >= MAX_CONSEC_DISCONNECTS:
                    print(f"    [!!] {consec_disconnect} consecutive "
                          f"disconnects — abandoning {what_to_show}")
                    chunks_failed += 1
                    raise
                if not _reconnect(ib):
                    chunks_failed += 1
                    raise ConnectionError("Gateway unreachable")
                try:
                    ib.qualifyContracts(contract)
                except Exception:
                    pass
                continue
            if "pacing" in msg or "162" in msg:
                consec_pacing += 1
                if consec_pacing >= MAX_CONSEC_PACING:
                    print(f"    [!] {consec_pacing} consecutive pacing "
                          f"errors — giving up on {what_to_show}")
                    chunks_failed += 1
                    break
                print(f"    [pacing] backing off {PACING_BACKOFF_S}s "
                      f"(consec={consec_pacing})")
                time.sleep(PACING_BACKOFF_S)
                continue
            print(f"    [!] {chunk_end.date()}: "
                  f"{type(exc).__name__}: {exc}")
            chunks_failed += 1
            cursor_start = chunk_end
            chunk_end = cursor_start + timedelta(days=CHUNK_DAYS)
            time.sleep(PACE_DELAY_S)
            continue

        if not bars:
            chunks_empty += 1
            consec_empty += 1
            if consec_empty >= EMPTY_STREAK_LIMIT:
                print(f"    {chunk_end.date()}: {consec_empty} consecutive "
                      f"empty — skipping {EMPTY_SKIP_DAYS}d")
                cursor_start = chunk_end + timedelta(days=EMPTY_SKIP_DAYS)
                chunk_end = cursor_start + timedelta(days=CHUNK_DAYS)
                consec_empty = 0
                time.sleep(PACE_DELAY_S)
                continue
            print(f"    {chunk_end.date()}: no data")
        else:
            df_chunk = util.df(bars)
            # FX has no real volume; keep only OHLC + date
            keep = [c for c in ("date", "open", "high", "low", "close")
                    if c in df_chunk.columns]
            df_chunk = df_chunk[keep].copy()
            df_chunk = _normalize_dates(df_chunk)
            accumulated.append(df_chunk)
            new_rows += len(df_chunk)
            chunks_ok += 1
            consec_pacing = 0
            consec_empty = 0
            consec_disconnect = 0

            combined = pd.concat(accumulated, ignore_index=True)
            _save(combined, csv_path)
            accumulated = [combined]

        cursor_start = chunk_end
        chunk_end = cursor_start + timedelta(days=CHUNK_DAYS)
        time.sleep(PACE_DELAY_S)

    df_final = _read_existing(csv_path)
    if df_final is None:
        return {"what": what_to_show, "status": "no_data",
                "rows": 0, "first": None, "last": None, "td_frac": 0.0}

    elapsed = time.time() - t_start
    print(f"    chunks ok/empty/failed = "
          f"{chunks_ok}/{chunks_empty}/{chunks_failed}  "
          f"new_rows={new_rows:,}  total={len(df_final):,}  "
          f"elapsed={elapsed/60:.1f}min")

    return {
        "what": what_to_show,
        "status": "ok" if chunks_failed == 0 else "partial",
        "rows": len(df_final),
        "first": df_final["date"].min(),
        "last": df_final["date"].max(),
        "td_frac": _trading_day_fraction(df_final, start, end),
    }


# ── Main ────────────────────────────────────────────────────────────────────

def run(data_types: tuple[str, ...], start: datetime, end: datetime,
        out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(f"  {PAIR} sequential 1-min downloader (IBKR Forex)")
    print(f"  Host:    {IBKR_HOST}:{IBKR_PORT}  clientId={IBKR_CLIENT_ID}")
    print(f"  Range:   {start.date()} → {end.date()}")
    print(f"  Bars:    {BAR_SIZE}  useRTH={USE_RTH}")
    print(f"  Chunks:  {CHUNK_DAYS} D  pace={PACE_DELAY_S}s  "
          f"pacing-backoff={PACING_BACKOFF_S}s")
    print(f"  Types:   {data_types}")
    print(f"  Output:  {out_dir}")
    print("=" * 78)

    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
    except Exception as e:
        print(f"\n  [FATAL] could not connect to IBKR at "
              f"{IBKR_HOST}:{IBKR_PORT} (clientId={IBKR_CLIENT_ID}): {e}")
        sys.exit(1)

    print("  connected.\n")

    # Qualify the Forex contract once (same conId used for all three pulls).
    contract = Forex(PAIR)
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        print(f"  [FATAL] could not qualify {PAIR}")
        ib.disconnect()
        sys.exit(1)
    print(f"  contract qualified: {PAIR} on {contract.exchange}")

    results = []
    try:
        for what in data_types:
            csv_path = out_dir / f"{PAIR}_{what}.csv"
            try:
                res = download_one_type(ib, contract, what, start, end, csv_path)
            except ConnectionError as exc:
                print(f"    [!!] {what}: {exc} — STOPPING run "
                      f"(remaining types untouched, will resume next run)")
                results.append({"what": what,
                                "status": "abandoned_no_gateway",
                                "rows": 0, "first": None, "last": None,
                                "td_frac": 0.0})
                break
            except Exception as exc:
                print(f"    [!!] {what} crashed mid-download: "
                      f"{type(exc).__name__}: {exc} — moving on to next type")
                res = {"what": what, "status": "crashed",
                       "rows": 0, "first": None, "last": None, "td_frac": 0.0}
                results.append(res)
            else:
                results.append(res)
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    return results


def print_report(results: list[dict], start: datetime, end: datetime) -> None:
    print()
    print("=" * 78)
    print(f"  DOWNLOAD REPORT  {PAIR}  ({start.date()} → {end.date()})")
    print("=" * 78)
    hdr = (f"  {'Type':<10} {'Status':<20} {'Rows':>10}  "
           f"{'First':<19}  {'Last':<19}  {'TDfrac':>7}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in results:
        first = str(r["first"])[:19] if r["first"] is not None else "—"
        last = str(r["last"])[:19] if r["last"] is not None else "—"
        print(f"  {r['what']:<10} {r['status']:<20} {r['rows']:>10,}  "
              f"{first:<19}  {last:<19}  {r['td_frac']:>7.2%}")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description=(f"Sequential 1-min downloader for {PAIR} with three "
                     f"data types: MIDPOINT, BID, ASK. clientId=3."))
    parser.add_argument(
        "--smoke", action="store_true",
        help=("Smoke test: 1 week of MIDPOINT only into sandbox dir. "
              "Used to verify the pipeline before the canonical run."))
    args = parser.parse_args()

    if args.smoke:
        data_types = ("MIDPOINT",)
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 8)
        out_dir = SMOKE_DIR
        print("\n  *** SMOKE TEST MODE — sandbox output, NOT canonical data ***\n")
    else:
        data_types = DATA_TYPES
        start = START_DATE
        end = _today_utc()
        out_dir = OUTPUT_DIR

    results = run(data_types, start, end, out_dir)
    print_report(results, start, end)


if __name__ == "__main__":
    main()
