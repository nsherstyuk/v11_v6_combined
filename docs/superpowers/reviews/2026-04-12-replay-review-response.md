# Review Response: Historical Replay Simulator

**Review:** `C:\Users\nsher\.windsurf\plans\historical-replay-simulator-review-3170d9.md`
**Plan:** `docs/superpowers/plans/2026-04-12-historical-replay-simulator.md`
**Date:** 2026-04-12

---

## Verdict: All actionable issues addressed. Plan updated and ready for implementation.

---

## Issue-by-issue response

### #1 Python 3.14 asyncio.wait_for patch — AGREE, FIXED

Good catch. The same issue from `bootstrap_ledger.py` (commit ba94672) would hit `run_replay.py`. The full compatibility patch from `v11/live/run_live.py` lines 27-68 has been added to Task 6 (`run_replay.py`). This includes the `_compat_wait_for` wrapper, event loop initialization, and `nest_asyncio.apply()`.

### #2 Session splitting / detector reset — DISAGREE (by design)

The reviewer recommends resetting DarvasDetector state between weekend gaps. We intentionally chose NOT to do this. Rationale:

1. **The live system never resets detectors.** When IBKR stops sending data on Friday and resumes Monday, `InstrumentEngine` and `LevelRetestEngine` continue with their existing state. There is no reset call anywhere in `run_live.py` or `MultiStrategyRunner` triggered by time gaps.

2. **The replay's purpose is to test the live code path.** If stale boxes from Friday persist into Monday and cause bad signals, that is exactly the kind of bug we want the replay to surface. Resetting detectors in replay would hide this class of bugs.

3. **The backtester resets between sessions because it's a different codebase.** `simulator.py` creates fresh `DarvasDetector` instances per session. The live system does not. Replay should match live, not the backtester.

**What we added instead:** `SESSION_GAP` events are emitted whenever a >30 minute gap is detected between consecutive bars. This gives full observability into when gaps occur without altering engine behavior. The event log lets us inspect whether any signal fired immediately after a gap (which would suggest stale state).

### #3 TradeManager dry_run path — CONFIRMED

Agreed, no fix needed. The verification is thorough:
- `enter_trade()` in dry_run sets state and returns True without IBKR calls
- `check_exit()` works identically in both modes
- `_execute_exit()` guards IBKR calls with `if not self._dry_run`
- `StubIBKRConnection` with `_StubIB.cancelOrder()` handles the edge case

### #4 CachedFilter ORB stub — CONFIRMED

Agreed. ORB is excluded from replay. The `evaluate_orb_signal()` stub returning passthrough is sufficient. If ORB replay is added later, CachedFilter would need to be extended.

### #5 Trade exit detection fragility — AGREE, FIXED

Good catch on the edge case. The plan now uses a dual check:

```python
if tm.daily_trades > trades_before or (was_in_trade and not tm.in_trade):
```

This catches:
- Normal exits: `daily_trades` counter increments (primary signal)
- Edge cases: state transition from `in_trade` to `not in_trade` without counter change (e.g., if a trade exits and a new one enters on the same bar, though unlikely with max-1-position-per-instrument)

The exit reason limitation remains — TradeManager resets state in `_execute_exit()` before we can read it. The PnL is captured via `daily_pnl` delta. For exact exit reasons, the trade CSV that TradeManager writes to `trades/trades_eurusd.csv` is the authoritative source.

### #6 data_loader path — CONFIRMED

No issues. `load_instrument_bars()` reads from `C:\nautilus0\data\1m_csv\` which contains 8 years of 1-min bars with `buy_volume`/`sell_volume` columns matching the `Bar` dataclass.

### #7 RiskManager constructor — CONFIRMED

Constructor signature matches: `RiskManager(max_daily_loss, max_daily_trades_per_strategy, max_concurrent_positions, log)`.

### #8 LiveConfig constructor — CONFIRMED with clarification

`LiveConfig` is a dataclass with all-default fields. Keyword args work. The reviewer correctly flagged a potential confusion:

- `LiveConfig.max_daily_trades` = per-instrument limit, checked by `InstrumentEngine._check_safety()` via `TradeManager.daily_trades`
- `RiskManager(max_daily_trades_per_strategy=...)` = per-strategy limit, checked by `RiskManager.can_trade()`

These are different knobs at different levels. Both are wired correctly in the plan — `ReplayConfig.max_daily_trades` feeds into both.

---

## Summary of plan changes

| Change | Location in plan |
|--------|-----------------|
| Added review adjustments table | Plan header (after Spec link) |
| Added Python 3.14 asyncio patch | Task 6, Step 1 (run_replay.py) |
| Added SESSION_GAP event emission | Task 5, Step 3 (replay loop) |
| Added SESSION_GAP to console events | Task 3, Step 3 (EventLogger) |
| Fixed exit detection to dual check | Task 5, Step 3 (replay loop) |
| Added comments explaining session gap design decision | Task 5, Step 3 |
