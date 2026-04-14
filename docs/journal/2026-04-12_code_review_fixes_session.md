# Code Review & Bug Fixes — Session Handoff

**Date**: 2026-04-12 (Monday evening ET)
**Author**: Claude Opus 4.6
**Scope**: V11 LLM filtering subsystem — systematic review + all fixes
**Status**: All fixes implemented, 345 tests passing (20 new), live system running on IBKR paper
**Commit**: `4a973d2` on `master`, pushed to `origin/master`

---

## 1. What Happened This Session

The user asked for a comprehensive code review of the V11 LLM filtering subsystem. I dispatched a `superpowers:code-reviewer` agent that read ~3950 lines across 13 files and identified 2 critical bugs, 4 important issues, and 7 suggestions. I then fixed all of them, wrote 20 new tests, and verified 345 total tests pass with zero regressions.

The live system was started at 22:02 ET Monday and is running cleanly on IBKR paper account.

---

## 2. What Was Fixed (with exact file paths and line numbers)

### Critical — Would crash in live trading

**C1. `position_vs_20d_sma` type mismatch (LIVE CRASH BUG)**
- **Where**: `v11/live/orb_adapter.py` — `_compute_trend_context()` method
- **Was**: `position_vs_20d_sma=round(position_vs_sma, 3)` → passed a `float` like `1.234`
- **Model expects**: `str` — `"above"`, `"below"`, or `"neutral"` (defined in `v11/llm/models.py:119`)
- **Impact**: Pydantic `ValidationError` every time the ORB LLM gate fired in live. Replay was fine (passed string correctly in `replay_orb.py`).
- **Fix**: Compute a string label: `>0.1%` → `"above"`, `<-0.1%` → `"below"`, else `"neutral"`.
- **Test**: `test_code_review_fixes.py::TestComputeTrendContext::test_position_vs_sma_returns_string`

**C2. Duplicate `async def on_bar()` in ORBAdapter**
- **Where**: `v11/live/orb_adapter.py` — lines 152 (no-op) and 245 (real LLM gate)
- **Was**: Two definitions. Python silently uses the last one. Worked by accident.
- **Fix**: Removed the no-op definition at line 152. Updated module docstring.

### Important — Should fix before real money

**I1. Synchronous HTTP blocking the IBKR event loop**
- **Where**: `v11/llm/grok_filter.py` — `evaluate_signal()` and `evaluate_orb_signal()`
- **Was**: `async def` methods calling synchronous `OpenAI` client. Blocked entire event loop for up to 30s per LLM call. During that time: no tick processing, no fill monitoring, no IBKR keepalive.
- **Fix**: Wrapped both calls in `asyncio.to_thread()`. LLM calls now run in a thread pool.
- **Why not AsyncOpenAI**: `asyncio.to_thread` is 2 lines change, stdlib, no new dependency.

**I2. ORB assessment: TP+SL on same bar counted as TP (optimistic)**
- **Where**: `v11/llm/assess_decisions.py` — `assess_orb_decision()` inner loop
- **Was**: A wide bar could set both `long_hit_tp=True` and `long_hit_sl=True`. Grading logic checked TP first → reported +1.5R when it should have been -1.0R.
- **Fix**: When both are hit on the same bar, conservatively assume SL first. Matches how the Darvas assessor already works.
- **Test**: `test_code_review_fixes.py::TestORBAssessmentSameBar::test_same_bar_tp_sl_assumes_sl`

**I3. Darvas auto-assessor matching could silently fail**
- **Where**: `v11/replay/auto_assessor.py` + `v11/live/multi_strategy_runner.py:379`
- **Was**: Callback passed `record.entry_price` as `breakout_price`, but the assessor matched against the signal's original `breakout_price` from the ledger context. If the LLM suggested a different entry price, match fails → decision never assessed → feedback loop has a gap.
- **Fix**: (a) Assessor now tries `breakout_price` match first, falls back to `entry_price` match. (b) Both assessors use a new public `find_unassessed(strategy, instrument, **context_filters)` method on `DecisionLedger` instead of accessing `ledger._records` directly.
- **Tests**: `test_code_review_fixes.py::TestFindUnassessed` (5 tests)

**I4. Decision ID collision in fast replay**
- **Where**: `v11/llm/decision_ledger.py` — `record_decision()`
- **Was**: ID = `{YYYY-MM-DD_HHMMSS}_{instrument}_{strategy}` at second granularity. Two decisions in the same second silently overwrite.
- **Fix**: Counter suffix (`_1`, `_2`) when collision detected.
- **Tests**: `test_code_review_fixes.py::TestDecisionIDCollision` (2 tests)

### Suggestions — Nice to have

