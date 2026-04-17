"""
Tests for ORBAdapter — V6 ORB integration into V11 MultiStrategyRunner.

Design decisions tested:
    1. Satisfies StrategyEngine protocol (pair_name, strategy_name, etc.)
    2. on_price throttles to poll_interval (2s default)
    3. Daily reset triggers on date change
    4. Risk gate blocks strategy only in RANGE_READY state
    5. Fill callback reports entries/exits to V11 RiskManager
    6. Trade window close marks strategy DONE_TODAY
    7. Range calculation triggered at range_end_hour
    8. add_orb_strategy() factory method on MultiStrategyRunner
    9. Cleanup cancels orders and disconnects context
   10. on_bar is a no-op (V6 is tick-driven)
"""
import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest

from v11.v6_orb.orb_strategy import ORBStrategy, StrategyState
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import Tick, Fill, RangeInfo
from v11.live.orb_adapter import ORBAdapter
from v11.live.risk_manager import RiskManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_v6_config(**overrides) -> V6StrategyConfig:
    """Create a V6 StrategyConfig with sensible test defaults."""
    defaults = dict(
        instrument="XAUUSD",
        range_start_hour=0,
        range_end_hour=6,
        trade_start_hour=8,
        trade_end_hour=16,
        velocity_filter_enabled=False,  # disable for simpler tests
        rr_ratio=2.5,
        min_range_pct=0.05,
        max_range_pct=2.0,
        gap_filter_enabled=False,
        qty=1,
        point_value=1.0,
        price_decimals=2,
    )
    defaults.update(overrides)
    return V6StrategyConfig(**defaults)


