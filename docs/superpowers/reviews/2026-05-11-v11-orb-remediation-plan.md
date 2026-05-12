# 2026-05-11 — V11 ORB No-Fill Remediation + Architecture Plan

## Executive Summary

This is the consolidated execution plan after reviewing the 2026-05-11 XAUUSD ORB no-fill incident and the surrounding V11 live architecture.

The earlier review `2026-05-11-v11-orb-no-fill-readiness-review.md` is useful as a discovery memo. This document should be treated as the clearer coding-agent handoff.

Core judgment:

> The system does not need a rewrite. It needs targeted architecture hardening around the active XAUUSD ORB path so order placement, reconnect, emergency shutdown, and position-state convergence become first-class and testable.

The no-fill incident should not be patched blindly. The already-applied May 11 order diagnostics must be observed first. The higher-priority structural fixes are:

1. ORB must rebind to the current `IB()` object after reconnect.
2. ORB safety-flatten fill reasons must converge strategy/risk-manager state.
3. Emergency shutdown must explicitly close ORB through broker-truth-aware logic.
4. A deterministic ORB lifecycle harness should test normal and abnormal trade paths end to end.

## Read First

1. `CLAUDE.md`
2. `docs/PROJECT_STATUS.md`
3. `docs/journal/2026-05-11_orb_no_fill_bug_and_code_review.md`
4. `docs/journal/2026-04-24_stp_trigger_method.md`
5. `docs/journal/2026-05-11_daily_restart_v11_pgrp_kill.md`
6. `docs/superpowers/reviews/2026-05-11-v11-orb-no-fill-readiness-review.md`
7. this document

## Hard Constraints

Do not:

- touch `.env`
- touch `~/ibc/config.ini` credentials
- use live port `4001`
- trigger real-money trading
- re-enable Darvas, 4H Level Retest, EURUSD, or any other inactive strategy
- add new strategy research before V11 ORB proof-of-life is operationally clean
- remove or weaken restart/reconnect/relogin/supervision features
- broadly rewrite V11
- modify protected `v11` production code without Nick's explicit approval

## Current Code State

As of this review, May 11 evening changes already exist in the repo:

- `v11/v6_orb/ibkr_executor.py`
- `v11/tests/test_order_observability.py`
- `docs/PROJECT_STATUS.md`

Current executor behavior:

- entry order IDs are recorded immediately after `placeOrder`, before `ib.sleep(1)`
- post-placement `ib.openTrades()` diagnostics are logged
- ORB entries changed from `STP` to `STP LMT`
- `triggerMethod=7` remains on entry stops and SL stops
- entry limit buffer is `max(0.5, 0.1 × range_width)`

Targeted tests run during review:

```bash
.venv/bin/python -m pytest \
  v11/tests/test_order_observability.py \
  v11/tests/test_orb_entry_fill_clears_ids.py \
  v11/tests/test_naked_position_invariant.py \
  v11/tests/test_reconcile_after_reconnect.py \
  v11/tests/test_close_at_market_broker_truth.py \
  -q
```

Result:

- `35 passed`

Important limitation:

- those tests are mocked/unit-level
- they do not prove IBKR paper Gateway will actually trigger/fill XAUUSD stop orders

## Overall Architecture Assessment

The system is not fundamentally broken.

The strongest parts are:

- active scope is narrow: XAUUSD ORB only
- incident journaling is detailed
- fixes are usually backed by regression tests
- operational supervision exists: daily restart, launchd, health check, heartbeat, reconnect logic
- weak research paths have been suspended instead of forced live

The weak point is:

> ORB is the active live strategy, but it is not yet a first-class V11 live engine. It is a V6 strategy/executor/context wrapped by `ORBAdapter`, with duplicated lifecycle responsibilities and private state coupling.

That architecture creates failure modes where one layer thinks the system is safe while another layer is stale or inconsistent.

Do not rewrite ORB strategy logic. Make ORB's **live lifecycle** first-class and broker-truth-driven.

## Source-of-Truth Contract

Future fixes should follow this hierarchy:

1. **Broker truth is authoritative**
   - actual IBKR positions and open orders are the source of truth
2. **Execution engine is operational state**
   - order IDs, current position side, entry fill time, SL/TP IDs
3. **Strategy state is decision state**
   - `RANGE_READY`, `ORDERS_PLACED`, `IN_TRADE`, `DONE_TODAY`
4. **RiskManager is a gate/cache**
   - not broker truth
5. **Heartbeat/status are observability**
   - never authoritative

Any entry, exit, flatten, reconnect, or emergency path must converge all layers:

- broker position correct
- executor state correct
- strategy state correct
- risk manager position cache correct
- heartbeat/status reflects the above

---

# Phase 0 — Immediate Tomorrow-Morning Readiness