**S1-S3. Missing tests** — Added 20 tests in `v11/tests/test_code_review_fixes.py`:
- `build_regime_filtered_table`: 6 tests (empty, match, fallback, boundary, strategy filter, no matches)
- `find_unassessed`: 5 tests (basic, skips assessed, no match, multi-strategy, float tolerance)
- `_compute_trend_context`: 6 tests (returns None <5 bars, returns TrendContext, string position, below SMA, positive slope, consecutive days)
- Same-bar TP+SL: 1 test
- ID collision: 2 tests

**S4. `evaluate_orb_signal` added to `LLMFilter` protocol** — `v11/llm/base.py`. Callers no longer need `hasattr` checks.

**S5. Ledger file growth** — Not changed. Currently small (dozens of decisions). Noted for future if it exceeds ~1000.

**S6. `from_dict` pop mutation** — `v11/llm/decision_ledger.py:67`. Added `d = dict(d)` to avoid mutating caller's dict.

**S7. Bootstrap missing `atr_regime`** — `v11/llm/bootstrap_ledger.py`. Now passes `atr_regime=range_vs_avg` to `ORBSignalContext` so bootstrapped decisions are visible to regime filtering.

---

## 3. Files Modified

| File | What Changed |
|---|---|
| `v11/live/orb_adapter.py` | C1: string label for position_vs_sma; C2: removed duplicate on_bar; updated docstring |
| `v11/llm/grok_filter.py` | I1: `asyncio.to_thread()` around both LLM API calls |
| `v11/llm/assess_decisions.py` | I2: conservative SL-first on same-bar; cleaned redundant grading logic |
| `v11/llm/decision_ledger.py` | I3: added `find_unassessed()` public method; I4: counter suffix for ID collisions; S6: from_dict shallow copy |
| `v11/replay/auto_assessor.py` | I3: uses `find_unassessed()` instead of `_records`; Darvas matching falls back to `entry_price` |
| `v11/llm/base.py` | S4: added `evaluate_orb_signal` to `LLMFilter` protocol |
| `v11/llm/bootstrap_ledger.py` | S7: passes `atr_regime` to `ORBSignalContext` |
| `v11/tests/test_code_review_fixes.py` | **New**: 20 tests covering all fix areas |

---

## 4. Current System State

### Live system (running as of 22:02 ET Monday)
- IBKR paper account, port 4002
- EURUSD: Darvas + 4H Retest, seeded with 5988 bars, SMA at 1.17103
- XAUUSD: V6 ORB, IDLE (past trade window), will calculate Asian range for Tuesday
- LLM: Grok filter active, dry-run mode
- No errors or crashes observed

### Test suite
```
345 passed, 26 warnings in 2.07s
```
Warnings are pre-existing Python 3.14 `asyncio.get_event_loop_policy` deprecations from `nest_asyncio`. Not actionable until nest_asyncio updates.

### Replay results (from prior session, unchanged)
| Version | Trades | Net PnL | Win Rate | Sharpe |
|---|---|---|---|---|
| Passthrough (no LLM) | 53 | +$117.18 | 39.6% | 1.14 |
| LLM + regime-filtered feedback | 35 | +$78.04 | 51.4% | 1.77 |

---

## 5. Known Limitations & Open Questions

These are the things the next agent should be aware of:

### From the prior session (2026-04-13 LLM filtering)

1. **Small sample size** — only 30 assessed decisions in 3 months of replay. The feedback loop needs more data to be statistically meaningful.

2. **Darvas accuracy is poor (48%)** — the LLM approves too many Darvas breakouts that hit SL. The feedback loop helps but the base quality is low.

3. **Regime tolerance is fixed at ±0.3** — may be too wide for some regimes, too narrow for others. No adaptive logic.

4. **No MISSED grades observed** — the LLM almost never rejects (only when confidence <75). The "MISSED" feedback category is unused. The LLM doesn't see examples of rejected-but-profitable setups.

5. **Only DeepSeek V3 tested** — other models may respond differently to the feedback table format.

### From this session's review

6. **Ledger file grows unbounded** — `decision_ledger.json` is rewritten on every decision/assessment. Fine for weeks, but after months of 24/5 operation it'll get large. Consider rotation or trimming old assessed decisions.

7. **Live auto-assessment for ORB is coupled** — `orb_adapter._assess_exit()` imports from `grok_filter` and `auto_assessor` at runtime. Works but fragile if class structure changes.

8. **No integration test for the full LLM gate flow in live** — we test individual pieces but not the end-to-end: tick arrives → RANGE_READY → LLM gate pending → on_bar fires → LLM evaluates → approved/rejected.

---

## 6. Forward-Looking Plan for Next Agent

### Priority 1: Improve Darvas LLM accuracy (currently 48%)

The Darvas filter is barely better than a coin flip. Investigate:

