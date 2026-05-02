# V11 Live Code Review — Agent-Safe Execution Plan

Date: 2026-04-24

Related review:

- `docs/superpowers/reviews/2026-04-24-v11-live-code-review.md`

## Purpose

This document converts the code review recommendations into a safe, staged execution plan for a coding agent. The goal is to improve reliability without destabilizing the live/paper trading path.

The plan is deliberately conservative. The system interacts with IBKR and can create financial risk if modified carelessly. The agent should prefer small, testable changes over broad refactors.

## Core Safety Rule

Do not change live trading behavior unless the change is explicitly listed in this plan and covered by tests.

Especially avoid:

- Changing strategy entry/exit logic.
- Changing ORB range parameters.
- Changing order prices, quantities, TIF, OCA behavior, or trigger methods unless explicitly requested.
- Re-enabling disabled strategies.
- Adding new instruments to live config.
- Removing existing safety comments, incident notes, or journal references.
- Refactoring `run_live.py` broadly before tests are green.

## Recommended Coding-Agent Model

Use a high-reliability coding model with strong long-context reasoning and low tendency to improvise.

Recommended:

1. **Claude 3.7 Sonnet / Claude Sonnet-class agent**
   - Best fit for this task because the plan requires careful multi-file reasoning, conservative edits, and respect for safety constraints.
   - Strong at reading existing code and making small targeted changes.

2. **GPT-4.1 / GPT-4.1-class coding agent**
   - Also appropriate, especially for test refactors and API cleanup.
   - Good at preserving behavior while adding tests.

3. **Avoid small or highly autonomous models for this plan**
   - Do not use lightweight models for broker/execution code changes.
   - The risk is not syntax; the risk is subtle live behavior changes.

If using Windsurf/Cascade, use it in a conservative mode:

- Require it to inspect files before editing.
- Require it to run relevant tests after each phase.
- Do not let it perform broad cleanup.
- Do not let it change strategy math or order semantics.

## Execution Overview

Phases:

1. **Phase 0 — Baseline and safety snapshot**
2. **Phase 1 — Fix deterministic failing tests**
3. **Phase 2 — Add public APIs around private-field mutation**
4. **Phase 3 — Strengthen broker/risk consistency checks**
5. **Phase 4 — Improve heartbeat watchdog documentation / optional script**
6. **Phase 5 — Controlled paper fault-test checklist**
7. **Phase 6 — Deferred refactor plan for `run_live.py`**

Phases 1 and 2 are safe to implement soon. Phases 3 and 4 are moderate-risk and should be done after tests are green. Phase 6 should be deferred until the system has completed a stable paper-trading period.

---

# Phase 0 — Baseline and Safety Snapshot

## Goal

Establish current repository state and verify the agent understands the live/paper risk boundary before editing.

## Preconditions

- IBKR Gateway/TWS may be running, but the agent must not place orders.
- No live-trading behavior should be changed in this phase.

## Agent Steps

1. Inspect git status.
2. Read these files:
   - `docs/superpowers/reviews/2026-04-24-v11-live-code-review.md`
   - `v11/live/run_live.py`
   - `v11/execution/ibkr_connection.py`
   - `v11/v6_orb/ibkr_executor.py`
   - `v11/live/risk_manager.py`
   - `v11/tests/test_phase_b_safety.py`
3. Run the test suite or at minimum relevant tests:
   - `python -m pytest v11/tests/test_phase_b_safety.py -q`
   - `python -m pytest v11/tests/test_force_disconnect_recovery.py -q`

## Expected Current Result

At review time, full suite had:

- 520 passed
- 2 failed
- 39 warnings

The two known failures are price-staleness tests caused by wall-clock dependence.

## Do Not

- Do not change config defaults.
- Do not run live trading scripts.
- Do not start/stop Gateway.
- Do not edit execution code in Phase 0.

## Completion Criteria

- Agent has confirmed current failing tests.
- Agent has identified whether the working tree already contains user changes.
- Agent has not modified code.

---

# Phase 1 — Fix Deterministic Failing Tests

