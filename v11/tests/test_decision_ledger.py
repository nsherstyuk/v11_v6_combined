"""
Tests for DecisionLedger — decision tracking and feedback loop.

Intent-based tests per test-creation-guide:
1. Decisions are recorded and persisted to disk
2. Assessments update records correctly
3. Feedback table is built from assessed decisions
4. Assessment grading logic for ORB and Darvas is correct
"""
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

from v11.llm.decision_ledger import DecisionLedger, DecisionRecord, DecisionOutcome
from v11.llm.assess_decisions import assess_orb_decision, assess_darvas_decision


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_ledger(tmp_path):
    """Create a ledger in a temporary directory."""
    return DecisionLedger(str(tmp_path))


def _make_orb_record(decision="REJECT", confidence=85):
    return DecisionRecord(
        id="2026-04-08_084700_XAUUSD_ORB",
        timestamp_utc="2026-04-08T08:47:00+00:00",
        strategy="ORB",
        instrument="XAUUSD",
        decision=decision,
        confidence=confidence,
        reasoning="Tight range",
        risk_flags=["tight_range"],
        context={
            "range_high": 4857.82,
            "range_low": 4788.40,
            "range_size": 69.42,
            "range_vs_avg": 0.3,
            "current_price": 4780.0,
            "session": "LONDON",
        },
    )


def _make_darvas_record(decision="APPROVE", confidence=80):
    return DecisionRecord(
        id="2026-04-08_120000_EURUSD_DARVAS",
        timestamp_utc="2026-04-08T12:00:00+00:00",
        strategy="DARVAS",
        instrument="EURUSD",
        decision=decision,
        confidence=confidence,
        reasoning="Clean breakout",
        risk_flags=[],
        context={
            "direction": "long",
            "breakout_price": 1.08300,
            "entry_price": 1.08300,
            "stop_price": 1.08100,
            "target_price": 1.08700,
            "box_top": 1.08300,
            "box_bottom": 1.08100,
            "atr": 0.00020,
            "session": "LONDON",
        },
    )


# ── 1. Recording and persistence ──────────────────────────────────────────

class TestRecording:
    def test_record_decision_creates_entry(self, tmp_ledger):
        """Recording a decision creates a ledger entry."""
        record = tmp_ledger.record_decision(
            strategy="ORB",
            instrument="XAUUSD",
            decision="REJECT",
            confidence=85,
            reasoning="Too tight",
            risk_flags=["tight_range"],
            context={"range_high": 4857.82},
        )
        assert record.decision == "REJECT"
        assert record.confidence == 85
        assert len(tmp_ledger.get_all()) == 1

    def test_persistence_across_reload(self, tmp_path):
        """Ledger data survives reload from disk."""
        ledger1 = DecisionLedger(str(tmp_path))
        ledger1.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="REJECT",
            confidence=85, reasoning="Test", risk_flags=[], context={},
        )

        # Reload from same directory
        ledger2 = DecisionLedger(str(tmp_path))
        assert len(ledger2.get_all()) == 1
        assert ledger2.get_all()[0].decision == "REJECT"

    def test_multiple_records(self, tmp_ledger):
        """Multiple decisions are stored independently."""
        for i in range(5):
            tmp_ledger.record_decision(
                strategy="ORB", instrument=f"INST{i}", decision="APPROVE",
                confidence=50+i, reasoning=f"Reason {i}",
                risk_flags=[], context={},
            )
        assert len(tmp_ledger.get_all()) == 5


# ── 2. Assessment ──────────────────────────────────────────────────────────

class TestAssessment:
    def test_assess_updates_outcome(self, tmp_ledger):
        """Assessment writes grade and outcome to the record."""
        record = tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="REJECT",
            confidence=85, reasoning="Tight", risk_flags=[], context={},
        )
        tmp_ledger.assess_decision(
            record_id=record.id,
            grade="CORRECT",
            what_happened="No breakout occurred",
            price_high_after=4860.0,
            price_low_after=4775.0,
        )

        assessed = tmp_ledger.get_assessed()
        assert len(assessed) == 1
        assert assessed[0].outcome.grade == "CORRECT"
        assert assessed[0].outcome.assessed is True

    def test_unassessed_returns_only_pending(self, tmp_ledger):
        """get_unassessed returns only records without outcomes."""
        r1 = tmp_ledger.record_decision(
            strategy="ORB", instrument="A", decision="REJECT",
            confidence=85, reasoning="", risk_flags=[], context={},
        )
        tmp_ledger.record_decision(
            strategy="ORB", instrument="B", decision="APPROVE",
            confidence=90, reasoning="", risk_flags=[], context={},
        )
        tmp_ledger.assess_decision(
            r1.id, "CORRECT", "ok", 100, 90,
        )

        assert len(tmp_ledger.get_unassessed()) == 1
        assert tmp_ledger.get_unassessed()[0].instrument == "B"


# ── 3. Feedback table ─────────────────────────────────────────────────────

