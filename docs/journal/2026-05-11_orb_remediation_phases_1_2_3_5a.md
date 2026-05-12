# ORB remediation Phases 1–3 + 5a — implemented per reviewer's revised plan

**Date:** 2026-05-11 evening
**Status:** Code changes landed, 548 / 548 v11 tests pass (+23 net new).
**Driver:** reviewer's revised execution order in
`docs/superpowers/reviews/2026-05-11-v11-orb-remediation-plan-reviewer-reply.md`.

This journal documents what was implemented this evening after Nick
approved the reviewer's reply as the final plan. Phases 0 (tomorrow
operational verification), 4 (order-placement diagnosis using
tomorrow's DIAG), 5b (full lifecycle matrix), and 6 (interface
cleanup) are deferred per the revised order — they need either real
data or further work that isn't blocking tomorrow's trade window.

## The loop that produced this work

Today the v11 paper system reproduced the 2026-04-24 BUY-stop no-fill
bug live for the first time (price went +$40 above the 4705.56 stop
and the order never triggered). After applying three local fixes
(A diagnostic, B logging-gap, C1 STP→STP LMT) this morning, the
reviewer wrote `2026-05-11-v11-orb-remediation-plan.md`. I responded
with `…-response.md` flagging eight counter-arguments. The reviewer
replied accepting most and correcting one — most importantly,
**verifying** Phase 1 was a real architectural bug, not speculative.

Nick approved the reviewer's revised order as the final plan and
asked me to go.

## What landed

### Phase 5a — minimal fake-IB lifecycle harness

New file: `v11/tests/lifecycle_harness.py`.

A stateful `FakeIB` that mimics the ib_insync surface ORB touches
(placeOrder, openTrades, trades, positions, cancelOrder, reqMktData,
sleep, isConnected, orderStatusEvent, pendingTickersEvent,
disconnectedEvent). Plus a `FakeIBKRConnection.simulate_reconnect()`
that re-assigns `self.ib = FakeIB()` — the exact production
behavior at `v11/execution/ibkr_connection.py:77`.

The harness is deliberately minimal — just enough to make the
Phase 1 rebind regression test honest (mock-based tests can't
distinguish "old instance" from "new instance" cleanly). Phase 5b
will expand it to the full normal/abnormal matrix (TP/SL fills,
naked-flatten, emergency shutdown with broker orphan,
reconnect-while-in-position).

Filename is `lifecycle_harness.py` not `test_*.py` so pytest doesn't
collect it; tests import via `from v11.tests.lifecycle_harness
import FakeIB, FakeIBKRConnection`.

### Phase 2 — safety-flatten convergence in `ORBAdapter._on_fill`

File: `v11/live/orb_adapter.py`.

V6's frozen `ORBStrategy.on_fill` handles only `ENTRY`, `SL`, `TP`,
`MARKET`, `BE`. The executor (`IBKRExecutionEngine`) emits three
additional defensive exit reasons that V6 ignores:

- **CLOSED** — position vanished without detected SL/TP fill
  (`_check_position_vanished` fallback in the executor).
- **ORPHAN_FLATTEN** — `reconcile_after_reconnect` couldn't re-arm
  the bracket (long outage or no range_info) and flattened the
  broker-side orphan.
- **NAKED_FLATTEN** — the per-tick `_check_naked_position_invariant`
  fired because a position was held without an active SL+TP at the
  broker.

Before this change, after any of those fired the broker was flat,
the executor reset its `_position`, but the strategy stayed
`IN_TRADE` and the risk manager kept the XAUUSD position open —
blocking future trades and producing a misleading status line.

Fix:

```python
EXIT_REASONS = frozenset((
    "SL", "TP", "BE", "MARKET",
    "CLOSED", "ORPHAN_FLATTEN", "NAKED_FLATTEN",
))
SAFETY_FLATTEN_REASONS = frozenset((
    "CLOSED", "ORPHAN_FLATTEN", "NAKED_FLATTEN",
))
```

`_on_fill` now (a) processes all `EXIT_REASONS` through the
risk-manager path, (b) guards against `entry_price == 0` or
`direction is None` by recording `PnL = $0.00` with a warning
(safety-flatten can fire on an orphan the strategy never saw),
(c) forces the strategy to `DONE_TODAY` for `SAFETY_FLATTEN_REASONS`
so V6's frozen state machine doesn't strand us `IN_TRADE`.

