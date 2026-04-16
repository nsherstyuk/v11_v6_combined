"""
Tests for TradeManager — Trade lifecycle from entry to exit.

Design decisions tested:
    1. Entry sets trade state and returns True on success (dry-run)
    2. Entry blocked when already in trade
    3. SL failure forces position close (no unhedged positions)
    4. check_exit detects SL hit (long and short)
    5. check_exit detects TARGET hit (uses ExitReason.TARGET, not TIME_STOP)
    6. check_exit detects TIME_STOP at max_hold_bars
    7. _execute_exit computes PnL correctly (long and short)
    8. _execute_exit resets trade state after exit
    9. Daily counters track trades and PnL
    10. reconcile_position detects state mismatch with broker
    11. force_close works when in trade
    12. CSV logging creates file with correct fields
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from v11.core.types import (
    Direction, BreakoutSignal, DarvasBox, FilterDecision, ExitReason,
)
from v11.config.live_config import EURUSD_INSTRUMENT, XAUUSD_INSTRUMENT
from v11.execution.trade_manager import TradeManager, TRADE_CSV_FIELDS


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_signal(direction=Direction.LONG, breakout_price=1.1050) -> BreakoutSignal:
    box = DarvasBox(
        top=1.1050, bottom=1.1000,
        top_confirmed_at=10, bottom_confirmed_at=20,
        formation_start=5, duration_bars=15, atr_at_formation=0.0010,
    )
    return BreakoutSignal(
        timestamp=datetime(2025, 1, 2, 10, 30, tzinfo=timezone.utc),
        direction=direction, box=box,
        breakout_price=breakout_price,
        breakout_bar_index=25, atr=0.0010,
    )


def _make_decision(entry=1.1050, stop=1.1000, target=1.1150) -> FilterDecision:
    return FilterDecision(
        approved=True, confidence=85,
        entry_price=entry, stop_price=stop, target_price=target,
        reasoning="test signal", risk_flags=[],
    )


@pytest.fixture
def log():
    return logging.getLogger("test_tm")


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.connected = True
    conn.get_position_size = MagicMock(return_value=0.0)
    conn.has_position = MagicMock(return_value=False)
    conn.submit_bracket_order = MagicMock(return_value=(None, None, None))
    conn.submit_sl_tp_oca = MagicMock(return_value=(None, None))
    return conn


@pytest.fixture
def tm(mock_conn, log, tmp_path):
    return TradeManager(
        conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
        trade_log_dir=tmp_path, dry_run=True, max_hold_bars=120,
    )


@pytest.fixture
def tm_xau(mock_conn, log, tmp_path):
    return TradeManager(
        conn=mock_conn, inst=XAUUSD_INSTRUMENT, log=log,
        trade_log_dir=tmp_path, dry_run=True, max_hold_bars=120,
    )


# ── 1. Entry success in dry-run ─────────────────────────────────────────────

class TestEntryDryRun:
    def test_enter_trade_returns_true(self, tm):
        ok = tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        assert ok is True

    def test_enter_trade_sets_state(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        assert tm.in_trade is True
        assert tm.direction == Direction.LONG
        assert tm.signal_entry_price == 1.1050
        assert tm.stop_price == 1.1000
        assert tm.target_price == 1.1150

    def test_enter_short_trade(self, tm):
        sig = _make_signal(direction=Direction.SHORT, breakout_price=1.1000)
        dec = _make_decision(entry=1.1000, stop=1.1050, target=1.0900)
        tm.enter_trade(sig, dec, 0.4, 100)
        assert tm.direction == Direction.SHORT


# ── 2. Entry blocked when in trade ──────────────────────────────────────────

class TestEntryBlocked:
    def test_second_entry_rejected(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        ok = tm.enter_trade(_make_signal(), _make_decision(), 0.6, 200)
        assert ok is False

    def test_state_unchanged_on_rejection(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        original_bar = tm.entry_bar_index
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 200)
        assert tm.entry_bar_index == original_bar  # unchanged


# ── 3. SL failure forces close (non-dry-run) ────────────────────────────────

class TestSLFailureForceClose:
    def test_sl_double_failure_closes_position(self, mock_conn, log, tmp_path):
        """If SL order fails twice, position must be force-closed."""
        tm = TradeManager(
            conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path, dry_run=False, max_hold_bars=120,
        )
        # Entry succeeds
        entry_trade = MagicMock()
        entry_trade.orderStatus.avgFillPrice = 1.1050
        entry_trade.orderStatus.status = "Filled"
        mock_conn.submit_market_order.return_value = entry_trade
        mock_conn.get_fill_commission.return_value = 0.0

        # SL fails both times
        mock_conn.submit_stop_order.return_value = None

        ok = tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)

        assert ok is False
        assert tm.in_trade is False  # force-closed
        mock_conn.close_position.assert_called_once()


# ── 4. SL hit detection ─────────────────────────────────────────────────────

class TestSLHit:
    def test_long_sl_hit(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.0990, bar_high=1.1010,
            bar_low=1.0990, current_bar_index=110)
        assert record is not None
        assert record.exit_reason == "SL"

    def test_short_sl_hit(self, tm):
        sig = _make_signal(direction=Direction.SHORT, breakout_price=1.1000)
        dec = _make_decision(entry=1.1000, stop=1.1050, target=1.0900)
        tm.enter_trade(sig, dec, 0.4, 100)
        record = tm.check_exit(
            current_price=1.1060, bar_high=1.1060,
            bar_low=1.1020, current_bar_index=110)
        assert record is not None
        assert record.exit_reason == "SL"

    def test_sl_not_hit(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.1040, bar_high=1.1060,
            bar_low=1.1020, current_bar_index=110)
        assert record is None


# ── 5. Target hit uses ExitReason.TARGET ─────────────────────────────────────

class TestTargetHit:
    def test_long_target_hit(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.1160, bar_high=1.1160,
            bar_low=1.1100, current_bar_index=110)
        assert record is not None
        assert record.exit_reason == "TARGET"

    def test_short_target_hit(self, tm):
        sig = _make_signal(direction=Direction.SHORT, breakout_price=1.1000)
        dec = _make_decision(entry=1.1000, stop=1.1050, target=1.0900)
        tm.enter_trade(sig, dec, 0.4, 100)
        record = tm.check_exit(
            current_price=1.0890, bar_high=1.0950,
            bar_low=1.0890, current_bar_index=110)
        assert record is not None
        assert record.exit_reason == "TARGET"


# ── 6. Time stop ────────────────────────────────────────────────────────────

class TestTimeStop:
    def test_time_stop_at_max_hold(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.1040, bar_high=1.1060,
            bar_low=1.1020, current_bar_index=220)  # 220 - 100 = 120 bars
        assert record is not None
        assert record.exit_reason == "TIME_STOP"

    def test_no_time_stop_before_max(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.1040, bar_high=1.1060,
            bar_low=1.1020, current_bar_index=219)  # 219 - 100 = 119 bars
        assert record is None

    def test_custom_max_hold_bars(self, mock_conn, log, tmp_path):
        """max_hold_bars from constructor is respected."""
        tm = TradeManager(
            conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path, dry_run=True, max_hold_bars=60,
        )
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.1040, bar_high=1.1060,
            bar_low=1.1020, current_bar_index=160)  # 160 - 100 = 60
        assert record is not None
        assert record.exit_reason == "TIME_STOP"


# ── 7. PnL computation ──────────────────────────────────────────────────────

class TestPnLComputation:
    def test_long_profit(self, tm):
        """Long trade with target hit → positive PnL."""
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.1160, bar_high=1.1160,
            bar_low=1.1100, current_bar_index=110)
        # PnL = (1.1150 - 1.1050) * quantity = 0.0100 * 20000 = 200.0
        assert record.pnl == pytest.approx(200.0, abs=1.0)

    def test_long_sl_loss(self, tm):
        """Long trade with SL hit → negative PnL."""
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.check_exit(
            current_price=1.0990, bar_high=1.1010,
            bar_low=1.0990, current_bar_index=110)
        # PnL = (1.1000 - 1.1050) * 20000 = -100.0
        assert record.pnl == pytest.approx(-100.0, abs=1.0)

    def test_short_profit(self, tm):
        sig = _make_signal(direction=Direction.SHORT, breakout_price=1.1000)
        dec = _make_decision(entry=1.1000, stop=1.1050, target=1.0900)
        tm.enter_trade(sig, dec, 0.4, 100)
        record = tm.check_exit(
            current_price=1.0890, bar_high=1.0950,
            bar_low=1.0890, current_bar_index=110)
        # PnL = (1.1000 - 1.0900) * 20000 = 200.0
        assert record.pnl == pytest.approx(200.0, abs=1.0)

    def test_xauusd_pnl(self, tm_xau):
        """XAUUSD: PnL = price_diff * qty (USD-quoted)."""
        sig = _make_signal(direction=Direction.LONG, breakout_price=2000.0)
        sig = BreakoutSignal(
            timestamp=sig.timestamp, direction=Direction.LONG,
            box=DarvasBox(top=2000, bottom=1990, top_confirmed_at=10,
                          bottom_confirmed_at=20, formation_start=5,
                          duration_bars=15, atr_at_formation=5.0),
            breakout_price=2000.0, breakout_bar_index=25, atr=5.0,
        )
        dec = _make_decision(entry=2000.0, stop=1990.0, target=2025.0)
        tm_xau.enter_trade(sig, dec, 0.6, 100)
        record = tm_xau.check_exit(
            current_price=2030, bar_high=2030,
            bar_low=2010, current_bar_index=110)
        # PnL = (2025 - 2000) * 1.0 = 25.0
        assert record.pnl == pytest.approx(25.0, abs=1.0)


# ── 8. State reset after exit ────────────────────────────────────────────────

class TestStateReset:
    def test_in_trade_false_after_exit(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                       bar_low=1.0990, current_bar_index=110)
        assert tm.in_trade is False
        assert tm.direction is None

    def test_can_enter_new_trade_after_exit(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                       bar_low=1.0990, current_bar_index=110)
        ok = tm.enter_trade(_make_signal(), _make_decision(), 0.6, 200)
        assert ok is True


# ── 9. Daily counters ────────────────────────────────────────────────────────

class TestDailyCounters:
    def test_trade_count_increments(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                       bar_low=1.0990, current_bar_index=110)
        assert tm.daily_trades == 1

    def test_pnl_accumulates(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                       bar_low=1.0990, current_bar_index=110)
        assert tm.daily_pnl < 0  # SL hit = loss

    def test_reset_daily_clears(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                       bar_low=1.0990, current_bar_index=110)
        tm.reset_daily()
        assert tm.daily_trades == 0
        assert tm.daily_pnl == 0.0


# ── 10. Position reconciliation ──────────────────────────────────────────────

class TestReconciliation:
    def test_in_trade_but_broker_flat_resets(self, tm, mock_conn):
        """If we think we're in trade but broker is flat → reset."""
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        mock_conn.get_position_size.return_value = 0.0  # broker is flat
        tm.reconcile_position()
        assert tm.in_trade is False

    def test_flat_and_broker_flat_ok(self, tm, mock_conn):
        """Both flat → no change."""
        mock_conn.get_position_size.return_value = 0.0
        tm.reconcile_position()
        assert tm.in_trade is False

    def test_both_in_trade_ok(self, tm, mock_conn):
        """Both in trade → no change."""
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        mock_conn.get_position_size.return_value = 20000.0  # broker has position
        tm.reconcile_position()
        assert tm.in_trade is True  # unchanged