class TestFeedbackTable:
    def test_empty_when_no_assessments(self, tmp_ledger):
        """No feedback table when no assessed decisions exist."""
        assert tmp_ledger.build_feedback_table() == ""

    def test_table_includes_assessed_decisions(self, tmp_ledger):
        """Feedback table contains assessed decision rows."""
        r = tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="REJECT",
            confidence=85, reasoning="Tight range", risk_flags=[], context={},
        )
        tmp_ledger.assess_decision(
            r.id, "CORRECT", "No breakout", 4860, 4775,
        )

        table = tmp_ledger.build_feedback_table()
        assert "XAUUSD" in table
        assert "REJECT" in table
        assert "CORRECT" in table
        assert "accuracy" in table.lower()

    def test_table_limited_to_max_rows(self, tmp_ledger):
        """Table respects max_rows parameter."""
        for i in range(30):
            r = tmp_ledger.record_decision(
                strategy="ORB", instrument=f"I{i}", decision="REJECT",
                confidence=50, reasoning="", risk_flags=[], context={},
            )
            tmp_ledger.assess_decision(r.id, "CORRECT", "ok", 100, 90)

        table = tmp_ledger.build_feedback_table(max_rows=5)
        # Count data rows (not header)
        data_rows = [l for l in table.split("\n")
                     if l.startswith("|") and "Date" not in l and "---" not in l]
        assert len(data_rows) == 5


# ── 4. ORB assessment grading ─────────────────────────────────────────────

class TestORBAssessment:
    def test_reject_no_breakout_is_correct(self):
        """REJECT + no breakout = CORRECT."""
        record = _make_orb_record(decision="REJECT")
        # Price stays within range
        bars = [{"high": 4850, "low": 4790, "close": 4820}] * 100
        grade, what, triggered, tp, sl, pnl = assess_orb_decision(record, bars)
        assert grade == "CORRECT"
        assert triggered is False

    def test_reject_but_would_have_profited_is_missed(self):
        """REJECT + breakout hit TP = MISSED."""
        record = _make_orb_record(decision="REJECT")
        # Price breaks above range_high and hits TP
        bars = [
            {"high": 4860, "low": 4850, "close": 4858},  # triggers long
        ] + [
            {"high": 4970, "low": 4860, "close": 4965},  # hits TP (4857.82 + 69.42*1.5 = 4961.95)
        ]
        grade, what, triggered, tp, sl, pnl = assess_orb_decision(record, bars)
        assert grade == "MISSED"

    def test_approve_breakout_hit_sl_is_wrong(self):
        """APPROVE + breakout hit SL = WRONG."""
        record = _make_orb_record(decision="APPROVE")
        bars = [
            {"high": 4860, "low": 4855, "close": 4858},  # triggers long
            {"high": 4855, "low": 4780, "close": 4785},  # hits SL (range_low=4788.40)
        ]
        grade, what, triggered, tp, sl, pnl = assess_orb_decision(record, bars)
        assert grade == "WRONG"
        assert sl is True


# ── 5. Darvas assessment grading ──────────────────────────────────────────

class TestDarvasAssessment:
    def test_approve_hit_tp_is_correct(self):
        """APPROVE + hit TP = CORRECT."""
        record = _make_darvas_record(decision="APPROVE")
        bars = [
            {"high": 1.08500, "low": 1.08250, "close": 1.08450},
            {"high": 1.08800, "low": 1.08400, "close": 1.08750},  # hits TP=1.08700
        ]
        grade, what, triggered, tp, sl, pnl = assess_darvas_decision(record, bars)
        assert grade == "CORRECT"
        assert tp is True

    def test_approve_hit_sl_is_wrong(self):
        """APPROVE + hit SL = WRONG."""
        record = _make_darvas_record(decision="APPROVE")
        bars = [
            {"high": 1.08300, "low": 1.08050, "close": 1.08100},  # hits SL=1.08100
        ]
        grade, what, triggered, tp, sl, pnl = assess_darvas_decision(record, bars)
        assert grade == "WRONG"
        assert sl is True

    def test_reject_would_have_hit_sl_is_correct(self):
        """REJECT + would have hit SL = CORRECT (saved a loss)."""
        record = _make_darvas_record(decision="REJECT")
        bars = [
            {"high": 1.08300, "low": 1.08050, "close": 1.08100},  # hits SL
        ]
        grade, what, triggered, tp, sl, pnl = assess_darvas_decision(record, bars)
        assert grade == "CORRECT"

    def test_reject_would_have_profited_is_missed(self):
        """REJECT + would have hit TP = MISSED."""
        record = _make_darvas_record(decision="REJECT")
        bars = [
            {"high": 1.08800, "low": 1.08250, "close": 1.08750},  # hits TP
        ]
        grade, what, triggered, tp, sl, pnl = assess_darvas_decision(record, bars)
        assert grade == "MISSED"


# ── 6. Stats ──────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_accuracy(self, tmp_ledger):
        """Stats accurately reflect ledger state."""
        for i, grade in enumerate(["CORRECT", "CORRECT", "WRONG", "MISSED"]):
            r = tmp_ledger.record_decision(
                strategy="ORB", instrument=f"I{i}", decision="REJECT",
                confidence=80, reasoning="", risk_flags=[], context={},
            )
            tmp_ledger.assess_decision(r.id, grade, "test", 100, 90)

        stats = tmp_ledger.stats
        assert stats["total"] == 4
        assert stats["assessed"] == 4
        assert stats["correct"] == 2
        assert stats["wrong"] == 1
        assert stats["missed"] == 1
        assert stats["accuracy_pct"] == 50.0