def _mock_ib():
    """Create a mock ib_insync.IB instance."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.reqMktData.return_value = MagicMock()
    ib.pendingTickersEvent = MagicMock()
    ib.sleep = MagicMock()
    ib.reqHistoricalDataAsync = AsyncMock(return_value=[])
    return ib


def _mock_contract():
    """Create a mock IBKR contract."""
    contract = MagicMock()
    contract.symbol = "XAUUSD"
    contract.conId = 12345
    return contract


@pytest.fixture
def log():
    return logging.getLogger("test_orb_adapter")


@pytest.fixture
def risk_manager(log):
    return RiskManager(
        max_daily_loss=500.0,
        max_daily_trades_per_strategy=5,
        max_concurrent_positions=3,
        log=log,
    )


@pytest.fixture
def adapter(risk_manager, log):
    """Create an ORBAdapter with mocked IBKR components."""
    ib = _mock_ib()
    contract = _mock_contract()
    v6_config = _make_v6_config()

    a = ORBAdapter(
        ib=ib,
        contract=contract,
        v6_config=v6_config,
        risk_manager=risk_manager,
        log=log,
        dry_run=True,
        poll_interval=2.0,
    )
    return a


def _ts(hour: int, minute: int = 0, day: int = 15) -> datetime:
    """Create a UTC timestamp on 2025-01-{day} at given hour:minute."""
    return datetime(2025, 1, day, hour, minute, 0, tzinfo=timezone.utc)


# ── 1. StrategyEngine protocol ───────────────────────────────────────────────

class TestProtocol:
    def test_pair_name(self, adapter):
        assert adapter.pair_name == "XAUUSD"

    def test_strategy_name(self, adapter):
        assert adapter.strategy_name == "V6_ORB"

    def test_bar_count_always_zero(self, adapter):
        assert adapter.bar_count == 0

    def test_in_trade_false_initially(self, adapter):
        assert adapter.in_trade is False

    def test_get_status_has_required_keys(self, adapter):
        status = adapter.get_status()
        assert "instrument" in status
        assert "strategy_name" in status
        assert "state" in status
        assert "in_trade" in status
        assert status["state"] == "IDLE"


# ── 2. on_price throttle ────────────────────────────────────────────────────

class TestThrottle:
    def test_first_call_always_processes(self, adapter):
        """First on_price call should always run (no prior poll time)."""
        now = _ts(10)
        adapter.on_price(2650.0, now)
        assert adapter._last_poll_time == now

    def test_second_call_within_interval_skipped(self, adapter):
        """Calls within poll_interval should be skipped."""
        t1 = _ts(10, 0)
        t2 = t1 + timedelta(seconds=1)  # 1s < 2s poll interval
        adapter.on_price(2650.0, t1)
        first_poll = adapter._last_poll_time
        adapter.on_price(2651.0, t2)
        # Poll time should NOT have advanced
        assert adapter._last_poll_time == first_poll

    def test_call_after_interval_processes(self, adapter):
        """Calls after poll_interval should process."""
        t1 = _ts(10, 0)
        t2 = t1 + timedelta(seconds=3)  # 3s > 2s poll interval
        adapter.on_price(2650.0, t1)
        adapter.on_price(2651.0, t2)
        assert adapter._last_poll_time == t2


# ── 3. Daily reset ──────────────────────────────────────────────────────────

class TestDailyReset:
    def test_date_change_triggers_reset(self, adapter):
        """Changing the date should reset strategy state."""
        # Set up some state on day 15
        adapter._current_date = "2025-01-15"
        adapter._range_calculated = True
        adapter._strategy.state = StrategyState.DONE_TODAY

        # Price tick on day 16
        adapter.on_price(2650.0, _ts(10, 0, day=16))

        assert adapter._current_date == "2025-01-16"
        assert adapter._range_calculated is False
        assert adapter._strategy.state != StrategyState.DONE_TODAY

    def test_same_date_no_reset(self, adapter):
        """Same date should NOT trigger reset."""
        adapter._current_date = "2025-01-15"
        adapter._range_calculated = True

        adapter.on_price(2650.0, _ts(10, 0, day=15))

        assert adapter._range_calculated is True  # unchanged

    def test_reset_cancels_lingering_orders(self, adapter):
        """Daily reset should cancel orders if strategy was in ORDERS_PLACED."""
        adapter._current_date = "2025-01-15"
        adapter._strategy.state = StrategyState.ORDERS_PLACED
        mock_exec = MagicMock()
        mock_exec.has_position.return_value = False
        adapter._execution = mock_exec

        adapter.on_price(2650.0, _ts(10, 0, day=16))

        mock_exec.cancel_orb_brackets.assert_called_once()


# ── 4. Risk gate ─────────────────────────────────────────────────────────────

class TestRiskGate:
    def test_blocks_in_range_ready(self, adapter, risk_manager):
        """Risk manager blocking should prevent strategy from seeing ticks
        when in RANGE_READY state (pre-bracket placement)."""
        adapter._strategy.state = StrategyState.RANGE_READY
        adapter._current_date = "2025-01-15"
        adapter._last_poll_time = None

        # Block: instrument already has position
        risk_manager.record_trade_entry("XAUUSD", "Other_Strategy")

        # Set up streaming data so tick would be available
        adapter._context._last_bid = 2650.0
        adapter._context._last_ask = 2650.50

        now = _ts(10, 0)
        adapter.on_price(2650.0, now)

        # Strategy should still be in RANGE_READY (not progressed)
        assert adapter._strategy.state == StrategyState.RANGE_READY

    def test_allows_idle_state(self, adapter, risk_manager):
        """Risk gate should NOT block IDLE state (range setup needs ticks)."""
        adapter._strategy.state = StrategyState.IDLE
        adapter._current_date = "2025-01-15"

        # Block: max positions reached
        risk_manager.record_trade_entry("EURUSD", "Darvas")
        risk_manager.record_trade_entry("GBPUSD", "Darvas")
        risk_manager.record_trade_entry("USDJPY", "Darvas")

        adapter._context._last_bid = 2650.0
        adapter._context._last_ask = 2650.50

        now = _ts(10, 0)
        adapter.on_price(2650.0, now)

        # Strategy should have been called (IDLE is not blocked)
        assert adapter._last_poll_time == now

    def test_allows_in_trade_state(self, adapter, risk_manager):
        """Risk gate should NOT block IN_TRADE state (position management)."""
        adapter._strategy.state = StrategyState.IN_TRADE
        adapter._current_date = "2025-01-15"

        adapter._context._last_bid = 2650.0
        adapter._context._last_ask = 2650.50

        now = _ts(10, 0)
        adapter.on_price(2650.0, now)

        assert adapter._last_poll_time == now


# ── 5. Fill callback to RiskManager ──────────────────────────────────────────

class TestFillCallback:
    def test_entry_fill_records_in_risk_manager(self, adapter, risk_manager):
        """ENTRY fill should call risk_manager.record_trade_entry."""
        # Set up strategy state for fill processing
        adapter._strategy.state = StrategyState.ORDERS_PLACED
        adapter._strategy.range = RangeInfo(
            high=2660.0, low=2650.0, start_time=None, end_time=None)

        fill = Fill(
            timestamp=_ts(10, 30),
            price=2660.0,
            direction="LONG",
            reason="ENTRY",
        )
        adapter._on_fill(fill)

        assert risk_manager.is_instrument_in_trade("XAUUSD")
        assert adapter._strategy.state == StrategyState.IN_TRADE

    def test_exit_fill_records_pnl_in_risk_manager(self, adapter, risk_manager):
        """Exit fill should call risk_manager.record_trade_exit with PnL."""
        # Set up as if we're in a LONG trade
        adapter._strategy.state = StrategyState.IN_TRADE
        adapter._strategy.direction = "LONG"
        adapter._strategy.entry_price = 2660.0
        adapter._strategy.range = RangeInfo(
            high=2660.0, low=2650.0, start_time=None, end_time=None)
        risk_manager.record_trade_entry("XAUUSD", "V6_ORB")

        fill = Fill(
            timestamp=_ts(11, 0),
            price=2665.0,
            direction="SHORT",
            reason="TP",
        )
        adapter._on_fill(fill)

        # PnL = (2665 - 2660) * 1 * 1.0 = $5.00
        assert risk_manager.combined_pnl == 5.0
        assert not risk_manager.is_instrument_in_trade("XAUUSD")
        assert adapter._strategy.state == StrategyState.DONE_TODAY

    def test_sl_fill_records_negative_pnl(self, adapter, risk_manager):
        """SL fill should record negative PnL."""
        adapter._strategy.state = StrategyState.IN_TRADE
        adapter._strategy.direction = "LONG"
        adapter._strategy.entry_price = 2660.0
        adapter._strategy.range = RangeInfo(
            high=2660.0, low=2650.0, start_time=None, end_time=None)
        risk_manager.record_trade_entry("XAUUSD", "V6_ORB")

        fill = Fill(
            timestamp=_ts(11, 0),
            price=2650.0,
            direction="SHORT",
            reason="SL",
        )
        adapter._on_fill(fill)

        # PnL = (2650 - 2660) * 1 * 1.0 = -$10.00
        assert risk_manager.combined_pnl == -10.0


# ── 6. Trade window close ───────────────────────────────────────────────────

class TestWindowClose:
    def test_idle_after_window_becomes_done(self, adapter):
        """Strategy in IDLE after trade_end_hour should become DONE_TODAY."""
        adapter._current_date = "2025-01-15"
        adapter._strategy.state = StrategyState.IDLE

        # Price at 17:00 (after trade_end_hour=16)
        adapter.on_price(2650.0, _ts(17, 0))

        assert adapter._strategy.state == StrategyState.DONE_TODAY

    def test_range_ready_after_window_becomes_done(self, adapter):
        """Strategy in RANGE_READY after trade_end_hour should become DONE_TODAY."""
        adapter._current_date = "2025-01-15"
        adapter._strategy.state = StrategyState.RANGE_READY

        adapter.on_price(2650.0, _ts(17, 0))

        assert adapter._strategy.state == StrategyState.DONE_TODAY


# ── 7. Range calculation ────────────────────────────────────────────────────

class TestRangeCalculation:
    def test_range_calculated_after_range_end_hour(self, adapter):
        """Range should be calculated when hour >= range_end_hour and strategy IDLE."""
        adapter._current_date = "2025-01-15"
        adapter._strategy.state = StrategyState.IDLE

        # Mock the context to return a range
        test_range = RangeInfo(
            high=2660.0, low=2650.0,
            start_time=_ts(0), end_time=_ts(6))
        adapter._context.calculate_daily_range = MagicMock(return_value=test_range)
        adapter._context.set_daily_range = MagicMock()

        # Tick at 07:00 (after range_end_hour=6, before trade_start_hour=8)
        adapter.on_price(2655.0, _ts(7, 0))

        adapter._context.calculate_daily_range.assert_called_once_with(0, 6)
        adapter._context.set_daily_range.assert_called_once()
        assert adapter._range_calculated is True

    def test_range_not_recalculated(self, adapter):
        """Range should only be calculated once per day."""
        adapter._current_date = "2025-01-15"
        adapter._range_calculated = True
        adapter._strategy.state = StrategyState.IDLE

        adapter._context.calculate_daily_range = MagicMock()

        adapter.on_price(2655.0, _ts(7, 0))

        adapter._context.calculate_daily_range.assert_not_called()


# ── 8. skip_weekdays enforcement ─────────────────────────────────────────────

class TestSkipWeekdays:
    """2025-01-15 is a Wednesday (weekday=2). 2025-01-14 is Tuesday. 2025-01-16 is Thursday."""

    def _make_adapter_with_skip(self, risk_manager, log, skip_weekdays=(2,)):
        """Create adapter with skip_weekdays configured."""
        ib = _mock_ib()
        contract = _mock_contract()
        v6_config = _make_v6_config(skip_weekdays=skip_weekdays)
        return ORBAdapter(
            ib=ib, contract=contract, v6_config=v6_config,
            risk_manager=risk_manager, log=log,
            dry_run=True, poll_interval=0.0,
        )

    def test_on_price_returns_early_on_skip_day(self, risk_manager, log):
        """on_price must return immediately on configured skip day."""
        adapter = self._make_adapter_with_skip(risk_manager, log)
        adapter._current_date = "2025-01-15"  # Wednesday

        # Wednesday 2025-01-15 at 10:00 UTC
        wednesday = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        adapter.on_price(2650.0, wednesday)

        # poll time should not advance — on_price returned before the throttle check
        assert adapter._last_poll_time is None

    def test_on_price_processes_normally_on_tuesday(self, risk_manager, log):
        """on_price must process normally on non-skip days."""
        adapter = self._make_adapter_with_skip(risk_manager, log)
        adapter._current_date = "2025-01-14"  # Tuesday

        tuesday = datetime(2025, 1, 14, 10, 0, 0, tzinfo=timezone.utc)
        adapter.on_price(2650.0, tuesday)

        assert adapter._last_poll_time == tuesday

    def test_on_price_processes_normally_on_thursday(self, risk_manager, log):
        """on_price must process normally on non-skip days."""
        adapter = self._make_adapter_with_skip(risk_manager, log)
        adapter._current_date = "2025-01-16"  # Thursday

        thursday = datetime(2025, 1, 16, 10, 0, 0, tzinfo=timezone.utc)
        adapter.on_price(2650.0, thursday)

        assert adapter._last_poll_time == thursday

    def test_empty_skip_weekdays_trades_all_days(self, risk_manager, log):
        """Empty skip_weekdays means no days are skipped."""
        adapter = self._make_adapter_with_skip(risk_manager, log, skip_weekdays=())
        adapter._current_date = "2025-01-15"  # Wednesday

        wednesday = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        adapter.on_price(2650.0, wednesday)

        assert adapter._last_poll_time == wednesday

    def test_daily_reset_still_fires_on_skip_day(self, risk_manager, log):
        """Daily reset must fire even on skip days (cancels lingering orders)."""
        adapter = self._make_adapter_with_skip(risk_manager, log)
        adapter._current_date = "2025-01-14"  # Tuesday — so Wednesday triggers reset

        wednesday = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        adapter.on_price(2650.0, wednesday)

        # Reset fired: date changed
        assert adapter._current_date == "2025-01-15"
        # But poll time not set — skipped after reset
        assert adapter._last_poll_time is None


# ── 9. MultiStrategyRunner integration ───────────────────────────────────────

class TestRunnerIntegration:
    def test_add_orb_strategy_creates_adapter(self, risk_manager, log, tmp_path):
        """add_orb_strategy should create ORBAdapter and register it."""
        from v11.live.multi_strategy_runner import MultiStrategyRunner
        from v11.config.live_config import XAUUSD_INSTRUMENT, LiveConfig

        mock_conn = MagicMock()
        mock_conn.connected = True
        mock_conn.ib = _mock_ib()
        mock_conn._contracts = {"XAUUSD": _mock_contract()}

        mock_llm = MagicMock()
        live_cfg = LiveConfig(dry_run=True)

        runner = MultiStrategyRunner(
            conn=mock_conn,
            llm_filter=mock_llm,
            live_config=live_cfg,
            risk_manager=risk_manager,
            log=log,
            trade_log_dir=str(tmp_path / "trades"),
        )

        v6_config = _make_v6_config()
        adapter = runner.add_orb_strategy(v6_config, XAUUSD_INSTRUMENT)

        assert adapter.strategy_name == "V6_ORB"
        assert adapter.pair_name == "XAUUSD"
        assert len(runner.engines) == 1
        assert "XAUUSD" in runner.feeds

    def test_add_orb_raises_without_qualified_contract(self, risk_manager, log, tmp_path):
        """add_orb_strategy should raise ValueError if contract not qualified."""
        from v11.live.multi_strategy_runner import MultiStrategyRunner
        from v11.config.live_config import XAUUSD_INSTRUMENT, LiveConfig

        mock_conn = MagicMock()
        mock_conn.ib = _mock_ib()
        mock_conn._contracts = {}  # no qualified contracts

        runner = MultiStrategyRunner(
            conn=mock_conn,
            llm_filter=MagicMock(),
            live_config=LiveConfig(dry_run=True),
            risk_manager=risk_manager,
            log=log,
            trade_log_dir=str(tmp_path / "trades"),
        )

        with pytest.raises(ValueError, match="Contract not qualified"):
            runner.add_orb_strategy(_make_v6_config(), XAUUSD_INSTRUMENT)

    def test_orb_coexists_with_darvas_on_different_instruments(
        self, risk_manager, log, tmp_path
    ):
        """ORB on XAUUSD and Darvas on EURUSD should get separate feeds."""
        from v11.live.multi_strategy_runner import MultiStrategyRunner
        from v11.config.live_config import (
            XAUUSD_INSTRUMENT, EURUSD_INSTRUMENT, LiveConfig)
        from v11.config.strategy_config import EURUSD_CONFIG

        mock_conn = MagicMock()
        mock_conn.ib = _mock_ib()
        mock_conn._contracts = {"XAUUSD": _mock_contract()}

        runner = MultiStrategyRunner(
            conn=mock_conn,
            llm_filter=MagicMock(),
            live_config=LiveConfig(dry_run=True),
            risk_manager=risk_manager,
            log=log,
            trade_log_dir=str(tmp_path / "trades"),
        )

        runner.add_darvas_strategy(EURUSD_CONFIG, EURUSD_INSTRUMENT)
        runner.add_orb_strategy(_make_v6_config(), XAUUSD_INSTRUMENT)

        assert len(runner.feeds) == 2
        assert len(runner.engines) == 2
        assert sorted(runner.get_feed_pairs()) == ["EURUSD", "XAUUSD"]


# ── 9. Cleanup ──────────────────────────────────────────────────────────────

class TestCleanup:
    def test_cleanup_cancels_resting_brackets(self, adapter):
        """Cleanup should cancel brackets if in ORDERS_PLACED state."""
        adapter._strategy.state = StrategyState.ORDERS_PLACED
        mock_exec = MagicMock()
        mock_exec.has_position.return_value = False
        adapter._execution = mock_exec
        mock_ctx = MagicMock()
        adapter._context = mock_ctx

        adapter.cleanup()

        mock_exec.cancel_orb_brackets.assert_called_once()
        mock_ctx.disconnect.assert_called_once()

    def test_cleanup_closes_position(self, adapter):
        """Cleanup should close position if one exists."""
        adapter._strategy.state = StrategyState.IN_TRADE
        mock_exec = MagicMock()
        mock_exec.has_position.return_value = True
        adapter._execution = mock_exec
        mock_ctx = MagicMock()
        adapter._context = mock_ctx

        adapter.cleanup()

        mock_exec.close_at_market.assert_called_once()
        mock_ctx.disconnect.assert_called_once()


# ── 10. on_bar stores bar in buffer ──────────────────────────────────────────

class TestOnBarBuffer:
    @pytest.mark.asyncio
    async def test_on_bar_stores_bar_in_buffer(self, adapter):
        """on_bar must buffer the bar for velocity computation."""
        bar = MagicMock()
        await adapter.on_bar(bar)
        assert len(adapter._bar_buffer) == 1
        assert adapter._bar_buffer[0] is bar

    @pytest.mark.asyncio
    async def test_on_bar_does_not_change_strategy_state(self, adapter):
        """Storing a bar must not affect strategy state."""
        bar = MagicMock()
        initial_state = adapter._strategy.state
        await adapter.on_bar(bar)
        assert adapter._strategy.state == initial_state
        assert adapter.bar_count == 0

    @pytest.mark.asyncio
    async def test_multiple_bars_accumulated(self, adapter):
        """Multiple on_bar calls accumulate bars in order."""
        bars = [MagicMock() for _ in range(5)]
        for b in bars:
            await adapter.on_bar(b)
        assert len(adapter._bar_buffer) == 5
        assert list(adapter._bar_buffer) == bars


# ── 11. Bar-level velocity ────────────────────────────────────────────────────

class TestBarVelocity:
    def _make_bar(self, ts: datetime, tick_count: int):
        """Create a real Bar object (not a mock) for velocity tests."""
        from v11.core.types import Bar
        return Bar(
            timestamp=ts,
            open=1.0, high=1.0, low=1.0, close=1.0,
            tick_count=tick_count,
            buy_volume=0.0, sell_volume=0.0,
        )

    def test_no_bars_returns_zero(self, adapter):
        """Velocity is 0 when bar buffer is empty."""
        now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        assert adapter._compute_bar_velocity(3, now) == 0.0

    def test_velocity_from_three_bars(self, adapter):
        """3 bars in window → sum(tick_counts) / lookback_minutes."""
        now = datetime(2025, 1, 15, 10, 3, 0, tzinfo=timezone.utc)
        for i, tc in enumerate([200, 150, 180]):
            ts = datetime(2025, 1, 15, 10, i, 0, tzinfo=timezone.utc)
            adapter._bar_buffer.append(self._make_bar(ts, tc))

        # 3-minute lookback: all 3 bars are within window
        velocity = adapter._compute_bar_velocity(3, now)
        assert abs(velocity - (200 + 150 + 180) / 3) < 0.01

    def test_bars_outside_window_excluded(self, adapter):
        """Bars older than lookback_minutes are not counted."""
        now = datetime(2025, 1, 15, 10, 5, 0, tzinfo=timezone.utc)
        # Bar 1 minute ago (in window)
        adapter._bar_buffer.append(
            self._make_bar(datetime(2025, 1, 15, 10, 4, 0, tzinfo=timezone.utc), 300))
        # Bar 6 minutes ago (outside 3-min window)
        adapter._bar_buffer.append(
            self._make_bar(datetime(2025, 1, 15, 9, 59, 0, tzinfo=timezone.utc), 999))

        velocity = adapter._compute_bar_velocity(3, now)
        assert abs(velocity - 300 / 3) < 0.01  # only the recent bar counts

    def test_fewer_bars_than_lookback(self, adapter):
        """Works correctly with fewer bars than lookback window."""
        now = datetime(2025, 1, 15, 10, 3, 0, tzinfo=timezone.utc)
        adapter._bar_buffer.append(
            self._make_bar(datetime(2025, 1, 15, 10, 2, 0, tzinfo=timezone.utc), 150))
        # Only 1 bar but 3-minute lookback → still divides by lookback
        velocity = adapter._compute_bar_velocity(3, now)
        assert abs(velocity - 150 / 3) < 0.01

    def test_context_get_velocity_uses_bar_level(self, adapter):
        """LiveMarketContext.get_velocity() is overridden to use bar tick_counts."""
        now = datetime(2025, 1, 15, 10, 3, 0, tzinfo=timezone.utc)
        for i, tc in enumerate([200, 150, 180]):
            ts = datetime(2025, 1, 15, 10, i, 0, tzinfo=timezone.utc)
            adapter._bar_buffer.append(self._make_bar(ts, tc))

        # The override should produce the same result as _compute_bar_velocity
        ctx_velocity = adapter._context.get_velocity(3, now)
        expected = adapter._compute_bar_velocity(3, now)
        assert abs(ctx_velocity - expected) < 0.01

    def test_exceeds_threshold_when_active(self, adapter):
        """Confirms the 168 threshold is reachable with bar-level data."""
        now = datetime(2025, 1, 15, 10, 3, 0, tzinfo=timezone.utc)
        for i, tc in enumerate([250, 200, 180]):
            ts = datetime(2025, 1, 15, 10, i, 0, tzinfo=timezone.utc)
            adapter._bar_buffer.append(self._make_bar(ts, tc))

        velocity = adapter._compute_bar_velocity(3, now)
        assert velocity > 168  # sum=630, /3 = 210 > threshold

    def test_snapshot_ticks_below_threshold(self, adapter):
        """Documents the bug: snapshot tick_count (~60) never exceeds 168 threshold."""
        now = datetime(2025, 1, 15, 10, 3, 0, tzinfo=timezone.utc)
        for i in range(3):
            ts = datetime(2025, 1, 15, 10, i, 0, tzinfo=timezone.utc)
            adapter._bar_buffer.append(self._make_bar(ts, 60))  # IBKR snapshot rate

        velocity = adapter._compute_bar_velocity(3, now)
        assert velocity < 168  # 60*3/3=60, well below threshold — velocity filter broken

    def test_real_tick_count_above_threshold(self, adapter):
        """Real market tick counts (mean ~144) can exceed the 168 threshold."""
        now = datetime(2025, 1, 15, 10, 3, 0, tzinfo=timezone.utc)
        for i in range(3):
            ts = datetime(2025, 1, 15, 10, i, 0, tzinfo=timezone.utc)
            adapter._bar_buffer.append(self._make_bar(ts, 200))  # real active market

        velocity = adapter._compute_bar_velocity(3, now)
        assert velocity > 168  # 200*3/3=200, above threshold — filter works


# ── 12. Bar tick_count enrichment from IBKR ───────────────────────────────────

class TestBarTickCountEnrichment:
    """Tests for _enrich_bar_tick_count — replacing snapshot tick_count with
    real IBKR market tick count via reqHistoricalDataAsync."""

    def _make_bar(self, tick_count: int = 60):
        from v11.core.types import Bar
        return Bar(
            timestamp=datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            open=2650.0, high=2651.0, low=2649.0, close=2650.5,
            tick_count=tick_count,
            buy_volume=30.0, sell_volume=30.0,
        )

    def _make_ibkr_bar(self, volume: int):
        """Mock ib_insync BarData with a volume field."""
        b = MagicMock()
        b.volume = volume
        return b

    @pytest.mark.asyncio
    async def test_enrichment_uses_ibkr_volume(self, adapter):
        """When IBKR returns a bar with volume>0, tick_count is replaced."""
        bar = self._make_bar(tick_count=60)
        ibkr_bar = self._make_ibkr_bar(volume=215)
        adapter._ib.reqHistoricalDataAsync = AsyncMock(return_value=[ibkr_bar])

        result = await adapter._enrich_bar_tick_count(bar)

        assert result.tick_count == 215
        # Other fields preserved
        assert result.open == bar.open
        assert result.high == bar.high

    @pytest.mark.asyncio
    async def test_enrichment_falls_back_on_empty_response(self, adapter):
        """When IBKR returns no bars, original bar is preserved."""
        bar = self._make_bar(tick_count=60)
        adapter._ib.reqHistoricalDataAsync = AsyncMock(return_value=[])

        result = await adapter._enrich_bar_tick_count(bar)

        assert result is bar
        assert result.tick_count == 60

    @pytest.mark.asyncio
    async def test_enrichment_falls_back_on_zero_volume(self, adapter):
        """When IBKR returns volume=0, original bar is preserved."""
        bar = self._make_bar(tick_count=60)
        ibkr_bar = self._make_ibkr_bar(volume=0)
        adapter._ib.reqHistoricalDataAsync = AsyncMock(return_value=[ibkr_bar])

        result = await adapter._enrich_bar_tick_count(bar)

        assert result is bar

    @pytest.mark.asyncio
    async def test_enrichment_falls_back_on_exception(self, adapter):
        """When IBKR request raises, original bar is preserved."""
        bar = self._make_bar(tick_count=60)
        adapter._ib.reqHistoricalDataAsync = AsyncMock(side_effect=Exception("timeout"))

        result = await adapter._enrich_bar_tick_count(bar)

        assert result is bar

    @pytest.mark.asyncio
    async def test_on_bar_uses_enriched_tick_count(self, adapter):
        """on_bar uses enriched tick_count when IBKR provides real volume."""
        bar = self._make_bar(tick_count=60)
        ibkr_bar = self._make_ibkr_bar(volume=215)
        adapter._ib.reqHistoricalDataAsync = AsyncMock(return_value=[ibkr_bar])

        await adapter.on_bar(bar)

        assert len(adapter._bar_buffer) == 1
        assert adapter._bar_buffer[0].tick_count == 215