Tests: `TestSafetyFlattenConvergence` in
`v11/tests/test_orb_adapter.py` — 6 tests covering ORPHAN_FLATTEN,
NAKED_FLATTEN, CLOSED, the missing-entry-basis guard, the warning
log emission, and a SL regression (the established path must still
flow through V6's state machine).

### Phase 1 — ORB reconnect rebind

Files:
- `v11/live/orb_adapter.py` (+ `rebind_ib`)
- `v11/live/multi_strategy_runner.py` (+ `rebind_orb_connections`)
- `v11/live/run_live.py` (call site in the main loop)

The verified bug: `IBKRConnection.connect()` line 77 executes
`self.ib = IB()`. ORB components (`ORBAdapter._ib`,
`LiveMarketContext.ib`, `IBKRExecutionEngine.ib`) cache the original
handle at construction and are never told about the swap. After
reconnect, every call against the cached handle silently hits a
disconnected socket — `placeOrder`, `openTrades`, `positions`,
`cancelOrder`, `reconcile_after_reconnect`, fill detection.

The fix walks every ORB-owned handle and swaps in the new one.
There's a wrinkle the original reviewer's writeup glossed over:
`LiveMarketContext` runs `_subscribe_ticks()` in `__init__`, which
calls `self.ib.reqMktData(...)` AND hooks `self.ib.pendingTickersEvent
+= self._on_ticker_update`. After reconnect, the cached `ticker`
handle is dead and the listener is on a defunct event object.
`rebind_ib` therefore also:

1. best-effort unhooks the old-ib listener,
2. re-runs `reqMktData` on the new ib,
3. re-hooks the listener on the new ib's `pendingTickersEvent`,
4. resets `IBKRExecutionEngine._hooked_status_event = False` so
   the next `set_orb_brackets` re-hooks `orderStatusEvent` on the
   new ib.

`MultiStrategyRunner.rebind_orb_connections()` iterates engines and
calls `rebind_ib` on any that expose it; non-ORB engines (Darvas,
LevelRetest) don't need this because TradeManager reads `conn.ib`
lazily.

Call site in `run_live.py`:

```python
if not was_connected and self.conn.connected:
    # Just reconnected — re-point ORB engines at the fresh ib
    # instance BEFORE reconciling, so the reconcile_after_reconnect
    # path queries the live socket.
    self.runner.rebind_orb_connections()
    self._reconcile_positions()
```

Tests: `v11/tests/test_orb_reconnect_rebind.py` — 10 tests including
end-to-end FakeIB swap via `simulate_reconnect`, verification that
the listener moves from old ib to new ib, the executor's hook flag
resets, the contract handle swaps through every component, the
runner skips engines without `rebind_ib`, and the runner warns on
missing qualified contract rather than crashing the reconnect path.

### Phase 3 — ORB-aware emergency shutdown

Files:
- `v11/live/orb_adapter.py` (+ `emergency_close(reason)`)
- `v11/live/run_live.py` (engine iteration inside `_emergency_shutdown`)

The scenario: `_emergency_shutdown` calls `conn.cancel_all_orders()`
which wipes protective SL/TP on any ORB position. The legacy
`TradeManager.emergency_close` path only handles Darvas/Retest. If
there's an ORB position at the broker (orphan, or just an active
trade) it survives emergency shutdown unprotected.

Fix: `ORBAdapter.emergency_close(reason)` delegates to the
executor's `cancel_orb_brackets()` + `close_at_market()`. The
executor's `close_at_market` is already broker-truth-aware (queries
`ib.positions()` when internal `_position == 0` to catch orphans —
the 2026-04-21 fix is unchanged), so this method intentionally does
**not** check `has_position()` — the whole point is to flatten
orphans the executor doesn't know about.

`_emergency_shutdown` now iterates `runner.engines` and calls
`emergency_close(reason)` on any engine that exposes it, between
`cancel_all_orders` and the legacy TradeManager block. Best-effort:
each engine's call is wrapped in try/except so one engine's failure
doesn't skip the next.

