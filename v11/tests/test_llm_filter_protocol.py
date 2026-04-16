"""Tests for the LLMFilter protocol outcome-recording methods.

Verifies the public record_darvas_outcome, record_orb_outcome, and
refresh_feedback methods exist on all LLMFilter implementations and
behave correctly (no-op for stateless filters, delegation for wrappers).

Added 2026-04-16 as part of the LLM interface formalization that removed
private-attribute (_ledger, _inner_filter) reach-ins from callers.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from v11.llm.base import LLMFilter
from v11.llm.passthrough_filter import PassthroughFilter
from v11.replay.cached_filter import CachedFilter


# ── Protocol conformance ─────────────────────────────────────────────────────

class TestProtocolConformance:
    """Verify each filter satisfies the LLMFilter protocol."""

    def test_passthrough_filter_satisfies_protocol(self):
        f = PassthroughFilter()
        assert isinstance(f, LLMFilter)
        assert callable(f.record_darvas_outcome)
        assert callable(f.record_orb_outcome)
        assert callable(f.refresh_feedback)

    def test_cached_filter_satisfies_protocol(self, tmp_path):
        f = CachedFilter(inner_filter=None, cache_path=str(tmp_path / "c.json"))
        assert isinstance(f, LLMFilter)
        assert callable(f.record_darvas_outcome)
        assert callable(f.record_orb_outcome)
        assert callable(f.refresh_feedback)


# ── PassthroughFilter no-ops ─────────────────────────────────────────────────

class TestPassthroughNoOps:
    """Stateless filter must accept the calls and do nothing."""

    def test_record_darvas_is_noop(self):
        f = PassthroughFilter()
        # Must not raise with any keyword arguments
        f.record_darvas_outcome(
            instrument="EURUSD",
            decision_timestamp="2026-04-16T15:00:00Z",
            approved=True,
            entry_price=1.10,
            exit_price=1.11,
            exit_reason="TARGET",
            pnl=100.0,
            breakout_price=1.10,
        )

    def test_record_orb_is_noop(self):
        f = PassthroughFilter()
        f.record_orb_outcome(
            instrument="XAUUSD",
            decision_date="2026-04-16",
            approved=True,
            entry_price=2400.0,
            exit_price=2405.0,
            exit_reason="TP",
            pnl=5.0,
            range_high=2410.0,
            range_low=2390.0,
        )

    def test_refresh_feedback_is_noop(self):
        f = PassthroughFilter()
        f.refresh_feedback()  # just must not raise


# ── CachedFilter delegation ─────────────────────────────────────────────────

class TestCachedFilterDelegation:
    """Wrapper must forward outcome recording to the inner filter when present."""

    def test_delegates_record_darvas_to_inner(self, tmp_path):
        inner = MagicMock()
        # Async methods so isinstance(LLMFilter) works
        inner.evaluate_signal = AsyncMock()
        inner.evaluate_orb_signal = AsyncMock()
        f = CachedFilter(inner_filter=inner, cache_path=str(tmp_path / "c.json"))
        f.record_darvas_outcome(
            instrument="EURUSD", decision_timestamp="t",
            approved=True, entry_price=1.0, exit_price=1.1,
            exit_reason="TARGET", pnl=10.0, breakout_price=1.0,
        )
        inner.record_darvas_outcome.assert_called_once()
        # All kwargs forwarded
        kwargs = inner.record_darvas_outcome.call_args.kwargs
        assert kwargs["instrument"] == "EURUSD"
        assert kwargs["pnl"] == 10.0

    def test_delegates_record_orb_to_inner(self, tmp_path):
        inner = MagicMock()
        inner.evaluate_signal = AsyncMock()
        inner.evaluate_orb_signal = AsyncMock()
        f = CachedFilter(inner_filter=inner, cache_path=str(tmp_path / "c.json"))
        f.record_orb_outcome(
            instrument="XAUUSD", decision_date="d",
            approved=True, entry_price=2400.0, exit_price=2410.0,
            exit_reason="TP", pnl=10.0,
            range_high=2410.0, range_low=2390.0,
        )
        inner.record_orb_outcome.assert_called_once()

    def test_delegates_refresh_feedback(self, tmp_path):
        inner = MagicMock()
        inner.evaluate_signal = AsyncMock()
        inner.evaluate_orb_signal = AsyncMock()
        f = CachedFilter(inner_filter=inner, cache_path=str(tmp_path / "c.json"))
        f.refresh_feedback()
        inner.refresh_feedback.assert_called_once()

    def test_no_inner_means_silent_noop(self, tmp_path):
        """CachedFilter with inner_filter=None must not raise on outcome calls."""
        f = CachedFilter(inner_filter=None, cache_path=str(tmp_path / "c.json"))
        f.record_darvas_outcome(
            instrument="EURUSD", decision_timestamp="t",
            approved=True, entry_price=1.0, exit_price=1.1,
            exit_reason="TARGET", pnl=10.0, breakout_price=1.0,
        )
        f.record_orb_outcome(
            instrument="XAUUSD", decision_date="d",
            approved=True, entry_price=2400.0, exit_price=2410.0,
            exit_reason="TP", pnl=10.0,
            range_high=2410.0, range_low=2390.0,
        )
        f.refresh_feedback()

    def test_inner_without_method_is_tolerated(self, tmp_path):
        """If inner is some minimal LLMFilter without the new methods,
        CachedFilter should not raise (hasattr check)."""
        class BareFilter:
            async def evaluate_signal(self, ctx): ...
            async def evaluate_orb_signal(self, ctx): ...
        f = CachedFilter(inner_filter=BareFilter(),
                         cache_path=str(tmp_path / "c.json"))
        f.record_darvas_outcome(
            instrument="EURUSD", decision_timestamp="t",
            approved=True, entry_price=1.0, exit_price=1.1,
            exit_reason="TARGET", pnl=10.0, breakout_price=1.0,
        )


# ── GrokFilter outcome recording ────────────────────────────────────────────

class TestGrokFilterOutcomeRecording:
    """GrokFilter.record_* must delegate to the ledger + auto_assessor and
    survive ledger errors (never raise)."""

    def test_no_ledger_is_silent_noop(self, tmp_path):
        from v11.llm.grok_filter import GrokFilter
        # Construct without log_dir -> ledger is None
        f = GrokFilter(api_key="test-key", log_dir=None)
        assert f._ledger is None
        # Should not raise
        f.record_darvas_outcome(
            instrument="EURUSD", decision_timestamp="t",
            approved=True, entry_price=1.0, exit_price=1.1,
            exit_reason="TARGET", pnl=10.0, breakout_price=1.0,
        )
        f.record_orb_outcome(
            instrument="XAUUSD", decision_date="d",
            approved=True, entry_price=2400.0, exit_price=2410.0,
            exit_reason="TP", pnl=10.0,
            range_high=2410.0, range_low=2390.0,
        )

    def test_never_raises_on_assessor_error(self, tmp_path, monkeypatch):
        """Even if the underlying assessor raises, record_* must swallow it."""
        from v11.llm.grok_filter import GrokFilter
        f = GrokFilter(api_key="test-key", log_dir=str(tmp_path))
        assert f._ledger is not None  # ledger created when log_dir given

        # Force the assessor to raise
        import v11.replay.auto_assessor as aa

        def boom(**_kwargs):
            raise RuntimeError("ledger disk full")

        monkeypatch.setattr(aa, "assess_darvas_decision", boom)
        monkeypatch.setattr(aa, "assess_orb_decision", boom)

        # Must NOT raise
        f.record_darvas_outcome(
            instrument="EURUSD", decision_timestamp="t",
            approved=True, entry_price=1.0, exit_price=1.1,
            exit_reason="TARGET", pnl=10.0, breakout_price=1.0,
        )
        f.record_orb_outcome(
            instrument="XAUUSD", decision_date="d",
            approved=True, entry_price=2400.0, exit_price=2410.0,
            exit_reason="TP", pnl=10.0,
            range_high=2410.0, range_low=2390.0,
        )
