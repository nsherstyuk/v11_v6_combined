# Session: 2026-04-08 — Grok LLM Gate Fixes + Diagnostic Logging

**Date:** 2026-04-08 06:55–08:46 ET  
**Agent:** Cascade  
**Status:** Complete

---

## What Happened

### 1. Reviewed Previous Session's Log (5+ hours of live run)

Checked `v11_live_20260407_145950.log` — system ran 14:59–20:20 ET with zero errors.
All three strategies running, daily reset worked correctly at 00:00 UTC, bars incrementing steadily (481→796).
ORB correctly went to DONE_TODAY on startup (started after Asian range window closed).

### 2. Fixed Three Bugs in the Grok LLM Gate

The previous agent built the ORB LLM gate but it was never tested with a live Grok call. Three bugs surfaced on first real run:

**Bug 1: `AsyncOpenAI` incompatible with `nest_asyncio`**
- **Root cause:** The entire run loop uses `loop.run_until_complete()` via `nest_asyncio`. The `openai.AsyncOpenAI` client uses `httpx.AsyncClient` → `anyio`, which can't detect the async library inside nested event loops.
- **Error:** `ORB LLM call failed: unknown async library, or not in async context`
- **Fix:** Switched from `AsyncOpenAI` to sync `OpenAI` client in `grok_filter.py`. Methods remain `async def` for protocol compatibility but use sync HTTP calls internally. This also fixes Darvas LLM calls (which hadn't been tested live yet).
- **Files:** `v11/llm/grok_filter.py`, `v11/tests/test_orb_llm_gate.py`

**Bug 2: `_log_conversation` assumed `context.direction` exists**
- **Root cause:** `ORBSignalContext` doesn't have a `direction` field (it's a range-based assessment, not directional).
- **Error:** `'ORBSignalContext' object has no attribute 'direction'`
- **Fix:** Used `getattr(context, 'direction', 'ORB')` for filename and log entry.
- **Files:** `v11/llm/grok_filter.py`