# ── 11. Force close ──────────────────────────────────────────────────────────

class TestForceClose:
    def test_force_close_returns_record(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        record = tm.force_close(1.1020, ExitReason.SHUTDOWN, 150)
        assert record is not None
        assert record.exit_reason == "SHUTDOWN"

    def test_force_close_when_flat_returns_none(self, tm):
        record = tm.force_close(1.1020, ExitReason.SHUTDOWN, 150)
        assert record is None


# ── 12. CSV logging ──────────────────────────────────────────────────────────

class TestCSVLogging:
    def test_csv_created_on_first_trade(self, tm, tmp_path):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                       bar_low=1.0990, current_bar_index=110)
        csv_path = tmp_path / "trades_eurusd.csv"
        assert csv_path.exists()

    def test_csv_has_correct_headers(self, tm, tmp_path):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                       bar_low=1.0990, current_bar_index=110)
        csv_path = tmp_path / "trades_eurusd.csv"
        with open(csv_path) as f:
            header = f.readline().strip()
        for field in TRADE_CSV_FIELDS:
            assert field in header


# ── 13. Emergency close logging ──────────────────────────────────────────────

class TestEmergencyClose:
    def test_emergency_close_logs_trade(self, mock_conn, log, tmp_path):
        """Emergency close should create a CSV log entry."""
        mock_conn.submit_bracket_order.return_value = (None, None, None)
        tm = TradeManager(
            conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path, dry_run=True, max_hold_bars=120,
        )
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        assert tm.in_trade is True
        tm.emergency_close("TEST")
        assert tm.in_trade is False
        csv_file = tmp_path / f"trades_{EURUSD_INSTRUMENT.pair_name.lower()}.csv"
        assert csv_file.exists()

    def test_emergency_close_fires_callback(self, mock_conn, log, tmp_path):
        """Emergency close should fire on_trade_closed callback."""
        mock_conn.submit_bracket_order.return_value = (None, None, None)
        tm = TradeManager(
            conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path, dry_run=True, max_hold_bars=120,
        )
        records = []
        tm.on_trade_closed = lambda r: records.append(r)
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.emergency_close("TEST")
        assert len(records) == 1
        assert records[0].exit_reason == "EMERGENCY"


