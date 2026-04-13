# Historical Replay Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a replay simulator that feeds historical 1-min bars through the actual live strategy code (Darvas + 4H Retest) to verify correctness and measure performance without IBKR.

**Architecture:** ReplayRunner loads historical bars via the existing `data_loader`, creates the same strategy engines as the live system (InstrumentEngine, LevelRetestEngine), and feeds bars directly to them bypassing BarAggregator. TradeManager runs in dry_run mode with a stub connection. LLM filter is configurable: passthrough, real Grok, or cached responses.

**Tech Stack:** Python 3.11+, pytest, asyncio, existing v11 modules (no new dependencies)

**Spec:** `docs/superpowers/specs/2026-04-12-historical-replay-simulator-design.md`

**Review:** `C:\Users\nsher\.windsurf\plans\historical-replay-simulator-review-3170d9.md`

### Review adjustments incorporated

| Review Issue | Resolution |
|---|---|
| #1 Python 3.14 asyncio patch | Added to Task 6 (run_replay.py) — same patch as run_live.py |
| #2 Session splitting / detector reset | **Disagree**: detectors are NOT reset between sessions, matching live behavior. Session gaps are logged as `SESSION_GAP` events for observability. If stale state causes bugs, that's what we want to discover. |
| #3 TradeManager dry_run path | Confirmed working, no changes needed |
| #4 CachedFilter ORB stub | Confirmed adequate, no changes needed |
| #5 Trade exit detection fragility | Fixed in Task 5: use both `daily_trades` counter AND `was_in_trade → not in_trade` state transition |
| #6 data_loader path | Confirmed working, no changes needed |
| #7 RiskManager constructor | Confirmed matching, no changes needed |
| #8 LiveConfig constructor | Verified: LiveConfig is a dataclass with defaults, keyword args work. `max_daily_trades` (LiveConfig, per-instrument) vs `max_daily_trades_per_strategy` (RiskManager) are different knobs — both wired correctly |

---

## File Structure

```
v11/replay/                     # New module
    __init__.py                 # Empty
    config.py                   # ReplayConfig dataclass
    stub_connection.py          # StubIBKRConnection (satisfies dry_run TradeManager)
    cached_filter.py            # CachedFilter (record/replay LLM responses)
    event_logger.py             # EventLogger (structured JSONL + console)
    metrics.py                  # Compute summary stats from trade records
    replay_runner.py            # ReplayRunner (main loop + assembly)
    run_replay.py               # CLI entry point (argparse)

v11/tests/
    test_replay_config.py       # Config validation tests
    test_stub_connection.py     # Stub connection tests
    test_cached_filter.py       # Cache hit/miss/persistence tests
    test_event_logger.py        # Event emission + file output tests
    test_replay_metrics.py      # Metrics computation tests
    test_replay_runner.py       # Integration: full replay on sample data
```

---

### Task 1: ReplayConfig + StubConnection

**Files:**
- Create: `v11/replay/__init__.py`
- Create: `v11/replay/config.py`
- Create: `v11/replay/stub_connection.py`
- Test: `v11/tests/test_replay_config.py`
- Test: `v11/tests/test_stub_connection.py`

- [ ] **Step 1: Create the replay package**

Create `v11/replay/__init__.py` as an empty file.

- [ ] **Step 2: Write failing test for ReplayConfig**

```python
# v11/tests/test_replay_config.py
"""Tests for ReplayConfig — validation and defaults."""
import pytest
from v11.replay.config import ReplayConfig


class TestReplayConfigDefaults:
    def test_default_llm_mode_is_passthrough(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        assert cfg.llm_mode == "passthrough"

    def test_default_slippage(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        assert cfg.slippage_pips == 0.5

    def test_default_dry_run_is_true(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        assert cfg.dry_run is True


class TestReplayConfigValidation:
    def test_validate_rejects_empty_instruments(self):
        cfg = ReplayConfig(instruments=[], start_date="2025-01-01", end_date="2025-03-31")
        with pytest.raises(ValueError, match="instruments"):
            cfg.validate()

    def test_validate_rejects_invalid_llm_mode(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31", llm_mode="gpt5")
        with pytest.raises(ValueError, match="llm_mode"):
            cfg.validate()

    def test_validate_rejects_start_after_end(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-06-01", end_date="2025-03-31")
        with pytest.raises(ValueError, match="start_date"):
            cfg.validate()

    def test_validate_passes_valid_config(self):
        cfg = ReplayConfig(instruments=["EURUSD"], start_date="2025-01-01", end_date="2025-03-31")
        cfg.validate()  # should not raise
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest v11/tests/test_replay_config.py -v`
Expected: ImportError — `v11.replay.config` does not exist yet

- [ ] **Step 4: Implement ReplayConfig**

```python
# v11/replay/config.py
"""Replay simulator configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

VALID_LLM_MODES = {"passthrough", "live", "cached"}


@dataclass
class ReplayConfig:
    """Configuration for a historical replay run."""

    # Required
    instruments: list[str]
    start_date: str              # "YYYY-MM-DD"
    end_date: str                # "YYYY-MM-DD"

    # LLM mode
    llm_mode: str = "passthrough"    # "passthrough" | "live" | "cached"
    grok_api_key: str = ""
    grok_model: str = "grok-4-1-fast-reasoning"
    llm_cache_path: str = "replay_llm_cache.json"
    llm_confidence_threshold: int = 75

    # Execution simulation
    slippage_pips: float = 0.5
    commission_per_lot: float = 2.0

    # Risk manager
    max_daily_loss: float = 500.0
    max_daily_trades: int = 10
    max_concurrent_positions: int = 3

    # Seeding
    seed_bars: int = 500             # bars to seed before replay starts

    # Output
    output_dir: str = "v11/replay/results"
    event_verbosity: str = "normal"  # "quiet" | "normal" | "verbose"

    # Dry run (always True for replay — no real orders)
    dry_run: bool = True

    def validate(self) -> None:
        """Validate config values. Raises ValueError on problems."""
        if not self.instruments:
            raise ValueError("instruments must not be empty")
        if self.llm_mode not in VALID_LLM_MODES:
            raise ValueError(
                f"llm_mode must be one of {VALID_LLM_MODES}, got '{self.llm_mode}'")
        start = datetime.strptime(self.start_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d")
        if start > end:
            raise ValueError(
                f"start_date ({self.start_date}) must be before end_date ({self.end_date})")

    @property
    def start_dt(self) -> datetime:
        return datetime.strptime(self.start_date, "%Y-%m-%d")

    @property
    def end_dt(self) -> datetime:
        return datetime.strptime(self.end_date, "%Y-%m-%d")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest v11/tests/test_replay_config.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Write failing test for StubIBKRConnection**

```python
# v11/tests/test_stub_connection.py
"""Tests for StubIBKRConnection — minimal stub for dry_run TradeManager."""
from v11.replay.stub_connection import StubIBKRConnection


