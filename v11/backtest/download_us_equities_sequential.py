"""Sequential US-equities 1-min bar downloader (IBKR `reqHistoricalData`).

Mirrors the structure of `download_xagusd_sequential.py`:
  - sequential, one chunk at a time (no parallel fan-out)
  - polite sleep between requests
  - resumable on re-run (reads existing CSV, continues from last bar)
  - one CSV per ticker
…but the transport is IBKR (`Stock` + `reqHistoricalData`), not Dukascopy,
because the universe is US equities/ETFs.

Pre-registered universe and date range are module-level constants. There are
NO command-line overrides for them — pre-registration is the entire point of
this script. The only flag accepted is `--smoke`, which runs SPY for one
short window to a sandbox directory and exists purely to verify the pipeline
without polluting canonical data.

TSM is an ADR. Pre-market behavior on the US ticker partly reflects overnight
trading of the underlying (2330.TW) on the Taiwan Stock Exchange. We
deliberately keep TSM in the universe so the research module can compare a
"foreign-overnight-influenced" name against pure US mega-caps.

Output:
    tick_vault_data/us_equities/<TICKER>.csv  with columns
    date, open, high, low, close, volume

Run:
    # Canonical run (15 tickers, 2018-01-01 → today, 1m bars, ext-hours, TRADES)
    python -m v11.backtest.download_us_equities_sequential

    # Smoke test (SPY only, short window, sandbox output)
    python -m v11.backtest.download_us_equities_sequential --smoke
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

from ib_insync import IB, Stock, util


# ── Project root / .env ─────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


# ── Pre-registered universe (DO NOT change without a new pre-registration) ──

ETFS = ["SPY", "QQQ", "IWM", "DIA"]

# Mega-caps. TSM is a Taiwan-Semiconductor ADR — pre-market activity partly
# reflects overnight trading of 2330.TW on the Taiwan exchange.
MEGA_CAPS = [
    "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOGL", "TSLA",
    "INTC", "MU", "AMD", "TSM",
]

UNIVERSE: list[str] = ETFS + MEGA_CAPS

# ── Pre-registered date range and bar settings ──────────────────────────────

START_DATE = datetime(2018, 1, 1)
# END_DATE is dynamic = "today" at run time. See `_today_utc()` below.

BAR_SIZE = "1 min"
WHAT_TO_SHOW = "TRADES"   # OHLCV with real volume for stocks/ETFs
USE_RTH = False           # include pre- and post-market

# Per-request chunk size. 5 D is the largest value that consistently
# returned data at every probed end-date back to 2018-06 in the
# `_probe_us_equities_head_timestamp.py` matrix run on 2026-05-08. 10 D
# (used by `download_1m_bars.py` in nautilus0 for FX) gets server-side
# "Query cancelled" errors for older equity dates because the trades-data
# computation times out. 5 D ≈ one trading week per chunk; doubles the
# request count vs 10 D but extends reachable depth into 2018.
CHUNK_DAYS = 5

# Pacing between successful requests. 12 s is the proven setting from the
# same nautilus0 downloader. Aggressive values (≤5 s) trigger IBKR error 162
# (pacing violation) on long runs even though individual chunks differ.
PACE_DELAY_S = 12.0
# Long backoff on pacing violation / 162 errors.
PACING_BACKOFF_S = 60.0
MAX_CONSEC_PACING = 3
# Per-request timeout. 1-min bar requests are usually back in <5s.
REQ_TIMEOUT_S = 30.0
# After this many consecutive empty chunks, jump forward by EMPTY_SKIP_DAYS
# rather than crawl through an IBKR data gap one chunk at a time.
EMPTY_STREAK_LIMIT = 10
EMPTY_SKIP_DAYS = 7
# IB Gateway restarts daily (IBC schedules a 22:00/14:00 ET cold restart).
# Without these, one restart kills a multi-hour run — the 2026-05-08 run
# lost 14 of 15 tickers exactly this way.
RECONNECT_MAX_ATTEMPTS = 10          # bumped from 6 on 2026-05-10 — the
                                     # 2026-05-09 kill-test came back on
                                     # attempt 6/6 (cumulative 210s sleeps
                                     # + 6×~20s timeouts = ~5.5min budget).
                                     # Gateway restart + 2FA approval can
                                     # plausibly take longer than that on a
                                     # bad day; 10 attempts buys ~9.5min.
RECONNECT_BASE_DELAY_S = 10.0        # 10, 20, 30, 40, 50, 60 — capped at 60
RECONNECT_MAX_DELAY_S = 60.0
# After this many consecutive disconnect errors on a single ticker (even
# with successful reconnects in between), give up on that ticker and move
# on. Prevents pathological loops if Gateway is in a bad state.
MAX_CONSEC_DISCONNECTS = 8

# ── Output paths ────────────────────────────────────────────────────────────

OUTPUT_DIR = ROOT / "tick_vault_data" / "us_equities"
SMOKE_DIR = OUTPUT_DIR / "_smoke"

# ── Connection (IB_CLIENT_ID forced to 2 to avoid colliding with run_live) ──

IBKR_HOST = os.getenv("IB_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IB_PORT", "4002"))
IBKR_CLIENT_ID = 2  # NOT IB_CLIENT_ID from .env; that one belongs to live runner.


# ── Helpers ─────────────────────────────────────────────────────────────────

def _today_utc() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _fmt_end(dt: datetime) -> str:
    """IBKR endDateTime: space-separated `YYYYMMDD HH:MM:SS UTC`.

    Two forms are documented (error 10314 lists both):
      - `YYYYMMDD-HH:MM:SS`            (dash, implicit UTC, NO suffix)
      - `YYYYMMDD HH:MM:SS US/Eastern` (space + explicit timezone)

    Empirically the dash form is finicky on some Gateway builds. The
    space + ' UTC' form is what nautilus0/download_1m_bars.py used
    successfully across thousands of requests, so we mirror that.
    """
    return dt.strftime("%Y%m%d %H:%M:%S") + " UTC"


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce the `date` column to tz-naive UTC.

    IBKR returns timestamps in the contract's local exchange time with a
    timezone offset (e.g. `2024-01-15 09:30:00-05:00` for NYSE). We convert
    to UTC and then strip the tz so every datetime in the script — IBKR
    response, CSV-on-disk, `start`/`end` constants — is naive-UTC. This
    avoids the offset-naive vs offset-aware comparison errors that bit us
    on the resume path.
    """
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
    """Try to bring `ib` back to a connected state with bounded retries.

    Mirrors the proven pattern in `v11/execution/ibkr_connection.py`:
      - already connected → True (no-op)
      - else: disconnect cleanly, then reconnect with linear backoff
      - bounded by RECONNECT_MAX_ATTEMPTS

    Returns True on success, False once we've given up.
    """
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
            or "connection" in msg and "lost" in msg)