1. **Run replay with higher confidence threshold for Darvas** (e.g., 80 or 85 instead of 75). Quick test:
   ```bash
   python -m v11.replay.run_replay --instrument EURUSD --start 2026-01-01 --end 2026-04-12 --llm live --confidence-threshold 80
   ```

2. **Review the Darvas prompt** (`v11/llm/prompt_templates.py` — `SYSTEM_PROMPT`). It may be too generic. Consider adding:
   - Explicit guidance about session timing (London open breakouts vs Asian fakeouts)
   - ATR-relative stop distance requirements
   - Recent Darvas-specific feedback (not just overall)

3. **Add volume/order flow context** to `SignalContext` if available from IBKR. The LLM has no volume data currently.

### Priority 2: Expand replay sample size

30 assessed decisions is too few. Options:

1. **Run replay from 2025-10-01** (6 months instead of 3). Requires IBKR historical data or CSV file.
2. **Use bootstrap_ledger.py** to backfill more days:
   ```bash
   python -m v11.llm.bootstrap_ledger --days 30 --from-cache
   ```
3. **Run EURUSD replay** (currently only XAUUSD has been replayed with LLM). EURUSD Darvas/Retest is the main strategy that needs feedback.

### Priority 3: Adaptive regime tolerance

Currently ±0.3 is hardcoded in `decision_ledger.py::build_regime_filtered_table()`. Ideas:

1. Compute tolerance as a function of the regime value distribution (e.g., ±1 standard deviation)
2. Use a percentage-based tolerance instead of absolute (e.g., ±30% of regime value)
3. Widen tolerance when few matches exist, narrow when many

### Priority 4: End-to-end live integration test

Write a test that simulates the full ORB LLM gate flow:
1. Adapter receives ticks → state reaches RANGE_READY
2. LLM gate becomes pending
3. `on_bar()` fires → LLM evaluates (mocked)
4. Decision recorded to ledger
5. Trade enters and exits
6. Auto-assessor grades the decision
7. Feedback table refreshed

This would catch integration bugs like C1 (type mismatch between live and replay code paths).

### Priority 5: Multi-model LLM comparison in live

The prior session tested 5 models in replay (DeepSeek V3 won). Consider:
1. Running parallel paper accounts with different models
2. A/B testing: alternate models by day and compare results
3. Adding model name to the decision ledger for per-model accuracy tracking

---

## 7. How to Get Oriented

1. **Start here**: Read this file for what just happened
2. **Prior session context**: `docs/journal/2026-04-13_llm_filtering_enhancements.md` — the LLM filtering work this review covered
3. **Full project state**: `docs/PROJECT_STATUS.md` — all projects, architecture, folder roles
4. **V11 architecture**: `docs/V11_DESIGN.md` — strategy design, LLM integration, data flow
5. **Run tests**: `python -m pytest v11/tests/ -v` — should see 345 passed

### Key code paths to understand

**LLM filter call flow (Darvas/Retest):**
```
live_engine.py::on_bar() → grok_filter.py::evaluate_signal() → asyncio.to_thread(OpenAI) 
  → decision_ledger.py::record_decision() → FilterDecision returned
  → trade_manager.py executes trade → on_trade_closed callback
  → auto_assessor.py::assess_darvas_decision() → decision_ledger.py::assess_decision()
  → grok_filter.py::refresh_feedback()
```

**LLM filter call flow (ORB):**
```
orb_adapter.py::on_price() → state=RANGE_READY → llm_gate_pending=True
  → orb_adapter.py::on_bar() → _evaluate_orb_signal()
  → grok_filter.py::evaluate_orb_signal() → asyncio.to_thread(OpenAI)
  → approved → brackets placed, or rejected → DONE_TODAY
  → on fill → _assess_exit() → auto_assessor.py::assess_orb_decision()
```

**Regime-filtered feedback:**
```
grok_filter.py::_build_orb_feedback(context) 
  → decision_ledger.py::build_regime_filtered_table(strategy="ORB", regime_key="atr_regime", ...)
  → filters assessed decisions by strategy + |past_regime - current_regime| ≤ 0.3
  → if <3 matches: falls back to overall track record
  → returns markdown table injected into LLM prompt
```

---

## 8. Reproduction Commands

```bash
# Run live (IBKR must be running on port 4002)
python -m v11.live.run_live

# Run all tests
python -m pytest v11/tests/ -v

# Run just the code review fix tests
python -m pytest v11/tests/test_code_review_fixes.py -v

# Run replay (requires 1-min CSV data)
python -m v11.replay.run_replay --instrument XAUUSD --start 2026-01-01 --end 2026-04-12 --llm live --model deepseek/deepseek-chat-v3-0324 --base-url https://openrouter.ai/api/v1 --seed-bars 1440

# Check decision ledger stats
python -m v11.llm.assess_decisions --stats

# Bootstrap ledger with historical data
python -m v11.llm.bootstrap_ledger --days 15 --dry-run
```
