# Tick Logging & Replay Design

**Date:** 2026-04-15  
**Status:** Approved  
**Scope:** Log raw IBKR price ticks during live trading; replay them through the identical V11 pipeline offline at accelerated speed with real LLM calls.

---

## 1. Goals

- Capture every price sample the live system receives from IBKR during a trading session.
- Store it in a format that allows exact replay: feed the logged ticks back through the same `BarAggregator` → `DarvasDetector` / `LevelRetestEngine` → LLM → `TradeManager` code path.
- Replay runs as fast as the CPU allows between signals; the only bottleneck is live Grok calls at signal time (expected ~100–300× real-time speedup on a typical day with 2–5 signals).
- LLM decisions are **not** stored — Grok is re-called during replay with the same prompts. This lets you iterate on LLM behaviour and test prompt changes against real historical data.

---

## 2. Data Format

### 2.1 File layout

```
data/ticks/{INSTRUMENT}/{YYYY-MM-DD}.csv
```

Examples:
```
data/ticks/EURUSD/2026-04-15.csv
data/ticks/XAUUSD/2026-04-15.csv
```

- One file per instrument per UTC calendar day.
- Files accumulate in place; old files are never modified.
- `data/ticks/` sits at the project root alongside source code — not under `v11/live/` — because tick files are long-lived data assets, not operational logs.

### 2.2 CSV schema

```
timestamp,mid,bid,ask,last,bid_size,ask_size,last_size
```

| Column | Type | Notes |
|--------|------|-------|
| `timestamp` | ISO-8601 UTC | Microsecond precision. e.g. `2026-04-15T14:30:00.123456+00:00` |
| `mid` | float | Computed `(bid + ask) / 2`; falls back to `ticker.close` if spread unavailable |
| `bid` | float | Best bid price. Blank if IBKR returns NaN or 0 |
| `ask` | float | Best ask price. Blank if IBKR returns NaN or 0 |
| `last` | float | Last traded price. Blank if unavailable |
| `bid_size` | float | Depth at best bid (millions of base currency for FX). Blank if unavailable |
| `ask_size` | float | Depth at best ask. Blank if unavailable |
| `last_size` | float | Size of last trade. Blank if unavailable |

All float fields formatted to 8 decimal places. Any IBKR field returning `NaN`, `0`, or `None` is written as an empty string (blank CSV field).

Header row written once when the file is created. The file is opened in append mode with `buffering=1` (line-buffered), so each row is flushed to disk immediately — a crash never produces a partial row.

### 2.3 Storage estimate

- Poll interval: 1 Hz (current `poll_interval = 1.0` in `run_live.py`)
- Rows per day per instrument: ~86,400
- Row size: ~90 bytes
- **~8 MB/day per instrument** uncompressed; ~1.5 MB gzipped
- Two instruments over 260 trading days/year: ~4 GB/year uncompressed

Old files can be gzipped for ~5× compression without any code changes — the replayer accepts both `.csv` and `.csv.gz`.

---

## 3. TickLogger

**File:** `v11/replay/tick_logger.py`

### 3.1 Interface

```python
class TickLogger:
    def __init__(self, base_dir: Path) -> None
    def record(self, pair: str, ts: datetime, mid: float,
               bid, ask, last, bid_size, ask_size, last_size) -> None
    def close(self) -> None
```

### 3.2 Behaviour

- Keeps one open file handle per instrument.
- On every `record()` call, checks whether `ts.date()` has rolled past the current file's date. If so, closes the current file and opens a new one (writing the header row).
- All writes are line-buffered (`buffering=1`). No explicit `flush()` calls needed.
- `close()` flushes and closes all open handles; called during live-session shutdown.
- The entire `record()` body is wrapped in `try/except`: a filesystem error logs a `WARNING` and does **not** propagate to the live trading loop.

### 3.3 Hook in `run_live.py`

Six lines added to the existing per-instrument poll loop, immediately after `get_mid_price()`:

```python
price = self.conn.get_mid_price(pair)   # existing
if price is None:                        # existing
    continue                             # existing

# Tick logging for replay
ticker = self.conn._tickers.get(pair)
if ticker and self._tick_logger:
    self._tick_logger.record(
        pair, now, price,
        bid=ticker.bid,       ask=ticker.ask,
        last=ticker.last,
        bid_size=ticker.bidSize, ask_size=ticker.askSize,
        last_size=ticker.lastSize,
    )
```

`self._tick_logger` is initialised in `V11LiveTrader.__init__` when `live_cfg.tick_logging` is `True`, and is `None` otherwise (zero overhead when disabled).

### 3.4 Configuration

Two new fields added to `LiveConfig`:

