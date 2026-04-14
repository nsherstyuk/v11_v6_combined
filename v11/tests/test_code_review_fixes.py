"""
Tests for code review fixes (2026-04-12 session).

Covers:
    S1: build_regime_filtered_table edge cases
    S2: Auto-assessor matching logic (find_unassessed)
    S3: ORB adapter _compute_trend_context
    I2: ORB assessment same-bar TP+SL conservative assumption
    I4: Decision ID collision prevention
"""
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from v11.llm.decision_ledger import DecisionLedger, DecisionRecord
from v11.llm.assess_decisions import assess_orb_decision
from v11.llm.models import TrendContext


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_ledger(tmp_path):
    return DecisionLedger(str(tmp_path))


def _record_and_assess(ledger, strategy, instrument, grade,
                       confidence=80, atr_regime=None, atr_vs_avg=None):
    """Helper: record a decision and immediately assess it."""
    ctx = {}
    if atr_regime is not None:
        ctx["atr_regime"] = atr_regime
    if atr_vs_avg is not None:
        ctx["atr_vs_avg"] = atr_vs_avg
    ctx["range_high"] = 100.0
    ctx["range_low"] = 90.0
    r = ledger.record_decision(
        strategy=strategy, instrument=instrument,
        decision="APPROVE", confidence=confidence,
        reasoning="test", risk_flags=[], context=ctx,
    )
    ledger.assess_decision(r.id, grade, "test outcome", 105.0, 85.0)
    return r


# ── S1: Regime-filtered feedback table ────────────────────────────────────


class TestRegimeFilteredTable:
    def test_empty_when_no_assessed(self, tmp_ledger):
        """Returns empty string with no assessed decisions."""
        result = tmp_ledger.build_regime_filtered_table(
            strategy="ORB", regime_key="atr_regime",
            regime_value=1.0, regime_tolerance=0.3,
        )
        assert result == ""

    def test_regime_matched_decisions_shown(self, tmp_ledger):
        """Decisions within regime tolerance appear in the table."""
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=1.0)
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "WRONG", atr_regime=1.1)
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=2.0)  # too far

        table = tmp_ledger.build_regime_filtered_table(
            strategy="ORB", regime_key="atr_regime",
            regime_value=1.05, regime_tolerance=0.3,
        )
        assert "2 matches" in table
        assert "XAUUSD" in table

    def test_fallback_when_few_regime_matches(self, tmp_ledger):
        """Shows overall track record when <3 regime matches."""
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=1.0)
        # Add decisions from a very different regime
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "WRONG", atr_regime=5.0)
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=5.1)

        table = tmp_ledger.build_regime_filtered_table(
            strategy="ORB", regime_key="atr_regime",
            regime_value=1.0, regime_tolerance=0.3,
        )
        assert "1 matches" in table
        assert "Overall Track Record" in table

    def test_no_regime_matches_shows_fallback(self, tmp_ledger):
        """No regime matches at all still shows overall track record."""
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=5.0)

        table = tmp_ledger.build_regime_filtered_table(
            strategy="ORB", regime_key="atr_regime",
            regime_value=1.0, regime_tolerance=0.3,
        )
        assert "No prior decisions in similar regime" in table
        assert "Overall Track Record" in table

    def test_tolerance_boundary(self, tmp_ledger):
        """Decision within tolerance is included, outside is excluded."""
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=1.25)
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=0.75)
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "WRONG", atr_regime=1.5)  # outside

        table = tmp_ledger.build_regime_filtered_table(
            strategy="ORB", regime_key="atr_regime",
            regime_value=1.0, regime_tolerance=0.3,
        )
        assert "2 matches" in table

    def test_filters_by_strategy(self, tmp_ledger):
        """Only decisions from the requested strategy appear in regime section."""
        # 3+ ORB matches so no fallback section is needed
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=1.0)
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "CORRECT", atr_regime=0.9)
        _record_and_assess(tmp_ledger, "ORB", "XAUUSD", "WRONG", atr_regime=1.1)
        _record_and_assess(tmp_ledger, "DARVAS", "EURUSD", "WRONG", atr_regime=1.0)

        table = tmp_ledger.build_regime_filtered_table(
            strategy="ORB", regime_key="atr_regime",
            regime_value=1.0, regime_tolerance=0.3,
        )
        assert "3 matches" in table
        # Regime section should not contain the DARVAS decision
        assert "EURUSD" not in table