def _trading_day_fraction(df: pd.DataFrame, start: datetime, end: datetime) -> float:
    """Approximate fraction of NYSE trading days present in `df`.

    Uses pandas business-day calendar (weekdays). This slightly over-counts
    because it does not subtract NYSE holidays — fine as a sanity check, not
    a precise measurement.
    """
    if df.empty:
        return 0.0
    bd = pd.bdate_range(start.date(), end.date())
    if len(bd) == 0:
        return 0.0
    have_dates = pd.to_datetime(df["date"]).dt.normalize().unique()
    have_set = set(pd.Timestamp(d) for d in have_dates)
    expected = set(bd)
    return len(have_set & expected) / len(expected)


# ── Per-ticker download ─────────────────────────────────────────────────────

def download_ticker(
    ib: IB,
    symbol: str,
    start: datetime,
    end: datetime,
    csv_path: Path,
) -> dict:
    """Download missing bars for `symbol` between `start` and `end` and
    append-merge into `csv_path`.

    Returns a small dict with summary info for the final report.
    """
    print(f"\n  {symbol}:")
    # If we drifted into a disconnected state during the previous ticker's
    # pacing sleeps (Gateway daily restart, etc.), reconnect before doing
    # anything else. Without this, qualifyContracts will raise immediately
    # and the ticker is wrongly marked crashed.
    if not ib.isConnected():
        if not _reconnect(ib):
            print(f"    [SKIP] reconnect failed before {symbol} — "
                  f"abandoning remaining tickers")
            raise ConnectionError("Gateway unreachable, giving up")
    contract = Stock(symbol, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        print(f"    [SKIP] could not qualify {symbol}")
        return {"symbol": symbol, "status": "qualify_failed",
                "rows": 0, "first": None, "last": None, "td_frac": 0.0}

    existing = _read_existing(csv_path)
    last_ts = _last_bar_ts(existing)

    if last_ts is not None:
        # Resume: only fetch what's after the last bar on disk
        resume_from = last_ts + timedelta(minutes=1)
        if resume_from >= end:
            print(f"    [DONE-ON-DISK] last bar {last_ts}, "
                  f"already covers requested range")
            df_full = existing.copy()
            return {
                "symbol": symbol, "status": "already_complete",
                "rows": len(df_full),
                "first": df_full["date"].min(),
                "last": df_full["date"].max(),
                "td_frac": _trading_day_fraction(df_full, start, end),
            }
        print(f"    RESUMING from {resume_from} (existing rows={len(existing):,})")
        cursor_start = resume_from
        accumulated = [existing]
    else:
        cursor_start = start
        accumulated = []

    # Walk forward in CHUNK_DAYS-sized windows.
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
                whatToShow=WHAT_TO_SHOW,
                useRTH=USE_RTH,
                formatDate=1,
                timeout=REQ_TIMEOUT_S,
            )
        except Exception as exc:
            msg = str(exc).lower()
            # Gateway daily restart / half-open socket — reconnect and retry
            # the same chunk. Bounded by MAX_CONSEC_DISCONNECTS so a wedged
            # Gateway can't burn the whole run on one ticker.
            if _is_disconnect_error(exc):
                consec_disconnect += 1
                if consec_disconnect >= MAX_CONSEC_DISCONNECTS:
                    print(f"    [!!] {consec_disconnect} consecutive disconnect "
                          f"errors on {symbol} — abandoning ticker")
                    chunks_failed += 1
                    raise  # bubble up so run() can decide whether to abort
                if not _reconnect(ib):
                    chunks_failed += 1
                    raise ConnectionError("Gateway unreachable")
                # Re-qualify after reconnect (contract conId is re-issued
                # in some failure modes) then retry the same chunk.
                try:
                    ib.qualifyContracts(contract)
                except Exception:
                    pass
                continue
            if "pacing" in msg or "162" in msg:
                consec_pacing += 1
                if consec_pacing >= MAX_CONSEC_PACING:
                    print(f"    [!] {consec_pacing} consecutive pacing errors — "
                          f"giving up on {symbol}")
                    chunks_failed += 1
                    break
                print(f"    [pacing] backing off {PACING_BACKOFF_S}s "
                      f"(consec={consec_pacing})")
                time.sleep(PACING_BACKOFF_S)
                # retry same chunk
                continue
            print(f"    [!] {chunk_end.date()}: {type(exc).__name__}: {exc}")
            chunks_failed += 1
            # advance past this chunk; do NOT retry-loop forever
            cursor_start = chunk_end
            chunk_end = cursor_start + timedelta(days=CHUNK_DAYS)
            time.sleep(PACE_DELAY_S)
            continue

        if not bars:
            chunks_empty += 1
            consec_empty += 1
            if consec_empty >= EMPTY_STREAK_LIMIT:
                print(f"    {chunk_end.date()}: {consec_empty} consecutive empty "
                      f"chunks — jumping ahead {EMPTY_SKIP_DAYS}d")
                cursor_start = chunk_end + timedelta(days=EMPTY_SKIP_DAYS)
                chunk_end = cursor_start + timedelta(days=CHUNK_DAYS)
                consec_empty = 0
                time.sleep(PACE_DELAY_S)
                continue
            print(f"    {chunk_end.date()}: no data")
        else:
            df_chunk = util.df(bars)
            # Keep only the requested columns
            keep = [c for c in ("date", "open", "high", "low", "close", "volume")
                    if c in df_chunk.columns]
            df_chunk = df_chunk[keep].copy()
            df_chunk = _normalize_dates(df_chunk)
            accumulated.append(df_chunk)
            new_rows += len(df_chunk)
            chunks_ok += 1
            consec_pacing = 0     # success → reset pacing counter
            consec_empty = 0      # success → reset empty-streak counter
            consec_disconnect = 0  # success → reset disconnect-streak counter

            # Checkpoint after each chunk so a kill mid-run is recoverable.
            combined = pd.concat(accumulated, ignore_index=True)
            _save(combined, csv_path)
            accumulated = [combined]  # collapse to a single frame

        # advance window
        cursor_start = chunk_end
        chunk_end = cursor_start + timedelta(days=CHUNK_DAYS)
        time.sleep(PACE_DELAY_S)

    # Final stats from disk (post-checkpoint).
    df_final = _read_existing(csv_path)
    if df_final is None:
        return {"symbol": symbol, "status": "no_data",
                "rows": 0, "first": None, "last": None, "td_frac": 0.0}

    elapsed = time.time() - t_start
    print(f"    chunks ok/empty/failed = {chunks_ok}/{chunks_empty}/{chunks_failed}  "
          f"new_rows={new_rows:,}  total={len(df_final):,}  "
          f"elapsed={elapsed/60:.1f}min")

    return {
        "symbol": symbol,
        "status": "ok" if chunks_failed == 0 else "partial",
        "rows": len(df_final),
        "first": df_final["date"].min(),
        "last": df_final["date"].max(),
        "td_frac": _trading_day_fraction(df_final, start, end),
    }


