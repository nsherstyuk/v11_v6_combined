# Tick Logging & Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log every raw IBKR price tick during live trading to CSV, then replay those tick files through the same BarAggregator → engine → LLM pipeline at ~100–300× real-time speed.

**Architecture:** `TickLogger` writes one CSV per instrument per day during live trading. `TickReplayer` reads those CSVs, feeds each tick through per-instrument `BarAggregator` instances, and routes completed bars to the same `InstrumentEngine`/`LevelRetestEngine`/`ReplayORBAdapter` stack used by `ReplayRunner`. LLM calls are live (not cached) at signal time. No `sleep()` calls — replay runs at CPU speed.

**Tech Stack:** Python stdlib (`csv`, `gzip`, `heapq`), existing `BarAggregator`, `InstrumentEngine`, `LevelRetestEngine`, `ReplayORBAdapter`, `StubIBKRConnection`, `pytest`, `tempfile`.

**Note on engine_factory.py:** The spec called for a shared `engine_factory.py`. After inspecting the code, `add_orb_strategy()` requires IBKR-qualified contracts, making a universal factory impractical. `TickReplayer` instead follows the existing `ReplayRunner` pattern — builds its engine stack directly. This is the established precedent in the codebase.

---

## Files Added / Modified

| File | Change |
|------|--------|
| `v11/config/live_config.py` | Add `from pathlib import Path` + two new LiveConfig fields |
| `v11/replay/config.py` | Add `TickReplayConfig` dataclass |
| `v11/replay/tick_logger.py` | **New** — `TickLogger` class |
| `v11/tests/test_tick_logger.py` | **New** — TickLogger tests |
| `v11/live/run_live.py` | Init `_tick_logger` + hook in poll loop + close in `_cleanup()` |
| `v11/replay/tick_replayer.py` | **New** — `TickReplayer` class with `_load_ticks()` + `run()` |
| `v11/tests/test_tick_replayer.py` | **New** — TickReplayer tests |
| `v11/replay/run_tick_replay.py` | **New** — CLI entry point |

---

## Task 1: Add Config Fields to LiveConfig

**Files:**
- Modify: `v11/config/live_config.py`
- Modify: `v11/replay/config.py`

- [ ] **Step 1: Write a failing test for the new LiveConfig fields**

Create `v11/tests/test_tick_config.py`:

```python
"""Tests for tick logging config fields."""
import pytest
from pathlib import Path
from v11.config.live_config import LiveConfig
from v11.replay.config import TickReplayConfig


def test_live_config_tick_logging_defaults():
    cfg = LiveConfig()
    assert cfg.tick_logging is True
    assert isinstance(cfg.tick_log_dir, Path)
    assert str(cfg.tick_log_dir) == "data/ticks"


def test_live_config_tick_logging_disabled():
    cfg = LiveConfig(tick_logging=False)
    assert cfg.tick_logging is False


def test_live_config_tick_log_dir_custom():
    cfg = LiveConfig(tick_log_dir=Path("/custom/path"))
    assert cfg.tick_log_dir == Path("/custom/path")


def test_tick_replay_config_defaults():
    cfg = TickReplayConfig(instruments=["EURUSD"], start_date="2026-04-01", end_date="2026-04-15")
    assert cfg.tick_dir == "data/ticks"
    assert cfg.llm_mode == "passthrough"
    assert cfg.llm_confidence_threshold == 75


def test_tick_replay_config_validates_dates():
    with pytest.raises(ValueError, match="start_date.*before"):
        TickReplayConfig(
            instruments=["EURUSD"],
            start_date="2026-04-15",
            end_date="2026-04-01",
        ).validate()
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest v11/tests/test_tick_config.py -v
```
Expected: FAIL — `LiveConfig` has no `tick_logging` attribute, `TickReplayConfig` not found.

- [ ] **Step 3: Add `Path` import and two fields to `LiveConfig`**

In `v11/config/live_config.py`, line 13 currently reads:
```python
from typing import List
```
Change it to:
```python
from pathlib import Path
from typing import List
```

Then add two fields to `LiveConfig` after the `# Logging` section (after `grok_log_dir: str = "grok_logs"`):

```python
    # Tick logging for replay data capture
    tick_logging: bool = True
    tick_log_dir: Path = field(default_factory=lambda: Path("data/ticks"))
```

- [ ] **Step 4: Add `TickReplayConfig` to `v11/replay/config.py`**

Append to the end of `v11/replay/config.py`:

