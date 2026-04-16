"""
Tests for RiskManager — Combined risk management across strategies.

Design decisions tested:
    1. Default state allows trading (no limits hit)
    2. Combined daily loss limit pauses ALL strategies
    3. Max concurrent positions enforced
    4. Max 1 position per instrument (first signal wins)
    5. Per-strategy daily trade limit
    6. record_trade_entry updates positions and counters
    7. record_trade_exit removes position and updates PnL
    8. reset_daily clears counters but preserves open positions
    9. Multiple strategies contribute to combined PnL
"""
import logging
import pytest

from v11.live.risk_manager import RiskManager


@pytest.fixture
def log():
    return logging.getLogger("test_risk")


@pytest.fixture
def rm(log):
    """Standard RiskManager: $500 daily loss, 10 trades/strategy, 3 max positions."""
    return RiskManager(
        max_daily_loss=500.0,
        max_daily_trades_per_strategy=10,
        max_concurrent_positions=3,
        log=log,
    )


# ── Allow trade when no limits hit ─────────────────────────────────────────

class TestCanTradeDefaults:
    def test_allow_when_no_limits_hit(self, rm):
        allowed, reason = rm.can_trade("EURUSD", "Darvas")
        assert allowed is True
        assert reason == ""


# ── Combined daily loss limit ──────────────────────────────────────────────

class TestCombinedDailyLoss:
    def test_block_when_combined_loss_exceeded(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=-300.0)
        rm.record_trade_entry("EURUSD", "Retest")
        rm.record_trade_exit("EURUSD", "Retest", pnl=-200.0)
        # Combined = -500, limit = -500
        allowed, reason = rm.can_trade("EURUSD", "Darvas")
        assert allowed is False
        assert "Combined daily loss" in reason

    def test_allow_when_just_under_limit(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=-499.99)
        allowed, _ = rm.can_trade("EURUSD", "Darvas")
        assert allowed is True


# ── Max concurrent positions ───────────────────────────────────────────────

