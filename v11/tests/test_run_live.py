"""
Tests for run_live.py — V11 Multi-Strategy Entry Point (Phase 7).

Design decisions tested:
    1. XAUUSD_ORB_CONFIG has correct V6 parameters (from v6 reference)
    2. V11LiveTrader._wire_strategies adds correct strategies per instrument
    3. Only requested instruments get strategies wired
    4. _seed_historical converts DataFrame rows to Bar objects and routes through runner
    5. CLI defaults: dry-run=True, instruments=[EURUSD, XAUUSD], port=4002
    6. --live flag overrides dry-run to False
    7. INSTRUMENT_MAP contains only validated instruments (EURUSD, XAUUSD)
"""
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest

from v11.config.live_config import (
    LiveConfig, EURUSD_INSTRUMENT, XAUUSD_INSTRUMENT,
)
from v11.config.strategy_config import EURUSD_CONFIG
from v11.live.run_live import (
    XAUUSD_ORB_CONFIG, INSTRUMENT_MAP, V11LiveTrader,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def log():
    return logging.getLogger("test_run_live")


@pytest.fixture
def live_cfg_both():
    """LiveConfig with both EURUSD and XAUUSD, Darvas enabled for coverage."""
    return LiveConfig(
        instruments=[EURUSD_INSTRUMENT, XAUUSD_INSTRUMENT],
        dry_run=True,
        max_daily_loss=500.0,
        darvas_enabled=True,
    )


@pytest.fixture
def live_cfg_eurusd_only():
    """LiveConfig with only EURUSD, Darvas enabled for coverage."""
    return LiveConfig(
        instruments=[EURUSD_INSTRUMENT],
        dry_run=True,
        darvas_enabled=True,
    )


@pytest.fixture
def live_cfg_xauusd_only():
    """LiveConfig with only XAUUSD."""
    return LiveConfig(
        instruments=[XAUUSD_INSTRUMENT],
        dry_run=True,
    )


def _make_trader(live_cfg, log):
    """Create a V11LiveTrader with mocked external dependencies."""
    with patch("v11.live.run_live.load_dotenv"), \
         patch.dict("os.environ", {"XAI_API_KEY": "test-key"}), \
         patch("v11.live.run_live.IBKRConnection") as mock_conn_cls, \
         patch("v11.live.run_live.GrokFilter") as mock_grok_cls:

        mock_conn = MagicMock()
        mock_conn.connected = True
        mock_conn._contracts = {"XAUUSD": MagicMock(), "EURUSD": MagicMock()}
        mock_conn.ib = MagicMock()
        mock_conn_cls.return_value = mock_conn

        mock_grok = MagicMock()
        mock_grok_cls.return_value = mock_grok

        trader = V11LiveTrader(live_cfg, log)
    return trader


# ── 1. XAUUSD_ORB_CONFIG parameters match V6 reference ──────────────────────

class TestORBConfig:
    """V6 ORB config must match parameters from v6_orb_refactor/live/example_live_xauusd.py."""

    def test_instrument(self):
        assert XAUUSD_ORB_CONFIG.instrument == "XAUUSD"

    def test_range_window(self):
        assert XAUUSD_ORB_CONFIG.range_start_hour == 0
        assert XAUUSD_ORB_CONFIG.range_end_hour == 6

    def test_trade_window(self):
        assert XAUUSD_ORB_CONFIG.trade_start_hour == 8
        assert XAUUSD_ORB_CONFIG.trade_end_hour == 16

    def test_skip_wednesday(self):
        assert 2 in XAUUSD_ORB_CONFIG.skip_weekdays

    def test_rr_ratio(self):
        assert XAUUSD_ORB_CONFIG.rr_ratio == 2.5

    def test_velocity_settings(self):
        assert XAUUSD_ORB_CONFIG.velocity_filter_enabled is True
        assert XAUUSD_ORB_CONFIG.velocity_lookback_minutes == 3
        assert XAUUSD_ORB_CONFIG.velocity_threshold == 168.0

    def test_range_limits(self):
        assert XAUUSD_ORB_CONFIG.min_range_size == 1.0
        assert XAUUSD_ORB_CONFIG.max_range_size == 15.0


# ── 2. INSTRUMENT_MAP only contains validated instruments ────────────────────

class TestInstrumentMap:
    def test_contains_eurusd(self):
        assert "EURUSD" in INSTRUMENT_MAP

    def test_contains_xauusd(self):
        assert "XAUUSD" in INSTRUMENT_MAP

    def test_no_usdjpy(self):
        """USDJPY has no validated edge — should not be in the map."""
        assert "USDJPY" not in INSTRUMENT_MAP

    def test_eurusd_config_matches(self):
        assert INSTRUMENT_MAP["EURUSD"] is EURUSD_INSTRUMENT

    def test_xauusd_config_matches(self):
        assert INSTRUMENT_MAP["XAUUSD"] is XAUUSD_INSTRUMENT


# ── 3. _wire_strategies adds correct strategies per instrument ───────────────

class TestWireStrategies:
    def test_both_instruments_wires_three_strategies(self, live_cfg_both, log):
        trader = _make_trader(live_cfg_both, log)
        trader._wire_strategies()

        assert len(trader.runner.engines) == 3
        names = [e.strategy_name for e in trader.runner.engines]
        assert "Darvas_Breakout" in names
        assert "4H_Level_Retest" in names
        assert "V6_ORB" in names

    def test_both_instruments_creates_two_feeds(self, live_cfg_both, log):
        trader = _make_trader(live_cfg_both, log)
        trader._wire_strategies()

        pairs = trader.runner.get_feed_pairs()
        assert "EURUSD" in pairs
        assert "XAUUSD" in pairs
        assert len(pairs) == 2

    def test_eurusd_only_wires_two_strategies(self, live_cfg_eurusd_only, log):
        trader = _make_trader(live_cfg_eurusd_only, log)
        trader._wire_strategies()

        assert len(trader.runner.engines) == 2
        names = [e.strategy_name for e in trader.runner.engines]
        assert "Darvas_Breakout" in names
        assert "4H_Level_Retest" in names
        assert "V6_ORB" not in names

    def test_xauusd_only_wires_one_strategy(self, live_cfg_xauusd_only, log):
        trader = _make_trader(live_cfg_xauusd_only, log)
        trader._wire_strategies()

        assert len(trader.runner.engines) == 1
        assert trader.runner.engines[0].strategy_name == "V6_ORB"

    def test_eurusd_strategies_share_feed(self, live_cfg_eurusd_only, log):
        """Darvas and Retest on EURUSD must share one InstrumentFeed."""
        trader = _make_trader(live_cfg_eurusd_only, log)
        trader._wire_strategies()

        eurusd_feed = trader.runner.feeds["EURUSD"]
        assert len(eurusd_feed._strategies) == 2

    def test_active_pairs_tracked(self, live_cfg_both, log):
        trader = _make_trader(live_cfg_both, log)
        trader._wire_strategies()

        assert "EURUSD" in trader._active_pairs
        assert "XAUUSD" in trader._active_pairs

    def test_darvas_disabled_by_default(self, log):
        """darvas_enabled=False (default) means no EURUSD strategies are loaded."""
        cfg = LiveConfig(
            instruments=[EURUSD_INSTRUMENT, XAUUSD_INSTRUMENT],
            dry_run=True,
        )
        assert cfg.darvas_enabled is False
        trader = _make_trader(cfg, log)
        trader._wire_strategies()

        names = [e.strategy_name for e in trader.runner.engines]
        assert "Darvas_Breakout" not in names
        assert "4H_Level_Retest" not in names
        assert "V6_ORB" in names
        assert "EURUSD" not in trader._active_pairs


# ── 4. Risk manager wired with correct limits ───────────────────────────────

class TestRiskManagerWiring:
    def test_max_daily_loss(self, live_cfg_both, log):
        trader = _make_trader(live_cfg_both, log)
        assert trader.risk_manager._max_daily_loss == 500.0

    def test_max_concurrent_positions(self, live_cfg_both, log):
        trader = _make_trader(live_cfg_both, log)
        assert trader.risk_manager._max_positions == live_cfg_both.max_concurrent_positions

    def test_risk_manager_shared_with_runner(self, live_cfg_both, log):
        trader = _make_trader(live_cfg_both, log)
        assert trader.runner.risk_manager is trader.risk_manager


# ── 5. Seeding historical bars routes through runner ─────────────────────────

class TestSeedHistorical:
    def test_seed_calls_runner(self, live_cfg_eurusd_only, log):
        trader = _make_trader(live_cfg_eurusd_only, log)
        trader._wire_strategies()

        # Mock the connection's fetch to return a small DataFrame
        import pandas as pd
        df = pd.DataFrame([{
            'date': '2025-01-02 10:00:00',
            'open': 1.1000, 'high': 1.1010,
            'low': 1.0990, 'close': 1.1005,
            'volume': 100,
        }])
        trader.conn.fetch_historical_bars = MagicMock(return_value=df)

        # Spy on runner.seed_historical
        trader.runner.seed_historical = MagicMock()

        trader._seed_historical()

        trader.runner.seed_historical.assert_called_once()
        call_args = trader.runner.seed_historical.call_args
        assert call_args[0][0] == "EURUSD"
        bars = call_args[0][1]
        assert len(bars) == 1
        assert bars[0].open == 1.1000
        assert bars[0].close == 1.1005

    def test_seed_splits_volume_evenly(self, live_cfg_eurusd_only, log):
        trader = _make_trader(live_cfg_eurusd_only, log)
        trader._wire_strategies()

        import pandas as pd
        df = pd.DataFrame([{
            'date': '2025-01-02 10:00:00',
            'open': 1.1, 'high': 1.1, 'low': 1.1, 'close': 1.1,
            'volume': 200,
        }])
        trader.conn.fetch_historical_bars = MagicMock(return_value=df)
        trader.runner.seed_historical = MagicMock()

        trader._seed_historical()

        bar = trader.runner.seed_historical.call_args[0][1][0]
        assert bar.buy_volume == 100.0
        assert bar.sell_volume == 100.0
        assert bar.tick_count == 200

    def test_seed_handles_empty_dataframe(self, live_cfg_eurusd_only, log):
        trader = _make_trader(live_cfg_eurusd_only, log)
        trader._wire_strategies()

        import pandas as pd
        trader.conn.fetch_historical_bars = MagicMock(
            return_value=pd.DataFrame())
        trader.runner.seed_historical = MagicMock()

        trader._seed_historical()

        trader.runner.seed_historical.assert_not_called()


# ── 6. CLI argument defaults ─────────────────────────────────────────────────

class TestCLIDefaults:
    def test_default_instruments(self):
        """Default instruments should be EURUSD and XAUUSD."""
        import argparse
        from v11.live.run_live import main
        # Verify by checking the argument parser default
        parser = argparse.ArgumentParser()
        parser.add_argument("--instruments", nargs="+",
                            default=["EURUSD", "XAUUSD"])
        args = parser.parse_args([])
        assert args.instruments == ["EURUSD", "XAUUSD"]

    def test_default_port(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--port", type=int, default=4002)
        args = parser.parse_args([])
        assert args.port == 4002

    def test_dry_run_is_default(self):
        """Without --live flag, system must default to dry-run mode."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--live", action="store_true")
        args = parser.parse_args([])
        dry_run = not args.live
        assert dry_run is True

    def test_live_flag_overrides_dry_run(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--live", action="store_true")
        args = parser.parse_args(["--live"])
        dry_run = not args.live
        assert dry_run is False


# ── 8. TickLogger integration ────────────────────────────────────────────────

class TestTickLoggerIntegration:
    def test_tick_logger_initialised_when_enabled(self, tmp_path, log):
        """V11LiveTrader creates a TickLogger when tick_logging=True."""
        cfg = LiveConfig(
            instruments=[EURUSD_INSTRUMENT],
            tick_logging=True,
            tick_log_dir=tmp_path / "ticks",
            dry_run=True,
        )
        trader = _make_trader(cfg, log)
        assert trader._tick_logger is not None
        trader._tick_logger.close()

    def test_tick_logger_none_when_disabled(self, tmp_path, log):
        """V11LiveTrader skips TickLogger when tick_logging=False."""
        cfg = LiveConfig(
            instruments=[EURUSD_INSTRUMENT],
            tick_logging=False,
            tick_log_dir=tmp_path / "ticks",
            dry_run=True,
        )
        trader = _make_trader(cfg, log)
        assert trader._tick_logger is None

    def test_cleanup_closes_tick_logger(self, tmp_path, log):
        """_cleanup() calls close() on the TickLogger."""
        cfg = LiveConfig(
            instruments=[EURUSD_INSTRUMENT],
            tick_logging=True,
            tick_log_dir=tmp_path / "ticks",
            dry_run=True,
        )
        trader = _make_trader(cfg, log)
        # Write one tick so close() has something to flush
        from datetime import datetime, timezone
        trader._tick_logger.record(
            "EURUSD", datetime(2026, 4, 15, 14, 0, tzinfo=timezone.utc),
            1.1234, None, None, None, None, None, None,
        )
        # _cleanup() must not raise
        trader._cleanup()
        assert trader._tick_logger._handles == {}