Tests: `v11/tests/test_orb_emergency_close.py` — 7 tests covering
cancel + close are called, unconditional on `has_position()`,
survives cancel raising, survives close raising, logs the reason,
and the emergency-shutdown iteration loop pattern (one engine
raising doesn't skip the next).

## Test results

```
.venv/bin/python -m pytest v11/tests/ -q
...
548 passed in 1.29s
```

Net change: +23 tests (525 → 548). All new tests cover the new code
paths; no existing tests required modification.

Touch points by file:

| File | Δ |
|---|---|
| `v11/live/orb_adapter.py` | +143 / −12 |
| `v11/live/multi_strategy_runner.py` | +35 / 0 |
| `v11/live/run_live.py` | +25 / −1 |
| `v11/tests/test_orb_adapter.py` | +146 / 0 (TestSafetyFlattenConvergence) |
| `v11/tests/lifecycle_harness.py` | new, 250 lines |
| `v11/tests/test_orb_reconnect_rebind.py` | new, 240 lines |
| `v11/tests/test_orb_emergency_close.py` | new, 170 lines |

## Design choices worth recording

1. **The `LiveMarketContext` rebind required re-subscription, not
   just attribute swap.** Original reviewer plan said
   `self._context.ib = ib`. That's necessary but not sufficient
   because V6 cached the ticker handle and hooked the listener at
   construction time. Documented in the `rebind_ib` docstring.
2. **The `_hooked_status_event` flag is reset rather than the hook
   being re-installed eagerly.** Lazy re-hook on the next
   `set_orb_brackets` call is cleaner than touching `orderStatusEvent
   += / -=` directly during rebind. Idempotent + side-effect-free
   during the reconnect path.
3. **`emergency_close` deliberately does NOT consult
   `has_position()`.** That's the whole bug class Phase 3 closes —
   internal state can be wrong, only the broker is authoritative.
4. **PnL-zero guard on safety-flatten reasons.** If an orphan came
   from reconcile-rebind adoption, V6's strategy state machine never
   saw the entry and `entry_price = 0.0`. The naive `_calc_pnl`
   would return the full exit price as PnL, a catastrophically
   misleading number that would also blow through the daily-loss
   gate. The guard records `$0.00` and warns. We could try to
   reconstruct the basis from broker `avgCost` but at that point
   it's been hours and the source-of-truth chain is broken — better
   to flag it explicitly and leave reconciliation to the operator.
5. **`lifecycle_harness.py` as a non-test module.** Pytest collects
   `test_*.py`; using a different prefix makes the harness importable
   without being collected, mirroring how some other projects
   organize fixtures.

## What's still pending

Per the reviewer's revised execution order:

- **Phase 0** — tomorrow morning's operational verification. Inspect
  `~/.daily_restart.log` for the 01:15 EDT unattended fire. Verify
  v11 picked up the new code. Watch for the first
  `DIAG: openTrades after placement` block in the v11 log around
  04:00 EDT. This is mostly Nick's call to watch — I can help
  inspect logs.
- **Phase 4** — order-placement diagnosis. Waits for Phase 0
  evidence. Includes the C1 exit clause (revert STP LMT if the
  diagnostic shows plain STP would have worked), the
  `STP_LMT_MISSED_LIMIT` classification, and the both-legs-
  `transmit=True` candidate fix.
- **Phase 5b** — expand the lifecycle harness to the full matrix
  (TP/SL fills, BE adjustment, naked-flatten, emergency-shutdown
  with broker orphan, reconnect-while-in-position, etc.). Should
  happen after Phases 1–3 are stable in production.
- **Phase 6** — narrow interface cleanup (`get_protection_status`,
  `get_broker_position`, `has_active_protection`, etc.). Only if
  needed.

## Where to look as a reviewer

- The reviewer's plan: `docs/superpowers/reviews/
  2026-05-11-v11-orb-remediation-plan.md`
- My response with counter-arguments: same dir, `…-response.md`
- The reviewer's reply that settled the plan: same dir,
  `…-reviewer-reply.md`
- The diff for this work — every change is in the files listed in
  the table above, all surrounded by `Phase {1,2,3,5a}, 2026-05-11
  remediation` comments.

## Caveat

Tomorrow's 04:00–12:00 EDT trade window still has to validate that
the morning's A+B+C1 fixes closed the no-fill bug. Phases 1–3 above
harden the surrounding architecture (state convergence, reconnect
correctness, emergency-close safety) but do not, by themselves,
prove the order will fill. The two work streams are independent.