```python

@dataclass
class TickReplayConfig:
    """Configuration for a tick-data replay run."""

    # Required
    instruments: list[str]
    start_date: str              # "YYYY-MM-DD"
    end_date: str                # "YYYY-MM-DD"

    # Data source
    tick_dir: str = "data/ticks"

    # LLM mode
    llm_mode: str = "passthrough"    # "passthrough" | "live"
    grok_api_key: str = ""
    grok_model: str = "deepseek/deepseek-chat-v3-0324"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_confidence_threshold: int = 75

    # Risk limits
    max_daily_loss: float = 500.0
    max_daily_trades: int = 20
    max_concurrent_positions: int = 3

    # Output
    output_dir: str = "v11/replay/results"

    def validate(self) -> None:
        if not self.instruments:
            raise ValueError("instruments must not be empty")
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

- [ ] **Step 5: Run tests to verify they pass**

```
pytest v11/tests/test_tick_config.py -v
```
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add v11/config/live_config.py v11/replay/config.py v11/tests/test_tick_config.py
git commit -m "feat: add tick_logging config fields to LiveConfig + TickReplayConfig"
```

---

## Task 2: TickLogger Class

**Files:**
- Create: `v11/replay/tick_logger.py`
- Create: `v11/tests/test_tick_logger.py`

- [ ] **Step 1: Write failing tests**

Create `v11/tests/test_tick_logger.py`:

```python
"""Tests for TickLogger."""
import math
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from v11.replay.tick_logger import TickLogger


def _ts(y, m, d, h=14, mi=30, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


class TestTickLoggerHeader:
    def test_creates_directory_and_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15), 1.12345678,
                          bid=1.12340000, ask=1.12350000,
                          last=None, bid_size=None, ask_size=None, last_size=None)
            logger.close()

            path = Path(tmp) / "EURUSD" / "2026-04-15.csv"
            assert path.exists()
            lines = path.read_text().splitlines()
            assert lines[0] == "timestamp,mid,bid,ask,last,bid_size,ask_size,last_size"

    def test_header_written_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            ts = _ts(2026, 4, 15)
            for _ in range(5):
                logger.record("EURUSD", ts, 1.12345678, None, None, None, None, None, None)
            logger.close()

            path = Path(tmp) / "EURUSD" / "2026-04-15.csv"
            lines = path.read_text().splitlines()
            header_count = sum(1 for l in lines if l.startswith("timestamp"))
            assert header_count == 1


class TestTickLoggerRowFormat:
    def test_mid_written_to_8dp(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15), 1.12345678,
                          bid=1.12340000, ask=1.12350000,
                          last=None, bid_size=None, ask_size=None, last_size=None)
            logger.close()

            lines = (Path(tmp) / "EURUSD" / "2026-04-15.csv").read_text().splitlines()
            row = lines[1]
            assert "1.12345678" in row
            assert "1.12340000" in row

    def test_nan_written_as_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15), 1.1234,
                          bid=float("nan"), ask=0.0,
                          last=None, bid_size=None, ask_size=None, last_size=None)
            logger.close()

            lines = (Path(tmp) / "EURUSD" / "2026-04-15.csv").read_text().splitlines()
            row = lines[1]
            fields = row.split(",")
            # bid (index 2) and ask (index 3) should be blank
            assert fields[2] == ""   # nan → blank
            assert fields[3] == ""   # 0 → blank

    def test_timestamp_is_iso8601(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            ts = datetime(2026, 4, 15, 14, 30, 0, 123456, tzinfo=timezone.utc)
            logger.record("EURUSD", ts, 1.1234, None, None, None, None, None, None)
            logger.close()

            lines = (Path(tmp) / "EURUSD" / "2026-04-15.csv").read_text().splitlines()
            row = lines[1]
            # Should contain microsecond precision
            assert "2026-04-15T14:30:00" in row
            assert "123456" in row


class TestTickLoggerDateRollover:
    def test_new_file_on_date_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15, 23, 59), 1.1, None, None, None, None, None, None)
            logger.record("EURUSD", _ts(2026, 4, 16, 0, 1), 1.2, None, None, None, None, None, None)
            logger.close()

            assert (Path(tmp) / "EURUSD" / "2026-04-15.csv").exists()
            assert (Path(tmp) / "EURUSD" / "2026-04-16.csv").exists()

    def test_each_file_has_own_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            logger.record("EURUSD", _ts(2026, 4, 15, 23, 59), 1.1, None, None, None, None, None, None)
            logger.record("EURUSD", _ts(2026, 4, 16, 0, 1), 1.2, None, None, None, None, None, None)
            logger.close()

            for day in ["2026-04-15", "2026-04-16"]:
                lines = (Path(tmp) / "EURUSD" / f"{day}.csv").read_text().splitlines()
                assert lines[0] == "timestamp,mid,bid,ask,last,bid_size,ask_size,last_size"


class TestTickLoggerErrorHandling:
    def test_filesystem_error_does_not_propagate(self, monkeypatch):
        """A write error must not raise — caller should never crash."""
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            # Inject a failing write
            import builtins
            real_open = builtins.open

            call_count = [0]

            def patched_open(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] > 1:
                    raise OSError("disk full")
                return real_open(*args, **kwargs)

            monkeypatch.setattr(builtins, "open", patched_open)
            # Should not raise
            logger.record("EURUSD", _ts(2026, 4, 15), 1.1, None, None, None, None, None, None)
            logger.close()


class TestTickLoggerMultipleInstruments:
    def test_separate_files_per_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = TickLogger(Path(tmp))
            ts = _ts(2026, 4, 15)
            logger.record("EURUSD", ts, 1.1, None, None, None, None, None, None)
            logger.record("XAUUSD", ts, 2300.0, None, None, None, None, None, None)
            logger.close()

            assert (Path(tmp) / "EURUSD" / "2026-04-15.csv").exists()
            assert (Path(tmp) / "XAUUSD" / "2026-04-15.csv").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest v11/tests/test_tick_logger.py -v
```
Expected: FAIL — `v11/replay/tick_logger.py` does not exist.