class TestStubConnection:
    def test_submit_market_order_returns_none(self):
        stub = StubIBKRConnection()
        result = stub.submit_market_order("EURUSD", "long", 20000)
        assert result is None

    def test_submit_stop_order_returns_none(self):
        stub = StubIBKRConnection()
        result = stub.submit_stop_order("EURUSD", "long", 20000, 1.1000)
        assert result is None

    def test_get_position_size_returns_zero(self):
        stub = StubIBKRConnection()
        assert stub.get_position_size("EUR", "CASH") == 0.0

    def test_has_position_returns_false(self):
        stub = StubIBKRConnection()
        assert stub.has_position("EUR", "CASH") is False

    def test_sleep_is_noop(self):
        stub = StubIBKRConnection()
        stub.sleep(5)  # should not block

    def test_get_fill_commission_returns_zero(self):
        stub = StubIBKRConnection()
        assert stub.get_fill_commission(None) == 0.0
```

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m pytest v11/tests/test_stub_connection.py -v`
Expected: ImportError

- [ ] **Step 8: Implement StubIBKRConnection**

```python
# v11/replay/stub_connection.py
"""Stub IBKR connection for replay mode.

Satisfies the duck-type interface that TradeManager calls when dry_run=True.
In dry_run mode, TradeManager never actually calls submit_market_order or
submit_stop_order — but we provide them anyway for safety.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class _StubIB:
    """Minimal stub for conn.ib attribute (never called in dry_run)."""

    def cancelOrder(self, order):
        pass


class StubIBKRConnection:
    """No-op IBKR connection for replay/dry-run use.

    TradeManager in dry_run=True mode never touches the connection,
    but it stores self._conn and references self._conn.ib in the
    _execute_exit block guarded by `if not self._dry_run`.
    This stub ensures no AttributeError if something unexpected
    accesses the connection.
    """

    def __init__(self):
        self.ib = _StubIB()

    def submit_market_order(self, pair_name, direction, quantity):
        log.debug(f"StubIBKR: market order {pair_name} {direction} {quantity} (no-op)")
        return None

    def submit_stop_order(self, pair_name, direction, quantity, stop_price, tick_size=0.01):
        log.debug(f"StubIBKR: stop order {pair_name} {stop_price} (no-op)")
        return None

    def close_position(self, pair_name, direction, quantity):
        return None

    def has_position(self, symbol, sec_type):
        return False

    def get_position_size(self, symbol, sec_type):
        return 0.0

    def get_fill_commission(self, trade):
        return 0.0

    def sleep(self, seconds):
        pass  # no-op in replay

    def cancel_all_orders(self):
        pass
```

- [ ] **Step 9: Run test to verify it passes**

Run: `python -m pytest v11/tests/test_stub_connection.py -v`
Expected: All 6 tests PASS

- [ ] **Step 10: Commit**

```bash
git add v11/replay/__init__.py v11/replay/config.py v11/replay/stub_connection.py v11/tests/test_replay_config.py v11/tests/test_stub_connection.py
git commit -m "feat(replay): add ReplayConfig and StubIBKRConnection"
```

---

### Task 2: CachedFilter

**Files:**
- Create: `v11/replay/cached_filter.py`
- Test: `v11/tests/test_cached_filter.py`

- [ ] **Step 1: Write failing tests for CachedFilter**

```python
# v11/tests/test_cached_filter.py
"""Tests for CachedFilter — record/replay LLM responses."""
import json
import asyncio
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

        result = asyncio.get_event_loop().run_until_complete(
            cache.evaluate_signal(_make_context()))

        assert result.approved is True
        assert result.confidence == 85
        inner.evaluate_signal.assert_called_once()

    def test_cache_hit_skips_inner_filter(self, tmp_path):
        inner = AsyncMock()
        inner.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache = CachedFilter(inner_filter=inner, cache_path=str(tmp_path / "cache.json"))
        ctx = _make_context()

        # First call: cache miss
        asyncio.get_event_loop().run_until_complete(cache.evaluate_signal(ctx))
        # Second call: cache hit
        result = asyncio.get_event_loop().run_until_complete(cache.evaluate_signal(ctx))

        assert result.approved is True
        assert inner.evaluate_signal.call_count == 1  # only called once


class TestCachePersistence:
    def test_cache_saves_to_file(self, tmp_path):
        inner = AsyncMock()
        inner.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache_path = str(tmp_path / "cache.json")
        cache = CachedFilter(inner_filter=inner, cache_path=cache_path)

        asyncio.get_event_loop().run_until_complete(
            cache.evaluate_signal(_make_context()))
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
        asyncio.get_event_loop().run_until_complete(
            cache1.evaluate_signal(_make_context()))
        cache1.save()

        # Second cache instance: load from file
        inner2 = AsyncMock()
        inner2.evaluate_signal = AsyncMock(return_value=_make_decision())
        cache2 = CachedFilter(inner_filter=inner2, cache_path=cache_path)

        result = asyncio.get_event_loop().run_until_complete(
            cache2.evaluate_signal(_make_context()))

        assert result.approved is True
        inner2.evaluate_signal.assert_not_called()  # served from persisted cache


class TestPassthroughOnCacheMiss:
    def test_no_inner_filter_returns_passthrough(self, tmp_path):
        cache = CachedFilter(inner_filter=None, cache_path=str(tmp_path / "cache.json"))

        result = asyncio.get_event_loop().run_until_complete(
            cache.evaluate_signal(_make_context()))

        # With no inner filter and no cache hit, falls back to passthrough
        assert result.approved is True
        assert result.confidence == 0
        assert "cache miss" in result.reasoning.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v11/tests/test_cached_filter.py -v`
