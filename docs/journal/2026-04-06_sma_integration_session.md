# Session Journal — 2026-04-06 (Phase 1: SMA Filter Integration)

**Time:** ~7:15 PM – 7:45 PM ET  
**Focus:** Build Phase 1 — Integrate 60-min SMA(50) direction filter into simulator and live engine  
**Next session should:** Start Phase 2 — Build 4H swing level detector module (`v11/core/level_detector.py`)

---

## What Happened This Session

### Risk Assessment (Before Coding)

| Element | Risk | Rationale |
|---|---|---|
| `StrategyConfig` | Medium | Near center (3+ modules depend on it), but adding fields with defaults doesn't break existing code |
| `simulator.py` `run_backtest()` | Low | Backtest only, no real money. Filter is additive (skips signals) |
| `live_engine.py` `InstrumentEngine` | Medium | Gates real trades. But it's a skip-gate (fails safe to no trade) |
| New `htf_sma_filter.py` | Low | Fresh module, nothing depends on it yet |

**Overall: Medium.** Filter is fail-safe: if SMA data unavailable → pass signal through, don't block.

### Implementation (COMPLETED)

Built three components for the SMA direction filter:

1. **`v11/core/htf_sma_filter.py`** — New module with two implementations:
   - `BatchHTFSMAFilter` — pre-computed SMA lookup for backtest (uses existing `htf_utils.py` batch functions)
   - `IncrementalHTFSMAFilter` — incremental SMA from live 1-min bar stream (accumulates bars, resamples at period boundaries, maintains rolling SMA)
   - `check_sma_alignment()` — shared pure function: LONG aligned when price > SMA, SHORT when price < SMA
   - Both implementations are fail-open: if SMA unavailable (insufficient history), signals pass through

2. **`v11/config/strategy_config.py`** — Added 3 config params:
   - `htf_sma_enabled: bool = True`
   - `htf_sma_bar_minutes: int = 60`
   - `htf_sma_period: int = 50`

3. **`v11/backtest/simulator.py`** — Modified `run_backtest()`:
   - Builds `BatchHTFSMAFilter` before session loop (if enabled)
   - At each signal, checks SMA alignment before simulating trade
   - Added `signals_filtered_sma` counter to `BacktestResult`

4. **`v11/live/live_engine.py`** — Modified `InstrumentEngine`:
   - Creates `IncrementalHTFSMAFilter` in `__init__` (if enabled)
   - Feeds every bar through the filter
   - Checks alignment in `_handle_signal()` before LLM call
   - Seeds filter during `add_historical_bar()` for warm-start
   - Added SMA status to `get_status()` diagnostics

### Testing (COMPLETED)

22 new tests in `v11/tests/test_htf_sma_filter.py`, following two-phase approach:

| Test Group | Count | What It Covers |
|---|---|---|
| Alignment logic | 6 | LONG > SMA, SHORT < SMA, exact equality |
| Look-ahead prevention | 1 | Uses previous completed HTF bar, not current |
| Fail-open | 4 | No data, insufficient history → signals pass through |
| Incremental matches batch | 1 | Live and backtest produce identical SMA values |
| HTF bar boundary | 4 | Period flooring, bar counting, multiple periods |
| SMA period requirement | 3 | None before period, available at exactly period, rolling forward |
| Disabled filter | 1 | Empty data → fail-open |
| Simulator integration | 2 | SMA enabled reduces signals; disabled → no filtering |

**All 73 tests pass (51 existing + 22 new). Zero regressions.**

### Bug Found and Fixed

`IncrementalHTFSMAFilter.htf_bars_count` initially used `len(self._closes)` which caps at `sma_period` because the deque has `maxlen`. Fixed by adding a dedicated `_total_htf_bars` counter. Caught by test before merge.

---

## Files Created This Session

| File | Purpose |
|---|---|
| `v11/core/htf_sma_filter.py` | BatchHTFSMAFilter + IncrementalHTFSMAFilter + check_sma_alignment() |
| `v11/tests/test_htf_sma_filter.py` | 22 tests covering all 7 design decisions |
| `docs/journal/2026-04-06_sma_integration_session.md` | This file |

## Files Modified This Session

| File | Change |
|---|---|
| `v11/config/strategy_config.py` | Added htf_sma_enabled, htf_sma_bar_minutes, htf_sma_period |
| `v11/backtest/simulator.py` | BatchHTFSMAFilter integration, signals_filtered_sma counter |
| `v11/live/live_engine.py` | IncrementalHTFSMAFilter integration, SMA check before LLM, diagnostics |
| `docs/PROJECT_STATUS.md` | Updated status, build table, roadmap Phase 1 → Complete, 73 tests |
| `docs/V11_DESIGN.md` | Updated Still Open: SMA integration marked complete |

---

## Design Decisions Made

None new — this session implemented the existing design from V11_DESIGN.md §10 without modification.

## Assumptions

1. **Fail-open is correct behavior** — when SMA data is unavailable (cold start), signals pass through rather than being blocked. Rationale: blocking all signals during warm-up period would miss valid trades.
2. **Backward compatibility preserved** — `htf_sma_enabled=True` by default, but existing pre-built configs (XAUUSD_CONFIG, EURUSD_CONFIG, USDJPY_CONFIG) inherit this default, which matches the design intent.
3. **BatchHTFSMAFilter reuses `htf_utils.py`** — the batch implementation delegates to the proven functions from the investigation scripts rather than duplicating the logic.

## What My Checks CANNOT Evaluate

1. **Real OOS performance impact** — I verified the filter is correctly wired and produces correct SMA values, but did not re-run the full backtest on real data to confirm the OOS improvement matches the investigation results.
2. **Live latency impact** — the incremental SMA adds O(1) work per bar, which should be negligible, but I did not measure actual latency.
3. **Cross-session SMA continuity** — if the live engine restarts mid-day, the SMA filter loses state. The `seed_bars()` method exists for warm-up but the live runner hasn't been tested with it.

---

## Build Roadmap Status

| Phase | Task | Status |
|---|---|---|
| 1 | Integrate SMA filter | **✅ Complete** |
| 2 | Build `level_detector.py` (4H swing levels) | 🔲 Next |
| 3 | Build `retest_detector.py` (break → pullback → rebreak) | 🔲 Pending |
| 4 | Build `MultiStrategyRunner` orchestrator | 🔲 Pending |
| 5 | Wire V6 ORB via adapter | 🔲 Pending |
| 6 | Write tests for new modules | 🔲 Pending |
| 7 | Paper trade | 🔲 Pending |
| 8 | Grok LLM enhancement | 🔲 Future |

---

## What NOT to Do Next Session

- **Don't re-run the full grid search** — SMA filter is already validated OOS from investigation sessions
- **Don't modify the SMA filter parameters** — 60-min SMA(50) is the proven config
- **Don't skip the level detector tests** — Phase 2 needs the same two-phase test approach