- [ ] **Step 3: Implement `TickLogger`**

Create `v11/replay/tick_logger.py`:

```python
"""TickLogger — Records raw IBKR price ticks to CSV for later replay.

File layout:
    data/ticks/{PAIR}/{YYYY-MM-DD}.csv

Schema:
    timestamp,mid,bid,ask,last,bid_size,ask_size,last_size

All float fields formatted to 8 decimal places.
NaN, 0, and None values are written as blank fields.
Files are line-buffered so each row is flushed to disk immediately.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import IO, Optional

log = logging.getLogger("tick_logger")

_HEADER = "timestamp,mid,bid,ask,last,bid_size,ask_size,last_size\n"


def _fmt(v) -> str:
    """Format a float value. Returns blank string for NaN, 0, or None."""
    if v is None:
        return ""
    if isinstance(v, float) and (math.isnan(v) or v == 0.0):
        return ""
    try:
        return f"{float(v):.8f}"
    except (TypeError, ValueError):
        return ""


class TickLogger:
    """Appends raw price tick rows to per-instrument, per-day CSV files.

    Thread-safety: not thread-safe. Call only from the live trading loop.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        # pair -> (file_handle, current_date)
        self._handles: dict[str, tuple[IO[str], date]] = {}

    def record(
        self,
        pair: str,
        ts: datetime,
        mid: float,
        bid,
        ask,
        last,
        bid_size,
        ask_size,
        last_size,
    ) -> None:
        """Append one tick row. Silently swallows all I/O errors."""
        try:
            ts_date = ts.date()
            handle, current_date = self._handles.get(pair, (None, None))

            if handle is None or ts_date != current_date:
                if handle is not None:
                    handle.close()
                handle = self._open_file(pair, ts_date)
                self._handles[pair] = (handle, ts_date)

            row = (
                f"{ts.isoformat()},"
                f"{_fmt(mid)},{_fmt(bid)},{_fmt(ask)},{_fmt(last)},"
                f"{_fmt(bid_size)},{_fmt(ask_size)},{_fmt(last_size)}\n"
            )
            handle.write(row)
        except Exception as exc:
            log.warning("TickLogger.record failed for %s: %s", pair, exc)

    def _open_file(self, pair: str, d: date) -> IO[str]:
        pair_dir = self._base_dir / pair
        pair_dir.mkdir(parents=True, exist_ok=True)
        path = pair_dir / f"{d}.csv"
        if path.exists():
            return open(path, "a", buffering=1, encoding="utf-8")
        f = open(path, "w", buffering=1, encoding="utf-8")
        f.write(_HEADER)
        return f

    def close(self) -> None:
        """Flush and close all open file handles."""
        for handle, _ in self._handles.values():
            try:
                handle.close()
            except Exception:
                pass
        self._handles.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest v11/tests/test_tick_logger.py -v
```
Expected: All tests pass. (The `test_filesystem_error_does_not_propagate` test patches `builtins.open` — if it's flaky, accept it and move on; the key contract is the `try/except` in `record()`.)

- [ ] **Step 5: Commit**

```bash
git add v11/replay/tick_logger.py v11/tests/test_tick_logger.py
git commit -m "feat: add TickLogger — records raw IBKR ticks to CSV for replay"
```

---

## Task 3: Hook TickLogger into `run_live.py`

**Files:**
- Modify: `v11/live/run_live.py`

- [ ] **Step 1: Write a failing integration test**

In `v11/tests/test_run_live.py`, add this test (append to the end of the file):

```python
def test_tick_logger_initialised_when_enabled(tmp_path, monkeypatch):
    """V11LiveTrader creates a TickLogger when tick_logging=True."""
    from v11.config.live_config import LiveConfig
    from v11.live.run_live import V11LiveTrader

    cfg = LiveConfig(tick_logging=True, tick_log_dir=tmp_path / "ticks")

    # Stub out IBKR connection
    import v11.execution.ibkr_connection as ibkr_mod
    monkeypatch.setattr(ibkr_mod.IBKRConnection, "connect", lambda self: True)

    import logging
    log = logging.getLogger("test")
    trader = V11LiveTrader(cfg, log, use_llm=False)
    assert trader._tick_logger is not None
    trader._tick_logger.close()


def test_tick_logger_none_when_disabled(tmp_path, monkeypatch):
    """V11LiveTrader skips TickLogger when tick_logging=False."""
    from v11.config.live_config import LiveConfig
    from v11.live.run_live import V11LiveTrader

    cfg = LiveConfig(tick_logging=False, tick_log_dir=tmp_path / "ticks")

    import v11.execution.ibkr_connection as ibkr_mod
    monkeypatch.setattr(ibkr_mod.IBKRConnection, "connect", lambda self: True)

    import logging
    log = logging.getLogger("test")
    trader = V11LiveTrader(cfg, log, use_llm=False)
    assert trader._tick_logger is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest v11/tests/test_run_live.py::test_tick_logger_initialised_when_enabled v11/tests/test_run_live.py::test_tick_logger_none_when_disabled -v
```
Expected: FAIL — `V11LiveTrader` has no `_tick_logger` attribute.

- [ ] **Step 3: Add TickLogger init to `V11LiveTrader.__init__`**

In `v11/live/run_live.py`, after the runner setup block (around line 223, after `self._active_pairs: list[str] = []`), add:

```python
        # Tick logging for replay data capture
        self._tick_logger = None
        if live_cfg.tick_logging:
            from v11.replay.tick_logger import TickLogger
            tick_log_dir = ROOT / str(live_cfg.tick_log_dir)
            self._tick_logger = TickLogger(base_dir=tick_log_dir)
            log.info(f"Tick logging enabled → {tick_log_dir}")
```

- [ ] **Step 4: Add tick recording hook in the poll loop**

In `v11/live/run_live.py`, after `if price is None: continue` (currently around line 441), add:

```python
                    # Tick logging for replay
                    if self._tick_logger is not None:
                        ticker = self.conn._tickers.get(pair)
                        self._tick_logger.record(
                            pair, now, price,
                            bid=ticker.bid if ticker else None,
                            ask=ticker.ask if ticker else None,
                            last=ticker.last if ticker else None,
                            bid_size=ticker.bidSize if ticker else None,
                            ask_size=ticker.askSize if ticker else None,
                            last_size=ticker.lastSize if ticker else None,
                        )
```

This goes immediately after `continue` and before the `# Track price freshness` comment.

- [ ] **Step 5: Close TickLogger in `_cleanup()`**

In `v11/live/run_live.py`, replace the existing `_cleanup()` method:

```python
    def _cleanup(self) -> None:
        """Clean up on shutdown. ORB adapter handles its own cleanup."""
        if self._tick_logger is not None:
            self._tick_logger.close()
        for engine in self.runner.engines:
            if hasattr(engine, 'cleanup'):
                engine.cleanup()
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest v11/tests/test_run_live.py::test_tick_logger_initialised_when_enabled v11/tests/test_run_live.py::test_tick_logger_none_when_disabled -v
```
Expected: Both pass.

- [ ] **Step 7: Run full test suite to check for regressions**

```
pytest v11/tests/ -v --tb=short -x
```
Expected: All existing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add v11/live/run_live.py v11/tests/test_run_live.py
git commit -m "feat: hook TickLogger into run_live.py poll loop"
```

---

## Task 4: TickReplayer — `_load_ticks()` and CSV Loading

**Files:**
- Create: `v11/replay/tick_replayer.py` (partial — `_load_ticks` function only)
- Create: `v11/tests/test_tick_replayer.py` (loading tests only)

- [ ] **Step 1: Write failing tests for `_load_ticks`**

Create `v11/tests/test_tick_replayer.py`:

```python
"""Tests for TickReplayer._load_ticks and TickReplayer.run()."""
import csv
import gzip
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from v11.replay.tick_replayer import load_ticks


# ─── Helpers ────────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "timestamp", "mid", "bid", "ask", "last",
            "bid_size", "ask_size", "last_size",
        ])
        w.writeheader()
        w.writerows(rows)