Expected: ImportError

- [ ] **Step 3: Implement CachedFilter**

```python
# v11/replay/cached_filter.py
"""CachedFilter — Record/replay LLM filter responses.

Wraps any LLMFilter implementation with a JSON cache layer.
On cache miss: calls inner filter (if provided), stores response.
On cache hit: returns stored response (instant, free, deterministic).

Cache key: SHA-256 of SignalContext JSON (stable, deterministic).
Cache storage: JSON file mapping hash -> FilterDecision fields.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from ..core.types import FilterDecision
from ..llm.models import SignalContext

log = logging.getLogger(__name__)


class CachedFilter:
    """LLM filter with transparent caching.

    Satisfies the LLMFilter protocol.
    """

    def __init__(
        self,
        inner_filter: Optional[object] = None,
        cache_path: str = "replay_llm_cache.json",
    ):
        self._inner = inner_filter
        self._cache_path = Path(cache_path)
        self._cache: dict[str, dict] = {}
        self._hits = 0
        self._misses = 0
        self._load_cache()

    def _load_cache(self) -> None:
        if self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text())
                log.info(f"CachedFilter: loaded {len(self._cache)} entries from {self._cache_path}")
            except (json.JSONDecodeError, IOError) as e:
                log.warning(f"CachedFilter: failed to load cache: {e}")
                self._cache = {}

    def save(self) -> None:
        """Persist cache to disk."""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache, indent=2))
        log.info(f"CachedFilter: saved {len(self._cache)} entries "
                 f"(hits={self._hits}, misses={self._misses})")

    @staticmethod
    def _cache_key(context: SignalContext) -> str:
        raw = context.model_dump_json(indent=None)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def evaluate_signal(self, context: SignalContext) -> FilterDecision:
        key = self._cache_key(context)

        if key in self._cache:
            self._hits += 1
            entry = self._cache[key]
            log.debug(f"CachedFilter: HIT {key[:8]} -> approved={entry['approved']}")
            return FilterDecision(
                approved=entry["approved"],
                confidence=entry["confidence"],
                entry_price=entry["entry_price"],
                stop_price=entry["stop_price"],
                target_price=entry["target_price"],
                reasoning=entry["reasoning"],
                risk_flags=entry.get("risk_flags", []),
            )

        self._misses += 1

        if self._inner is not None:
            decision = await self._inner.evaluate_signal(context)
            self._cache[key] = {
                "approved": decision.approved,
                "confidence": decision.confidence,
                "entry_price": decision.entry_price,
                "stop_price": decision.stop_price,
                "target_price": decision.target_price,
                "reasoning": decision.reasoning,
                "risk_flags": list(decision.risk_flags),
            }
            log.debug(f"CachedFilter: MISS {key[:8]} -> called inner, approved={decision.approved}")
            return decision

        # No inner filter and no cache hit: passthrough
        log.debug(f"CachedFilter: MISS {key[:8]} -> no inner filter, passthrough")
        return FilterDecision(
            approved=True,
            confidence=0,
            entry_price=context.breakout_price,
            stop_price=context.box_bottom,
            target_price=0.0,
            reasoning="Cache miss — no inner filter, passthrough approval",
            risk_flags=["cache_miss"],
        )

    async def evaluate_orb_signal(self, context) -> FilterDecision:
        """ORB not supported in replay — passthrough."""
        return FilterDecision(
            approved=True, confidence=0,
            entry_price=0.0, stop_price=0.0, target_price=0.0,
            reasoning="ORB not supported in replay",
        )

    @property
    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v11/tests/test_cached_filter.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add v11/replay/cached_filter.py v11/tests/test_cached_filter.py
git commit -m "feat(replay): add CachedFilter for LLM response caching"
```

---

### Task 3: EventLogger

**Files:**
- Create: `v11/replay/event_logger.py`
- Test: `v11/tests/test_event_logger.py`

- [ ] **Step 1: Write failing tests for EventLogger**

```python
# v11/tests/test_event_logger.py
"""Tests for EventLogger — structured replay event logging."""
import json
from pathlib import Path

import pytest

from v11.replay.event_logger import EventLogger


class TestEventEmission:
    def test_emit_writes_to_file(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T14:32:00",
                     data={"top": 1.0892, "bottom": 1.0875})
        logger.close()

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "BOX_FORMED"
        assert event["strategy"] == "DARVAS"
        assert event["instrument"] == "EURUSD"
        assert event["data"]["top"] == 1.0892

    def test_multiple_events_append(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T14:32:00", data={})
        logger.emit("BREAKOUT_DETECTED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T14:47:00", data={})
        logger.close()

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2


class TestEventCounting:
    def test_event_counts_tracked(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="t1", data={})
        logger.emit("BOX_FORMED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="t2", data={})
        logger.emit("TRADE_ENTERED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="t3", data={})

        counts = logger.get_counts()
        assert counts["BOX_FORMED"] == 2
        assert counts["TRADE_ENTERED"] == 1


class TestTradeRecordCollection:
    def test_trade_exited_events_collected(self, tmp_path):
        path = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=str(path), verbosity="quiet")

        trade_data = {
            "instrument": "EURUSD", "strategy": "DARVAS",
            "direction": "long", "entry_price": 1.1050,
            "exit_price": 1.1100, "pnl": 100.0,
            "exit_reason": "TARGET", "hold_bars": 45,
            "llm_confidence": 85,
        }
        logger.emit("TRADE_EXITED", strategy="DARVAS", instrument="EURUSD",
                     timestamp="2025-01-15T15:30:00", data=trade_data)

        assert len(logger.trade_records) == 1
        assert logger.trade_records[0]["pnl"] == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v11/tests/test_event_logger.py -v`
Expected: ImportError

- [ ] **Step 3: Implement EventLogger**

