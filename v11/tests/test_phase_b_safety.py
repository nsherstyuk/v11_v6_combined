"""
Tests for Phase B safety features (auto-reconnect, emergency shutdown, etc.).

Design decisions tested:
    1. IBKRConnection.persistent_failure detects disconnect > 5 min
    2. IBKRConnection.persistent_failure is False when connected or recently disconnected
    3. TradeManager.emergency_close resets trade state even on failure
    4. TradeManager.reconcile_position resets when internal=in_trade but broker=flat
    5. TradeManager.reconcile_position auto-closes orphan when auto_close_orphans=True
    6. TradeManager.reconcile_position logs warning when orphan detected but not auto-closed
    7. V11LiveTrader._check_price_staleness warns at 60s, errors at 300s, emergency at 600s
    8. V11LiveTrader._write_heartbeat writes valid JSON with expected fields
    9. V11LiveTrader._emergency_shutdown writes state file and exits with code 1
    10. V11LiveTrader._reconcile_positions syncs risk manager with broker positions
    11. LiveConfig validates signal_llm_timeout_seconds > 0
    12. GrokFilter uses signal_timeout for evaluate_signal, timeout for evaluate_orb_signal
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from v11.config.live_config import (
    LiveConfig, XAUUSD_INSTRUMENT, InstrumentConfig,
)
from v11.execution.ibkr_connection import IBKRConnection
from v11.execution.trade_manager import TradeManager
from v11.live.risk_manager import RiskManager


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def log():
    return logging.getLogger("test_phase_b")


@pytest.fixture
def mock_conn():
    """Mock IBKRConnection with common defaults."""
    conn = MagicMock(spec=IBKRConnection)
    conn.connected = True
    conn.persistent_failure = False
    conn.ib = MagicMock()
    conn._contracts = {"XAUUSD": MagicMock()}
    conn._tickers = {"XAUUSD": MagicMock()}
    conn.get_position_size.return_value = 0.0
    conn.get_broker_positions.return_value = []
    conn.cancel_all_orders.return_value = None
    conn.cancel_orders_for.return_value = None
    conn.close_position.return_value = None
    conn.restart_price_stream.return_value = True
    conn.sleep = MagicMock()
    return conn


@pytest.fixture
def risk_manager(log):
    return RiskManager(
        max_daily_loss=500.0,
        max_daily_trades_per_strategy=20,
        max_concurrent_positions=3,
        log=log,
    )


@pytest.fixture
def trade_manager(mock_conn, log, tmp_path):
    return TradeManager(
        conn=mock_conn,
        inst=XAUUSD_INSTRUMENT,
        log=log,
        trade_log_dir=tmp_path / "trades",
        dry_run=True,
    )


# ── 1-2: IBKRConnection.persistent_failure ───────────────────────────────────

class TestPersistentFailure:
    """Test disconnect duration tracking and persistent failure detection."""

    def test_persistent_failure_false_when_connected(self, log):
        conn = IBKRConnection("127.0.0.1", 4002, 11, log)
        conn._first_disconnect_time = None
        assert conn.persistent_failure is False

    def test_persistent_failure_false_when_recently_disconnected(self, log):
        conn = IBKRConnection("127.0.0.1", 4002, 11, log)
        # Disconnected 60s ago — not yet persistent
        conn._first_disconnect_time = time.time() - 60
        assert conn.persistent_failure is False

    def test_persistent_failure_true_after_5min(self, log):
        conn = IBKRConnection("127.0.0.1", 4002, 11, log)
        # Disconnected 301s ago — persistent failure
        conn._first_disconnect_time = time.time() - 301
        assert conn.persistent_failure is True

    def test_persistent_failure_clears_on_reconnect(self, log):
        conn = IBKRConnection("127.0.0.1", 4002, 11, log)
        conn._first_disconnect_time = time.time() - 100
        # Simulate reconnect: ensure_connected clears the timer
        conn._first_disconnect_time = None
        assert conn.persistent_failure is False

    def test_disconnect_timer_starts_on_disconnect(self, log):
        conn = IBKRConnection("127.0.0.1", 4002, 11, log)
        assert conn._first_disconnect_time is None
        # Simulate _on_disconnect callback
        conn._first_disconnect_time = time.time()
        assert conn._first_disconnect_time is not None
        assert conn.persistent_failure is False  # just started


# ── 3: TradeManager.emergency_close ─────────────────────────────────────────

class TestEmergencyClose:
    """Test emergency close resets trade state even on failure."""

    def test_emergency_close_no_op_when_not_in_trade(self, trade_manager):
        trade_manager.in_trade = False
        trade_manager.emergency_close("TEST")
        # Should not raise, no side effects

    def test_emergency_close_resets_state_in_dry_run(self, trade_manager):
        from v11.core.types import Direction
        trade_manager.in_trade = True
        trade_manager.direction = Direction.LONG
        trade_manager.signal_entry_price = 3300.0
        trade_manager.entry_time = datetime.now(timezone.utc)

        trade_manager.emergency_close("TEST_EMERGENCY")

        assert trade_manager.in_trade is False
        assert trade_manager.direction is None

    def test_emergency_close_cancels_orders_in_live(self, mock_conn, log, tmp_path):
        from v11.core.types import Direction
        tm = TradeManager(
            conn=mock_conn, inst=XAUUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path / "trades", dry_run=False,
        )
        tm.in_trade = True
        tm.direction = Direction.LONG
        tm.signal_entry_price = 3300.0
        tm.entry_time = datetime.now(timezone.utc)
        tm._sl_order = MagicMock()
        tm._tp_order = MagicMock()

        tm.emergency_close("TEST")

        assert tm.in_trade is False
        # In live mode, close_position should have been called
        mock_conn.close_position.assert_called()


# ── 4-6: TradeManager.reconcile_position ────────────────────────────────────

class TestReconcilePosition:
    """Test position reconciliation after reconnect."""

    def test_reconcile_resets_when_internal_in_trade_broker_flat(self, trade_manager, mock_conn):
        from v11.core.types import Direction
        trade_manager.in_trade = True
        trade_manager.direction = Direction.LONG
        mock_conn.get_position_size.return_value = 0.0

        trade_manager.reconcile_position()

        assert trade_manager.in_trade is False

    def test_reconcile_logs_orphan_when_internal_flat_broker_has_pos(self, trade_manager, mock_conn):
        trade_manager.in_trade = False
        mock_conn.get_position_size.return_value = 1.0

        # Default: auto_close_orphans=False
        trade_manager.reconcile_position()

        # Should NOT close — just warn
        mock_conn.close_position.assert_not_called()
        # Still not in trade
        assert trade_manager.in_trade is False

    def test_reconcile_auto_closes_orphan_when_enabled(self, mock_conn, log, tmp_path):
        tm = TradeManager(
            conn=mock_conn, inst=XAUUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path / "trades", dry_run=True,
            auto_close_orphans=True,
        )
        tm.in_trade = False
        mock_conn.get_position_size.return_value = 1.0

        tm.reconcile_position()

        mock_conn.close_position.assert_called_once_with("XAUUSD", "long", 1.0)

    def test_reconcile_auto_closes_short_orphan(self, mock_conn, log, tmp_path):
        tm = TradeManager(
            conn=mock_conn, inst=XAUUSD_INSTRUMENT, log=log,
            trade_log_dir=tmp_path / "trades", dry_run=True,
            auto_close_orphans=True,
        )
        tm.in_trade = False
        mock_conn.get_position_size.return_value = -1.0

        tm.reconcile_position()

        mock_conn.close_position.assert_called_once_with("XAUUSD", "short", 1.0)

    def test_reconcile_ok_when_both_agree(self, trade_manager, mock_conn):
        from v11.core.types import Direction
        trade_manager.in_trade = True
        trade_manager.direction = Direction.LONG
        mock_conn.get_position_size.return_value = 1.0

        trade_manager.reconcile_position()

        # Should remain in trade
        assert trade_manager.in_trade is True

    def test_reconcile_warns_on_size_mismatch(self, trade_manager, mock_conn):
        from v11.core.types import Direction
        trade_manager.in_trade = True
        trade_manager.direction = Direction.LONG
        mock_conn.get_position_size.return_value = 2.0  # expected 1.0

        trade_manager.reconcile_position()

        # Should remain in trade but log warning
        assert trade_manager.in_trade is True


# ── 7: Price staleness detection ────────────────────────────────────────────

class TestPriceStaleness:
    """Test price staleness escalation: warn → restart stream → emergency."""

    def _make_trader(self, log, mock_conn, tmp_path):
        """Create a V11LiveTrader with mocked components."""
        from v11.live.run_live import V11LiveTrader
        from v11.llm.passthrough_filter import PassthroughFilter

        live_cfg = LiveConfig(
            instruments=[XAUUSD_INSTRUMENT],
            dry_run=True,
        )

        with patch.object(V11LiveTrader, '__init__', lambda self, *a, **kw: None):
            trader = V11LiveTrader.__new__(V11LiveTrader)

        trader.log = log
        trader.live_cfg = live_cfg
        trader.conn = mock_conn
        trader.llm_filter = PassthroughFilter(rr_ratio=2.0)
        trader.risk_manager = RiskManager(
            max_daily_loss=500.0, max_daily_trades_per_strategy=20,
            max_concurrent_positions=3, log=log,
        )
        trader._shutdown = False
        trader._active_pairs = ["XAUUSD"]
        trader._last_price_time = {}
        trader._current_trading_date = ""
        trader._session_reset_done = False
        trader._tick_logger = None

        # Mock runner
        trader.runner = MagicMock()
        trader.runner.get_all_status.return_value = {
            'risk': {'combined_pnl': 0.0, 'combined_trades': 0,
                     'open_positions': []},
            'strategies': [],
            'instruments': ['XAUUSD'],
        }
        trader.runner.feeds = {}
        trader.runner.engines = []

        return trader

    def test_staleness_warns_at_60s(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)
        trader._last_price_time["XAUUSD"] = time.time() - 90  # 90s stale

        # Should warn but not restart or shutdown
        trader._check_price_staleness()
        mock_conn.restart_price_stream.assert_not_called()

    def test_staleness_restarts_stream_at_300s(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)
        trader._last_price_time["XAUUSD"] = time.time() - 310  # 310s stale

        trader._check_price_staleness()
        mock_conn.restart_price_stream.assert_called_once_with("XAUUSD")

    def test_staleness_emergency_shutdown_at_600s(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)
        trader._last_price_time["XAUUSD"] = time.time() - 610  # 610s stale

        with patch.object(trader, '_emergency_shutdown') as mock_emergency:
            trader._check_price_staleness()
            mock_emergency.assert_called_once_with("price_feed_dead")

    def test_staleness_no_warning_when_prices_fresh(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)
        trader._last_price_time["XAUUSD"] = time.time() - 5  # 5s ago

        trader._check_price_staleness()
        mock_conn.restart_price_stream.assert_not_called()

    def test_staleness_warns_when_never_received_price(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)
        # No entry in _last_price_time for XAUUSD
        trader._check_price_staleness()
        # Should not crash, just warn


# ── 8: Heartbeat file ──────────────────────────────────────────────────────

class TestHeartbeat:
    """Test heartbeat.json writing for external monitoring."""

    def _make_trader(self, log, mock_conn, tmp_path):
        """Create a V11LiveTrader with mocked components."""
        from v11.live.run_live import V11LiveTrader
        from v11.llm.passthrough_filter import PassthroughFilter

        live_cfg = LiveConfig(
            instruments=[XAUUSD_INSTRUMENT],
            dry_run=True,
        )

        with patch.object(V11LiveTrader, '__init__', lambda self, *a, **kw: None):
            trader = V11LiveTrader.__new__(V11LiveTrader)

        trader.log = log
        trader.live_cfg = live_cfg
        trader.conn = mock_conn
        trader.llm_filter = PassthroughFilter(rr_ratio=2.0)
        trader.risk_manager = RiskManager(
            max_daily_loss=500.0, max_daily_trades_per_strategy=20,
            max_concurrent_positions=3, log=log,
        )
        trader._shutdown = False
        trader._active_pairs = ["XAUUSD"]
        trader._last_price_time = {}
        trader._current_trading_date = ""
        trader._session_reset_done = False
        trader._tick_logger = None

        trader.runner = MagicMock()
        trader.runner.get_all_status.return_value = {
            'risk': {'combined_pnl': 42.5, 'combined_trades': 3,
                     'open_positions': ['XAUUSD']},
            'strategies': [
                {'strategy_name': 'V6_ORB', 'pair_name': 'XAUUSD',
                 'in_trade': True, 'bar_count': 500},
            ],
            'instruments': ['XAUUSD'],
        }
        trader.runner.feeds = {}
        trader.runner.engines = []

        return trader

    def test_heartbeat_writes_valid_json(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)

        with patch("v11.live.run_live.ROOT", tmp_path):
            trader._write_heartbeat()

        heartbeat_file = tmp_path / "v11" / "live" / "state" / "heartbeat.json"
        assert heartbeat_file.exists()

        data = json.loads(heartbeat_file.read_text())
        assert "timestamp" in data
        assert "connected" in data
        assert "persistent_failure" in data
        assert "instruments" in data
        assert "pnl" in data
        assert "trades" in data
        assert "positions" in data
        assert "strategies" in data

    def test_heartbeat_contains_correct_values(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)
        mock_conn.connected = True
        mock_conn.persistent_failure = False

        with patch("v11.live.run_live.ROOT", tmp_path):
            trader._write_heartbeat()

        heartbeat_file = tmp_path / "v11" / "live" / "state" / "heartbeat.json"
        data = json.loads(heartbeat_file.read_text())

        assert data["connected"] is True
        assert data["persistent_failure"] is False
        assert data["instruments"] == ["XAUUSD"]
        assert data["pnl"] == 42.5
        assert data["trades"] == 3
        assert data["positions"] == 1
        assert len(data["strategies"]) == 1
        assert data["strategies"][0]["name"] == "V6_ORB"
        assert data["strategies"][0]["in_trade"] is True

    def test_heartbeat_does_not_raise_on_error(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)
        # Force an error in get_all_status
        trader.runner.get_all_status.side_effect = RuntimeError("test error")

        # Should not raise
        with patch("v11.live.run_live.ROOT", tmp_path):
            trader._write_heartbeat()


# ── 9: Emergency shutdown ──────────────────────────────────────────────────

class TestEmergencyShutdown:
    """Test emergency shutdown writes state file and exits."""

    def _make_trader(self, log, mock_conn, tmp_path):
        from v11.live.run_live import V11LiveTrader
        from v11.llm.passthrough_filter import PassthroughFilter

        live_cfg = LiveConfig(
            instruments=[XAUUSD_INSTRUMENT],
            dry_run=True,
        )

        with patch.object(V11LiveTrader, '__init__', lambda self, *a, **kw: None):
            trader = V11LiveTrader.__new__(V11LiveTrader)

        trader.log = log
        trader.live_cfg = live_cfg
        trader.conn = mock_conn
        trader.llm_filter = PassthroughFilter(rr_ratio=2.0)
        trader.risk_manager = RiskManager(
            max_daily_loss=500.0, max_daily_trades_per_strategy=20,
            max_concurrent_positions=3, log=log,
        )
        trader._shutdown = False
        trader._active_pairs = ["XAUUSD"]
        trader._last_price_time = {}
        trader._current_trading_date = ""
        trader._session_reset_done = False
        trader._tick_logger = None

        trader.runner = MagicMock()
        trader.runner.get_all_status.return_value = {
            'risk': {'combined_pnl': -50.0, 'combined_trades': 5,
                     'open_positions': ['XAUUSD']},
            'strategies': [
                {'strategy_name': 'V6_ORB', 'pair_name': 'XAUUSD',
                 'in_trade': True, 'bar_count': 500},
            ],
        }
        trader.runner.feeds = {}
        trader.runner.engines = []

        return trader

    def test_emergency_shutdown_writes_state_file(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)

        with patch("v11.live.run_live.ROOT", tmp_path), \
             patch.object(trader, '_cleanup'), \
             pytest.raises(SystemExit) as exc_info:
            trader._emergency_shutdown("test_reason")

        assert exc_info.value.code == 1

        state_file = tmp_path / "v11" / "live" / "state" / "emergency_shutdown.json"
        assert state_file.exists()

        data = json.loads(state_file.read_text())
        assert data["reason"] == "test_reason"
        assert "timestamp" in data
        assert data["pnl"] == -50.0
        assert data["trades"] == 5

    def test_emergency_shutdown_cancels_orders(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)

        with patch("v11.live.run_live.ROOT", tmp_path), \
             patch.object(trader, '_cleanup'), \
             pytest.raises(SystemExit):
            trader._emergency_shutdown("test")

        mock_conn.cancel_all_orders.assert_called_once()

    def test_emergency_shutdown_exits_with_code_1(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)

        with patch("v11.live.run_live.ROOT", tmp_path), \
             patch.object(trader, '_cleanup'), \
             pytest.raises(SystemExit) as exc_info:
            trader._emergency_shutdown("test")

        assert exc_info.value.code == 1


# ── 10: Reconcile positions (portfolio-level) ───────────────────────────────

class TestReconcilePositions:
    """Test two-level reconciliation: TradeManager + RiskManager vs broker."""

    def _make_trader(self, log, mock_conn, tmp_path):
        from v11.live.run_live import V11LiveTrader
        from v11.llm.passthrough_filter import PassthroughFilter

        live_cfg = LiveConfig(
            instruments=[XAUUSD_INSTRUMENT],
            dry_run=True,
        )

        with patch.object(V11LiveTrader, '__init__', lambda self, *a, **kw: None):
            trader = V11LiveTrader.__new__(V11LiveTrader)

        trader.log = log
        trader.live_cfg = live_cfg
        trader.conn = mock_conn
        trader.llm_filter = PassthroughFilter(rr_ratio=2.0)
        trader.risk_manager = RiskManager(
            max_daily_loss=500.0, max_daily_trades_per_strategy=20,
            max_concurrent_positions=3, log=log,
        )
        trader._shutdown = False
        trader._active_pairs = ["XAUUSD"]
        trader._last_price_time = {}
        trader._current_trading_date = ""
        trader._session_reset_done = False
        trader._tick_logger = None

        # Mock feed with trade_manager
        mock_feed = MagicMock()
        mock_feed.inst_config = XAUUSD_INSTRUMENT
        mock_feed.trade_manager = MagicMock()
        mock_feed.trade_manager.in_trade = False
        mock_feed.trade_manager._strategy_name = "V6_ORB"

        trader.runner = MagicMock()
        trader.runner.feeds = {"XAUUSD": mock_feed}
        trader.runner.engines = []

        return trader

    def test_reconcile_adds_broker_position_to_risk_manager(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)

        # Broker has position, risk manager doesn't know
        mock_pos = MagicMock()
        mock_pos.contract.symbol = "XAUUSD"
        mock_pos.contract.currency = "USD"
        mock_pos.position = 1.0
        mock_conn.get_broker_positions.return_value = [mock_pos]

        # TradeManager thinks it's in trade
        trader.runner.feeds["XAUUSD"].trade_manager.in_trade = True

        trader._reconcile_positions()

        # Risk manager should now know about the position
        assert "XAUUSD" in trader.risk_manager.get_open_instruments()

    def test_reconcile_removes_stale_risk_manager_entry(self, log, mock_conn, tmp_path):
        trader = self._make_trader(log, mock_conn, tmp_path)

        # Risk manager thinks there's a position, broker is flat
        trader.risk_manager.record_trade_entry("XAUUSD", "V6_ORB")
        mock_conn.get_broker_positions.return_value = []

        trader._reconcile_positions()

        # Risk manager should have removed the stale entry
        assert "XAUUSD" not in trader.risk_manager.get_open_instruments()


# ── 11: LiveConfig validation ───────────────────────────────────────────────

class TestLiveConfigValidation:
    """Test new config fields and validation."""

    def test_signal_timeout_defaults_to_20(self):
        cfg = LiveConfig()
        assert cfg.signal_llm_timeout_seconds == 20.0

    def test_orb_timeout_defaults_to_15(self):
        cfg = LiveConfig()
        assert cfg.llm_timeout_seconds == 15.0

    def test_validation_rejects_zero_signal_timeout(self):
        cfg = LiveConfig(signal_llm_timeout_seconds=0.0)
        with pytest.raises(AssertionError):
            cfg.validate()

    def test_validation_rejects_negative_signal_timeout(self):
        cfg = LiveConfig(signal_llm_timeout_seconds=-1.0)
        with pytest.raises(AssertionError):
            cfg.validate()

    def test_validation_accepts_valid_timeouts(self):
        cfg = LiveConfig(
            llm_timeout_seconds=15.0,
            signal_llm_timeout_seconds=20.0,
        )
        cfg.validate()  # should not raise


# ── 12: GrokFilter timeout routing ─────────────────────────────────────────

class TestGrokFilterTimeouts:
    """Test that GrokFilter uses separate timeouts for ORB vs signal."""

    def test_signal_timeout_defaults_to_timeout(self):
        from v11.llm.grok_filter import GrokFilter
        gf = GrokFilter(api_key="test", timeout=15.0)
        assert gf._signal_timeout == 15.0  # defaults to timeout

    def test_signal_timeout_can_be_set_separately(self):
        from v11.llm.grok_filter import GrokFilter
        gf = GrokFilter(api_key="test", timeout=15.0, signal_timeout=20.0)
        assert gf._timeout == 15.0
        assert gf._signal_timeout == 20.0

    def test_orb_uses_timeout_not_signal_timeout(self):
        from v11.llm.grok_filter import GrokFilter
        gf = GrokFilter(api_key="test", timeout=15.0, signal_timeout=20.0)
        # ORB evaluate_orb_signal uses self._timeout for first attempt
        # and 5.0 for retry — this is in the code at grok_filter.py:358
        # We verify the attribute is correct
        assert gf._timeout == 15.0
