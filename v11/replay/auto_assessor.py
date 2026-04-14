"""Auto-assessor — grades past LLM decisions against actual trade outcomes.

Called by the replay runner after each trade completes. Looks up the
corresponding LLM decision in the ledger and grades it CORRECT/WRONG/MISSED.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..llm.decision_ledger import DecisionLedger

logger = logging.getLogger("v11_replay.assessor")


def assess_orb_decision(
    ledger: DecisionLedger,
    instrument: str,
    decision_date: str,       # YYYY-MM-DD
    approved: bool,
    entry_price: float,
    exit_price: float,
    exit_reason: str,         # SL, TP, MARKET, EOD, DAILY_RESET
    pnl: float,
    range_high: float,
    range_low: float,
) -> None:
    """Assess an ORB LLM decision after the trade completes.

    Grades:
    - CORRECT: approved + profitable, or rejected + would have been unprofitable
    - WRONG: approved + unprofitable
    - MISSED: rejected + would have been profitable
    """
    # Find the decision for this date + instrument + ORB
    # Match by instrument + strategy + range_high/low from context
    record = ledger.find_unassessed(
        "ORB", instrument,
        range_high=range_high, range_low=range_low,
    )

    if record is None:
        logger.debug(f"No ORB decision found for {instrument} range={range_low}-{range_high}")
        return

    record_id = record.id

    # Determine grade
    profitable = pnl > 0

    if approved:
        if profitable:
            grade = "CORRECT"
            what = f"Trade profitable: PnL=${pnl:+.2f} ({exit_reason})"
        else:
            grade = "WRONG"
            what = f"Trade unprofitable: PnL=${pnl:+.2f} ({exit_reason})"
    else:
        # Rejected — simulate what would have happened
        # For ORB, we check if price would have hit TP or SL
        # We don't have the full bar data here, so use the actual outcome
        # as a proxy: if the trade would have been profitable, it's MISSED
        if profitable:
            grade = "MISSED"
            what = f"Rejected but would have been profitable: PnL=${pnl:+.2f}"
        else:
            grade = "CORRECT"
            what = f"Rejected correctly — would have lost: PnL=${pnl:+.2f}"

    # Simulated PnL in R-multiples
    risk = abs(range_high - range_low)  # 1R = range size for ORB
    pnl_r = pnl / risk if risk > 0 else 0.0

    ledger.assess_decision(
        record_id=record_id,
        grade=grade,
        what_happened=what,
        price_high_after=0.0,  # not available in this context
        price_low_after=0.0,
        breakout_triggered=approved,
        would_have_hit_tp=profitable and exit_reason == "TP",
        would_have_hit_sl=not profitable and exit_reason == "SL",
        simulated_pnl_r=round(pnl_r, 2),
    )

    logger.info(f"Assessed ORB {decision_date} {instrument}: {grade} (PnL=${pnl:+.2f})")


def assess_darvas_decision(
    ledger: DecisionLedger,
    instrument: str,
    decision_timestamp: str,  # ISO timestamp of the LLM call
    approved: bool,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    pnl: float,
    breakout_price: float = 0.0,  # for matching
) -> None:
    """Assess a Darvas/LevelRetest LLM decision after the trade completes."""
    # Find the decision by instrument + strategy + unassessed.
    # Match on breakout_price from context first; fall back to entry_price
    # comparison (LLM may suggest a different entry than the signal breakout).
    # Find by breakout_price first; fall back to entry_price
    record = ledger.find_unassessed(
        "DARVAS,4H_RETEST", instrument,
        breakout_price=breakout_price,
    )
    if record is None and entry_price > 0:
        record = ledger.find_unassessed(
            "DARVAS,4H_RETEST", instrument,
            entry_price=entry_price,
        )

    if record is None:
        return

    record_id = record.id

    profitable = pnl > 0

    if approved:
        if profitable:
            grade = "CORRECT"
            what = f"Trade profitable: PnL=${pnl:+.2f} ({exit_reason})"
        else:
            grade = "WRONG"
            what = f"Trade unprofitable: PnL=${pnl:+.2f} ({exit_reason})"
    else:
        if profitable:
            grade = "MISSED"
            what = f"Rejected but would have been profitable: PnL=${pnl:+.2f}"
        else:
            grade = "CORRECT"
            what = f"Rejected correctly — would have lost: PnL=${pnl:+.2f}"

    risk = abs(entry_price - exit_price) if abs(entry_price - exit_price) > 0 else 1.0
    pnl_r = pnl / risk if risk > 0 else 0.0

    ledger.assess_decision(
        record_id=record_id,
        grade=grade,
        what_happened=what,
        price_high_after=0.0,
        price_low_after=0.0,
        breakout_triggered=approved,
        would_have_hit_tp=profitable and exit_reason in ("TP", "TARGET"),
        would_have_hit_sl=not profitable and exit_reason in ("SL", "STOP"),
        simulated_pnl_r=round(pnl_r, 2),
    )

    logger.info(f"Assessed Darvas {decision_timestamp[:10]} {instrument}: {grade} (PnL=${pnl:+.2f})")