## Goal

Make price-staleness tests deterministic by removing wall-clock dependence.

## Risk Level

Low, if implemented narrowly.

## Files Likely Involved

- `v11/live/run_live.py`
- `v11/tests/test_phase_b_safety.py`

## Background

`_check_price_staleness()` currently calls:

```python
if self._is_market_closed_window(datetime.now(timezone.utc)):
    return
```

This makes tests depend on the actual day/hour. During the market-closed window, staleness escalation is intentionally skipped, so tests expecting restart/emergency behavior can fail.

## Recommended Implementation

Change `_check_price_staleness()` to accept an optional timestamp:

```python
def _check_price_staleness(self, now_utc: datetime | None = None) -> None:
    now_utc = now_utc or datetime.now(timezone.utc)
    if self._is_market_closed_window(now_utc):
        return
    now = time.time()
    ...
```

Then update tests to pass a deterministic market-open timestamp, for example:

```python
now_utc=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
```

Add or update a test for market-closed behavior:

```python
now_utc=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
```

Expected behavior:

- During market open, stale feed escalates at 300s and 600s.
- During market closed, stale feed does not restart stream or emergency shutdown.

## Guardrails

- Do not change the staleness thresholds.
- Do not remove market-closed suppression.
- Do not alter emergency shutdown behavior.
- Do not alter stream restart behavior.

## Tests to Run

Minimum:

```powershell
python -m pytest v11/tests/test_phase_b_safety.py -q
```

Then:

```powershell
python -m pytest v11/tests -q
```

## Completion Criteria

- The two known staleness tests pass regardless of current wall-clock time.
- New or updated tests explicitly cover market-open and market-closed behavior.
- Full test suite has no failures except pre-existing unrelated warnings.

---

# Phase 2 — Add Public APIs Around Private-Field Mutation

## Goal

Reduce fragile private-field coupling without changing runtime behavior.

## Risk Level

Low to medium. Safe if implemented as wrappers around existing behavior.

## Files Likely Involved

- `v11/v6_orb/ibkr_executor.py`
- `v11/execution/ibkr_connection.py`
- `v11/live/orb_adapter.py`
- `v11/live/run_live.py`
- Relevant tests

## Subphase 2A — Executor Entry Failure API

### Problem

`run_live._reconcile_orders()` currently mutates executor private fields directly:

```python
exec_._entry_failed = True
exec_._entry_failure_reason = ...
```

### Implementation

Add a public method to `IBKRExecutionEngine`:

```python
def mark_entry_failed(self, reason: str) -> None:
    self._entry_failed = True
    self._entry_failure_reason = reason
```

Update `_on_order_status()` to use this method internally when practical.

Update `run_live._reconcile_orders()`:

```python
if hasattr(exec_, "mark_entry_failed"):
    exec_.mark_entry_failed(reason)
elif hasattr(exec_, "_entry_failed"):
    exec_._entry_failed = True
    exec_._entry_failure_reason = reason
```

The fallback preserves compatibility for tests/mocks.

### Tests

Add or update a test proving:

- Reconciler marks missing entry orders via `mark_entry_failed()`.
- `entry_placement_failed()` returns true.
- `entry_failure_reason()` returns the expected text.

## Subphase 2B — Connection Contract Accessor

### Problem

Some code accesses connection internals like `_contracts`.

### Implementation

Add to `IBKRConnection`:

```python
def get_contract(self, pair_name: str):
    return self._contracts.get(pair_name)
```

Use this method where convenient.

### Guardrail

This must be a read-only accessor. Do not change contract qualification behavior.

## Subphase 2C — ORB Context Setter

### Problem

External code may assign daily/hourly context directly into adapter private fields.

### Implementation

Add to `ORBAdapter`:

```python
def set_llm_context(self, daily_bars, hourly_bars=None) -> None:
    self._daily_bars = daily_bars
    if hourly_bars is not None:
        self._hourly_bars = hourly_bars
```

Update callers to use the method if present.

### Guardrail

Do not change how context is computed or used. This is only an API wrapper.

## Tests to Run