## Goal

Do not patch blindly before the next trade window. First verify whether the already-applied May 11 order-placement changes are actually running and what IBKR accepted.

## 0.1 Confirm V11 restarted and loaded new code

The old May 11 process held the old module in memory. The new code only takes effect after a restart.

Tomorrow's placement log must include:

- `Entry stops placed: BUY id=... @ ... lmt=...`
- `SELL id=... @ ... lmt=...`
- `DIAG: openTrades after placement:`
- order type `STP LMT`
- `trigger=7`
- `tif=DAY`
- expected OCA group
- active status, ideally `Submitted`

If no `lmt=...` or `DIAG` appears, the old code is still running.

## 0.2 Confirm daily restart survived launchd process-group cleanup

Verify:

- `~/.daily_restart.log` shows success
- v11 process survives after the daily-restart script exits
- v11 reaches range calculation after 06:00 UTC
- no immediate `Signal 15 received — shutting down`

If v11 dies after daily restart, stop strategy work and fix launchd lifecycle first.

## 0.3 Do not change order type again before reading diagnostics

The first post-fix placement should answer:

- Did IBKR receive `triggerMethod=7`?
- Did IBKR receive `STP LMT`?
- Did both OCA entry legs become active?
- Did staged BUY `transmit=False` become active after SELL `transmit=True`?
- Did `orderStatusEvent` now log early lifecycle transitions?

Do not change to MIT, software-triggered market, `bracketOrder`, larger limit buffers, or live-account testing before seeing this evidence.

## Phase 0 Acceptance Criteria

Phase 0 is successful if:

- v11 is running the new code
- daily restart/relogin path did not kill v11
- ORB placed diagnostic-visible orders
- logs tell us whether the orders were truly active at IBKR

---

# Phase 1 — Fix ORB Reconnect Architecture

## Finding

`IBKRConnection.connect()` creates a new `IB()` object after reconnect:

- `self.ib = IB()`
- contracts are re-qualified on the new object
- market streams are restarted on the new object

But ORB components are constructed once with the original `conn.ib`:

- `ORBAdapter._ib`
- `LiveMarketContext.ib`
- `IBKRExecutionEngine.ib`

I did not find a rebind path after reconnect.

## Why This Matters

After reconnect:

- `IBKRConnection` can be healthy on the new socket
- ORB execution may still call `placeOrder`, `openTrades`, `positions`, `cancelOrder`, and `close_at_market` on the stale disconnected socket
- ORB `reconcile_after_reconnect()` may return `error` even though V11 is connected
- fill checking and SL/TP management can silently stop working after reconnect

This is a root architecture issue.

## Required Change

Add an explicit ORB rebind path.

Recommended implementation:

1. Add `ORBAdapter.rebind_ib(ib, contract)`.

It should update:

- `self._ib`
- `self._contract`
- `self._context.ib`
- `self._context.contract`
- `self._execution.ib`
- `self._execution.contract`

2. Add `MultiStrategyRunner.rebind_orb_connections()`.

It should:

- iterate engines
- find engines with `rebind_ib`
- look up the current qualified contract from `self._conn._contracts`
- rebind to `self._conn.ib`

3. In `V11LiveTrader.run()`, after `ensure_connected()` succeeds and before `_reconcile_positions()`, call the runner rebind when reconnect is detected.

## Tests Required

Add tests proving:

- ORB adapter/executor/context initially hold `old_ib`
- simulated reconnect swaps `conn.ib` to `new_ib`
- runner rebind updates ORB adapter/executor/context to `new_ib`
- `_reconcile_positions()` invokes ORB reconciliation using the new `ib`

## Guardrails

Do not:

- rewrite `IBKRConnection`
- remove `force_disconnect`
- remove `persistent_failure`
- remove `last_outage_s`
- remove stream restart logic
- weaken launchd/daily restart behavior

## Phase 1 Acceptance Criteria

After any reconnect:

- ORB uses the current live `IB()` instance
- ORB can query broker positions
- ORB can detect fills
- ORB can place/cancel SL/TP
- ORB can flatten if needed

---

# Phase 2 — Fix ORB Position-State Convergence

## Finding

`IBKRExecutionEngine` emits safety-flatten fills:

- `ORPHAN_FLATTEN`
- `NAKED_FLATTEN`

But `ORBAdapter._on_fill()` only treats these as exits:

- `SL`
- `TP`
- `BE`
- `MARKET`
- `CLOSED`

It does not include:

- `ORPHAN_FLATTEN`
- `NAKED_FLATTEN`

`ORBStrategy.on_fill()` also only handles:

- `SL`
- `TP`
- `MARKET`
- `BE`

It ignores:

- `CLOSED`
- `ORPHAN_FLATTEN`
- `NAKED_FLATTEN`