# ── S2: Auto-assessor find_unassessed ─────────────────────────────────────


class TestFindUnassessed:
    def test_finds_by_strategy_and_instrument(self, tmp_ledger):
        """Finds unassessed decision by strategy + instrument."""
        tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="APPROVE",
            confidence=80, reasoning="test", risk_flags=[],
            context={"range_high": 100.0, "range_low": 90.0},
        )
        result = tmp_ledger.find_unassessed(
            "ORB", "XAUUSD", range_high=100.0, range_low=90.0)
        assert result is not None
        assert result.strategy == "ORB"

    def test_skips_assessed(self, tmp_ledger):
        """Assessed decisions are not returned."""
        r = tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="APPROVE",
            confidence=80, reasoning="test", risk_flags=[],
            context={"range_high": 100.0, "range_low": 90.0},
        )
        tmp_ledger.assess_decision(r.id, "CORRECT", "ok", 105, 85)
        result = tmp_ledger.find_unassessed(
            "ORB", "XAUUSD", range_high=100.0, range_low=90.0)
        assert result is None

    def test_returns_none_when_no_match(self, tmp_ledger):
        """Returns None when nothing matches."""
        tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="APPROVE",
            confidence=80, reasoning="test", risk_flags=[],
            context={"range_high": 100.0, "range_low": 90.0},
        )
        result = tmp_ledger.find_unassessed(
            "ORB", "XAUUSD", range_high=200.0, range_low=190.0)
        assert result is None

    def test_multi_strategy_search(self, tmp_ledger):
        """Comma-separated strategy list matches any."""
        tmp_ledger.record_decision(
            strategy="4H_RETEST", instrument="EURUSD", decision="APPROVE",
            confidence=80, reasoning="test", risk_flags=[],
            context={"breakout_price": 1.083},
        )
        result = tmp_ledger.find_unassessed(
            "DARVAS,4H_RETEST", "EURUSD", breakout_price=1.083)
        assert result is not None
        assert result.strategy == "4H_RETEST"

    def test_float_tolerance(self, tmp_ledger):
        """Float comparison uses tolerance for matching."""
        tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="APPROVE",
            confidence=80, reasoning="test", risk_flags=[],
            context={"range_high": 100.0000001},
        )
        result = tmp_ledger.find_unassessed(
            "ORB", "XAUUSD", range_high=100.0)
        assert result is not None


# ── S3: _compute_trend_context ────────────────────────────────────────────


