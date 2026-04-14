"""
Decision Assessment Script — Grades past Grok decisions against actual market data.

EDGE module: does not affect trading logic. Safe to run anytime.

Usage:
    # With IBKR connection (fetches real data):
    python -m v11.llm.assess_decisions --live

    # Dry run (shows what would be assessed):
    python -m v11.llm.assess_decisions --dry-run

    # Show ledger stats only:
    python -m v11.llm.assess_decisions --stats

How it works:
    1. Reads all unassessed decisions from the ledger
    2. For each decision, fetches historical 1-min bars from IBKR
       for the assessment window (rest of trading day for ORB,
       next 4 hours for Darvas/4H_RETEST)
    3. Simulates the trade outcome:
       - ORB: would brackets at range high/low have been triggered?
              If triggered, would TP or SL have been hit?
       - Darvas: would the breakout trade have hit TP or SL?
    4. Grades the decision (CORRECT, WRONG, MISSED)
    5. Writes assessment back to the ledger

Grading rules:
    APPROVE + profitable outcome   -> CORRECT
    APPROVE + losing outcome       -> WRONG
    REJECT  + would have lost      -> CORRECT (saved a loss)
    REJECT  + would have profited  -> MISSED  (missed a winner)
    REJECT  + no breakout occurred -> CORRECT (nothing happened)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from v11.llm.decision_ledger import DecisionLedger, DecisionRecord

logger = logging.getLogger(__name__)


# ── ORB assessment ─────────────────────────────────────────────────────────

def assess_orb_decision(
    record: DecisionRecord,
    bars: list,
) -> Tuple[str, str, bool, bool, bool, float]:
    """Assess an ORB decision against actual price data.

    Args:
        record: The decision record with context
        bars: List of 1-min bars (dicts with 'high', 'low', 'close') for the
              assessment window (rest of trading day after decision)

    Returns:
        (grade, what_happened, breakout_triggered,
         would_have_hit_tp, would_have_hit_sl, simulated_pnl_r)
    """
    ctx = record.context
    range_high = ctx.get("range_high", 0)
    range_low = ctx.get("range_low", 0)
    range_size = ctx.get("range_size", range_high - range_low)

    if range_size <= 0 or not bars:
        return ("CORRECT", "No data to assess", False, False, False, 0.0)

    # ORB brackets: long entry at range_high, short entry at range_low
    # TP = 1.5x range size from entry, SL = opposite bracket
    long_entry = range_high
    long_tp = range_high + range_size * 1.5
    long_sl = range_low

    short_entry = range_low
    short_tp = range_low - range_size * 1.5
    short_sl = range_high

    # Simulate: did price trigger a bracket?
    long_triggered = False
    short_triggered = False
    long_hit_tp = False
    long_hit_sl = False
    short_hit_tp = False
    short_hit_sl = False

    for bar in bars:
        h = bar.get("high", 0)
        l = bar.get("low", 0)

        if not long_triggered and not short_triggered:
            # Check which bracket triggers first
            if h >= long_entry:
                long_triggered = True
            elif l <= short_entry:
                short_triggered = True

        if long_triggered and not long_hit_tp and not long_hit_sl:
            if l <= long_sl and h >= long_tp:
                # Both hit on same bar — conservatively assume SL hit first
                long_hit_sl = True
            elif h >= long_tp:
                long_hit_tp = True
            elif l <= long_sl:
                long_hit_sl = True

        if short_triggered and not short_hit_tp and not short_hit_sl:
            if h >= short_sl and l <= short_tp:
                # Both hit on same bar — conservatively assume SL hit first
                short_hit_sl = True
            elif l <= short_tp:
                short_hit_tp = True
            elif h >= short_sl:
                short_hit_sl = True

    # Determine outcome
    breakout_triggered = long_triggered or short_triggered
    hit_tp = long_hit_tp or short_hit_tp
    hit_sl = long_hit_sl or short_hit_sl

    # Calculate simulated PnL in R-multiples
    if long_triggered:
        if long_hit_tp:
            pnl_r = 1.5  # TP = 1.5R
        elif long_hit_sl:
            pnl_r = -1.0
        else:
            # Still open — use last close
            last_close = bars[-1].get("close", long_entry)
            pnl_r = (last_close - long_entry) / range_size if range_size > 0 else 0
    elif short_triggered:
        if short_hit_tp:
            pnl_r = 1.5
        elif short_hit_sl:
            pnl_r = -1.0
        else:
            last_close = bars[-1].get("close", short_entry)
            pnl_r = (short_entry - last_close) / range_size if range_size > 0 else 0
    else:
        pnl_r = 0.0

    # Grade
    is_approve = record.decision == "APPROVE"

    if not breakout_triggered:
        if is_approve:
            grade = "WRONG"
            what = f"APPROVED but no breakout occurred. Range held."
        else:
            grade = "CORRECT"
            what = f"No breakout. Range held [{range_low:.2f}-{range_high:.2f}]"
        return (grade, what, False, False, False, 0.0)

    direction = "LONG" if long_triggered else "SHORT"
    if hit_tp:
        what = f"{direction} breakout hit TP ({pnl_r:+.1f}R)"
        grade = "CORRECT" if is_approve else "MISSED"
    elif hit_sl:
        what = f"{direction} breakout hit SL ({pnl_r:+.1f}R)"
        grade = "WRONG" if is_approve else "CORRECT"
    else:
        if pnl_r > 0:
            what = f"{direction} breakout still open, unrealized {pnl_r:+.1f}R"
            grade = "CORRECT" if is_approve else "MISSED"
        else:
            what = f"{direction} breakout still open, unrealized {pnl_r:+.1f}R"
            grade = "WRONG" if is_approve else "CORRECT"

    return (grade, what, breakout_triggered, hit_tp, hit_sl, round(pnl_r, 2))


# ── Darvas / 4H Retest assessment ──────────────────────────────────────────

def assess_darvas_decision(
    record: DecisionRecord,
    bars: list,
) -> Tuple[str, str, bool, bool, bool, float]:
    """Assess a Darvas or 4H Retest decision against actual price data.

    Args:
        record: The decision record with context
        bars: List of 1-min bars for the assessment window (4-8 hours after)

    Returns:
        (grade, what_happened, breakout_triggered,
         would_have_hit_tp, would_have_hit_sl, simulated_pnl_r)
    """
    ctx = record.context
    entry_price = ctx.get("entry_price", ctx.get("breakout_price", 0))
    stop_price = ctx.get("stop_price", 0)
    target_price = ctx.get("target_price", 0)
    direction = ctx.get("direction", "long")

    if entry_price <= 0 or not bars:
        return ("CORRECT", "No data to assess", False, False, False, 0.0)

    # Calculate risk (distance from entry to stop)
    if direction == "long":
        risk = abs(entry_price - stop_price) if stop_price > 0 else 0
    else:
        risk = abs(stop_price - entry_price) if stop_price > 0 else 0

    if risk <= 0:
        # Use ATR as fallback risk
        risk = ctx.get("atr", 0.001)

    # Default target = 2R from entry if not specified
    if target_price <= 0:
        if direction == "long":
            target_price = entry_price + risk * 2
        else:
            target_price = entry_price - risk * 2

    hit_tp = False
    hit_sl = False

    for bar in bars:
        h = bar.get("high", 0)
        l = bar.get("low", 0)

        if direction == "long":
            if h >= target_price:
                hit_tp = True
                break
            if l <= stop_price and stop_price > 0:
                hit_sl = True
                break
        else:
            if l <= target_price:
                hit_tp = True
                break
            if h >= stop_price and stop_price > 0:
                hit_sl = True
                break

    # PnL in R
    if hit_tp:
        pnl_r = abs(target_price - entry_price) / risk if risk > 0 else 2.0
    elif hit_sl:
        pnl_r = -1.0
    else:
        last_close = bars[-1].get("close", entry_price)
        if direction == "long":
            pnl_r = (last_close - entry_price) / risk if risk > 0 else 0
        else:
            pnl_r = (entry_price - last_close) / risk if risk > 0 else 0

    pnl_r = round(pnl_r, 2)

    is_approve = record.decision == "APPROVE"

    if hit_tp:
        what = f"{direction.upper()} hit TP ({pnl_r:+.1f}R)"
        grade = "CORRECT" if is_approve else "MISSED"
    elif hit_sl:
        what = f"{direction.upper()} hit SL ({pnl_r:+.1f}R)"
        grade = "WRONG" if is_approve else "CORRECT"
    else:
        if pnl_r >= 0:
            what = f"{direction.upper()} ended flat/positive ({pnl_r:+.1f}R)"
            grade = "CORRECT" if is_approve else "MISSED"
        else:
            what = f"{direction.upper()} ended negative ({pnl_r:+.1f}R)"
            grade = "WRONG" if is_approve else "CORRECT"

    return (grade, what, True, hit_tp, hit_sl, pnl_r)


# ── IBKR data fetching ────────────────────────────────────────────────────

def fetch_bars_for_decision(
    conn, record: DecisionRecord
) -> Optional[list]:
    """Fetch historical 1-min bars from IBKR for the assessment window.

    Args:
        conn: IBKRConnection instance
        record: Decision record to assess

    Returns:
        List of bar dicts with 'high', 'low', 'close', 'timestamp' keys,
        or None if fetch fails.
    """
    from v11.execution.ibkr_connection import IBKRConnection

    decision_time = datetime.fromisoformat(record.timestamp_utc)
    instrument = record.instrument

    # Assessment window: rest of trading day for ORB, 4 hours for Darvas/Retest
    if record.strategy == "ORB":
        # ORB: assess until end of NY session (~22:00 UTC)
        end_time = decision_time.replace(hour=22, minute=0, second=0)
        if end_time <= decision_time:
            end_time += timedelta(days=1)
        duration_hours = (end_time - decision_time).total_seconds() / 3600
        duration_str = f"{int(duration_hours * 3600)} S"
    else:
        # Darvas/4H: assess next 4 hours
        duration_str = "14400 S"  # 4 hours

    try:
        # end_datetime for IBKR = end of assessment window
        end_dt = decision_time + timedelta(
            seconds=int(duration_str.split()[0]))
        # Format for IBKR
        end_str = end_dt.strftime("%Y%m%d %H:%M:%S")

        df = conn.fetch_historical_bars(
            instrument,
            duration=duration_str,
            bar_size="1 min",
            end_datetime=end_str,
        )

        if df is None or df.empty:
            return None

        bars = []
        for _, row in df.iterrows():
            bars.append({
                "high": row.get("high", 0),
                "low": row.get("low", 0),
                "close": row.get("close", 0),
                "timestamp": str(row.get("date", "")),
            })
        return bars

    except Exception as e:
        logger.error(f"Failed to fetch bars for {record.id}: {e}")
        return None


# ── Main assessment loop ──────────────────────────────────────────────────

def run_assessment(log_dir: str, live: bool = False, dry_run: bool = False):
    """Assess all unassessed decisions in the ledger.

    Args:
        log_dir: Path to grok_logs directory
        live: If True, connect to IBKR and fetch real data
        dry_run: If True, just show what would be assessed
    """
    ledger = DecisionLedger(log_dir)
    unassessed = ledger.get_unassessed()

    print(f"\n{'='*60}")
    print(f"Decision Ledger Stats: {ledger.stats}")
    print(f"Unassessed decisions: {len(unassessed)}")
    print(f"{'='*60}\n")

    if not unassessed:
        print("Nothing to assess.")
        return

    for r in unassessed:
        print(f"  [{r.id}] {r.strategy} {r.instrument}: "
              f"{r.decision} (conf={r.confidence})")
        print(f"    Reasoning: {r.reasoning[:100]}")

    if dry_run:
        print("\n[DRY RUN] No assessments performed.")
        return

    conn = None
    if live:
        try:
            from v11.execution.ibkr_connection import IBKRConnection
            from v11.config.live_config import LiveConfig
            cfg = LiveConfig()
            conn = IBKRConnection(cfg.ibkr_host, cfg.ibkr_port, cfg.ibkr_client_id + 50)
            conn.connect()
            print("Connected to IBKR for historical data\n")
        except Exception as e:
            print(f"Failed to connect to IBKR: {e}")
            print("Cannot assess without market data. Use --dry-run to preview.")
            return

    assessed_count = 0
    for record in unassessed:
        # Need at least 4 hours of data after the decision
        decision_time = datetime.fromisoformat(record.timestamp_utc)
        min_assess_time = decision_time + timedelta(hours=4)
        now = datetime.now(timezone.utc)

        if now < min_assess_time:
            print(f"  [{record.id}] Too recent — need data until "
                  f"{min_assess_time.strftime('%H:%M UTC')}")
            continue

        if not live or conn is None:
            print(f"  [{record.id}] Skipped (no IBKR connection)")
            continue

        # Fetch bars
        bars = fetch_bars_for_decision(conn, record)
        if not bars:
            print(f"  [{record.id}] No bars fetched — skipping")
            continue

        # Get price extremes
        price_high = max(b["high"] for b in bars)
        price_low = min(b["low"] for b in bars)

        # Assess based on strategy
        if record.strategy == "ORB":
            grade, what, triggered, hit_tp, hit_sl, pnl_r = \
                assess_orb_decision(record, bars)
        else:
            grade, what, triggered, hit_tp, hit_sl, pnl_r = \
                assess_darvas_decision(record, bars)

        # Write assessment
        ledger.assess_decision(
            record_id=record.id,
            grade=grade,
            what_happened=what,
            price_high_after=price_high,
            price_low_after=price_low,
            breakout_triggered=triggered,
            would_have_hit_tp=hit_tp,
            would_have_hit_sl=hit_sl,
            simulated_pnl_r=pnl_r,
        )

        grade_icon = {"CORRECT": "✓", "WRONG": "✗", "MISSED": "⚠"}.get(grade, "?")
        print(f"  [{record.id}] {grade_icon} {grade}: {what}")
        assessed_count += 1

    if conn:
        try:
            conn.disconnect()
        except Exception:
            pass

    print(f"\nAssessed {assessed_count}/{len(unassessed)} decisions.")
    print(f"Updated stats: {ledger.stats}")


def show_stats(log_dir: str):
    """Show ledger statistics and recent decisions."""
    ledger = DecisionLedger(log_dir)
    stats = ledger.stats
    all_records = ledger.get_all()

    print(f"\n{'='*60}")
    print(f"Decision Ledger: {log_dir}")
    print(f"{'='*60}")
    print(f"  Total decisions:  {stats['total']}")
    print(f"  Assessed:         {stats['assessed']}")
    print(f"  Unassessed:       {stats['unassessed']}")
    print(f"  Correct:          {stats['correct']}")
    print(f"  Wrong:            {stats['wrong']}")
    print(f"  Missed:           {stats['missed']}")
    print(f"  Accuracy:         {stats['accuracy_pct']}%")
    print()

    if all_records:
        print("Recent decisions:")
        for r in all_records[:10]:
            grade_str = ""
            if r.outcome.assessed:
                icon = {"CORRECT": "✓", "WRONG": "✗", "MISSED": "⚠"}.get(
                    r.outcome.grade, "?")
                grade_str = f" -> {icon} {r.outcome.grade}: {r.outcome.what_happened[:60]}"
            print(f"  [{r.timestamp_utc[:16]}] {r.strategy} {r.instrument}: "
                  f"{r.decision} (conf={r.confidence}){grade_str}")

    # Show feedback table
    feedback = ledger.build_feedback_table()
    if feedback:
        print(f"\n{'='*60}")
        print("Feedback table (injected into Grok prompts):")
        print(f"{'='*60}")
        print(feedback)


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(
        description="Assess past Grok LLM decisions against actual market data")
    parser.add_argument("--live", action="store_true",
                        help="Connect to IBKR and fetch real historical data")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be assessed without doing it")
    parser.add_argument("--stats", action="store_true",
                        help="Show ledger statistics only")
    parser.add_argument("--log-dir", default=None,
                        help="Path to grok_logs directory")
    args = parser.parse_args()

    if args.log_dir:
        log_dir = args.log_dir
    else:
        log_dir = str(ROOT / "grok_logs")

    if args.stats:
        show_stats(log_dir)
    else:
        run_assessment(log_dir, live=args.live, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