# ── Main ────────────────────────────────────────────────────────────────────

def run(universe: list[str], start: datetime, end: datetime,
        out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("  US EQUITIES sequential 1-min downloader (IBKR)")
    print(f"  Host:    {IBKR_HOST}:{IBKR_PORT}  clientId={IBKR_CLIENT_ID}")
    print(f"  Range:   {start.date()} → {end.date()}")
    print(f"  Bars:    {BAR_SIZE}  whatToShow={WHAT_TO_SHOW}  useRTH={USE_RTH}")
    print(f"  Chunks:  {CHUNK_DAYS} D  pace={PACE_DELAY_S}s  "
          f"pacing-backoff={PACING_BACKOFF_S}s")
    print(f"  Tickers: {universe}")
    print(f"  Output:  {out_dir}")
    print("=" * 78)

    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
    except Exception as e:
        print(f"\n  [FATAL] could not connect to IBKR at "
              f"{IBKR_HOST}:{IBKR_PORT} (clientId={IBKR_CLIENT_ID}): {e}")
        print("  Is IB Gateway running? "
              "Did you bring it up via ~/ibc/gatewaystartmacos.sh ?")
        sys.exit(1)

    print("  connected.\n")
    results = []
    try:
        for sym in universe:
            csv_path = out_dir / f"{sym}.csv"
            try:
                res = download_ticker(ib, sym, start, end, csv_path)
            except ConnectionError as exc:
                # Gateway is unreachable even after reconnect attempts.
                # Don't churn through the remaining tickers marking each
                # crashed — surface a clean stop with a dedicated status so
                # the next run can pick up from here.
                print(f"    [!!] {sym}: {exc} — STOPPING run "
                      f"(remaining tickers untouched, will resume next run)")
                results.append({"symbol": sym, "status": "abandoned_no_gateway",
                                "rows": 0, "first": None, "last": None, "td_frac": 0.0})
                break
            except Exception as exc:
                print(f"    [!!] {sym} crashed mid-download: "
                      f"{type(exc).__name__}: {exc} — moving on")
                res = {"symbol": sym, "status": "crashed",
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
    print(f"  DOWNLOAD REPORT  ({start.date()} → {end.date()})")
    print("=" * 78)
    hdr = f"  {'Symbol':<8} {'Status':<18} {'Rows':>10}  {'First':<19}  {'Last':<19}  {'TDfrac':>7}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in results:
        first = str(r["first"])[:19] if r["first"] is not None else "—"
        last = str(r["last"])[:19] if r["last"] is not None else "—"
        print(f"  {r['symbol']:<8} {r['status']:<18} {r['rows']:>10,}  "
              f"{first:<19}  {last:<19}  {r['td_frac']:>7.2%}")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(
        description=("Sequential 1-min bar downloader for the pre-registered "
                     "US equities universe. The universe and date range are "
                     "module-level constants; there are no overrides for them."))
    parser.add_argument(
        "--smoke", action="store_true",
        help=("Smoke test: SPY only, 2024-01-01 → 2024-02-01, sandbox output "
              "directory. Used solely to verify the pipeline before running "
              "the canonical job."))
    args = parser.parse_args()

    if args.smoke:
        universe = ["SPY"]
        start = datetime(2024, 1, 1)
        end = datetime(2024, 2, 1)
        out_dir = SMOKE_DIR
        print("\n  *** SMOKE TEST MODE — sandbox output, NOT canonical data ***\n")
    else:
        universe = UNIVERSE
        start = START_DATE
        end = _today_utc()
        out_dir = OUTPUT_DIR

    results = run(universe, start, end, out_dir)
    print_report(results, start, end)


if __name__ == "__main__":
    main()