## Why This Matters

After a protective flatten:

- broker can be flat
- executor can reset flat
- but strategy can remain `IN_TRADE`
- risk manager can still think XAUUSD has an open position
- future trades can be blocked incorrectly
- heartbeat/status can be misleading

This is state corruption after a safety feature fires.

## Required Change

Fix this in `ORBAdapter`, not in frozen V6 strategy code unless Nick explicitly approves.

Recommended design:

```python
EXIT_REASONS = {
    "SL", "TP", "BE", "MARKET", "CLOSED",
    "ORPHAN_FLATTEN", "NAKED_FLATTEN",
}
```

For every exit reason:

- call V6 strategy if it can handle the reason
- force strategy state to `DONE_TODAY` if needed
- clear RiskManager position with `record_trade_exit`
- compute PnL when entry basis is known
- if entry basis is unknown or invalid, record `0.0` PnL and log a warning

## Tests Required

Add tests in `v11/tests/test_orb_adapter.py`:

- `ORPHAN_FLATTEN` clears RiskManager position
- `ORPHAN_FLATTEN` sets ORB strategy `DONE_TODAY`
- `NAKED_FLATTEN` clears RiskManager position
- `NAKED_FLATTEN` sets ORB strategy `DONE_TODAY`
- `CLOSED` also forces strategy `DONE_TODAY`
- if `entry_price == 0` or direction missing, PnL is recorded as `0.0` with warning, not a bogus large number

## Guardrails

Do not:

- remove the naked-position invariant
- remove orphan flatten behavior
- rewrite V6 strategy logic unless explicitly approved

## Phase 2 Acceptance Criteria

Any exit or safety flatten converges:

- broker flat
- executor flat
- strategy `DONE_TODAY`
- risk manager no open XAUUSD position
- heartbeat/status no longer says in-trade

---

# Phase 3 — Make Emergency Shutdown ORB-Aware

## Finding

`V11LiveTrader._emergency_shutdown()` currently:

1. cancels all orders globally
2. if disconnected, attempts reconnect and closes legacy `TradeManager` positions
3. writes emergency state
4. calls `_cleanup()`
5. disconnects and exits

`ORBAdapter.cleanup()` closes ORB only if `self._execution.has_position()` is true.

But `IBKRExecutionEngine.close_at_market()` already has broker-truth logic to flatten orphan positions even when internal `_position == 0`.

That broker-truth close path is not guaranteed to run in emergency shutdown if ORB internal state is flat but broker has a position.

## Why This Matters

An emergency shutdown while ORB has a broker-side orphan could:

- cancel all SL/TP orders
- fail to close the actual broker position
- exit the process
- leave a naked position requiring manual intervention

## Required Change

Make ORB emergency close explicit.

Recommended implementation:

1. Add `ORBAdapter.emergency_close(reason: str)`.

It should:

- log an ORB emergency close attempt
- call executor's broker-truth-aware `close_at_market()` path
- not depend only on `has_position()`

2. In `V11LiveTrader._emergency_shutdown()`, iterate engines and call `emergency_close()` if present.

3. Keep existing legacy `TradeManager` emergency behavior for non-ORB systems.

## Tests Required

Add tests proving:

- emergency shutdown calls ORB `emergency_close`
- ORB `emergency_close` calls broker-truth-aware close path even if internal position is flat
- existing `cancel_all_orders` and non-ORB emergency behavior remain intact

## Guardrails

Do not:

- remove `cancel_all_orders()` unless separately reviewed
- remove `ORBAdapter.cleanup()`
- weaken emergency shutdown exit behavior

## Phase 3 Acceptance Criteria

Emergency shutdown while ORB is involved must attempt broker-truth flatten before process exit.

---

# Phase 4 — Order Placement Validation and No-Fill Root Cause

## Finding

The May 11 no-fill recurrence has not yet been fully root-caused.

Current possibilities:

1. `triggerMethod=7` was not received or honored by IBKR/Gateway.
2. The BUY leg was not active because of OCA/transmit staging behavior.
3. `STP LMT` triggers but misses due to limit envelope.
4. Contract type / paper data farm / Gateway version changed behavior.
5. `orderStatusEvent` observability was incomplete before the May 11 ID-ordering fix.

## Required Diagnostic Interpretation

Use tomorrow's first `DIAG: openTrades after placement` block.

Check:

- both BUY and SELL appear
- both have same OCA group
- order type is `STP LMT`
- `triggerMethod=7`
- `tif=DAY`
- status is active
- BUY is not stuck staged/unsubmitted

Then if price crosses:

- did status become `Filled`?
- did status become a live limit order?
- did it remain `Submitted` with no trigger?
- did it cancel/inactivate?

## Do Not Patch Blindly