**Bug 3: `LLMResponse` validator rejected `stop=0.0`**
- **Root cause:** Pydantic validator required `stop > 0`. Grok correctly returns `stop=0.0` for ORB signals (ORB uses V6's bracket logic, not LLM-provided stops).
- **Error:** `Value error, stop must be > 0, got 0.0`
- **Fix:** Changed validator from `stop > 0` to `stop >= 0`. Darvas signals with stop=0 will still be caught downstream by TradeManager.
- **Files:** `v11/llm/models.py`, `v11/tests/test_llm_models.py`

**Bug 2b: ORB LLM gate called from sync `on_price()` context**
- **Root cause:** Even after switching to sync client, the LLM call was in `on_price()` via `loop.run_until_complete()`. Moved to async `on_bar()` for cleaner execution.
- **Fix:** Added `on_bar()` to ORBAdapter. `on_price()` sets `_llm_gate_pending=True`, `on_bar()` evaluates ~1 min later.
- **Files:** `v11/live/orb_adapter.py`

### 3. Increased Historical Seed from 8H to 5D

- SMA(50) on 60-min bars needs 50 hourly bars — was `None` with 8H seed
- 4H level detector needs 21 4H bars (84 hours) — had only 2 with 8H seed
- Changed from `"28800 S"` → `"5 D"` in `run_live.py`
- Result: ~7200 bars seeded, SMA ready immediately, 4H levels available within a few hours
- **File:** `v11/live/run_live.py`

### 4. Added Rich Diagnostic Logging

The system had zero visibility into detector internals. Added:

**New diagnostic properties (center modules — read-only):**
- `DarvasDetector.formation_progress` — returns dict with candidate top/bottom, confirmation bar counts, box edges
- `RetestDetector.get_pending_details()` — returns list of pending retests with elapsed bars, pullback state
- `IncrementalSwingLevelDetector.buffer_fill` — e.g. "18/21"
- `IncrementalSwingLevelDetector.levels_ready` — bool

**Enhanced INFO status lines (console, every 5 min):**
- Darvas: shows formation stage (forming top=X 3/5, BOX [lo-hi], BREAKOUT LONG 1/2), SMA with bar count
- 4H Retest: shows nearest level with ATR distance, buffer fill, pending count
- Both: SMA value or "warming" with bar count

**New DEBUG logging (file only, every 60 bars = 1 hour):**
- Darvas: formation progress, box distance from close in ATR units
- 4H Retest: each active level with distance/ATR, each pending retest with elapsed/max bars and pullback state
- Both: state transitions logged on every change

**Files:**
- `v11/core/darvas_detector.py` — added `formation_progress` property
- `v11/core/retest_detector.py` — added `get_pending_details()` method
- `v11/core/level_detector.py` — added `buffer_fill`, `levels_ready` properties
- `v11/live/live_engine.py` — added formation/box progress DEBUG logging, `formation_progress` in `get_status()`
- `v11/live/level_retest_engine.py` — added level/retest DEBUG logging, nearest level in `get_status()`
- `v11/live/run_live.py` — enhanced status line formatting

### 5. Verified Grok API Connectivity

Sent test request to `https://api.x.ai/v1/chat/completions` — got 200 OK, "OK." response. API key valid.

---

## Commits

| Hash | Message |
|---|---|
| `85f7cd0` | Add diagnostic logging: detector state, box/level/SMA detail in status + DEBUG transitions |
| `071d488` | Fix ORB LLM gate: move async call to on_bar(), fix _log_conversation for ORBSignalContext |
| `c0e799b` | Fix Grok: switch AsyncOpenAI to sync OpenAI client (httpx async incompatible with nest_asyncio) |
| `c06343e` | Allow stop=0 in LLMResponse for ORB signals (brackets managed by V6, not LLM) |
| `664070e` | Increase historical seed from 8H to 3D so SMA(50) is ready at startup |
| `855670a` | Increase historical seed to 5D for 4H level detection at startup |
| `0e0b813` | Add rich diagnostic logging: formation progress, level proximity, pending retests, buffer fill |
| `34ed566` | Session docs: 2026-04-08 diagnostic session journal + PROJECT_STATUS update |
| `813bfb7` | Fix timeout handling: catch APITimeoutError, increase timeout to 30s, fallback bypasses confidence threshold |

---

### 6. Fixed Timeout Handling (Post-Documentation Fix)

After the session was documented, a restart revealed the Grok call was timing out (10s default too short for reasoning model). Three sub-issues:

**Bug 4: Wrong timeout exception type for sync client**
- **Root cause:** Sync `OpenAI` client throws `openai.APITimeoutError`, not `asyncio.TimeoutError`. The retry logic never triggered — timeout fell through to `except Exception` → no retry → immediate fallback.
- **Fix:** Added `APITimeoutError` to the caught exceptions in both `evaluate_signal()` and `evaluate_orb_signal()`.
- **Files:** `v11/llm/grok_filter.py`

**Bug 5: Mechanical fallback defeated by confidence threshold**
- **Root cause:** On double timeout, `evaluate_orb_signal` returns `FilterDecision(approved=True, confidence=0, risk_flags=["llm_fallback"])`. But `orb_adapter._evaluate_orb_signal` checked `confidence < 75` → rejected. The whole point of the fallback is to approve mechanically.
- **Fix:** If `"llm_fallback"` in `risk_flags`, skip the confidence threshold check.
- **Files:** `v11/live/orb_adapter.py`

**Bug 6: Default timeout too short**
- 10s is too tight for `grok-4-1-fast-reasoning`. Increased default to 30s (retry still 5s).
- **Files:** `v11/llm/grok_filter.py`

**2 new tests:**
- `test_api_timeout_error_triggers_retry` — verifies `APITimeoutError` is retried and falls back
- `test_fallback_bypasses_confidence_threshold` — verifies adapter approves on mechanical fallback

---

## Test Results

279 tests passing (2 new), zero regressions after all changes.

---

## Live Grok Response (First Successful Call)

```
ORB LLM gate: evaluating range 4788.40-4857.82 (size=69.42, vs_avg=0.3x)
Response: approved=false, confidence=85
Reasoning: "Very tight Asian range (range_vs_avg 0.33) prone to false breakouts.
            Macro regime shows high volatility with sharp spikes/drops, not normal
            trending conditions. Current price below range low mid-London."
Risk flags: tight_range, volatile_macro, price_outside_range
Result: RANGE_READY -> DONE_TODAY (LLM rejected correctly)
```

---

## Handoff Notes

### What's Working
- All 3 strategies running live with Grok LLM enabled
- ORB LLM gate calls Grok once/day at RANGE_READY, correctly rejects or approves
- Darvas detector actively forming boxes (CONFIRMING_TOP/BOTTOM cycling)
- Diagnostic logging provides full visibility into detector internals
- SMA ready at startup with 5D historical seed
- 4H level detector needs ~1 more day of running to start detecting levels (21 4H bars needed, ~30 available from 5D seed)

### What to Monitor
- Check log file for DEBUG messages showing box formation and level detection
- First Darvas signal likely within 1-2 days of normal market conditions
- First 4H level detection should happen within hours of next restart (5D seed provides ~30 4H bars > 21 needed)
- Grok LLM should approve ORB on a day with average+ range (0.3x was too tight today)

### Remaining Gaps
- No live Darvas or 4H Retest LLM calls tested yet (no signals have fired — need market movement)
- Daily bar refresh for ORB context not implemented (daily bars only loaded at startup)
- Recent 1-min bars not included in ORBSignalContext (design note from previous session)
- Walk-forward validation still TODO
- Integration replay test still TODO