```python
# v11/replay/event_logger.py
"""EventLogger — Structured event logging for replay runs.

Emits JSON lines to a file and optionally to console.
Collects trade records for metrics computation.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


class EventLogger:
    """Structured event logger for replay runs.

    Events are written as JSONL (one JSON object per line).
    Trade exit events are also collected for metrics.
    """

    def __init__(
        self,
        output_path: str,
        verbosity: str = "normal",
    ):
        self._output_path = Path(output_path)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._output_path, "w")
        self._verbosity = verbosity
        self._counts: Counter = Counter()
        self._trade_records: list[dict] = []

        # Events shown at each verbosity level
        self._console_events = {
            "quiet": {"TRADE_ENTERED", "TRADE_EXITED", "RISK_LIMIT_HIT"},
            "normal": {
                "TRADE_ENTERED", "TRADE_EXITED", "SIGNAL_APPROVED",
                "SIGNAL_REJECTED", "LLM_RESPONSE", "RISK_LIMIT_HIT",
                "DAILY_RESET", "SESSION_START", "SESSION_GAP",
            },
            "verbose": None,  # None = show all
        }

    def emit(
        self,
        event: str,
        strategy: str,
        instrument: str,
        timestamp: str,
        data: dict,
    ) -> None:
        """Emit a structured event."""
        record = {
            "ts": timestamp,
            "event": event,
            "strategy": strategy,
            "instrument": instrument,
            "data": data,
        }
        self._file.write(json.dumps(record, default=str) + "\n")
        self._counts[event] += 1

        # Collect trade exit records for metrics
        if event == "TRADE_EXITED":
            self._trade_records.append(data)

        # Console output based on verbosity
        allowed = self._console_events.get(self._verbosity)
        if allowed is None or event in allowed:
            self._print_event(record)

    def _print_event(self, record: dict) -> None:
        event = record["event"]
        ts = record["ts"]
        inst = record["instrument"]
        strategy = record["strategy"]
        data = record["data"]

        if event == "TRADE_ENTERED":
            direction = data.get("direction", "?")
            entry = data.get("entry_price", 0)
            sl = data.get("stop_price", 0)
            tp = data.get("target_price", 0)
            print(f"  [{ts}] {inst} {strategy} ENTER {direction} @ {entry} SL={sl} TP={tp}")
        elif event == "TRADE_EXITED":
            pnl = data.get("pnl", 0)
            reason = data.get("exit_reason", "?")
            hold = data.get("hold_bars", 0)
            print(f"  [{ts}] {inst} {strategy} EXIT {reason} PnL=${pnl:+.2f} hold={hold}bars")
        elif event == "SIGNAL_APPROVED":
            conf = data.get("confidence", 0)
            direction = data.get("direction", "?")
            print(f"  [{ts}] {inst} {strategy} APPROVED {direction} conf={conf}")
        elif event == "SIGNAL_REJECTED":
            reason = data.get("reason", "?")
            print(f"  [{ts}] {inst} {strategy} REJECTED: {reason[:80]}")
        elif event == "DAILY_RESET":
            print(f"  [{ts}] --- DAILY RESET ---")
        elif event == "SESSION_START":
            bars = data.get("bars", 0)
            print(f"  [{ts}] {inst} SESSION START ({bars} bars)")
        elif event == "SESSION_GAP":
            gap = data.get("gap_minutes", 0)
            print(f"  [{ts}] {inst} SESSION GAP ({gap:.0f} min)")
        else:
            print(f"  [{ts}] {inst} {strategy} {event}")

    @property
    def trade_records(self) -> list[dict]:
        return self._trade_records

    def get_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def close(self) -> None:
        self._file.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v11/tests/test_event_logger.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add v11/replay/event_logger.py v11/tests/test_event_logger.py
git commit -m "feat(replay): add EventLogger for structured replay logging"
```

---

### Task 4: Replay Metrics

**Files:**
- Create: `v11/replay/metrics.py`
- Test: `v11/tests/test_replay_metrics.py`

- [ ] **Step 1: Write failing tests for metrics computation**

```python
# v11/tests/test_replay_metrics.py
"""Tests for replay metrics computation."""
import pytest
from v11.replay.metrics import compute_metrics


def _make_trades(pnls):
    """Helper: make trade records from a list of PnLs."""
    return [
        {"pnl": pnl, "strategy": "DARVAS", "instrument": "EURUSD",
         "exit_reason": "TARGET" if pnl > 0 else "SL"}
        for pnl in pnls
    ]


class TestComputeMetrics:
    def test_empty_trades(self):
        m = compute_metrics([])
        assert m["total_trades"] == 0
        assert m["net_pnl"] == 0.0

    def test_all_winners(self):
        trades = _make_trades([100, 200, 50])
        m = compute_metrics(trades)
        assert m["total_trades"] == 3
        assert m["win_rate"] == 1.0
        assert m["net_pnl"] == 350.0

    def test_mixed_trades(self):
        trades = _make_trades([100, -50, 200, -30, -20])
        m = compute_metrics(trades)
        assert m["total_trades"] == 5
        assert m["win_rate"] == pytest.approx(0.4)
        assert m["net_pnl"] == 200.0

    def test_profit_factor(self):
        trades = _make_trades([100, -50, 200])
        m = compute_metrics(trades)
        # profit_factor = gross_profit / gross_loss = 300 / 50 = 6.0
        assert m["profit_factor"] == pytest.approx(6.0)

    def test_max_drawdown(self):
        trades = _make_trades([100, -50, -80, 200])
        m = compute_metrics(trades, starting_equity=10000)
        # Equity: 10000, 10100, 10050, 9970, 10170
        # Peak: 10000, 10100, 10100, 10100, 10170
        # DD:   0,     0,     50,    130,   0
        assert m["max_drawdown"] == pytest.approx(130.0)
        assert m["max_drawdown_pct"] == pytest.approx(1.287, abs=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v11/tests/test_replay_metrics.py -v`
Expected: ImportError

- [ ] **Step 3: Implement metrics**

