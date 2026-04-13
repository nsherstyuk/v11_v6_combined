"""Tests for CachedFilter — record/replay LLM responses."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from v11.core.types import FilterDecision
from v11.llm.models import SignalContext, BarData
from v11.replay.cached_filter import CachedFilter


def _make_context(instrument="EURUSD", breakout_price=1.1050) -> SignalContext:
    return SignalContext(
        direction="long",
        instrument=instrument,
        box_top=1.1050,
        box_bottom=1.1000,
        box_duration_bars=30,
        box_width_atr=1.5,
        breakout_price=breakout_price,
        atr=0.0010,
        buy_ratio_at_breakout=0.65,
        buy_ratio_trend="increasing",
        tick_quality="HIGH",
        volume_classification="CONFIRMING",
        recent_bars=[BarData(t="2025-01-15T14:30:00", o=1.104, h=1.105, l=1.103, c=1.105, bv=100, sv=80, tc=50)],
        current_time_utc="2025-01-15T14:47:00",
        session="LONDON_NY_OVERLAP",
    )


def _make_decision() -> FilterDecision:
    return FilterDecision(
        approved=True, confidence=85,
        entry_price=1.1050, stop_price=1.1000, target_price=1.1150,
        reasoning="test", risk_flags=[],
    )


class TestCacheHitMiss:
    def test_cache_miss_calls_inner_filter(self, tmp_path):
        inner = AsyncMock()
        inner.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache = CachedFilter(inner_filter=inner, cache_path=str(tmp_path / "cache.json"))

        result = asyncio.run(cache.evaluate_signal(_make_context()))

        assert result.approved is True
        assert result.confidence == 85
        inner.evaluate_signal.assert_called_once()

    def test_cache_hit_skips_inner_filter(self, tmp_path):
        inner = AsyncMock()
        inner.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache = CachedFilter(inner_filter=inner, cache_path=str(tmp_path / "cache.json"))
        ctx = _make_context()

        # First call: cache miss
        asyncio.run(cache.evaluate_signal(ctx))
        # Second call: cache hit
        result = asyncio.run(cache.evaluate_signal(ctx))

        assert result.approved is True
        assert inner.evaluate_signal.call_count == 1  # only called once


class TestCachePersistence:
    def test_cache_saves_to_file(self, tmp_path):
        inner = AsyncMock()
        inner.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache_path = str(tmp_path / "cache.json")
        cache = CachedFilter(inner_filter=inner, cache_path=cache_path)

        asyncio.run(cache.evaluate_signal(_make_context()))
        cache.save()

        assert Path(cache_path).exists()
        data = json.loads(Path(cache_path).read_text())
        assert len(data) == 1

    def test_cache_loads_from_file(self, tmp_path):
        inner = AsyncMock()
        inner.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache_path = str(tmp_path / "cache.json")

        # First cache instance: populate
        cache1 = CachedFilter(inner_filter=inner, cache_path=cache_path)
        asyncio.run(cache1.evaluate_signal(_make_context()))
        cache1.save()

        # Second cache instance: load from file
        inner2 = AsyncMock()
        inner2.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache2 = CachedFilter(inner_filter=inner2, cache_path=cache_path)

        result = asyncio.run(cache2.evaluate_signal(_make_context()))

        assert result.approved is True
        inner2.evaluate_signal.assert_not_called()  # served from persisted cache


class TestPassthroughOnCacheMiss:
    def test_no_inner_filter_returns_passthrough(self, tmp_path):
        cache = CachedFilter(inner_filter=None, cache_path=str(tmp_path / "cache.json"))

        result = asyncio.run(cache.evaluate_signal(_make_context()))

        # With no inner filter and no cache hit, falls back to passthrough
        assert result.approved is True
        assert result.confidence == 0
        assert "cache miss" in result.reasoning.lower()
