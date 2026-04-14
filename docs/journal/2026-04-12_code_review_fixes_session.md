# Code Review Fixes — Journal Entry

**Date**: 2026-04-12
**Author**: Claude Opus 4.6 (code reviewer + implementer)
**Scope**: V11 LLM filtering subsystem — bug fixes from systematic code review
**Status**: All fixes implemented, 345 tests passing (20 new)

---

## 1. Objective

Systematic code review of the V11 LLM filtering subsystem, followed by fixing all identified issues. The review covered ~3950 lines across 13 files, evaluating correctness, architecture, robustness, data integrity, test coverage, and code quality.

---

## 2. Issues Found & Fixed

### Critical (2)

**C1. TrendContext `position_vs_20d_sma` type mismatch — live crash bug**

- **File**: `v11/live/orb_adapter.py` line 525
- **Problem**: Passed a `float` (e.g. `1.234`) to a field typed `str` (expects `"above"`/`"below"`/`"neutral"`). Pydantic rejects this with `ValidationError`. Would crash every live LLM gate evaluation. Replay code was correct (passed a string).
- **Fix**: Compute a string label from the float value using 0.1% thresholds.

**C2. Duplicate `async def on_bar()` — dead code / confusing**

- **File**: `v11/live/orb_adapter.py` lines 152 and 245
- **Problem**: Two definitions of `on_bar()`. First was a no-op, second had the real LLM gate logic. Python silently uses the last definition, so it worked by accident.
- **Fix**: Removed the no-op first definition. Updated module docstring.

### Important (4)

**I1. Synchronous HTTP blocking async event loop**

- **File**: `v11/llm/grok_filter.py` lines 120 and 296
- **Problem**: `evaluate_signal` and `evaluate_orb_signal` are `async def` but called the synchronous `OpenAI` client. Blocks the IBKR event loop for up to 30s per LLM call — no tick processing, potential IBKR disconnection.
- **Fix**: Wrapped both calls in `asyncio.to_thread()` so they run in a thread pool without blocking.

**I2. ORB assessment TP+SL same-bar issue**

- **File**: `v11/llm/assess_decisions.py` lines 107-117
- **Problem**: A wide bar could trigger both TP and SL, and the code assumed TP (optimistic). The Darvas assessor correctly used `break`.
- **Fix**: When both TP and SL are possible on the same bar, conservatively assume SL hit first. Also cleaned up redundant no-breakout grading logic (lines 148-156).

**I3. Darvas breakout_price matching could silently fail**

- **Files**: `v11/live/multi_strategy_runner.py` line 379, `v11/replay/auto_assessor.py`
- **Problem**: Callback passed `entry_price` as `breakout_price`, but the assessor matched against the signal's original `breakout_price` in the ledger context. If the LLM suggested a different entry, the match fails silently.
- **Fix**: Assessor now tries `breakout_price` match first, falls back to `entry_price` match. Both assessors now use a new public `find_unassessed()` method instead of accessing `ledger._records` directly.

**I4. Decision ID collision in fast replay**

- **File**: `v11/llm/decision_ledger.py` line 139
- **Problem**: ID format used second-level granularity. Two decisions in the same second silently overwrite each other.
- **Fix**: Added counter suffix (`_1`, `_2`, etc.) when collision detected.

### Suggestions (7)

**S1-S3. Added 20 new tests** covering:
- `build_regime_filtered_table` — 6 tests (empty, regime match, fallback, boundary, strategy filter, no matches)
- `find_unassessed` — 5 tests (basic, skips assessed, no match, multi-strategy, float tolerance)
- `_compute_trend_context` — 6 tests (too few bars, returns TrendContext, string position, below SMA, positive slope, consecutive days)
- ORB same-bar TP+SL — 1 test
- Decision ID collision — 2 tests

**S4. Added `evaluate_orb_signal` to LLMFilter protocol** (`v11/llm/base.py`).

**S5. Ledger file growth** — noted but not changed. Current size is small (dozens of decisions). Will revisit if file exceeds ~1000 decisions.

**S6. `from_dict` pop mutation** — fixed with shallow copy to avoid mutating input dict.

**S7. Bootstrap ledger missing `atr_regime`** — now passes `atr_regime` (using `range_vs_avg` as proxy) to `ORBSignalContext` so bootstrapped decisions are visible to regime filtering.

---

## 3. Files Modified

| File | Change |
|---|---|
| `v11/live/orb_adapter.py` | C1: position_vs_20d_sma returns string label; C2: removed duplicate on_bar; updated docstring |
| `v11/llm/grok_filter.py` | I1: wrapped sync OpenAI calls in asyncio.to_thread() |
| `v11/llm/assess_decisions.py` | I2: conservative SL-first on same-bar TP+SL; cleaned redundant no-breakout logic |
| `v11/llm/decision_ledger.py` | I4: counter suffix for ID collisions; S6: from_dict shallow copy; I3: added find_unassessed() public method |
| `v11/replay/auto_assessor.py` | I3: use find_unassessed() instead of _records; Darvas matching falls back to entry_price |
| `v11/llm/base.py` | S4: added evaluate_orb_signal to LLMFilter protocol |
| `v11/llm/bootstrap_ledger.py` | S7: pass atr_regime to ORBSignalContext |
| `v11/tests/test_code_review_fixes.py` | **New file** — 20 tests for all review fix areas |

---

## 4. Test Results

```
345 passed, 26 warnings in 2.07s
```

- 20 new tests added (test_code_review_fixes.py)
- 325 existing tests unaffected
- Warnings are pre-existing Python 3.14 asyncio deprecations

---

## 5. Architecture Notes

### Key design decision: asyncio.to_thread vs AsyncOpenAI

Chose `asyncio.to_thread()` over switching to `AsyncOpenAI` because:
1. Minimal code change (2 lines vs rewriting HTTP client setup)
2. Same behavior: non-blocking LLM calls
3. No new dependency (asyncio.to_thread is stdlib)
4. Thread pool is managed by asyncio — no manual thread management

### Key design decision: Conservative SL-first assumption

When a wide bar triggers both TP and SL, we assume SL hit first. Rationale:
- In real markets, SL is closer to entry than TP
- For a trading system, overstating performance is worse than understating
- This matches how the Darvas assessor already works (break on first hit)