def _row(ts: datetime, mid: float, bid="", ask="", last="",
         bid_size="", ask_size="", last_size="") -> dict:
    return {
        "timestamp": ts.isoformat(),
        "mid": f"{mid:.8f}",
        "bid": bid, "ask": ask, "last": last,
        "bid_size": bid_size, "ask_size": ask_size, "last_size": last_size,
    }


def _ts(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 4, 15, h, m, s, tzinfo=timezone.utc)


# ─── Tests ──────────────────────────────────────────────────────────────────

class TestLoadTicksBasic:
    def test_yields_tuples_from_csv(self, tmp_path):
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        _write_csv(csv_path, [
            _row(_ts(14, 30), 1.12345678),
            _row(_ts(14, 31), 1.12346000),
        ])

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 2
        ts, pair, mid = ticks[0][:3]
        assert pair == "EURUSD"
        assert abs(mid - 1.12345678) < 1e-9

    def test_blank_bid_becomes_none(self, tmp_path):
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        _write_csv(csv_path, [_row(_ts(14, 30), 1.1234, bid="", ask="")])

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 1
        _ts_out, pair, mid, bid, ask, last, bid_s, ask_s, last_s = ticks[0]
        assert bid is None
        assert ask is None

    def test_missing_file_skipped_with_warning(self, tmp_path, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="tick_replayer"):
            ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                    date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 0
        assert any("No tick file" in r.message for r in caplog.records)