```python
# v11/replay/metrics.py
"""Replay metrics — compute summary statistics from trade records."""
from __future__ import annotations

import math
from typing import List


def compute_metrics(
    trades: List[dict],
    starting_equity: float = 100_000.0,
) -> dict:
    """Compute summary metrics from a list of trade records.

    Each trade record must have at least a 'pnl' key (float).

    Returns dict with: total_trades, net_pnl, win_rate, profit_factor,
    max_drawdown, max_drawdown_pct, sharpe, avg_pnl, avg_winner, avg_loser.
    """
    if not trades:
        return {
            "total_trades": 0, "net_pnl": 0.0, "win_rate": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0, "sharpe": 0.0,
            "avg_pnl": 0.0, "avg_winner": 0.0, "avg_loser": 0.0,
        }

    pnls = [t["pnl"] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))

    # Equity curve for drawdown
    equity = [starting_equity]
    for p in pnls:
        equity.append(equity[-1] + p)

    peak = equity[0]
    max_dd = 0.0
    for e in equity[1:]:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualized, assuming ~252 trading days)
    mean_pnl = sum(pnls) / len(pnls)
    if len(pnls) > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance)
        sharpe = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "total_trades": len(pnls),
        "net_pnl": sum(pnls),
        "win_rate": len(winners) / len(pnls) if pnls else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown": max_dd,
        "max_drawdown_pct": (max_dd / starting_equity) * 100 if starting_equity > 0 else 0.0,
        "sharpe": round(sharpe, 2),
        "avg_pnl": mean_pnl,
        "avg_winner": sum(winners) / len(winners) if winners else 0.0,
        "avg_loser": sum(losers) / len(losers) if losers else 0.0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v11/tests/test_replay_metrics.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add v11/replay/metrics.py v11/tests/test_replay_metrics.py
git commit -m "feat(replay): add metrics computation for replay results"
```

---

### Task 5: ReplayRunner

The core component. Assembles existing live components and drives them with historical data.

**Files:**
- Create: `v11/replay/replay_runner.py`
- Test: `v11/tests/test_replay_runner.py`

**Dependencies:** Tasks 1-4 must be complete.

- [ ] **Step 1: Write failing integration test**

```python
# v11/tests/test_replay_runner.py
"""Integration tests for ReplayRunner — full pipeline on synthetic data."""
import asyncio
from datetime import datetime, timedelta

import pytest

from v11.core.types import Bar
from v11.replay.config import ReplayConfig
from v11.replay.replay_runner import ReplayRunner


def _make_bars(n=600, start_price=1.1000, start_time=None):
    """Generate n synthetic 1-min bars with a consolidation pattern.

    Creates bars with slight variation to exercise strategy engines
    without necessarily triggering a full Darvas box (that depends
    on parameter tuning). The goal is pipeline smoke-testing.
    """
    if start_time is None:
        # Monday 8:00 UTC (London session)
        start_time = datetime(2025, 1, 6, 8, 0, 0)

    bars = []
    price = start_price
    for i in range(n):
        ts = start_time + timedelta(minutes=i)
        # Oscillate around start_price with small moves
        delta = 0.0001 * ((i % 7) - 3)
        price = start_price + delta

        bars.append(Bar(
            timestamp=ts,
            open=price - 0.00005,
            high=price + 0.0002,
            low=price - 0.0003,
            close=price,
            tick_count=50,
            buy_volume=60.0 if i % 3 == 0 else 40.0,
            sell_volume=40.0 if i % 3 == 0 else 50.0,
        ))
    return bars


class TestReplayRunnerBasic:
    def test_replay_completes_without_error(self, tmp_path):
        """Smoke test: replay processes bars without crashing."""
        config = ReplayConfig(
            instruments=["EURUSD"],
            start_date="2025-01-06",
            end_date="2025-01-06",
            llm_mode="passthrough",
            output_dir=str(tmp_path / "results"),
            event_verbosity="quiet",
        )
        runner = ReplayRunner(config)
        bars_by_instrument = {"EURUSD": _make_bars(600)}

        result = asyncio.get_event_loop().run_until_complete(
            runner.run(bars_by_instrument))

        assert result["bars_processed"]["EURUSD"] > 0
        assert "error" not in result

    def test_replay_produces_event_log(self, tmp_path):
        """Event log file is created with at least one event."""
        config = ReplayConfig(
            instruments=["EURUSD"],
            start_date="2025-01-06",
            end_date="2025-01-06",
            llm_mode="passthrough",
            output_dir=str(tmp_path / "results"),
            event_verbosity="quiet",
        )
        runner = ReplayRunner(config)
        bars_by_instrument = {"EURUSD": _make_bars(600)}

        asyncio.get_event_loop().run_until_complete(
            runner.run(bars_by_instrument))

        event_file = tmp_path / "results" / "replay_events.jsonl"
        assert event_file.exists()
        content = event_file.read_text()
        assert len(content.strip()) > 0

    def test_replay_seeds_initial_bars(self, tmp_path):
        """First seed_bars are used for seeding, not replay."""
        config = ReplayConfig(
            instruments=["EURUSD"],
            start_date="2025-01-06",
            end_date="2025-01-06",
            llm_mode="passthrough",
            output_dir=str(tmp_path / "results"),
            event_verbosity="quiet",
            seed_bars=100,
        )
        runner = ReplayRunner(config)
        all_bars = _make_bars(200)
        bars_by_instrument = {"EURUSD": all_bars}

        result = asyncio.get_event_loop().run_until_complete(
            runner.run(bars_by_instrument))

        # 200 total bars, 100 seeded = 100 replayed
        assert result["bars_processed"]["EURUSD"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest v11/tests/test_replay_runner.py -v`
Expected: ImportError

- [ ] **Step 3: Implement ReplayRunner**

