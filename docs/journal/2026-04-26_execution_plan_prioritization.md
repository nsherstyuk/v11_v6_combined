# 2026-04-26 — Execution Plan Prioritization Notes

Related plan: `docs/superpowers/reviews/2026-04-24-v11-live-code-review-execution-plan.md`

## Context

The 2026-04-24 code review produced a 6-phase execution plan. Phase 1
(deterministic staleness tests) was completed the same day. This note
records a re-assessment of the remaining phases heading into the first
week where `triggerMethod=7` is deployed and a real paper fill is
possible.

## Phase 1 — Done

Committed `c9a7d8f`. `_check_price_staleness()` now accepts an optional
`now_utc` parameter. Suite: 523 passed, 0 failures. No further work
needed here.

## Recommended priority re-ordering

### Do first: Phase 5 — Fault drills (originally sequenced 5th)

The plan lists fault drills as P1 but sequences them after Phases 2–4.
That sequencing is wrong. The fault drills are the highest-value
activity before relying on this system unattended, and they require no
code changes — just an operator, TWS paper, and a checklist.

No amount of API cleanup (Phase 2) or diagnostic methods (Phase 3)
tells you whether the naked-position invariant actually fires when you
manually cancel an SL in TWS, or whether the half-open socket tripwire
actually restarts the process under realistic conditions. Those
behaviors exist in code but have never been stress-tested against a
live IBKR paper session.

Run these drills Monday or Tuesday while you're watching the screen:

1. Kill Gateway while flat → confirm clean reconnect, no phantom orders
2. Kill Gateway while entry orders are resting → confirm reconciliation
   surfaces missing orders, strategy returns to RANGE_READY or DONE_TODAY
3. Kill Gateway immediately after entry fill → confirm position detected
   on reconnect, SL/TP re-armed (short outage) or flattened (long outage)
4. Manually cancel SL in TWS while in position → confirm naked-position
   invariant flattens within the grace window
5. Simulate placement stuck → confirm placement_stuck tripwire fires and
   wrapper restarts

Every failure in a fault drill becomes a journal entry and regression
test. That is how the system gets trustworthy.

### Do second: Phase 4 — Heartbeat watchdog script

A passive `check_heartbeat.py` that reads the heartbeat JSON and exits
nonzero on critical conditions is genuinely useful for unattended
operation. Wire it into Windows Task Scheduler or a simple polling loop.

Critical conditions to check:
- Heartbeat file missing or stale > 10 minutes
- `connected=false` beyond threshold
- `broker_pos != 0` and `has_sl == false`
- `broker_pos != 0` and strategy `in_trade == false`
- `persistent_failure=true`

The script should be passive by default — read, print, exit nonzero.
No order placement, no process killing unless explicitly enabled.

### Do third: Phase 2A — `mark_entry_failed()` public API

The private field mutation in `_reconcile_orders()` works and is tested.
It is a code quality item, not a safety item. Worth doing but not urgent.
Implement when next touching the reconciliation path.

### Do fourth: Phase 3 — RiskManager broker consistency

Useful eventually, but theoretical until the system has seen actual
fills. The position tracking hasn't been stress-tested because there
have been no fills yet. Implement after a week of real paper trades so
the divergence cases are empirically motivated rather than hypothetical.

### Defer: Phase 6 — Refactor `run_live.py`

Correctly deferred in the original plan. Do not touch until at least
one stable paper week is complete. The refactor is a complexity
reduction, not a safety improvement, and it carries real risk of
introducing subtle regressions in the live supervision path.

## The gate the plan doesn't mention

The `triggerMethod=7` fix landed after the plan was written. Monday is
the first empirical test of the full order lifecycle end-to-end:

```
IDLE → RANGE_READY → ORDERS_PLACED → (bid/ask excursion) → FILLED
→ SL/TP armed → exit
```

Every prior week validated a different segment of this chain. The
triggerMethod fix is the last missing piece. Before advancing to any
phase of the execution plan, the priority is to observe one clean fill
→ SL/TP armed → exit cycle on paper. That is the real gate for
declaring the system operationally ready.

## Summary

| Phase | Original sequence | Recommended sequence | Rationale |
|---|---|---|---|
| 1 — Deterministic tests | 1st | Done | Completed 2026-04-24 |
| 5 — Fault drills | 5th | 1st | Proves live safety behavior; no code needed |
| 4 — Heartbeat watchdog | 4th | 2nd | Unattended operation safety net |
| 2A — Public API | 2nd | 3rd | Code quality, not safety |
| 3 — Risk consistency | 3rd | 4th | Theoretical until fills happen |
| 6 — Refactor | 6th | Defer | Only after stable paper week |