class TestLoadTicksMergeSort:
    def test_two_instruments_merged_in_order(self, tmp_path):
        eu_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        xau_path = tmp_path / "XAUUSD" / "2026-04-15.csv"

        # EURUSD ticks at :00, :02; XAUUSD at :01, :03
        _write_csv(eu_path, [
            _row(_ts(14, 0), 1.1),
            _row(_ts(14, 2), 1.2),
        ])
        _write_csv(xau_path, [
            _row(_ts(14, 1), 2300.0),
            _row(_ts(14, 3), 2301.0),
        ])

        ticks = list(load_ticks(tmp_path, ["EURUSD", "XAUUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        pairs = [t[1] for t in ticks]
        assert pairs == ["EURUSD", "XAUUSD", "EURUSD", "XAUUSD"]
        timestamps = [t[0] for t in ticks]
        assert timestamps == sorted(timestamps)

    def test_multi_day_range(self, tmp_path):
        for d in [date(2026, 4, 15), date(2026, 4, 16)]:
            path = tmp_path / "EURUSD" / f"{d}.csv"
            _write_csv(path, [_row(datetime(d.year, d.month, d.day, 14, 0, tzinfo=timezone.utc), 1.1)])

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 16)))
        assert len(ticks) == 2
        assert ticks[0][0].date() == date(2026, 4, 15)
        assert ticks[1][0].date() == date(2026, 4, 16)