```python
# v11/replay/replay_runner.py
"""ReplayRunner — Feed historical bars through live strategy engines.

Reuses InstrumentEngine, LevelRetestEngine, RiskManager, TradeManager
exactly as the live system does. Replaces IBKR with stub connection
and BarAggregator with direct bar injection.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..core.types import Bar
from ..config.strategy_config import StrategyConfig, EURUSD_CONFIG, USDJPY_CONFIG, XAUUSD_CONFIG
from ..config.live_config import (
    LiveConfig, InstrumentConfig,
    EURUSD_INSTRUMENT, USDJPY_INSTRUMENT, XAUUSD_INSTRUMENT,
)
from ..execution.trade_manager import TradeManager
from ..live.live_engine import InstrumentEngine
from ..live.level_retest_engine import LevelRetestEngine
from ..live.risk_manager import RiskManager
from ..llm.passthrough_filter import PassthroughFilter

from .config import ReplayConfig
from .stub_connection import StubIBKRConnection
from .cached_filter import CachedFilter
from .event_logger import EventLogger
from .metrics import compute_metrics

log = logging.getLogger("v11_replay")

# Map instrument names to configs
STRATEGY_CONFIGS = {
    "EURUSD": EURUSD_CONFIG,
    "USDJPY": USDJPY_CONFIG,
    "XAUUSD": XAUUSD_CONFIG,
}

INSTRUMENT_CONFIGS = {
    "EURUSD": EURUSD_INSTRUMENT,
    "USDJPY": USDJPY_INSTRUMENT,
    "XAUUSD": XAUUSD_INSTRUMENT,
}


class ReplayRunner:
    """Replays historical bars through the live strategy pipeline.

    Usage:
        runner = ReplayRunner(config)
        result = await runner.run(bars_by_instrument)
    """

    def __init__(self, config: ReplayConfig):
        self._config = config
        config.validate()

        # Output directory
        self._output_dir = Path(config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Stub connection (TradeManager never uses it in dry_run)
        self._conn = StubIBKRConnection()

        # LLM filter
        self._llm_filter = self._build_llm_filter()

        # Live config (controls thresholds, buffer sizes, etc.)
        self._live_config = LiveConfig(
            dry_run=True,
            llm_confidence_threshold=config.llm_confidence_threshold,
            max_daily_trades=config.max_daily_trades,
            max_daily_loss=config.max_daily_loss,
            max_concurrent_positions=config.max_concurrent_positions,
        )

        # Risk manager
        self._risk_manager = RiskManager(
            max_daily_loss=config.max_daily_loss,
            max_daily_trades_per_strategy=config.max_daily_trades,
            max_concurrent_positions=config.max_concurrent_positions,
            log=log,
        )

        # Event logger
        self._event_logger = EventLogger(
            output_path=str(self._output_dir / "replay_events.jsonl"),
            verbosity=config.event_verbosity,
        )

        # Per-instrument engines (built in run())
        self._engines: Dict[str, List] = {}  # pair -> [engine, ...]
        self._trade_managers: Dict[str, TradeManager] = {}

    def _build_llm_filter(self):
        """Build LLM filter based on config mode."""
        if self._config.llm_mode == "passthrough":
            return PassthroughFilter()

        if self._config.llm_mode == "cached":
            inner = None
            if self._config.grok_api_key:
                from ..llm.grok_filter import GrokFilter
                inner = GrokFilter(
                    api_key=self._config.grok_api_key,
                    model=self._config.grok_model,
                )
            return CachedFilter(
                inner_filter=inner,
                cache_path=self._config.llm_cache_path,
            )

        if self._config.llm_mode == "live":
            from ..llm.grok_filter import GrokFilter
            return GrokFilter(
                api_key=self._config.grok_api_key,
                model=self._config.grok_model,
            )

        return PassthroughFilter()

    def _build_engines(self, instrument: str) -> List:
        """Create Darvas + LevelRetest engines for one instrument."""
        strategy_config = STRATEGY_CONFIGS.get(instrument)
        inst_config = INSTRUMENT_CONFIGS.get(instrument)
        if strategy_config is None or inst_config is None:
            raise ValueError(f"No config for instrument: {instrument}")

        trade_manager = TradeManager(
            conn=self._conn,
            inst=inst_config,
            log=log,
            trade_log_dir=self._output_dir / "trades",
            dry_run=True,
        )
        self._trade_managers[instrument] = trade_manager

        darvas = InstrumentEngine(
            strategy_config=strategy_config,
            inst_config=inst_config,
            llm_filter=self._llm_filter,
            trade_manager=trade_manager,
            live_config=self._live_config,
            log=log,
        )
        darvas.strategy_name = "Darvas_Breakout"
        darvas._risk_check = self._risk_manager.can_trade

        retest = LevelRetestEngine(
            strategy_config=strategy_config,
            inst_config=inst_config,
            llm_filter=self._llm_filter,
            trade_manager=trade_manager,
            live_config=self._live_config,
            log=log,
        )
        retest._risk_check = self._risk_manager.can_trade

        return [darvas, retest]

    async def run(self, bars_by_instrument: Dict[str, List[Bar]]) -> dict:
        """Run the full replay.

        Args:
            bars_by_instrument: {"EURUSD": [Bar, ...], "USDJPY": [Bar, ...]}

        Returns:
            Summary dict with bars_processed, trade_count, metrics, etc.
        """
        result = {"bars_processed": {}, "trades": [], "metrics": {}}

        # Build engines for each instrument
        for instrument in self._config.instruments:
            if instrument not in bars_by_instrument:
                log.warning(f"No bars provided for {instrument}, skipping")
                continue
            self._engines[instrument] = self._build_engines(instrument)

        # Process each instrument
        for instrument, bars in bars_by_instrument.items():
            if instrument not in self._engines:
                continue

            engines = self._engines[instrument]
            seed_count = min(self._config.seed_bars, len(bars))
            replay_bars = bars[seed_count:]

            log.info(f"Replay {instrument}: {len(bars)} total bars, "
                     f"{seed_count} seeded, {len(replay_bars)} replayed")

            # Seed historical bars
            for bar in bars[:seed_count]:
                for engine in engines:
                    engine.add_historical_bar(bar)

            self._event_logger.emit(
                "SESSION_START", strategy="ALL", instrument=instrument,
                timestamp=replay_bars[0].timestamp.isoformat() if replay_bars else "",
                data={"total_bars": len(bars), "seed_bars": seed_count,
                      "replay_bars": len(replay_bars)},
            )

            # Track current date for daily resets and previous bar for gap detection
            current_date: Optional[str] = None
            prev_bar: Optional[Bar] = None

            # Replay loop
            # NOTE (Review #2): Detectors are NOT reset between session gaps.
            # This matches live behavior where the system runs continuously.
            # If stale state across weekends causes bugs, that's exactly what
            # we want the replay to discover. Gaps are logged for observability.
            for i, bar in enumerate(replay_bars):
                bar_date = bar.timestamp.strftime("%Y-%m-%d")

                # Session gap detection (Review #2: log but don't reset)
                if prev_bar is not None:
                    gap_minutes = (bar.timestamp - prev_bar.timestamp).total_seconds() / 60
                    if gap_minutes > 30:
                        self._event_logger.emit(
                            "SESSION_GAP", strategy="ALL", instrument=instrument,
                            timestamp=bar.timestamp.isoformat(),
                            data={"gap_minutes": round(gap_minutes, 1),
                                  "prev_bar": prev_bar.timestamp.isoformat()},
                        )
                prev_bar = bar

                # Daily reset on date change
                if current_date is not None and bar_date != current_date:
                    self._risk_manager.reset_daily()
                    tm = self._trade_managers[instrument]
                    tm.reset_daily()
                    self._event_logger.emit(
                        "DAILY_RESET", strategy="ALL", instrument=instrument,
                        timestamp=bar.timestamp.isoformat(), data={},
                    )
                current_date = bar_date

                # Feed price to engines (slippage tracking)
                for engine in engines:
                    engine.on_price(bar.close, bar.timestamp)

                # Process the bar through all engines
                for engine in engines:
                    tm = self._trade_managers[instrument]
                    was_in_trade = tm.in_trade
                    pnl_before = tm.daily_pnl
                    trades_before = tm.daily_trades

                    await engine.on_bar(bar)

                    # Detect trade entry (Review #5: state transition check)
                    if not was_in_trade and tm.in_trade:
                        self._event_logger.emit(
                            "TRADE_ENTERED", strategy=engine.strategy_name,
                            instrument=instrument,
                            timestamp=bar.timestamp.isoformat(),
                            data={
                                "direction": tm.direction.value if tm.direction else "?",
                                "entry_price": tm.signal_entry_price,
                                "stop_price": tm.stop_price,
                                "target_price": tm.target_price,
                                "llm_confidence": tm.llm_confidence,
                            },
                        )

                    # Detect trade exit (Review #5: dual check — counter + state)
                    # Primary: daily_trades counter incremented (reliable)
                    # Secondary: was_in_trade but no longer (catches edge cases)
                    if tm.daily_trades > trades_before or (was_in_trade and not tm.in_trade):
                        pnl_delta = tm.daily_pnl - pnl_before
                        self._event_logger.emit(
                            "TRADE_EXITED", strategy=engine.strategy_name,
                            instrument=instrument,
                            timestamp=bar.timestamp.isoformat(),
                            data={
                                "instrument": instrument,
                                "strategy": engine.strategy_name,
                                "pnl": pnl_delta,
                                "exit_reason": "check_exit",
                                "hold_bars": 0,
                                "llm_confidence": 0,
                            },
                        )

                # Progress logging every 10000 bars
                if (i + 1) % 10000 == 0:
                    log.info(f"Replay {instrument}: {i + 1}/{len(replay_bars)} bars")

            result["bars_processed"][instrument] = len(replay_bars)

        # Save LLM cache if applicable
        if isinstance(self._llm_filter, CachedFilter):
            self._llm_filter.save()

        # Compute metrics from trade records
        result["event_counts"] = self._event_logger.get_counts()
        result["trade_records"] = self._event_logger.trade_records
        result["metrics"] = compute_metrics(self._event_logger.trade_records)

        # Write summary
        self._write_summary(result)
        self._event_logger.close()

        return result

    def _write_summary(self, result: dict) -> None:
        """Write human-readable summary file."""
        summary_path = self._output_dir / "replay_summary.txt"
        m = result["metrics"]
        counts = result.get("event_counts", {})

        lines = [
            f"Replay: {', '.join(self._config.instruments)} "
            f"{self._config.start_date} to {self._config.end_date} "
            f"(LLM: {self._config.llm_mode})",
            "",
        ]

        for inst, n in result.get("bars_processed", {}).items():
            lines.append(f"  {inst}: {n} bars replayed")
        lines.append("")

        lines.append(f"Events: {dict(counts)}")
        lines.append("")

        lines.append("Metrics:")
        lines.append(f"  Total trades: {m['total_trades']}")
        lines.append(f"  Net PnL: ${m['net_pnl']:+.2f}")
        lines.append(f"  Win rate: {m['win_rate']:.1%}")
        lines.append(f"  Profit factor: {m['profit_factor']:.2f}")
        lines.append(f"  Sharpe: {m['sharpe']:.2f}")
        lines.append(f"  Max drawdown: ${m['max_drawdown']:.2f} ({m['max_drawdown_pct']:.1f}%)")
        lines.append(f"  Avg winner: ${m['avg_winner']:+.2f}")
        lines.append(f"  Avg loser: ${m['avg_loser']:+.2f}")

        summary_path.write_text("\n".join(lines))
        log.info(f"Summary written to {summary_path}")
        print("\n" + "\n".join(lines))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest v11/tests/test_replay_runner.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest v11/tests/ -v --tb=short`
