"""
Bootstrap Decision Ledger — Backfill Grok decisions from historical data.

Fetches historical IBKR data, simulates ORB/Darvas setups for past days,
calls Grok to get decisions, assesses against actual outcomes, and populates
the decision ledger so future Grok calls have a track record.

EDGE module: does not affect trading logic. Safe to run anytime.

Usage:
    # Preview what would happen (no IBKR, no Grok calls):
    python -m v11.llm.bootstrap_ledger --dry-run

    # Run with IBKR + Grok (costs API tokens):
    python -m v11.llm.bootstrap_ledger --days 15

    # Use existing fetched data (skip IBKR, still calls Grok):
    python -m v11.llm.bootstrap_ledger --from-cache

Flow:
    1. Connect to IBKR, fetch 5-min bars for last N days (XAUUSD)
    2. Fetch daily bars for context
    3. For each trading day:
       a. Extract Asian range (00:00-06:00 UTC) → ORBSignalContext
       b. Call Grok with context + any existing feedback
       c. Assess decision against 08:00-22:00 price action
       d. Record decision + assessment to ledger
    4. Print summary table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Python 3.14 compatibility (same patch as run_live.py) ──────────────────
# Python 3.14 changed asyncio.wait_for to use asyncio.timeout() internally,
# which requires being inside a running task. ib_insync calls wait_for from
# a sync context via nest_asyncio, which doesn't set current_task properly.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

if sys.version_info >= (3, 14):
    _original_wait_for = asyncio.wait_for

    async def _compat_wait_for(fut, timeout, **kwargs):
        if timeout is None:
            return await fut
        fut = asyncio.ensure_future(fut)
        loop = asyncio.get_event_loop()
        timed_out = False

        def _on_timeout():
            nonlocal timed_out
            timed_out = True
            fut.cancel()

        handle = loop.call_later(timeout, _on_timeout)
        try:
            return await fut
        except asyncio.CancelledError:
            if timed_out:
                raise asyncio.TimeoutError()
            raise
        finally:
            handle.cancel()

    asyncio.wait_for = _compat_wait_for

import nest_asyncio
nest_asyncio.apply()

# Project root
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from v11.llm.decision_ledger import DecisionLedger
from v11.llm.assess_decisions import assess_orb_decision

logger = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────────────────────

def _bars_for_day(all_bars: list, target_date: str,
                  start_hour: int, end_hour: int) -> list:
    """Filter bars for a specific date and hour range.

    Args:
        all_bars: List of bar dicts with 'date' (datetime), 'high', 'low', etc.
        target_date: Date string 'YYYY-MM-DD'
        start_hour: Start hour UTC (inclusive)
        end_hour: End hour UTC (exclusive)
    """
    result = []
    for bar in all_bars:
        dt = bar["date"]
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        if date_str == target_date and start_hour <= dt.hour < end_hour:
            result.append(bar)
    return result


def _compute_range(bars: list) -> Optional[Tuple[float, float]]:
    """Get high/low from a list of bars."""
    if not bars:
        return None
    high = max(b["high"] for b in bars)
    low = min(b["low"] for b in bars)
    if high <= low:
        return None
    return (high, low)


def _get_trading_dates(all_bars: list) -> List[str]:
    """Extract unique trading dates from bars, sorted."""
    dates = set()
    for bar in all_bars:
        dt = bar["date"]
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dates.add(dt.strftime("%Y-%m-%d"))
    return sorted(dates)


# ── IBKR data fetching ────────────────────────────────────────────────────

def fetch_ibkr_data(days: int, cache_path: Path) -> Tuple[list, list]:
    """Fetch historical bars from IBKR and cache to disk.

    Returns (intraday_bars, daily_bars) as lists of dicts.
    Uses async connection inside a task for Python 3.14 compatibility.
    """

    async def _fetch():
        from ib_insync import IB, Contract
        from v11.config.live_config import XAUUSD_INSTRUMENT

        ib = IB()
        print("Connecting to IBKR...")
        await ib.connectAsync("127.0.0.1", 4002, clientId=99, timeout=20)
        print(f"Connected: {ib.isConnected()}")

        # Build XAUUSD contract
        contract = Contract(
            symbol=XAUUSD_INSTRUMENT.symbol,
            exchange=XAUUSD_INSTRUMENT.exchange,
            secType=XAUUSD_INSTRUMENT.sec_type,
            currency=XAUUSD_INSTRUMENT.currency,
        )
        ib.qualifyContracts(contract)

        # Fetch 5-min bars (covers more days than 1-min)
        duration = f"{days} D"
        print(f"Fetching {duration} of 5-min bars for XAUUSD...")
        bars_5m = await ib.reqHistoricalDataAsync(
            contract, endDateTime="",
            durationStr=duration, barSizeSetting="5 mins",
            whatToShow="MIDPOINT", useRTH=False, formatDate=2)
        print(f"  Got {len(bars_5m)} 5-min bars")

        intraday = []
        for b in bars_5m:
            dt = b.date
            if hasattr(dt, 'tzinfo') and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            intraday.append({
                "date": dt.isoformat() if hasattr(dt, 'isoformat') else str(dt),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": getattr(b, 'volume', 0),
            })

        # Fetch daily bars for context (30 days)
        print("Fetching 30 daily bars for XAUUSD context...")
        bars_daily = await ib.reqHistoricalDataAsync(
            contract, endDateTime="",
            durationStr="30 D", barSizeSetting="1 day",
            whatToShow="MIDPOINT", useRTH=False, formatDate=2)
        print(f"  Got {len(bars_daily)} daily bars")

        daily = []
        for b in bars_daily:
            dt = b.date
            daily.append({
                "date": str(dt),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
            })

        ib.disconnect()
        print("IBKR disconnected.")
        return intraday, daily

    loop = asyncio.get_event_loop()
    intraday, daily = loop.run_until_complete(_fetch())

    # Cache to disk
    cache_data = {"intraday": intraday, "daily": daily,
                  "fetched_at": datetime.now(timezone.utc).isoformat()}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(cache_data, f, indent=2, default=str)
    print(f"Cached data to {cache_path}")

    return intraday, daily


def load_cached_data(cache_path: Path) -> Tuple[list, list]:
    """Load previously fetched data from cache file."""
    with open(cache_path, "r") as f:
        data = json.load(f)
    print(f"Loaded cached data from {cache_path} "
          f"(fetched {data.get('fetched_at', '?')})")
    return data["intraday"], data["daily"]


# ── Grok calling ──────────────────────────────────────────────────────────

def call_grok_for_day(
    grok_filter,
    instrument: str,
    range_high: float,
    range_low: float,
    daily_bars_before: list,
    day_date: str,
    day_of_week: str,
    feedback_table: str,
) -> dict:
    """Build ORBSignalContext and call Grok. Returns decision dict."""
    import asyncio
    from v11.llm.models import ORBSignalContext, DailyBarData

    range_size = range_high - range_low
    mid = (range_high + range_low) / 2
    size_pct = (range_size / mid * 100) if mid > 0 else 0.0

    # Compute range_vs_avg from daily bars
    if daily_bars_before:
        avg_range = sum(
            d["high"] - d["low"] for d in daily_bars_before
        ) / len(daily_bars_before)
        range_vs_avg = range_size / avg_range if avg_range > 0 else 1.0
    else:
        range_vs_avg = 1.0

    # Build daily bar context
    daily_bar_models = []
    for d in daily_bars_before[-10:]:
        daily_bar_models.append(DailyBarData(
            date=d["date"][:10] if len(d["date"]) > 10 else d["date"],
            o=d["open"], h=d["high"], l=d["low"], c=d["close"],
        ))

    # Compute atr_regime from daily bars (range_size / avg daily range)
    atr_regime = range_vs_avg  # best proxy available from daily data

    context = ORBSignalContext(
        instrument=instrument,
        range_high=range_high,
        range_low=range_low,
        range_size=round(range_size, 2),
        range_size_pct=round(size_pct, 3),
        range_vs_avg=round(range_vs_avg, 2),
        atr_regime=round(atr_regime, 2),
        current_price=mid,
        distance_from_high=round(mid - range_high, 2),
        distance_from_low=round(mid - range_low, 2),
        session="LONDON",
        day_of_week=day_of_week,
        current_time_utc=f"{day_date}T08:00:00+00:00",
        recent_bars=[],
        daily_bars=daily_bar_models,
    )

    # Call Grok (using existing event loop — nest_asyncio allows re-entrant calls)
    loop = asyncio.get_event_loop()
    decision = loop.run_until_complete(
        grok_filter.evaluate_orb_signal(context))

    return {
        "approved": decision.approved,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        "risk_flags": list(decision.risk_flags),
        "context": {
            "range_high": range_high,
            "range_low": range_low,
            "range_size": round(range_size, 2),
            "range_vs_avg": round(range_vs_avg, 2),
            "current_price": mid,
            "session": "LONDON",
            "day_of_week": day_of_week,
        },
    }


# ── Main bootstrap loop ──────────────────────────────────────────────────

def run_bootstrap(days: int, dry_run: bool = False, from_cache: bool = False,
                  log_dir: str = None, delay: float = 3.0):
    """Bootstrap the decision ledger with historical ORB decisions.

    Args:
        days: Number of trading days to backfill
        dry_run: If True, compute ranges but don't call Grok
        from_cache: If True, use cached data instead of fetching from IBKR
        log_dir: Path to grok_logs directory
        delay: Seconds to wait between Grok calls (rate limiting)
    """
    if log_dir is None:
        log_dir = str(ROOT / "grok_logs")

    cache_path = Path(log_dir) / "bootstrap_cache.json"
    ledger = DecisionLedger(log_dir)

    # ── Step 1: Get data ──────────────────────────────────────────
    if from_cache:
        if not cache_path.exists():
            print(f"No cache found at {cache_path}. Run without --from-cache first.")
            return
        intraday, daily = load_cached_data(cache_path)
    else:
        if dry_run:
            print("[DRY RUN] Would fetch IBKR data. Use --from-cache with existing data.")
            return
        intraday, daily = fetch_ibkr_data(days + 5, cache_path)

    # ── Step 2: Identify trading days ────────────────────────────
    trading_dates = _get_trading_dates(intraday)
    print(f"\nFound {len(trading_dates)} trading days in data")

    # Skip weekends and last day (incomplete)
    valid_dates = []
    for d in trading_dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        if dt.weekday() < 5:  # Mon-Fri
            valid_dates.append(d)

    # Don't process today (incomplete)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    valid_dates = [d for d in valid_dates if d != today]

    # Limit to requested number of days
    valid_dates = valid_dates[-days:]
    print(f"Processing {len(valid_dates)} trading days (excluding today)\n")

    # ── Step 3: Initialize Grok (if not dry-run) ─────────────────
    grok_filter = None
    if not dry_run:
        api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
        if not api_key:
            print("ERROR: No XAI_API_KEY or GROK_API_KEY in .env")
            return
        from v11.llm.grok_filter import GrokFilter
        grok_filter = GrokFilter(
            api_key=api_key,
            timeout=45.0,  # generous for historical batch
            log_dir=log_dir,
        )

    # ── Step 4: Process each day ─────────────────────────────────
    results = []

    for i, date_str in enumerate(valid_dates):
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dow = dt.strftime("%A")

        # Check if already in ledger
        existing_id = f"{date_str.replace('-', '')}_080000_XAUUSD_ORB"
        # Quick check: skip if a decision for this date already exists
        existing = [r for r in ledger.get_all()
                    if date_str.replace("-", "") in r.id and r.strategy == "ORB"]
        if existing:
            print(f"  [{date_str}] Already in ledger — skipping")
            continue

        # Asian range: 00:00-06:00 UTC
        range_bars = _bars_for_day(intraday, date_str, 0, 6)
        range_result = _compute_range(range_bars)

        if not range_result:
            print(f"  [{date_str}] No range data (market closed?) — skipping")
            continue

        range_high, range_low = range_result
        range_size = range_high - range_low

        # Trading session bars: 08:00-22:00 UTC (for assessment)
        trading_bars = _bars_for_day(intraday, date_str, 8, 22)

        # Daily bars before this date (for context)
        daily_before = [d for d in daily if d["date"][:10] < date_str]

        # Compute range_vs_avg
        if daily_before:
            avg_range = sum(
                d["high"] - d["low"] for d in daily_before[-10:]
            ) / min(len(daily_before), 10)
            rvs = range_size / avg_range if avg_range > 0 else 1.0
        else:
            rvs = 1.0

        print(f"  [{date_str} {dow}] Range: {range_low:.2f}-{range_high:.2f} "
              f"(size={range_size:.2f}, vs_avg={rvs:.2f}x) "
              f"bars: {len(range_bars)} range, {len(trading_bars)} trading")

        if dry_run:
            results.append({
                "date": date_str, "range_high": range_high,
                "range_low": range_low, "range_size": range_size,
                "range_vs_avg": rvs, "decision": "N/A (dry run)",
            })
            continue

        # ── Call Grok ─────────────────────────────────────────────
        print(f"    Calling Grok... ", end="", flush=True)
        try:
            result = call_grok_for_day(
                grok_filter,
                instrument="XAUUSD",
                range_high=range_high,
                range_low=range_low,
                daily_bars_before=daily_before[-10:],
                day_date=date_str,
                day_of_week=dow,
                feedback_table=ledger.build_feedback_table(),
            )
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        decision_str = "APPROVE" if result["approved"] else "REJECT"
        print(f"{decision_str} (conf={result['confidence']}) "
              f"— {result['reasoning'][:80]}")

        # ── Assess ────────────────────────────────────────────────
        from v11.llm.decision_ledger import DecisionRecord
        record = DecisionRecord(
            id=f"{date_str.replace('-', '_')}_080000_XAUUSD_ORB",
            timestamp_utc=f"{date_str}T08:00:00+00:00",
            strategy="ORB",
            instrument="XAUUSD",
            decision=decision_str,
            confidence=result["confidence"],
            reasoning=result["reasoning"][:500],
            risk_flags=result["risk_flags"],
            context=result["context"],
        )

        if trading_bars:
            grade, what, triggered, tp, sl, pnl_r = \
                assess_orb_decision(record, trading_bars)
            price_high = max(b["high"] for b in trading_bars)
            price_low = min(b["low"] for b in trading_bars)
        else:
            grade, what = "CORRECT", "No trading data available"
            triggered, tp, sl, pnl_r = False, False, False, 0.0
            price_high, price_low = 0.0, 0.0

        grade_icon = {"CORRECT": "✓", "WRONG": "✗", "MISSED": "⚠"}.get(grade, "?")
        print(f"    Assessment: {grade_icon} {grade}: {what}")

        # ── Record to ledger (decision + immediate assessment) ────
        rec = ledger.record_decision(
            strategy="ORB",
            instrument="XAUUSD",
            decision=decision_str,
            confidence=result["confidence"],
            reasoning=result["reasoning"][:500],
            risk_flags=result["risk_flags"],
            context=result["context"],
            timestamp=dt.replace(hour=8),
        )
        ledger.assess_decision(
            record_id=rec.id,
            grade=grade,
            what_happened=what,
            price_high_after=price_high,
            price_low_after=price_low,
            breakout_triggered=triggered,
            would_have_hit_tp=tp,
            would_have_hit_sl=sl,
            simulated_pnl_r=pnl_r,
        )

        results.append({
            "date": date_str,
            "range_size": range_size,
            "range_vs_avg": rvs,
            "decision": decision_str,
            "confidence": result["confidence"],
            "grade": grade,
            "pnl_r": pnl_r,
        })

        # Rate limiting
        if i < len(valid_dates) - 1:
            print(f"    (waiting {delay}s for rate limit...)")
            time.sleep(delay)

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("BOOTSTRAP SUMMARY")
    print(f"{'='*70}")

    if dry_run:
        print("\n[DRY RUN] No Grok calls made. Here are the ranges found:\n")
        for r in results:
            print(f"  {r['date']}: {r['range_low']:.2f}-{r['range_high']:.2f} "
                  f"(size={r['range_size']:.2f}, vs_avg={r['range_vs_avg']:.2f}x)")
    else:
        correct = sum(1 for r in results if r.get("grade") == "CORRECT")
        wrong = sum(1 for r in results if r.get("grade") == "WRONG")
        missed = sum(1 for r in results if r.get("grade") == "MISSED")
        total = len(results)
        accuracy = correct / total * 100 if total > 0 else 0

        print(f"\nProcessed {total} days")
        print(f"Correct: {correct} | Wrong: {wrong} | Missed: {missed} | "
              f"Accuracy: {accuracy:.0f}%\n")

        for r in results:
            icon = {"CORRECT": "✓", "WRONG": "✗", "MISSED": "⚠"}.get(
                r.get("grade", ""), " ")
            print(f"  {r['date']}: {r.get('decision','?')} "
                  f"(conf={r.get('confidence', '?')}) "
                  f"→ {icon} {r.get('grade','?')} "
                  f"({r.get('pnl_r', 0):+.1f}R)")

    print(f"\nLedger stats: {ledger.stats}")
    feedback = ledger.build_feedback_table()
    if feedback:
        print(f"\n{'='*70}")
        print("FEEDBACK TABLE (will be injected into future Grok calls):")
        print(f"{'='*70}")
        print(feedback)


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s  %(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(
        description="Bootstrap decision ledger with historical ORB data + Grok decisions")
    parser.add_argument("--days", type=int, default=15,
                        help="Number of trading days to backfill (default: 15)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute ranges but don't call Grok")
    parser.add_argument("--from-cache", action="store_true",
                        help="Use previously fetched IBKR data")
    parser.add_argument("--log-dir", default=None,
                        help="Path to grok_logs directory")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="Seconds between Grok calls (rate limiting)")
    args = parser.parse_args()

    log_dir = args.log_dir or str(ROOT / "grok_logs")

    run_bootstrap(
        days=args.days,
        dry_run=args.dry_run,
        from_cache=args.from_cache,
        log_dir=log_dir,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