# ── 14. Entry time tracking (regression for 2026-04-16 bug) ──────────────────

class TestEntryTime:
    """Regression tests: TradeRecord.entry_time used to be set to `now()` at
    exit, losing the true entry time. enter_trade() must capture the entry
    time and _execute_exit()/emergency_close() must preserve it."""

    def test_entry_time_captured_on_enter(self, tm):
        assert tm.entry_time is None
        before = datetime.now(timezone.utc)
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        after = datetime.now(timezone.utc)
        assert tm.entry_time is not None
        assert before <= tm.entry_time <= after

    def test_entry_time_cleared_after_exit(self, tm):
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        tm.check_exit(current_price=1.0990, bar_high=1.1010,
                      bar_low=1.0990, current_bar_index=110)
        assert tm.entry_time is None

    def test_record_entry_time_precedes_exit_time(self, tm):
        """Trade record should show entry_time strictly before exit_time."""
        import time
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        entry_ts = tm.entry_time
        time.sleep(0.01)  # ensure measurable delta
        record = tm.check_exit(current_price=1.0990, bar_high=1.1010,
                               bar_low=1.0990, current_bar_index=110)
        assert record is not None
        assert record.entry_time == entry_ts
        assert record.entry_time < record.exit_time

    def test_emergency_close_preserves_entry_time(self, mock_conn, log, tmp_path):
        tm = TradeManager(
            conn=mock_conn, inst=EURUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path, dry_run=True, max_hold_bars=120,
        )
        records = []
        tm.on_trade_closed = lambda r: records.append(r)
        tm.enter_trade(_make_signal(), _make_decision(), 0.6, 100)
        entry_ts = tm.entry_time
        tm.emergency_close("TEST")
        assert len(records) == 1
        assert records[0].entry_time == entry_ts
        assert records[0].entry_time <= records[0].exit_time