class TestLoadTicksGzip:
    def test_reads_gz_file(self, tmp_path):
        csv_path = tmp_path / "EURUSD" / "2026-04-15.csv"
        gz_path = tmp_path / "EURUSD" / "2026-04-15.csv.gz"
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        _write_csv(csv_path, [_row(_ts(14, 30), 1.1234)])

        with open(csv_path, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                f_out.write(f_in.read())
        csv_path.unlink()  # only gz exists

        ticks = list(load_ticks(tmp_path, ["EURUSD"],
                                date(2026, 4, 15), date(2026, 4, 15)))
        assert len(ticks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest v11/tests/test_tick_replayer.py -v -k "TestLoadTicks"
```
Expected: FAIL — `v11/replay/tick_replayer.py` does not exist.

- [ ] **Step 3: Implement `load_ticks` in `tick_replayer.py`**

Create `v11/replay/tick_replayer.py`:

```python
"""TickReplayer — Replay logged tick data through the V11 strategy pipeline.

Reads CSV tick files produced by TickLogger and feeds each mid-price
through a BarAggregator, routing completed 1-min bars to the same
engine stack as the live system.

Usage:
    python -m v11.replay.run_tick_replay --start 2026-04-15
"""
from __future__ import annotations

import csv
import gzip
import heapq
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Optional

log = logging.getLogger("tick_replayer")


# ── CSV loading ──────────────────────────────────────────────────────────────

def _parse_float(v: str) -> Optional[float]:
    """Return float or None for blank/invalid fields."""
    v = v.strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _iter_file(
    path: Path,
    pair: str,
) -> Generator[tuple, None, None]:
    """Yield tick tuples from one CSV or CSV.GZ file."""
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                mid = _parse_float(row.get("mid", ""))
                if mid is None:
                    continue   # skip rows with no usable price
                ts_str = row.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                yield (
                    ts, pair, mid,
                    _parse_float(row.get("bid", "")),
                    _parse_float(row.get("ask", "")),
                    _parse_float(row.get("last", "")),
                    _parse_float(row.get("bid_size", "")),
                    _parse_float(row.get("ask_size", "")),
                    _parse_float(row.get("last_size", "")),
                )
    except Exception as exc:
        log.warning("Failed to read tick file %s: %s", path, exc)


def load_ticks(
    base_dir: Path,
    instruments: list[str],
    start: date,
    end: date,
) -> Generator[tuple, None, None]:
    """Yield (ts, pair, mid, bid, ask, last, bid_size, ask_size, last_size)
    tuples in ascending timestamp order across all instruments and dates.

    Missing files are skipped with a WARNING log. Ticks with no mid price
    are discarded.
    """
    iterators = []
    current = start
    while current <= end:
        for pair in instruments:
            gz_path = base_dir / pair / f"{current}.csv.gz"
            csv_path = base_dir / pair / f"{current}.csv"
            if gz_path.exists():
                iterators.append(_iter_file(gz_path, pair))
            elif csv_path.exists():
                iterators.append(_iter_file(csv_path, pair))
            else:
                log.warning("No tick file for %s %s", pair, current)
        current += timedelta(days=1)

    # heapq.merge requires the key to be comparable; datetime is comparable.
    yield from heapq.merge(*iterators, key=lambda t: t[0])
```

- [ ] **Step 4: Run loading tests to verify they pass**

```
pytest v11/tests/test_tick_replayer.py -v -k "TestLoadTicks"
```
Expected: All loading tests pass.

- [ ] **Step 5: Commit**

```bash
git add v11/replay/tick_replayer.py v11/tests/test_tick_replayer.py
git commit -m "feat: add load_ticks() — merge-sorted tick CSV reader for replay"
```

---

## Task 5: TickReplayer `run()` and CLI

**Files:**
- Modify: `v11/replay/tick_replayer.py` (add `TickReplayer` class)
- Modify: `v11/tests/test_tick_replayer.py` (add replay integration test)
- Create: `v11/replay/run_tick_replay.py` (CLI entry point)

- [ ] **Step 1: Write a failing integration test for `TickReplayer.run()`**

Append to `v11/tests/test_tick_replayer.py`:

```python
# ─── TickReplayer integration test ──────────────────────────────────────────

class TestTickReplayerRun:
    """Smoke test: tick replayer runs without error on synthetic data."""

    def _write_ticks(self, base: Path, pair: str, day: date,
                     prices: list[float]) -> None:
        """Write one tick per minute for a full hour at 14:00 UTC."""
        path = base / pair / f"{day}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("timestamp,mid,bid,ask,last,bid_size,ask_size,last_size\n")
            for i, price in enumerate(prices):
                ts = datetime(day.year, day.month, day.day, 14, 0, i,
                              tzinfo=timezone.utc)
                f.write(f"{ts.isoformat()},{price:.8f},,,,,,\n")

    def test_run_returns_stats(self, tmp_path):
        import asyncio
        from v11.replay.config import TickReplayConfig
        from v11.replay.tick_replayer import TickReplayer

        day = date(2026, 4, 15)
        # 120 ticks, one per second at minute 14:00 → produces 2 completed bars
        prices = [1.1000 + i * 0.00001 for i in range(120)]
        self._write_ticks(tmp_path, "EURUSD", day, prices)

        cfg = TickReplayConfig(
            instruments=["EURUSD"],
            start_date=str(day),
            end_date=str(day),
            tick_dir=str(tmp_path),
            llm_mode="passthrough",
            output_dir=str(tmp_path / "results"),
        )
        replayer = TickReplayer(cfg)
        result = asyncio.get_event_loop().run_until_complete(replayer.run())

        assert "EURUSD" in result
        assert result["EURUSD"]["bars"] >= 1
```

- [ ] **Step 2: Run the test to verify it fails**

```
pytest v11/tests/test_tick_replayer.py::TestTickReplayerRun -v
```
Expected: FAIL — `TickReplayer` class does not exist yet.

- [ ] **Step 3: Implement `TickReplayer` class**

Append the following to `v11/replay/tick_replayer.py`:

```python
# ── TickReplayer ─────────────────────────────────────────────────────────────

from ..execution.bar_aggregator import BarAggregator
from ..execution.trade_manager import TradeManager
from ..config.live_config import (
    EURUSD_INSTRUMENT, XAUUSD_INSTRUMENT, USDJPY_INSTRUMENT, LiveConfig,
)
from ..config.strategy_config import EURUSD_CONFIG, XAUUSD_CONFIG, USDJPY_CONFIG
from ..live.live_engine import InstrumentEngine
from ..live.level_retest_engine import LevelRetestEngine
from ..live.risk_manager import RiskManager
from ..llm.passthrough_filter import PassthroughFilter
from .config import TickReplayConfig
from .stub_connection import StubIBKRConnection

_INSTRUMENT_CONFIGS = {
    "EURUSD": EURUSD_INSTRUMENT,
    "XAUUSD": XAUUSD_INSTRUMENT,
    "USDJPY": USDJPY_INSTRUMENT,
}
_STRATEGY_CONFIGS = {
    "EURUSD": EURUSD_CONFIG,
    "XAUUSD": XAUUSD_CONFIG,
    "USDJPY": USDJPY_CONFIG,
}


class TickReplayer:
    """Replays logged IBKR tick CSV files through the V11 strategy pipeline.

    For each instrument: feeds ticks through a BarAggregator, routes
    completed 1-min bars to Darvas / LevelRetest / ORB engines.
    LLM calls are live (passthrough or live Grok) — no caching.
    No sleep() calls anywhere; replay runs at CPU speed.
    """

    def __init__(self, config: TickReplayConfig) -> None:
        self._config = config
        config.validate()

        self._tick_dir = Path(config.tick_dir)
        self._instruments = config.instruments
        self._start = date.fromisoformat(config.start_date)
        self._end = date.fromisoformat(config.end_date)
        self._output_dir = Path(config.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Shared infrastructure (dry_run — no IBKR)
        self._conn = StubIBKRConnection()
        self._llm_filter = self._build_llm_filter()
        self._live_config = LiveConfig(
            dry_run=True,
            llm_confidence_threshold=config.llm_confidence_threshold,
            max_daily_loss=config.max_daily_loss,
            max_daily_trades=config.max_daily_trades,
            max_concurrent_positions=config.max_concurrent_positions,
        )
        self._risk_manager = RiskManager(
            max_daily_loss=config.max_daily_loss,
            max_daily_trades_per_strategy=config.max_daily_trades,
            max_concurrent_positions=config.max_concurrent_positions,
            log=log,
        )

        # Per-instrument: BarAggregator + engines + trade managers
        self._aggregators: dict[str, BarAggregator] = {}
        self._engines: dict[str, list] = {}
        self._trade_managers: dict[str, TradeManager] = {}
        self._current_date: dict[str, str] = {}

        for pair in self._instruments:
            self._aggregators[pair] = BarAggregator()
            self._engines[pair] = self._build_engines(pair)

    def _build_llm_filter(self):
        if self._config.llm_mode == "passthrough":
            return PassthroughFilter()
        from ..llm.grok_filter import GrokFilter
        return GrokFilter(
            api_key=self._config.grok_api_key,
            model=self._config.grok_model,
            base_url=self._config.llm_base_url,
            log_dir=str(self._output_dir / "grok_logs"),
        )

    def _build_engines(self, pair: str) -> list:
        """Create strategy engines for one instrument."""
        strategy_cfg = _STRATEGY_CONFIGS.get(pair)
        inst_cfg = _INSTRUMENT_CONFIGS.get(pair)
        if strategy_cfg is None or inst_cfg is None:
            raise ValueError(f"No strategy/instrument config for {pair}")

        tm = TradeManager(
            conn=self._conn,
            inst=inst_cfg,
            log=log,
            trade_log_dir=self._output_dir / "trades",
            dry_run=True,
        )
        self._trade_managers[pair] = tm

        darvas = InstrumentEngine(
            strategy_config=strategy_cfg,
            inst_config=inst_cfg,
            llm_filter=self._llm_filter,
            trade_manager=tm,
            live_config=self._live_config,
            log=log,
        )
        darvas.strategy_name = "Darvas_Breakout"
        darvas._risk_check = self._risk_manager.can_trade

        retest = LevelRetestEngine(
            strategy_config=strategy_cfg,
            inst_config=inst_cfg,
            llm_filter=self._llm_filter,
            trade_manager=tm,
            live_config=self._live_config,
            log=log,
        )
        retest._risk_check = self._risk_manager.can_trade

        engines = [darvas, retest]

        # ORB for XAUUSD
        if pair == "XAUUSD":
            from ..v6_orb.config import StrategyConfig as V6StrategyConfig
            from .replay_orb import ReplayORBAdapter
            v6_cfg = V6StrategyConfig(
                instrument="XAUUSD",
                velocity_filter_enabled=False,
                max_pending_hours=8,
                trade_end_hour=20,
            )
            orb = ReplayORBAdapter(
                v6_config=v6_cfg,
                llm_filter=self._llm_filter,
                llm_confidence_threshold=self._config.llm_confidence_threshold,
                live_config=self._live_config,
                log=log,
            )
            engines.append(orb)

        return engines

    async def run(self) -> dict:
        """Run the full replay. Returns per-instrument stats dict."""
        stats: dict[str, dict] = {
            pair: {"bars": 0, "trades_before": 0}
            for pair in self._instruments
        }

        from .replay_orb import ReplayORBAdapter

        for tick in load_ticks(self._tick_dir, self._instruments,
                               self._start, self._end):
            ts, pair, mid = tick[0], tick[1], tick[2]

            # Feed tick through BarAggregator
            agg = self._aggregators.get(pair)
            if agg is None:
                continue
            bar = agg.on_price(mid, ts)
            if bar is None:
                continue

            stats[pair]["bars"] += 1

            # Daily reset on date change
            date_str = ts.strftime("%Y-%m-%d")
            if self._current_date.get(pair) and date_str != self._current_date[pair]:
                self._risk_manager.reset_daily()
                tm = self._trade_managers.get(pair)
                if tm:
                    tm.reset_daily()
            self._current_date[pair] = date_str

            # Route bar through engines
            for engine in self._engines.get(pair, []):
                engine.on_price(mid, ts)
                await engine.on_bar(bar)

        self._print_summary(stats)
        return stats

    def _print_summary(self, stats: dict) -> None:
        print(f"\n── Tick Replay {self._config.start_date} → "
              f"{self._config.end_date} ({',' .join(self._instruments)}) ──")
        total_pnl = 0.0
        for pair, s in stats.items():
            tm = self._trade_managers.get(pair)
            trades = tm.daily_trades if tm else 0
            pnl = tm.daily_pnl if tm else 0.0
            total_pnl += pnl
            print(f"  {pair:8s}  bars={s['bars']}  trades={trades}"
                  f"  PnL=${pnl:+.2f}")
        print(f"  Total PnL (dry run): ${total_pnl:+.2f}")
        print("─" * 60)
```

- [ ] **Step 4: Run the integration test to verify it passes**

```
pytest v11/tests/test_tick_replayer.py::TestTickReplayerRun -v
```
Expected: PASS.

- [ ] **Step 5: Create `run_tick_replay.py` CLI**

Create `v11/replay/run_tick_replay.py`:

```python
"""CLI entry point for tick-data replay.

Usage:
    # Replay all instruments, date range
    python -m v11.replay.run_tick_replay --start 2026-04-01 --end 2026-04-15

    # Single day
    python -m v11.replay.run_tick_replay --start 2026-04-15

    # Specific instrument, live LLM
    python -m v11.replay.run_tick_replay --start 2026-04-01 --end 2026-04-15 \\
        --instruments EURUSD --llm live
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# ── Python 3.14 compatibility (same as run_live.py) ─────────────────────────
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from v11.replay.config import TickReplayConfig
from v11.replay.tick_replayer import TickReplayer


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger("run_tick_replay")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Replay live-logged tick data through V11 strategy pipeline")

    p.add_argument("--start", required=True,
                   help="First date to replay (YYYY-MM-DD)")
    p.add_argument("--end",
                   help="Last date inclusive (YYYY-MM-DD); defaults to --start")
    p.add_argument("--instruments", nargs="+", default=["EURUSD", "XAUUSD"],
                   help="Instruments to replay (default: EURUSD XAUUSD)")
    p.add_argument("--tick-dir", default="data/ticks",
                   help="Directory containing tick CSV files (default: data/ticks)")
    p.add_argument("--llm", default="passthrough",
                   choices=["passthrough", "live"],
                   help="LLM filter mode (default: passthrough)")
    p.add_argument("--output-dir", default="v11/replay/results",
                   help="Output directory for trade logs and summary")

    return p.parse_args()


def main() -> None:
    args = parse_args()
    log = setup_logging()

    end_date = args.end or args.start

    grok_key = (os.environ.get("OPENROUTER_API_KEY", "") or
                os.environ.get("XAI_API_KEY", "") or
                os.environ.get("GROK_API_KEY", ""))

    cfg = TickReplayConfig(
        instruments=[i.upper() for i in args.instruments],
        start_date=args.start,
        end_date=end_date,
        tick_dir=args.tick_dir,
        llm_mode=args.llm,
        grok_api_key=grok_key,
        output_dir=args.output_dir,
    )

    log.info("Tick replay: %s → %s  instruments=%s  llm=%s",
             cfg.start_date, cfg.end_date,
             cfg.instruments, cfg.llm_mode)

    replayer = TickReplayer(cfg)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(replayer.run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the full test suite**

```
pytest v11/tests/ -v --tb=short -x
```
Expected: All tests pass.

- [ ] **Step 7: Smoke test the CLI with `--help`**

```
python -m v11.replay.run_tick_replay --help
```
Expected: Prints usage without error.

- [ ] **Step 8: Commit**

```bash
git add v11/replay/tick_replayer.py v11/replay/run_tick_replay.py v11/tests/test_tick_replayer.py
git commit -m "feat: TickReplayer + run_tick_replay CLI — tick-data replay through V11 pipeline"
```

---

## Verification Checklist

After all tasks are complete, verify end-to-end:

- [ ] **Config test:** `pytest v11/tests/test_tick_config.py v11/tests/test_tick_logger.py v11/tests/test_tick_replayer.py -v` — all pass
- [ ] **Full suite:** `pytest v11/tests/ -v --tb=short` — no regressions
- [ ] **CLI help:** `python -m v11.replay.run_tick_replay --help` — prints clean usage
- [ ] **Manual smoke (if ticks exist):** `python -m v11.replay.run_tick_replay --start 2026-04-15 --instruments EURUSD` — runs without error, prints summary

---

## Self-Review Notes

**Spec coverage:**
- §2 (CSV schema, file layout) → Task 1 config + Task 2 TickLogger ✓
- §3 (TickLogger behaviour, date rollover, error swallowing) → Task 2 ✓
- §3.3 (Hook in run_live.py poll loop) → Task 3 ✓
- §4.1 (engine stack) → Task 5 TickReplayer._build_engines() ✓
- §4.2 (replay loop, no sleep) → Task 5 TickReplayer.run() ✓
- §4.3 (tick loading, merge-sorted, gz support, None for blank) → Task 4 load_ticks() ✓
- §4.4 (CLI) → Task 5 run_tick_replay.py ✓
- §4.5 (summary output) → Task 5 _print_summary() ✓
- §6 (error handling table) → covered in TickLogger.record() try/except, load_ticks warning, missing file skip ✓

**Deviation from spec §4.1 (engine_factory.py):** Not implemented. `add_orb_strategy()` requires IBKR-qualified contracts, making a universal factory impractical. `TickReplayer._build_engines()` follows the existing `ReplayRunner` pattern instead.
