"""
Decision Ledger — Tracks all Grok LLM decisions with outcomes for feedback loop.

Edge module: records decisions, stores assessments, builds feedback tables.
Does not affect trading logic. Safe to modify freely.

Usage:
    ledger = DecisionLedger("grok_logs/")
    ledger.record_decision(...)        # called by GrokFilter after each call
    ledger.assess_decision(id, ...)    # called by assess_decisions.py script
    table = ledger.build_feedback_table(n=20)  # injected into Grok prompt
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

LEDGER_FILENAME = "decision_ledger.json"


# ── Data models ────────────────────────────────────────────────────────────

@dataclass
class DecisionOutcome:
    """Assessment of a past decision against actual market data."""
    assessed: bool = False
    assessed_at: Optional[str] = None
    grade: Optional[str] = None         # CORRECT, WRONG, MISSED
    what_happened: str = ""
    price_high_after: float = 0.0       # highest price in assessment window
    price_low_after: float = 0.0        # lowest price in assessment window
    breakout_triggered: Optional[bool] = None  # did price break the range/box?
    would_have_hit_tp: Optional[bool] = None
    would_have_hit_sl: Optional[bool] = None
    simulated_pnl_r: float = 0.0        # in R-multiples (1R = risk unit)


@dataclass
class DecisionRecord:
    """A single Grok LLM decision with context and (later) outcome."""
    id: str                             # unique: YYYY-MM-DD_HHMMSS_INSTR_STRATEGY
    timestamp_utc: str
    strategy: str                       # "ORB", "DARVAS", "4H_RETEST"
    instrument: str
    decision: str                       # "APPROVE" or "REJECT"
    confidence: int
    reasoning: str
    risk_flags: List[str] = field(default_factory=list)

    # Key context at decision time (enough to simulate outcome)
    context: Dict = field(default_factory=dict)

    # Assessment (filled in later)
    outcome: DecisionOutcome = field(default_factory=DecisionOutcome)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> DecisionRecord:
        outcome_data = d.pop("outcome", {})
        outcome = DecisionOutcome(**outcome_data) if outcome_data else DecisionOutcome()
        return cls(outcome=outcome, **d)


# ── Ledger ─────────────────────────────────────────────────────────────────

class DecisionLedger:
    """Persistent store of all Grok decisions with outcome tracking.

    Stores as a single JSON file (decision_ledger.json) in the grok_logs dir.
    Thread-safe for single-writer (live engine is single-threaded).
    """

    def __init__(self, log_dir: str):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._ledger_path = self._log_dir / LEDGER_FILENAME
        self._records: Dict[str, DecisionRecord] = {}
        self._load()

    def _load(self) -> None:
        """Load existing ledger from disk."""
        if not self._ledger_path.exists():
            self._records = {}
            return

        try:
            with open(self._ledger_path, "r") as f:
                data = json.load(f)
            self._records = {
                r["id"]: DecisionRecord.from_dict(r)
                for r in data.get("decisions", [])
            }
            logger.info(f"Loaded {len(self._records)} decision records from ledger")
        except Exception as e:
            logger.error(f"Failed to load decision ledger: {e}")
            self._records = {}

    def _save(self) -> None:
        """Persist ledger to disk."""
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_decisions": len(self._records),
            "assessed_count": sum(
                1 for r in self._records.values() if r.outcome.assessed),
            "decisions": [r.to_dict() for r in self._records.values()],
        }
        try:
            with open(self._ledger_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Failed to save decision ledger: {e}")

    # ── Recording decisions ────────────────────────────────────────────

    def record_decision(
        self,
        strategy: str,
        instrument: str,
        decision: str,
        confidence: int,
        reasoning: str,
        risk_flags: List[str],
        context: Dict,
        timestamp: Optional[datetime] = None,
    ) -> DecisionRecord:
        """Record a new Grok decision. Called after each LLM call."""
        now = timestamp or datetime.now(timezone.utc)
        ts_str = now.strftime("%Y-%m-%d_%H%M%S")
        record_id = f"{ts_str}_{instrument}_{strategy}"

        record = DecisionRecord(
            id=record_id,
            timestamp_utc=now.isoformat(),
            strategy=strategy,
            instrument=instrument,
            decision=decision,
            confidence=confidence,
            reasoning=reasoning[:500],  # cap reasoning length
            risk_flags=risk_flags,
            context=context,
        )

        self._records[record_id] = record
        self._save()
        logger.debug(f"Recorded decision: {record_id} -> {decision} ({confidence})")
        return record

    # ── Assessment ─────────────────────────────────────────────────────

    def assess_decision(
        self,
        record_id: str,
        grade: str,
        what_happened: str,
        price_high_after: float,
        price_low_after: float,
        breakout_triggered: Optional[bool] = None,
        would_have_hit_tp: Optional[bool] = None,
        would_have_hit_sl: Optional[bool] = None,
        simulated_pnl_r: float = 0.0,
    ) -> None:
        """Assess a past decision with actual market outcome."""
        if record_id not in self._records:
            logger.warning(f"Decision {record_id} not found in ledger")
            return

        record = self._records[record_id]
        record.outcome = DecisionOutcome(
            assessed=True,
            assessed_at=datetime.now(timezone.utc).isoformat(),
            grade=grade,
            what_happened=what_happened,
            price_high_after=price_high_after,
            price_low_after=price_low_after,
            breakout_triggered=breakout_triggered,
            would_have_hit_tp=would_have_hit_tp,
            would_have_hit_sl=would_have_hit_sl,
            simulated_pnl_r=simulated_pnl_r,
        )
        self._save()
        logger.info(f"Assessed {record_id}: {grade}")

    # ── Feedback table ─────────────────────────────────────────────────

    def get_unassessed(self) -> List[DecisionRecord]:
        """Get decisions that haven't been assessed yet."""
        return [r for r in self._records.values() if not r.outcome.assessed]

    def get_assessed(self) -> List[DecisionRecord]:
        """Get all assessed decisions, newest first."""
        assessed = [r for r in self._records.values() if r.outcome.assessed]
        assessed.sort(key=lambda r: r.timestamp_utc, reverse=True)
        return assessed

    def get_all(self) -> List[DecisionRecord]:
        """Get all decisions, newest first."""
        records = list(self._records.values())
        records.sort(key=lambda r: r.timestamp_utc, reverse=True)
        return records

    def build_feedback_table(self, max_rows: int = 20) -> str:
        """Build a markdown table of recent assessed decisions for Grok context.

        Returns empty string if no assessed decisions exist.
        """
        assessed = self.get_assessed()
        if not assessed:
            return ""

        rows = assessed[:max_rows]

        # Summary stats
        total = len(assessed)
        correct = sum(1 for r in assessed if r.outcome.grade == "CORRECT")
        wrong = sum(1 for r in assessed if r.outcome.grade == "WRONG")
        missed = sum(1 for r in assessed if r.outcome.grade == "MISSED")
        accuracy = correct / total * 100 if total > 0 else 0

        lines = [
            f"## Your Decision Track Record ({total} assessed, {accuracy:.0f}% accuracy)",
            f"Correct: {correct} | Wrong: {wrong} | Missed opportunities: {missed}",
            "",
            "| Date | Strategy | Instrument | Decision | Conf | Outcome | Grade |",
            "|------|----------|------------|----------|------|---------|-------|",
        ]

        for r in rows:
            date = r.timestamp_utc[:10]
            outcome_short = r.outcome.what_happened[:60]
            grade_icon = {
                "CORRECT": "✓",
                "WRONG": "✗",
                "MISSED": "⚠",
            }.get(r.outcome.grade, "?")
            lines.append(
                f"| {date} | {r.strategy} | {r.instrument} | "
                f"{r.decision} | {r.confidence} | "
                f"{outcome_short} | {grade_icon} {r.outcome.grade} |"
            )

        lines.append("")
        lines.append(
            "Use this history to calibrate your confidence. "
            "If you've been wrong on similar setups, be more conservative. "
            "If you've been rejecting setups that would have been profitable, "
            "consider being less conservative."
        )

        return "\n".join(lines)

    @property
    def stats(self) -> Dict:
        """Summary statistics for logging."""
        total = len(self._records)
        assessed = sum(1 for r in self._records.values() if r.outcome.assessed)
        correct = sum(
            1 for r in self._records.values()
            if r.outcome.grade == "CORRECT")
        wrong = sum(
            1 for r in self._records.values()
            if r.outcome.grade == "WRONG")
        missed = sum(
            1 for r in self._records.values()
            if r.outcome.grade == "MISSED")
        return {
            "total": total,
            "assessed": assessed,
            "unassessed": total - assessed,
            "correct": correct,
            "wrong": wrong,
            "missed": missed,
            "accuracy_pct": round(correct / assessed * 100, 1) if assessed else 0,
        }
