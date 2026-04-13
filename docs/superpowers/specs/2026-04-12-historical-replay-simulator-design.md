# Historical Replay Simulator — Design Spec

**Date:** 2026-04-12
**Status:** Approved
**Scope:** Darvas + 4H Level Retest strategies on EURUSD/USDJPY (ORB excluded)

## Problem

The v11 live trading system ran 16 sessions over Apr 6-11 with:
- 7 sessions crashing in <5 minutes (IBKR connection failures)
- Only 2 trades executed (both losers)
- 11,311 errors in the longest session
- LLM async context bugs
- No way to tell whether strategies are working correctly vs infrastructure failing

There is no way to verify the full live code path (signal generation + LLM filter + risk manager + trade management) without running against real IBKR data for days/weeks. The existing backtester in `v11/backtest/simulator.py` only tests Darvas with mechanical SL/TP — it skips the LLM, risk manager, multi-strategy orchestration, and all live infrastructure.

## Solution

A replay simulator that feeds historical 1-min bars through the **actual live code** (not a separate backtest implementation), replacing only the data source (CSV instead of IBKR) and trade execution (simulated fills instead of real orders).

### Goals

1. **Correctness verification** — Replay known data, read event logs, verify every box/signal/trade step-by-step
2. **Performance measurement** — Full metrics with all components active (LLM + risk manager + multi-strategy)
3. **Stability testing** — Run months of data without crashes before committing to live
4. **LLM evaluation** — Compare passthrough vs real Grok on identical data

### Non-goals

- ORB (XAUUSD) replay — excluded due to tick-driven architecture mismatch with 1-min bars
- Sub-minute tick simulation — historical data is 1-min bars, intra-bar behavior not simulated
- Live IBKR connection testing — this tests strategy logic, not network resilience

## Architecture

```
Historical CSV (data_loader.py)
    |
    v
List[Bar] --> ReplayRunner
                |
                |-- MultiStrategyRunner (existing, unmodified)
                |     |-- InstrumentEngine (Darvas)
                |     +-- LevelRetestEngine (4H Retest)
                |
                |-- RiskManager (existing, unmodified)
                |
                |-- LLM Filter (configurable mode)
                |     |-- mode=passthrough -> PassthroughFilter
                |     |-- mode=live       -> GrokFilter (real API)
                |     +-- mode=cached     -> CachedFilter (record/replay)
                |
                |-- MockIBKRConnection (simulated fills, no network)
                |
                +-- EventLogger + TradeLog + Metrics
```

### Principle: Reuse live code, don't fork it

The entire point is to verify the live code works. These components are used **as-is, unmodified**:

- `InstrumentEngine` (Darvas signal pipeline)
- `LevelRetestEngine` (4H Retest signal pipeline)
- `DarvasDetector` (box formation state machine)
- `RetestDetector` (break-pullback-rebreak state machine)
- `ImbalanceClassifier` (volume analysis)
- `IncrementalHTFSMAFilter` (60-min SMA direction filter)
- `IncrementalSwingLevelDetector` (4H level detection)
- `RiskManager` (daily loss limits, trade limits)
- `PassthroughFilter` (mechanical LLM bypass)
- `GrokFilter` (real LLM calls, when mode=live or mode=cached first-run)

The only things replaced are I/O boundaries: data source and order execution.

## Components

### 1. ReplayRunner (new: `v11/replay/replay_runner.py`)

