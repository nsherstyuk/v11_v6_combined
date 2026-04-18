# Phase B Safety Fixes — 2026-04-17

**Session goal:** Fix 4 open issues from Phase B (daily bar refresh, LLM timeout, heartbeat file, unit tests).

**Result:** All 4 fixed. 452 tests pass (was 417 before). No regressions.

---

## What Was Done

### #7: Daily Bar Refresh on Date Change

**Problem:** ORB LLM context (20 daily bars + 4h bars) was only loaded at startup. After running 24/5 unattended, daily bars became stale — the LLM was making decisions with outdated trend/regime information.

**Fix:**
- Extracted `_refresh_llm_context()` method from `_seed_historical()` in `run_live.py`
- Removed XAUUSD hardcode — now iterates all engines with `_daily_bars` attribute (works for any ORB instrument)
- Called `_refresh_llm_context()` on UTC midnight date change in the main loop (line 429)
- Still called at startup as before

**Files changed:**
- `v11/live/run_live.py` — new method `_refresh_llm_context()`, call on date change

**Impact:** LLM always gets fresh daily/4h bars. On day 2+ of unattended operation, trend context (SMA slope, consecutive days, range_vs_avg) is accurate.

---

### #12: LLM Timeout Split (ORB vs Darvas/4H)

**Problem:** Single `llm_timeout_seconds=10.0` used for both ORB and Darvas/4H. ORB has retry + mechanical fallback (timeout is mitigated). Darvas/4H has no fallback — timeout = signal rejected. DeepSeek V3 averages ~8s with spikes to 18s.

**Fix:**
- `llm_timeout_seconds` increased from 10→15s (ORB, has retry + fallback)
- New `signal_llm_timeout_seconds=20.0` (Darvas/4H, no fallback — needs more time)
- `GrokFilter` now accepts `signal_timeout` parameter, defaults to `timeout`
- `evaluate_signal()` (Darvas/4H) uses `self._signal_timeout`
- `evaluate_orb_signal()` (ORB) uses `self._timeout` (same as before)
- `LiveConfig.validate()` checks both timeouts > 0

**Files changed:**
- `v11/config/live_config.py` — new field `signal_llm_timeout_seconds`, increased `llm_timeout_seconds`
- `v11/llm/grok_filter.py` — new `signal_timeout` param, `evaluate_signal` uses it
- `v11/live/run_live.py` — wires `signal_timeout` from LiveConfig

**Impact:** Darvas/4H signals (when re-enabled) won't be lost to 10s timeout spikes. ORB keeps its fast timeout with fallback.

---

### #16: Heartbeat File for External Monitoring

**Problem:** If V11 freezes (not crash, but hang — deadlock, infinite loop, IBKR API hang), there's no external way to detect it. `start_v11.bat` only restarts on exit code 1. A hung process doesn't exit.

**Fix:**
- New `_write_heartbeat()` method writes `v11/live/state/heartbeat.json` every 5 minutes
- Contains: `{timestamp, connected, persistent_failure, instruments, pnl, trades, positions, strategies}`
- Never raises — errors caught silently (heartbeat is non-critical)
- Called in the periodic status block alongside `_check_price_staleness()` and `_log_status()`

**External monitoring pattern:**
```powershell
# Check if heartbeat is stale (>10 min old)
$file = "C:\ibkr_grok-_wing_agent\v11\live\state\heartbeat.json"
$age = (Get-Date) - (Get-Item $file).LastWriteTime
if ($age.TotalMinutes -gt 10) {
    # Kill and restart V11
    Stop-Process -Name python -Force
    Start-Process "C:\ibkr_grok-_wing_agent\v11\live\start_v11.bat"
}
```

**Files changed:**
- `v11/live/run_live.py` — new method `_write_heartbeat()`, call in periodic block

**Impact:** External scripts can detect hung processes and auto-restart. Combined with price staleness detection (300s → restart stream, 600s → emergency shutdown), V11 has three layers of liveness monitoring.

---

### #15: Phase B Unit Tests

**Problem:** No tests existed for Phase B safety-critical code paths: emergency shutdown, price staleness, orphan close, broker sync, persistent failure, heartbeat.

**Fix:** 35 tests in `v11/tests/test_phase_b_safety.py` across 8 test classes:

| Test Class | Tests | What's Tested |
|---|---|---|
| `TestPersistentFailure` | 5 | Disconnect timer, 5-min threshold, clear on reconnect |
| `TestEmergencyClose` | 3 | No-op when flat, resets state in dry-run, cancels orders in live |
| `TestReconcilePosition` | 6 | Internal vs broker state, orphan auto-close (long/short), size mismatch |
| `TestPriceStaleness` | 5 | 60s warn, 300s restart, 600s emergency, fresh OK, never-received |
| `TestHeartbeat` | 3 | Valid JSON, correct values, no-raise on error |
| `TestEmergencyShutdown` | 3 | State file written, orders cancelled, exit code 1 |
| `TestReconcilePositions` | 2 | Broker→risk manager sync, stale entry removal |
| `TestLiveConfigValidation` | 5 | New timeout defaults, validation |
| `TestGrokFilterTimeouts` | 3 | Separate timeout routing |

**Files created:**
- `v11/tests/test_phase_b_safety.py`

**Impact:** Safety-critical code paths now have regression tests. Before going live with real $, these provide confidence that emergency shutdown, position reconciliation, and staleness detection work correctly.

---

## Test Results

```
452 passed, 30 warnings in 8.47s
```

- 35 new tests in `test_phase_b_safety.py`
- 0 regressions in existing tests
- Warnings are all Python 3.14 asyncio deprecation (known, not actionable)

---

## Files Changed (Summary)

| File | Change |
|---|---|
| `v11/live/run_live.py` | +`_refresh_llm_context()`, +`_write_heartbeat()`, call on date change + periodic |
| `v11/config/live_config.py` | +`signal_llm_timeout_seconds=20.0`, `llm_timeout_seconds` 10→15, +validation |
| `v11/llm/grok_filter.py` | +`signal_timeout` param, `evaluate_signal` uses `self._signal_timeout` |
| `v11/tests/test_phase_b_safety.py` | **NEW** — 35 tests |
| `docs/PROJECT_STATUS.md` | Updated: open questions #7/#12/#15/#16 resolved, roadmap #29-32, test count 452 |
| `docs/journal/2026-04-17_phase_b_safety_fixes.md` | **NEW** — this file |

---

## Remaining Open Questions

| # | Issue | Status |
|---|---|---|
| 2 | Execution simulation (slippage, spread, partial fills) | Open |
| 5 | Integration replay test | Open |
| 11 | LLM feedback loop Step 3 (rejection bias tracking) | Open |
| 13 | Drawdown management (tighter risk controls) | Open |
| 18 | LLM staleness re-check (re-evaluate if breakout hours later) | Open |
| 19 | EURUSD data integrity investigation | Open |
| 24 | Paper trade ORB 4-6 weeks | **Next priority** |
| 28 | Trend following implementation decision | Pending |

---

## Handoff Notes

- V11 is paper-trading ready. All safety features are implemented and tested.
- **Next step:** Run `start_v11.bat --live --no-llm` for 4-6 weeks of paper trading (roadmap #24).
- Silver (XAGUSD) addition was discussed but deferred — needs backtest first to verify ORB edge exists on silver, and correlation with XAUUSD is ~0.85-0.95 (not diversification).
- The `_refresh_llm_context()` method is instrument-agnostic — when XAGUSD is added, it will automatically refresh its daily/4h bars too.