class TestComputeTrendContext:
    def _make_adapter(self):
        """Create ORBAdapter with mocked dependencies."""
        from v11.live.orb_adapter import ORBAdapter
        from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
        from v11.live.risk_manager import RiskManager

        log = logging.getLogger("test_trend")
        ib = MagicMock()
        ib.isConnected.return_value = True
        ib.reqMktData.return_value = MagicMock()
        ib.pendingTickersEvent = MagicMock()
        ib.sleep = MagicMock()
        contract = MagicMock()
        config = V6StrategyConfig(
            instrument="XAUUSD", range_start_hour=0, range_end_hour=6,
            trade_start_hour=8, trade_end_hour=16,
            velocity_filter_enabled=False, gap_filter_enabled=False,
            qty=1, point_value=1.0, price_decimals=2,
        )
        rm = RiskManager(
            max_daily_loss=500.0, max_daily_trades_per_strategy=10,
            max_concurrent_positions=3, log=log,
        )
        adapter = ORBAdapter(
            ib=ib, contract=contract, v6_config=config,
            risk_manager=rm, log=log, dry_run=True,
        )
        return adapter

    def _make_daily_bars(self, n=20, base_price=100.0, trend=0.5):
        """Create mock daily bars with controllable trend."""
        bars = []
        for i in range(n):
            price = base_price + i * trend
            bar = MagicMock()
            bar.o = price
            bar.h = price + 2
            bar.l = price - 2
            bar.c = price + trend * 0.5
            bars.append(bar)
        return bars

    def test_returns_none_with_few_bars(self):
        """Returns None with fewer than 5 daily bars."""
        adapter = self._make_adapter()
        adapter._daily_bars = self._make_daily_bars(n=3)
        result = adapter._compute_trend_context(100.0)
        assert result is None

    def test_returns_trend_context_with_enough_bars(self):
        """Returns TrendContext with >= 5 bars."""
        adapter = self._make_adapter()
        adapter._daily_bars = self._make_daily_bars(n=20)
        result = adapter._compute_trend_context(110.0)
        assert isinstance(result, TrendContext)

    def test_position_vs_sma_returns_string(self):
        """position_vs_20d_sma is a string, not a float."""
        adapter = self._make_adapter()
        adapter._daily_bars = self._make_daily_bars(n=20)
        result = adapter._compute_trend_context(200.0)  # way above SMA
        assert isinstance(result.position_vs_20d_sma, str)
        assert result.position_vs_20d_sma == "above"

    def test_position_below_sma(self):
        """Price far below SMA returns 'below'."""
        adapter = self._make_adapter()
        adapter._daily_bars = self._make_daily_bars(n=20, base_price=100.0)
        result = adapter._compute_trend_context(50.0)  # way below SMA
        assert result.position_vs_20d_sma == "below"

    def test_uptrend_has_positive_slope(self):
        """Uptrending bars produce positive SMA slope."""
        adapter = self._make_adapter()
        adapter._daily_bars = self._make_daily_bars(n=25, trend=1.0)
        result = adapter._compute_trend_context(125.0)
        assert result.sma20_slope > 0

    def test_consecutive_up_days(self):
        """Counts consecutive up days correctly."""
        adapter = self._make_adapter()
        bars = self._make_daily_bars(n=10, trend=1.0)  # all up
        adapter._daily_bars = bars
        result = adapter._compute_trend_context(110.0)
        assert result.consecutive_up_days > 0
        assert result.consecutive_down_days == 0


# ── I2: ORB assessment same-bar TP+SL ────────────────────────────────────


class TestORBAssessmentSameBar:
    def _make_record(self, decision="APPROVE"):
        return DecisionRecord(
            id="test_id",
            timestamp_utc="2026-01-01T08:00:00+00:00",
            strategy="ORB",
            instrument="XAUUSD",
            decision=decision,
            confidence=80,
            reasoning="test",
            context={
                "range_high": 100.0,
                "range_low": 90.0,
                "range_size": 10.0,
            },
        )

    def test_same_bar_tp_sl_assumes_sl(self):
        """When both TP and SL are hit on the same bar, conservatively assume SL."""
        record = self._make_record(decision="APPROVE")
        # Bar triggers long entry (high >= 100) then hits both TP (115) and SL (90) in one bar
        bars = [
            {"high": 120, "low": 85, "close": 95},  # triggers long + hits both TP and SL
        ]
        grade, what, triggered, tp, sl, pnl = assess_orb_decision(record, bars)
        # Should conservatively assume SL hit first
        assert sl is True
        assert tp is False
        assert grade == "WRONG"
        assert pnl == -1.0


# ── I4: Decision ID collision ────────────────────────────────────────────


class TestDecisionIDCollision:
    def test_same_second_gets_unique_ids(self, tmp_ledger):
        """Two decisions at the same timestamp get different IDs."""
        ts = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        r1 = tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="APPROVE",
            confidence=80, reasoning="first", risk_flags=[], context={},
            timestamp=ts,
        )
        r2 = tmp_ledger.record_decision(
            strategy="ORB", instrument="XAUUSD", decision="REJECT",
            confidence=60, reasoning="second", risk_flags=[], context={},
            timestamp=ts,
        )
        assert r1.id != r2.id
        assert len(tmp_ledger.get_all()) == 2

    def test_three_same_second_all_unique(self, tmp_ledger):
        """Three decisions at the same timestamp all get different IDs."""
        ts = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
        ids = set()
        for i in range(3):
            r = tmp_ledger.record_decision(
                strategy="ORB", instrument="XAUUSD", decision="APPROVE",
                confidence=80, reasoning=f"r{i}", risk_flags=[], context={},
                timestamp=ts,
            )
            ids.add(r.id)
        assert len(ids) == 3