Expected: All existing tests still pass, all new tests pass

- [ ] **Step 6: Commit**

```bash
git add v11/replay/replay_runner.py v11/tests/test_replay_runner.py
git commit -m "feat(replay): add ReplayRunner — main replay loop with engine assembly"
```

---

### Task 6: CLI Entry Point

**Files:**
- Create: `v11/replay/run_replay.py`

- [ ] **Step 1: Implement the CLI**

```python
# v11/replay/run_replay.py
"""CLI entry point for historical replay.

Usage:
    python -m v11.replay.run_replay --instrument EURUSD --start 2025-01-01 --end 2025-03-31
    python -m v11.replay.run_replay --instrument EURUSD USDJPY --start 2025-01-01 --end 2025-03-31 --llm cached
"""
from __future__ import annotations

import asyncio
import argparse
import logging
import sys
from datetime import datetime

# ── Python 3.14 compatibility (Review #1) ───────────────────────────────────
# Same patch as v11/live/run_live.py lines 27-68.
# Python 3.14 changed asyncio.wait_for to use asyncio.timeout() internally,
# which requires being inside a running task. ib_insync calls wait_for from
# a sync context via nest_asyncio, which doesn't set current_task properly.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

if sys.version_info >= (3, 14):
    _original_wait_for = asyncio.wait_for

    async def _compat_wait_for(fut, timeout, **kwargs):
        if timeout is None:
            return await fut
        fut = asyncio.ensure_future(fut)
        loop = asyncio.get_event_loop()
        timed_out = False

        def _on_timeout():
            nonlocal timed_out
            timed_out = True
            fut.cancel()

        handle = loop.call_later(timeout, _on_timeout)
        try:
            return await fut
        except asyncio.CancelledError:
            if timed_out:
                raise asyncio.TimeoutError()
            raise
        finally:
            handle.cancel()

    asyncio.wait_for = _compat_wait_for

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass
# ── End Python 3.14 compatibility ───────────────────────────────────────────

from ..backtest.data_loader import load_instrument_bars
from .config import ReplayConfig
from .replay_runner import ReplayRunner


def setup_logging(verbosity: str) -> None:
    level = logging.DEBUG if verbosity == "verbose" else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy loggers
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay historical bars through V11 live strategy code")

    p.add_argument("--instrument", nargs="+", required=True,
                   help="Instruments to replay (e.g. EURUSD USDJPY)")
    p.add_argument("--start", required=True,
                   help="Start date (YYYY-MM-DD)")
    p.add_argument("--end", required=True,
                   help="End date (YYYY-MM-DD)")
    p.add_argument("--llm", default="passthrough",
                   choices=["passthrough", "live", "cached"],
                   help="LLM filter mode (default: passthrough)")
    p.add_argument("--grok-key", default="",
                   help="Grok API key (required if --llm is live or cached)")
    p.add_argument("--verbosity", default="normal",
                   choices=["quiet", "normal", "verbose"],
                   help="Console output verbosity")
    p.add_argument("--output-dir", default="v11/replay/results",
                   help="Output directory for results")
    p.add_argument("--seed-bars", type=int, default=500,
                   help="Bars to seed before replay starts")
    p.add_argument("--confidence", type=int, default=75,
                   help="LLM confidence threshold")

    return p.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbosity)
    log = logging.getLogger("v11_replay")

    config = ReplayConfig(
        instruments=[i.upper() for i in args.instrument],
        start_date=args.start,
        end_date=args.end,
        llm_mode=args.llm,
        grok_api_key=args.grok_key,
        llm_confidence_threshold=args.confidence,
        output_dir=args.output_dir,
        event_verbosity=args.verbosity,
        seed_bars=args.seed_bars,
    )

    print(f"Replay: {', '.join(config.instruments)} "
          f"from {config.start_date} to {config.end_date} "
          f"(LLM: {config.llm_mode})")
    print()

    # Load historical data
    bars_by_instrument = {}
    for instrument in config.instruments:
        start_dt = datetime.strptime(config.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(config.end_date, "%Y-%m-%d")

        log.info(f"Loading {instrument} bars from {config.start_date} to {config.end_date}...")
        bars = load_instrument_bars(instrument, start=start_dt, end=end_dt)
        log.info(f"Loaded {len(bars)} bars for {instrument}")

        if len(bars) < config.seed_bars + 100:
            log.error(f"{instrument}: only {len(bars)} bars, need at least "
                      f"{config.seed_bars + 100} (seed + 100 replay). Skipping.")
            continue

        bars_by_instrument[instrument] = bars

    if not bars_by_instrument:
        log.error("No instruments with sufficient data. Exiting.")
        sys.exit(1)

    # Run replay
    runner = ReplayRunner(config)
    result = asyncio.get_event_loop().run_until_complete(
        runner.run(bars_by_instrument))

    # Print final stats
    m = result.get("metrics", {})
    print(f"\nDone. {m.get('total_trades', 0)} trades, "
          f"PnL=${m.get('net_pnl', 0):+.2f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs with --help**

Run: `python -m v11.replay.run_replay --help`
Expected: Shows usage with all arguments listed

- [ ] **Step 3: Commit**

```bash
git add v11/replay/run_replay.py
git commit -m "feat(replay): add CLI entry point run_replay.py"
```

---

### Task 7: Integration Test with Real Data

Manual verification against actual historical data.

**Dependencies:** Tasks 1-6 must be complete.

- [ ] **Step 1: Run a 1-week replay on EURUSD (passthrough)**

```bash
python -m v11.replay.run_replay --instrument EURUSD --start 2025-01-06 --end 2025-01-10 --llm passthrough --verbosity verbose
```

Expected: Completes without errors. Check `v11/replay/results/`:
- `replay_events.jsonl` — SESSION_START events, possibly BOX_FORMED / BREAKOUT_DETECTED
- `replay_summary.txt` — bars processed count
- Console shows progress and events

- [ ] **Step 2: Run a 3-month replay to get actual trades**

```bash
python -m v11.replay.run_replay --instrument EURUSD --start 2025-01-01 --end 2025-03-31 --llm passthrough --verbosity normal
```

Expected: Completes without errors. Should produce some trades. Check:
- Trade CSV in `v11/replay/results/trades/trades_eurusd.csv`
- Event log has TRADE_ENTERED and TRADE_EXITED events
- Summary shows trade count, win rate, PnL

- [ ] **Step 3: Verify event log against expectations**

Open `replay_events.jsonl` and spot-check:
- Session starts at reasonable times
- Boxes form with sensible top/bottom prices
- Breakouts occur after boxes
- Trade entries have SL below entry (for longs)

- [ ] **Step 4: Run multi-instrument replay**

```bash
python -m v11.replay.run_replay --instrument EURUSD USDJPY --start 2025-01-01 --end 2025-03-31 --llm passthrough --verbosity normal
```

Expected: Both instruments process, risk manager tracks combined positions.

- [ ] **Step 5: Add results gitignore and commit**

```bash
echo "*" > v11/replay/results/.gitignore
echo "!.gitignore" >> v11/replay/results/.gitignore
git add v11/replay/results/.gitignore
git commit -m "chore(replay): gitignore results directory"
```

---

### Task 8: Run Full Test Suite and Final Commit

- [ ] **Step 1: Run all tests**

```bash
python -m pytest v11/tests/ -v --tb=short
```

Expected: All existing tests pass + all new replay tests pass.

- [ ] **Step 2: Verify no production code was modified**

```bash
git diff HEAD~8 -- v11/core/ v11/execution/ v11/live/ v11/config/ v11/llm/
```

Expected: Empty diff — zero changes to production code. All new code is in `v11/replay/` and `v11/tests/`.