Main orchestration loop. Responsibilities:
- Load historical bars via `data_loader.load_instrument_bars()`
- Split by sessions via `data_loader.split_by_sessions()`
- Feed bars sequentially to strategy engines via `on_bar(bar)`
- Simulate time progression (each bar's timestamp = current time)
- Trigger daily resets when date changes (UTC midnight)
- Feed `on_price(bar.close, bar.timestamp)` before each `on_bar()` to keep price tracking in sync
- Seed initial historical bars to strategies via `add_historical_bar()` (first 500 bars of each session, matching live startup)
- Collect and report results

The replay loop is async (matching live code's async architecture) but runs without real delays — `await` calls complete immediately against mocks.

### 2. MockIBKRConnection (new: `v11/replay/mock_ibkr.py`)

Implements the same interface that `TradeManager` calls on `IBKRConnection`:
- `submit_market_order()` — records the order, simulates fill at next bar's open + configurable slippage
- `submit_stop_order()` — records SL order, triggers when price crosses stop level
- `cancel_order()` — removes pending order
- `get_position_size()` — returns tracked simulated position
- `get_mid_price()` — returns current bar's close price

Does NOT implement streaming, contract qualification, or reconnection logic (not needed for replay).

**Fill simulation:**
- Market orders: fill at next bar's open price + slippage (configurable, default 0.5 pips for FX)
- Stop orders: fill at stop price when bar's high/low crosses the level (conservative: checks high for sells, low for buys)
- Commission: configurable per instrument (default matches IBKR FX rates)

### 3. TradeManager modification (minor: `v11/execution/trade_manager.py`)

Current `TradeManager` takes an `IBKRConnection` in its constructor. The modification:
- Extract the connection interface methods it actually calls into a Protocol/ABC
- Both `IBKRConnection` and `MockIBKRConnection` satisfy this protocol
- TradeManager code unchanged — just the type hint widens

If this is too invasive, alternative: `MockIBKRConnection` duck-types the same methods. Python's duck typing means `TradeManager` works without any code change as long as the mock has the same method signatures.

**Decision: Use duck typing (no TradeManager changes) unless testing reveals issues.** This is the minimal-change approach.

### 4. CachedFilter (new: `v11/replay/cached_filter.py`)

Wraps `GrokFilter` with a JSON cache layer:
- **Cache key:** SHA-256 hash of `SignalContext.model_dump_json()` (deterministic)
- **Cache storage:** Single JSON file (`replay_llm_cache.json`) mapping hash → `FilterDecision` dict
- **On evaluate_signal():**
  - If hash in cache: return stored decision (instant, free)
  - If hash not in cache and mode=`cached`: call real GrokFilter, store response, return it
  - If hash not in cache and mode=`cached-strict`: return passthrough (no API call)
- **Cache is append-only** — grows over runs, never deletes entries
- Implements `LLMFilter` protocol (same as GrokFilter and PassthroughFilter)

### 5. EventLogger (new: `v11/replay/event_logger.py`)

Structured event logging for correctness verification:
- Emits events as JSON lines to `replay_events.jsonl`
- Also prints key events to console (configurable verbosity)
- Event types:
  - `SESSION_START` / `SESSION_END` — session boundaries from data gaps
  - `BOX_FORMED` — Darvas box confirmed (top, bottom, duration)
  - `BREAKOUT_DETECTED` — price broke above/below box
  - `LEVEL_DETECTED` — 4H swing level identified
  - `RETEST_SIGNAL` — break-pullback-rebreak pattern completed
  - `SMA_FILTER_REJECT` — signal rejected by HTF SMA direction
  - `VOLUME_REJECT` — signal rejected by volume divergence
  - `LLM_CALLED` — full request context sent to LLM
  - `LLM_RESPONSE` — full response including confidence, reasoning, risk flags
  - `LLM_CACHE_HIT` — response served from cache (with original timestamp)
  - `SIGNAL_APPROVED` / `SIGNAL_REJECTED` — final disposition after all filters
  - `TRADE_ENTERED` — entry price, SL, TP, strategy, instrument
  - `TRADE_EXITED` — exit price, PnL, hold time, exit reason
  - `DAILY_RESET` — date boundary crossed, counters reset
  - `RISK_LIMIT_HIT` — daily loss or trade count limit reached

**Integration approach:** The event logger is injected into strategy engines via a callback/hook mechanism. Rather than modifying engine code, the ReplayRunner wraps the existing logging (`logging.getLogger("v11_live")`) with a handler that captures structured events. The engines already log all key decisions — we parse and structure those log messages.

If log parsing proves fragile, fallback: add an optional `event_callback` parameter to `InstrumentEngine` and `LevelRetestEngine` constructors. Engines call it at each decision point. Live mode passes `None` (no overhead). Replay mode passes the EventLogger.

### 6. ReplayConfig (new: `v11/replay/config.py`)

```python
@dataclass
class ReplayConfig:
    # Date range
    start_date: str              # "2025-01-01"
    end_date: str                # "2025-03-31"

    # Instruments (Darvas + 4H Retest only, no ORB)
    instruments: list[str]       # ["EURUSD"] or ["EURUSD", "USDJPY"]

    # LLM mode
    llm_mode: str                # "passthrough" | "live" | "cached"
    grok_api_key: str = ""       # required if llm_mode != "passthrough"
    grok_model: str = "grok-4-1-fast-reasoning"
    llm_cache_path: str = "replay_llm_cache.json"

    # Execution simulation
    slippage_pips: float = 0.5   # market order slippage
    commission_per_lot: float = 2.0  # USD per standard lot round-trip

    # Risk manager
    max_daily_loss: float = 500.0
    max_daily_trades: int = 10

    # Output
    output_dir: str = "v11/replay/results"
    event_log_verbosity: str = "normal"  # "quiet" | "normal" | "verbose"

    # Strategy configs (use existing defaults)
    # Per-instrument StrategyConfig from v11/config/strategy_config.py
```

### 7. Entry point (new: `v11/replay/run_replay.py`)

CLI interface:
```bash
# Basic correctness check
python -m v11.replay.run_replay \
    --instrument EURUSD \
    --start 2025-01-01 --end 2025-03-31 \
    --llm passthrough

# With real Grok (records to cache on first run)
python -m v11.replay.run_replay \
    --instrument EURUSD \
    --start 2025-01-01 --end 2025-03-31 \
    --llm cached

# Multi-instrument
python -m v11.replay.run_replay \
    --instrument EURUSD USDJPY \
    --start 2025-01-01 --end 2025-03-31 \
    --llm passthrough

# Verbose event log for debugging a specific week
python -m v11.replay.run_replay \
    --instrument EURUSD \
    --start 2025-02-10 --end 2025-02-14 \
    --llm passthrough --verbosity verbose
```

## Output

A single replay run produces three files in the output directory:

### 1. `replay_events.jsonl`

One JSON object per line, every significant event:
```json
{"ts": "2025-01-15T14:32:00", "event": "BOX_FORMED", "strategy": "DARVAS", "instrument": "EURUSD", "data": {"top": 1.0892, "bottom": 1.0875, "duration_bars": 45, "width_atr": 1.2}}
{"ts": "2025-01-15T14:47:00", "event": "BREAKOUT_DETECTED", "strategy": "DARVAS", "instrument": "EURUSD", "data": {"direction": "long", "price": 1.0893, "box_top": 1.0892}}
{"ts": "2025-01-15T14:47:00", "event": "LLM_CALLED", "strategy": "DARVAS", "instrument": "EURUSD", "data": {"mode": "passthrough"}}
{"ts": "2025-01-15T14:47:00", "event": "SIGNAL_APPROVED", "strategy": "DARVAS", "instrument": "EURUSD", "data": {"confidence": 100, "entry": 1.0893, "sl": 1.0875, "tp": 1.0911}}
{"ts": "2025-01-15T14:47:00", "event": "TRADE_ENTERED", "strategy": "DARVAS", "instrument": "EURUSD", "data": {"direction": "long", "entry": 1.08935, "sl": 1.0875, "tp": 1.0911}}
```

### 2. `replay_trades.csv`

Same schema as live trade logs for direct comparison:
```
timestamp,instrument,strategy,direction,entry_price,exit_price,sl,tp,pnl,pnl_pips,hold_bars,exit_reason,llm_confidence,llm_reasoning
```

### 3. `replay_summary.txt`

Human-readable summary:
```
Replay: EURUSD 2025-01-01 to 2025-03-31 (LLM: passthrough)
Bars processed: 87,420
Sessions: 63
---
DARVAS:
  Signals generated: 24
  SMA filtered: 8
  Volume filtered: 3
  LLM approved: 13
  Trades entered: 13
  Win rate: 53.8%
  Profit factor: 1.82
  Avg hold: 47 bars
  
4H_RETEST:
  Levels detected: 156
  Retest signals: 11
  Trades entered: 7
  Win rate: 57.1%
  Profit factor: 2.10
  
Combined:
  Total trades: 20
  Net PnL: +$842
  Sharpe: 1.24
  Max drawdown: -$310 (-3.1%)
  Risk manager blocks: 0
```

## Testing Strategy

### Phase 1: Correctness (manual verification)

1. Run replay on 1 week of EURUSD data with `--llm passthrough --verbosity verbose`
2. Open the event log alongside a chart of that week
3. Verify: Do boxes form where expected? Do breakouts fire at the right price? Are SL/TP levels correct? Do exits trigger properly?
4. Compare trade log against what the existing `simulator.py` backtester produces for the same period — trade count and direction should match (PnL may differ slightly due to slippage simulation)

### Phase 2: Stability

1. Run replay on full 2024-2025 EURUSD (2 years) — should complete without errors
2. Run multi-instrument (EURUSD + USDJPY) — verify risk manager correctly tracks combined exposure
3. Run multiple date ranges and confirm deterministic output (same input = same output in passthrough mode)

### Phase 3: LLM integration

1. Run with `--llm cached` on 1 month — populates cache with real Grok responses
2. Re-run same period — verify cache hits produce identical results
3. Compare passthrough vs cached results: how many trades does the LLM filter out? Does filtering improve metrics?

## File Structure

```
v11/replay/
    __init__.py
    config.py           # ReplayConfig dataclass
    replay_runner.py     # Main ReplayRunner loop
    mock_ibkr.py         # MockIBKRConnection (simulated fills)
    cached_filter.py     # CachedFilter (record/replay LLM)
    event_logger.py      # Structured event logging
    run_replay.py        # CLI entry point
    results/             # Output directory (gitignored)
```

## Risks and Mitigations

**Risk: Strategy engines have hidden dependencies on IBKRConnection behavior.**
Mitigation: MockIBKRConnection duck-types the exact methods called. If something breaks, it surfaces immediately as an AttributeError — easy to fix.

**Risk: Event logging via log message parsing is fragile.**
Mitigation: Start with log parsing. If it breaks on edge cases, add explicit event callbacks to engines (small, contained change).

**Risk: Historical bar timestamps don't align perfectly with live session detection.**
Mitigation: Use the same `split_by_sessions()` function the backtester already uses. Session gaps in historical data are natural (weekends, holidays).

**Risk: Simulated fills don't match real IBKR fills.**
Mitigation: Conservative defaults (0.5 pip slippage, next-bar-open fill). This is standard practice and sufficient for validation. Exact fill simulation is a non-goal.
