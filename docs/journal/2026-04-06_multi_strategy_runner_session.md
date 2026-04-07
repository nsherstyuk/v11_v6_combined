# Session Journal — 2026-04-06 (Phase 4: MultiStrategyRunner)

**Time:** ~7:41 PM – 8:30 PM ET  
**Focus:** Build Phase 4 — MultiStrategyRunner orchestrator, RiskManager, LevelRetestEngine  
**Next session should:** Start Phase 5 — Wire V6 ORB into the runner via adapter

---

## What Happened This Session

### Risk Assessment (Before Coding)

| Element | Risk | Rationale |
|---|---|---|
| New `risk_manager.py` | Low | Fresh module, nothing depends on it |
| New `level_retest_engine.py` | Low | Fresh module, nothing depends on it |
| New `multi_strategy_runner.py` | Low | Fresh module, nothing depends on it |
| `live_engine.py` | Low | One additive change: `strategy_name` attribute |
| `trade_manager.py` (CENTER) | None | No modifications — shared between strategies as-is |
| `types.py` | None | No changes needed |
| `strategy_config.py` | None | All retest params already in place |

**Overall: Low.** All changes are additive — three new files, one attribute added to existing module. Zero modifications to center modules.

### Key Design Insight

Both EURUSD strategies (Darvas + 4H Level Retest) share the same `TradeManager` instance. When one strategy enters a trade, `TradeManager.in_trade=True` causes the other strategy's `on_bar()` to skip signal generation. This gives "max 1 position per instrument" for free without modifying the CENTER TradeManager module.

### Implementation (COMPLETED)

Built three layers:

1. **`v11/live/risk_manager.py`** — `RiskManager` class:
   - Combined daily loss limit across all strategies
   - Max concurrent positions (across all instruments)
   - Max 1 position per instrument (first signal wins)
   - Per-strategy daily trade count limit
   - `can_trade()` → (bool, reason) gate for strategies
   - `record_trade_entry/exit()` for position tracking
   - `reset_daily()` for market open reset

2. **`v11/live/level_retest_engine.py`** — `LevelRetestEngine` class:
   - Mirrors `InstrumentEngine` structure but uses level detector + retest detector
   - `IncrementalSwingLevelDetector` (4H levels from 1-min bars)
   - `RetestDetector` (break → pullback → rebreak state machine)
   - `IncrementalHTFSMAFilter` (60-min SMA direction filter)
   - `ImbalanceClassifier` (CONFIRMING volume filter)
   - Own ATR computation (EMA, same formula as DarvasDetector)
   - Structural SL/TP from V11_DESIGN.md §12 (level ± 0.3 ATR, R:R=2.0)
   - Synthetic BreakoutSignal adapter for TradeManager compatibility
   - Risk check callback wired by MultiStrategyRunner

3. **`v11/live/multi_strategy_runner.py`** — `MultiStrategyRunner` + `InstrumentFeed` classes:
   - `InstrumentFeed`: shared BarAggregator per instrument, routes bars to all strategies
   - `MultiStrategyRunner`: owns IBKRConnection, RiskManager, LLM filter
   - `add_darvas_strategy()` / `add_level_retest_strategy()` factory methods
   - Shared TradeManager per instrument (created once per feed)
   - `on_price()` → `on_bar()` pipeline with feed routing
   - `seed_historical()` reaches all strategies on an instrument
   - `reset_daily()` resets risk manager + all trade managers
   - `get_all_status()` comprehensive diagnostic snapshot

4. **`v11/live/live_engine.py`** — One additive change:
   - Added `strategy_name: str = "Darvas_Breakout"` attribute for identification

### Testing (COMPLETED)

40 new tests across 3 test files, following two-phase approach:

| Test File | Count | What It Covers |
|---|---|---|
| `test_risk_manager.py` | 17 | Daily loss limit, max positions, instrument conflict, per-strategy limits, entry/exit tracking, reset, combined PnL, status |
| `test_level_retest_engine.py` | 8 | ATR computation, historical seeding, status fields, in-trade blocking, identity |
| `test_multi_strategy_runner.py` | 15 | Add strategies, shared TradeManager, separate feeds, bar routing, seeding, status, reset, feed pairs, open positions |