class TestMaxConcurrentPositions:
    def test_block_at_max_positions(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_entry("XAUUSD", "ORB")
        rm.record_trade_entry("USDJPY", "Darvas")
        # 3 open, max=3
        allowed, reason = rm.can_trade("GBPUSD", "Darvas")
        assert allowed is False
        assert "Max concurrent" in reason

    def test_allow_after_position_closes(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_entry("XAUUSD", "ORB")
        rm.record_trade_entry("USDJPY", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=10.0)
        # 2 open now
        allowed, _ = rm.can_trade("GBPUSD", "Darvas")
        assert allowed is True


# ── Instrument conflict (max 1 per instrument) ────────────────────────────

class TestInstrumentConflict:
    def test_block_when_instrument_has_position(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        allowed, reason = rm.can_trade("EURUSD", "Retest")
        assert allowed is False
        assert "already has position" in reason

    def test_allow_different_instrument(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        allowed, _ = rm.can_trade("XAUUSD", "ORB")
        assert allowed is True

    def test_allow_after_instrument_exit(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=5.0)
        allowed, _ = rm.can_trade("EURUSD", "Retest")
        assert allowed is True


# ── Per-strategy daily trade limit ─────────────────────────────────────────

class TestPerStrategyTradeLimit:
    def test_block_at_strategy_limit(self, rm):
        for i in range(10):
            rm.record_trade_entry("EURUSD", "Darvas")
            rm.record_trade_exit("EURUSD", "Darvas", pnl=1.0)
        # 10 trades for Darvas, limit=10
        allowed, reason = rm.can_trade("EURUSD", "Darvas")
        assert allowed is False
        assert "daily trade limit" in reason

    def test_other_strategy_unaffected(self, rm):
        for i in range(10):
            rm.record_trade_entry("EURUSD", "Darvas")
            rm.record_trade_exit("EURUSD", "Darvas", pnl=1.0)
        # Darvas hit limit, but Retest hasn't traded
        allowed, _ = rm.can_trade("EURUSD", "Retest")
        assert allowed is True


# ── record_trade_entry tracking ────────────────────────────────────────────

class TestRecordTradeEntry:
    def test_updates_positions_and_counts(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        assert rm.open_position_count == 1
        assert rm.combined_trades == 1
        assert rm.is_instrument_in_trade("EURUSD") is True
        assert rm.is_instrument_in_trade("XAUUSD") is False

    def test_multiple_instruments(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_entry("XAUUSD", "ORB")
        assert rm.open_position_count == 2
        assert rm.combined_trades == 2


# ── record_trade_exit tracking ─────────────────────────────────────────────

class TestRecordTradeExit:
    def test_removes_position_updates_pnl(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=-50.0)
        assert rm.open_position_count == 0
        assert rm.combined_pnl == -50.0
        assert rm.is_instrument_in_trade("EURUSD") is False

    def test_multiple_exits_accumulate(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=-100.0)
        rm.record_trade_entry("EURUSD", "Retest")
        rm.record_trade_exit("EURUSD", "Retest", pnl=30.0)
        assert rm.combined_pnl == -70.0


# ── reset_daily ────────────────────────────────────────────────────────────

class TestResetDaily:
    def test_clears_counters(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=-100.0)
        rm.reset_daily()
        assert rm.combined_pnl == 0.0
        assert rm.combined_trades == 0
        stats = rm.get_strategy_stats("Darvas")
        assert stats.daily_trades == 0
        assert stats.daily_pnl == 0.0

    def test_open_positions_preserved_after_reset(self, rm):
        """Open positions are NOT cleared by daily reset — they persist."""
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.reset_daily()
        assert rm.is_instrument_in_trade("EURUSD") is True
        assert rm.open_position_count == 1


# ── Combined PnL from multiple strategies ──────────────────────────────────

class TestCombinedPnL:
    def test_both_strategies_contribute(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", pnl=-200.0)
        rm.record_trade_entry("XAUUSD", "ORB")
        rm.record_trade_exit("XAUUSD", "ORB", pnl=-150.0)
        # Combined = -350, but strategy-specific PnLs differ
        assert rm.combined_pnl == -350.0
        assert rm.get_strategy_stats("Darvas").daily_pnl == -200.0
        assert rm.get_strategy_stats("ORB").daily_pnl == -150.0


# ── get_status ─────────────────────────────────────────────────────────────

class TestGetStatus:
    def test_returns_complete_snapshot(self, rm):
        rm.record_trade_entry("EURUSD", "Darvas")
        status = rm.get_status()
        assert 'combined_pnl' in status
        assert 'combined_trades' in status
        assert 'open_positions' in status
        assert 'strategies' in status
        assert status['open_positions'] == {'EURUSD': 'Darvas'}


# ── Per-strategy daily loss limit (optional, added 2026-04-16) ─────────────

class TestPerStrategyDailyLoss:
    """New optional feature: a strategy whose daily PnL drops below
    -max_daily_loss_per_strategy is paused, while other strategies continue
    trading (subject to the combined limit)."""

    def test_default_disabled_preserves_old_behavior(self, rm):
        """Default rm fixture uses max_daily_loss_per_strategy=0.0 (disabled).
        A strategy can lose arbitrarily much up to the combined limit."""
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", -450.0)  # big single loss
        allowed, _reason = rm.can_trade("EURUSD", "Darvas")
        # Strategy lost $450 but combined limit is $500, per-strategy disabled
        assert allowed is True

    def test_blocks_strategy_exceeding_its_own_limit(self, log):
        rm = RiskManager(
            max_daily_loss=1000.0,
            max_daily_trades_per_strategy=10,
            max_concurrent_positions=3,
            log=log,
            max_daily_loss_per_strategy=200.0,
        )
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", -250.0)  # exceeds $200 limit
        allowed, reason = rm.can_trade("EURUSD", "Darvas")
        assert allowed is False
        assert "Darvas" in reason
        assert "daily loss limit" in reason

    def test_does_not_block_other_strategies(self, log):
        """One strategy hitting its per-strategy limit must not pause others."""
        rm = RiskManager(
            max_daily_loss=1000.0,
            max_daily_trades_per_strategy=10,
            max_concurrent_positions=3,
            log=log,
            max_daily_loss_per_strategy=200.0,
        )
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", -250.0)  # Darvas paused
        # ORB has lost nothing, must still be allowed
        allowed, reason = rm.can_trade("XAUUSD", "ORB")
        assert allowed is True
        assert reason == ""

    def test_exact_limit_blocks(self, log):
        rm = RiskManager(
            max_daily_loss=1000.0,
            max_daily_trades_per_strategy=10,
            max_concurrent_positions=3,
            log=log,
            max_daily_loss_per_strategy=200.0,
        )
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", -200.0)  # exactly at limit
        allowed, _reason = rm.can_trade("EURUSD", "Darvas")
        assert allowed is False  # <= comparison includes equality

    def test_below_limit_allowed(self, log):
        rm = RiskManager(
            max_daily_loss=1000.0,
            max_daily_trades_per_strategy=10,
            max_concurrent_positions=3,
            log=log,
            max_daily_loss_per_strategy=200.0,
        )
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", -150.0)  # below limit
        allowed, _reason = rm.can_trade("EURUSD", "Darvas")
        assert allowed is True

    def test_reset_daily_clears_per_strategy_loss(self, log):
        rm = RiskManager(
            max_daily_loss=1000.0,
            max_daily_trades_per_strategy=10,
            max_concurrent_positions=3,
            log=log,
            max_daily_loss_per_strategy=200.0,
        )
        rm.record_trade_entry("EURUSD", "Darvas")
        rm.record_trade_exit("EURUSD", "Darvas", -250.0)
        assert rm.can_trade("EURUSD", "Darvas")[0] is False
        rm.reset_daily()
        assert rm.can_trade("EURUSD", "Darvas")[0] is True