```python
tick_logging: bool = True
tick_log_dir: Path = Path("data/ticks")
```

---

## 4. TickReplayer

**Files:**
- `v11/replay/tick_replayer.py` — `TickReplayer` class
- `v11/replay/run_replay.py` — CLI entry point

### 4.1 Engine stack construction

`run_live.py` currently constructs the `MultiStrategyRunner` inline. This logic is extracted into a shared factory function:

```python
# v11/live/engine_factory.py  (new)
def build_engine_stack(live_cfg: LiveConfig, dry_run: bool) -> MultiStrategyRunner:
    ...
```

`run_live.py` calls `build_engine_stack(cfg, dry_run=False)` and then connects to IBKR as before.  
`run_replay.py` calls `build_engine_stack(cfg, dry_run=True)` and skips IBKR entirely.

With `dry_run=True`, `TradeManager` logs every entry and exit decision but never calls any IBKR method.

### 4.2 Replay loop

```python
async def run(self) -> None:
    for ts, pair, mid, *_ in self._load_ticks():   # merge-sorted across instruments
        bar = self.runner.on_price(pair, mid, ts)
        if bar:
            await self.runner.on_bar(pair, bar)    # LLM called here on signals
    self._print_summary()
```

- No `sleep()` calls anywhere in the replay path.
- Ticks from multiple instruments are merged and sorted by `timestamp` before iteration, preserving the interleaved order that would have occurred live.
- The `ts` from the file replaces wall-clock time throughout — `BarAggregator` uses it for bar boundary detection, producing identical bar shapes to the live run.

### 4.3 Tick loading

`_load_ticks()` is a generator that:
1. Enumerates all CSV (or `.csv.gz`) files for the requested instruments and date range.
2. Yields `(timestamp, pair, mid, bid, ask, last, bid_size, ask_size, last_size)` tuples in ascending timestamp order across all files.
3. Skips missing files with a logged warning rather than erroring.
4. Handles blank fields by substituting `None` (matching what `TickLogger` writes when IBKR returns NaN).

### 4.4 CLI

```bash
# Replay all instruments, date range
python -m v11.replay.run_replay --start 2026-04-01 --end 2026-04-15

# Single day
python -m v11.replay.run_replay --start 2026-04-15

# Specific instrument
python -m v11.replay.run_replay --start 2026-04-01 --end 2026-04-15 --instruments EURUSD
```

Arguments:
- `--start` (required): first date, `YYYY-MM-DD`
- `--end` (optional): last date inclusive, `YYYY-MM-DD`; defaults to `--start`
- `--instruments` (optional): one or more pair names; defaults to all configured instruments

### 4.5 End-of-replay summary

Printed to stdout after the last tick is processed:

```
── Replay 2026-04-01 → 2026-04-15 (EURUSD, XAUUSD) ────────
  EURUSD   bars=20160  signals=8  LLM approved=3  trades=3
  XAUUSD   bars=20160  signals=5  LLM approved=2  trades=2
  Total PnL (dry run):  +$341.20
─────────────────────────────────────────────────────────────
```

Replay also writes a standard log file to `v11/live/logs/` using the existing log setup so signal-level detail is preserved for post-analysis.

---

## 5. Files Added / Modified

| File | Change |
|------|--------|
| `v11/replay/tick_logger.py` | New — `TickLogger` class |
| `v11/replay/tick_replayer.py` | New — `TickReplayer` class |
| `v11/replay/run_replay.py` | New — CLI entry point |
| `v11/replay/__init__.py` | New — empty package marker |
| `v11/live/engine_factory.py` | New — `build_engine_stack()` extracted from `run_live.py` |
| `v11/live/run_live.py` | 6-line hook in poll loop; `TickLogger` init/close in startup/shutdown; delegate to `engine_factory` |
| `v11/config/live_config.py` | Add `tick_logging: bool = True` and `tick_log_dir: Path = Path("data/ticks")` |

---

## 6. Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Filesystem error during `record()` | `WARNING` logged; live session continues unaffected |
| IBKR field is NaN / 0 / None | Written as blank string; replayer substitutes `None` on read |
| Tick file missing for a date | Replayer logs `WARNING` and skips that day |
| Grok timeout during replay | Same retry logic as live (`LLMFilter` handles it) |
| Replay interrupted mid-run | No state to clean up; re-run from `--start` |

---

## 7. Out of Scope

- Storing LLM decisions (re-running Grok is the explicit choice).
- Changing bar size or BarAggregator parameters at replay time (ticks support this in principle but no UI is provided).
- A GUI or notebook interface for replay results.
- Automated comparison of replay trades vs. live trades.