```powershell
python -m pytest v11/tests/test_force_disconnect_recovery.py -q
python -m pytest v11/tests/test_order_observability.py -q
python -m pytest v11/tests -q
```

## Completion Criteria

- Direct private mutation is reduced in the live supervisor.
- Behavior remains identical.
- Existing tests pass.
- New tests cover the new public APIs where useful.

---

# Phase 3 — Strengthen Broker/Risk Consistency Checks

## Goal

Make divergence between `RiskManager` state and broker positions more visible and testable.

## Risk Level

Medium.

This phase should not auto-flatten or mutate broker state unless explicitly reviewed. Start with diagnostics and internal repair only.

## Files Likely Involved

- `v11/live/risk_manager.py`
- `v11/live/run_live.py`
- New or existing tests

## Recommended Implementation

Add a non-broker-mutating consistency method to `RiskManager`:

```python
def reconcile_open_positions(self, broker_positions_by_instrument: dict[str, float]) -> list[str]:
    ...
```

Alternative safer naming:

```python
def check_broker_consistency(self, broker_positions_by_instrument: dict[str, float]) -> list[str]:
    ...
```

Prefer first implementation to be diagnostic only:

- If broker has nonzero position and risk manager says flat: return warning.
- If risk manager says position but broker is flat: return warning.
- If both agree: no warning.

Do not have this method place orders, cancel orders, or flatten positions.

## Suggested Behavior

Inputs:

```python
broker_positions_by_instrument = {
    "XAUUSD": 1.0,
}
```

Return:

```python
[
    "RiskManager flat but broker has XAUUSD position 1.0"
]
```

Then `run_live._reconcile_positions()` or heartbeat/status logging can call this and log critical warnings.

## Guardrails

- Do not change `can_trade()` behavior in this phase unless tests require it.
- Do not reset positions automatically at first.
- Do not flatten broker positions from `RiskManager`.
- Do not query IBKR from inside `RiskManager`; pass broker snapshot in.

## Tests

Add tests for:

- Internal flat / broker flat → no warnings.
- Internal flat / broker long → warning.
- Internal position / broker flat → warning.
- Internal position / broker same direction → no warning.
- Internal position / broker opposite direction → warning.

## Completion Criteria

- Risk/broker divergence becomes visible in logs or status.
- No broker-mutating behavior added.
- Tests pass.

---

# Phase 4 — Heartbeat Watchdog Documentation or Script

## Goal

Turn heartbeat fields into an explicit operational safety checklist or external watchdog.

## Risk Level

Low if documentation-only. Medium if script kills/restarts processes.

## Recommended First Step

Documentation-only or passive script.

Create:

- `docs/PAPER_TRADE_HEARTBEAT_WATCHDOG.md`

or:

- `v11/live/check_heartbeat.py`

If creating a script, default it to passive mode:

- Read heartbeat.
- Print status.
- Exit nonzero on critical condition.
- Do not kill processes by default.

## Critical Conditions

Heartbeat should flag:

- Heartbeat file missing.
- Heartbeat timestamp older than 10 minutes.
- `connected=false` for longer than threshold.
- `persistent_failure=true`.
- `broker_pos != 0` and `has_sl == false`.
- `broker_pos != 0` and strategy `in_trade == false`.
- `internal_pos != 0` and `broker_pos == 0` for repeated checks.

## Guardrails

- Do not make the script place/cancel orders.
- Do not make the script flatten positions.
- Do not kill/restart processes unless the user explicitly requests active mode.

## Tests

If script is created, add tests using temporary heartbeat JSON fixtures.

## Completion Criteria

- Operator has a clear heartbeat monitoring procedure.
- If a script exists, it is passive by default and test-covered.

---

# Phase 5 — Controlled Paper Fault-Test Checklist

## Goal

Validate that existing safety logic works under realistic IBKR/Gateway failure conditions.

## Risk Level

Operational medium. Code changes should not be made during fault tests unless a failure is observed and diagnosed.

## Recommended Document

Create or update:

- `docs/PAPER_TRADE_FAULT_TEST_PLAN.md`