**All 163 tests pass (123 existing + 40 new). Zero regressions.**

---

## Files Created This Session

| File | Purpose |
|---|---|
| `v11/live/risk_manager.py` | RiskManager — combined risk across all strategies |
| `v11/live/level_retest_engine.py` | LevelRetestEngine — 4H level retest strategy engine |
| `v11/live/multi_strategy_runner.py` | MultiStrategyRunner + InstrumentFeed orchestrator |
| `v11/tests/test_risk_manager.py` | 17 tests for RiskManager |
| `v11/tests/test_level_retest_engine.py` | 8 tests for LevelRetestEngine |
| `v11/tests/test_multi_strategy_runner.py` | 15 tests for MultiStrategyRunner |
| `docs/journal/2026-04-06_multi_strategy_runner_session.md` | This file |

## Files Modified This Session

| File | Change |
|---|---|
| `v11/live/live_engine.py` | Added `strategy_name` attribute to `InstrumentEngine` |
| `docs/PROJECT_STATUS.md` | Updated status, build table, roadmap Phase 4 → Complete, 163 tests |
| `docs/V11_DESIGN.md` | Updated Still Open: MultiStrategyRunner marked complete |

---

## Design Decisions Made

1. **Shared TradeManager enforces position limit** — Both EURUSD strategies share one TradeManager. When one is `in_trade`, the other's `on_bar()` returns early. No modification to CENTER code needed.

2. **InstrumentFeed owns BarAggregator** — The feed creates bars and routes them to all strategies. Each strategy also maintains its own internal aggregator (unused in multi-strategy context, but harmless).

3. **RiskManager is a read-only gate** — Strategies call `can_trade()` before entry. The risk manager doesn't own or submit orders; it's an advisory layer.

4. **LevelRetestEngine uses synthetic BreakoutSignal** — TradeManager expects `BreakoutSignal` for entry. The engine creates a synthetic one from `RetestSignal`, mapping level price → box top/bottom. This avoids modifying the CENTER TradeManager interface.

5. **Structural SL/TP overrides LLM** — The LLM evaluates the signal context, but the entry/stop/target prices are computed from the level + ATR offset, not from LLM output. The LLM decides whether to approve, but structural levels determine risk parameters.

## Assumptions

1. **Sequential bar processing** — Strategies on the same instrument process bars sequentially (Darvas first, then LevelRetest). In the current single-threaded async loop, this is guaranteed.
2. **ATR formula consistency** — LevelRetestEngine uses the same EMA ATR as DarvasDetector. If the ATR formula changes in DarvasDetector, it should change here too (potential drift risk).
3. **run_live.py not yet updated** — The entry point still uses the old `V11LiveTrader`. It should be updated to use `MultiStrategyRunner` before paper trading. Left for Phase 5 or a separate session.

## What My Checks CANNOT Evaluate

1. **Real-data integration** — Tests use mocks for IBKR and LLM. The full pipeline (real ticks → bars → signals → LLM → orders) hasn't been tested end-to-end.
2. **Timing interactions** — When both strategies fire signals on nearby bars, the sequential processing order matters. With real data, the Darvas strategy processes first and may claim the position slot.
3. **ATR divergence** — Each engine computes its own ATR from the same bars. In theory they should converge, but different seeding histories could cause slight differences.

---

## Build Roadmap Status

| Phase | Task | Status |
|---|---|---|
| 1 | Integrate SMA filter | **✅ Complete** |
| 2 | Build `level_detector.py` (4H swing levels) | **✅ Complete** |
| 3 | Build `retest_detector.py` (break → pullback → rebreak) | **✅ Complete** |
| 4 | Build `MultiStrategyRunner` orchestrator | **✅ Complete** |
| 5 | Wire V6 ORB via adapter | 🔲 Next |
| 6 | Update `run_live.py` entry point to use runner | 🔲 Pending |
| 7 | Paper trade | 🔲 Pending |
| 8 | Grok LLM enhancement | 🔲 Future |

---

## What NOT to Do Next Session

- **Don't modify TradeManager** — the shared-instance pattern works without changes
- **Don't modify DarvasDetector or RetestDetector** — they're proven with tests
- **Don't skip V6 ORB adapter tests** — Phase 5 needs the same two-phase test approach