Do not change order placement again until the diagnostic says which problem exists.

If IBKR shows trigger method stripped:

- investigate IB API/Gateway contract/order attribute compatibility

If BUY leg is not active:

- revisit OCA/transmit staging
- consider both legs `transmit=True` with OCA if safe and tested

If `STP LMT` triggers but rests unfilled:

- limit buffer is too tight for observed XAU behavior
- consider larger buffer or return to `STP` if trigger evidence is now clean

If order remains active and untriggered despite bid/ask crossing:

- this is likely IBKR paper/Gateway/contract trigger behavior
- consider a controlled paper integration test and/or IBKR support escalation

## Tests Required

Add or extend a deterministic fake-IB order lifecycle test harness that can simulate:

- placement accepted
- status transitions
- BUY entry fill
- SELL sibling OCA cancel
- `STP LMT` trigger but no fill
- terminal cancel before fill
- diagnostic openTrades snapshot

Do not rely only on order object attribute tests.

## Phase 4 Acceptance Criteria

The team can explain the next no-fill/fill outcome from logs without guessing.

---

# Phase 5 — ORB Lifecycle Test Harness

## Finding

Current tests cover many pieces, but not the full ORB lifecycle.

Existing tests cover:

- order attributes
- entry IDs cleared after fill
- reconnect recovery components
- naked-position invariant
- broker-truth orphan close

Missing is an integrated deterministic lifecycle test.

## Required Change

Build a fake-IB lifecycle harness for ORB, not connected to IBKR.

It should simulate:

1. range ready
2. entry OCA placement
3. early order status events
4. entry fill
5. SL/TP placement
6. TP fill
7. SL fill
8. `CLOSED` fallback
9. reconnect while flat
10. reconnect while in position
11. broker has position but internal flat
12. internal has position but broker flat
13. naked-position invariant flatten
14. emergency shutdown while ORB broker position exists

## Why This Matters

This catches convergence bugs across:

- executor
- adapter
- strategy state
- risk manager
- broker truth

That is where the current risk is.

## Phase 5 Acceptance Criteria

One test suite can prove normal and abnormal ORB lifecycle paths converge to expected state.

---

# Phase 6 — Optional Interface Cleanup After Safety Fixes

Only after Phases 1–5 are done.

## Suggested Small Architecture Additions

Add narrow public methods to ORB live components:

- `ORBAdapter.rebind_ib(ib, contract)`
- `ORBAdapter.emergency_close(reason)`
- `ORBAdapter.get_protection_status()`
- `IBKRExecutionEngine.get_broker_position()`
- `IBKRExecutionEngine.has_active_protection()`
- `IBKRExecutionEngine.get_active_order_snapshot()`
- `IBKRExecutionEngine.mark_entry_failed(reason)`

## Why

This reduces private-field coupling from:

- `run_live.py`
- heartbeat writer
- order reconciler
- tests

## Guardrail

Do not turn this into a broad refactor. Add only methods used by existing safety/reconnect flows.

---

# Non-Goals

Do not do these as part of this remediation:

- rewrite V11
- rewrite V6 ORB strategy math
- introduce new strategy research
- re-enable EURUSD
- enable LLM price-only gating
- move to live money
- replace IBKR
- refactor `run_live.py` broadly
- change daily restart / launchd / health-check architecture unless a specific failure recurs

---

# Recommended Execution Order for Coding Agent

## Step 1 — Tomorrow operational verification

Perform Phase 0 checks and preserve logs.

## Step 2 — Safety architecture fixes

Implement Phases 1–3:

1. ORB reconnect rebind
2. safety-flatten fill convergence
3. ORB-aware emergency shutdown

Run relevant tests after each phase.

## Step 3 — Order-placement diagnosis

Use Phase 4 to decide whether more order changes are justified.

Do not change order semantics until diagnostic evidence identifies the failure mode.

## Step 4 — Lifecycle harness

Implement Phase 5 to prevent future recurrence of state convergence bugs.

## Step 5 — Small interface cleanup

Only if needed, add Phase 6 methods to reduce private-state coupling.

---

# Final Judgment

The first review was sufficient to identify the main risks. It was not ideal as the final execution artifact because it mixed tomorrow's readiness checklist, incident analysis, and architectural suggestions.

This rewritten plan should be used as the coding-agent handoff.

If its phases are followed, the system should become workable in the practical sense required for paper proof-of-life:

- entry placement is observable
- reconnect does not strand ORB on stale sockets
- positions converge across broker/executor/strategy/risk state
- emergency shutdown is ORB-aware
- future fixes are guided by lifecycle tests rather than ad hoc patches

The system still needs live paper validation. No document or mocked test can prove IBKR paper XAUUSD trigger behavior without observing the broker-side order lifecycle.