## Fault Tests

### Test 1 — Gateway killed while flat

Expected:

- V11 detects disconnect.
- Reconnect starts.
- No orders placed.
- No positions created.
- Heartbeat shows disconnected/persistent state appropriately.

### Test 2 — Gateway killed while entry orders are resting

Expected:

- Reconnect occurs.
- Order reconcile does not produce false positives for terminal orders.
- Missing active orders are surfaced as entry placement failure.
- Strategy returns safely to `RANGE_READY` or `DONE_TODAY` depending on context.

### Test 3 — Gateway killed immediately after entry fill

Expected:

- On reconnect, broker position is detected.
- If outage is short and range info exists, SL/TP may be re-armed.
- If outage is long or uncertain, position is flattened.

### Test 4 — Manually cancel SL while in position

Expected:

- Naked-position invariant detects missing SL/TP after grace period.
- Position is force-flattened.
- Heartbeat shows protection state before/after.

### Test 5 — Half-open socket simulation if possible

Expected:

- Placement errors increment connectivity counter.
- `force_disconnect()` is invoked.
- `placement_stuck` eventually trips if reconnect cannot recover.
- Wrapper restarts process.

## Guardrails

- Paper account only.
- Minimal quantity only.
- Operator watches TWS/Gateway and logs.
- Do not run during volatile/news window if avoidable.
- Do not test on real-money account.

## Completion Criteria

- Each fault test has observed result documented.
- Any failure becomes a journal entry and regression test.

---

# Phase 6 — Deferred Refactor of `run_live.py`

## Goal

Reduce long-term complexity by extracting focused services from `run_live.py`.

## Risk Level

Medium to high.

Do not do this until:

- Full test suite is green.
- XAUUSD ORB paper run is stable.
- Fault tests have been completed or scheduled.

## Candidate Extractions

### 1. `PriceStalenessMonitor`

Move:

- `_is_market_closed_window()`
- `_check_price_staleness()`

Inputs:

- active pairs
- last price times
- connection object
- emergency callback
- clock

### 2. `HeartbeatWriter`

Move:

- `_write_heartbeat()`

Inputs:

- runner
- connection
- active pairs
- output path

### 3. `OrderReconciler`

Move:

- `_reconcile_orders()`

Inputs:

- connection
- runner
- logger

### 4. `PositionReconciler`

Move:

- `_reconcile_positions()`

Inputs:

- connection
- runner
- logger

### 5. `StatusReporter`

Move:

- `_log_status()`
- `_log_last_llm_decision()`

Inputs:

- runner
- config
- llm filter
- logger

## Refactor Guardrails

- One extraction per PR/change batch.
- No behavior change in extraction commits.
- Add tests before or during extraction.
- Keep old method signatures temporarily as wrappers if useful.
- Compare logs before/after if possible.

## Completion Criteria

- `run_live.py` becomes a composition/root loop.
- Extracted services have unit tests.
- Live behavior remains unchanged.

---

# Final Agent Checklist

Before every edit:

- Confirm the file is relevant to the phase.
- Check existing tests for the behavior.
- Prefer adding tests before changing logic.

After every edit:

- Run the narrow relevant test file.
- Run the full `v11/tests` suite if the edit touches live/execution code.
- Inspect failures carefully; do not paper over them.

Never do automatically:

- Change live config to real trading.
- Increase quantity.
- Add instruments.
- Re-enable disabled strategies.
- Remove safety checks.
- Delete incident-related comments or tests.
- Refactor large files and behavior in the same change.

Recommended implementation order:

1. Phase 1: deterministic staleness tests.
2. Phase 2A: `mark_entry_failed()` public API.
3. Phase 2B: `get_contract()` accessor.
4. Phase 2C: ORB context setter.
5. Phase 3: diagnostic risk/broker consistency checks.
6. Phase 4: passive heartbeat watchdog doc/script.
7. Phase 5: paper fault-test plan.
8. Phase 6: defer refactor until stable paper period.

The safest path is to keep each change small, test-covered, and operationally reversible.
