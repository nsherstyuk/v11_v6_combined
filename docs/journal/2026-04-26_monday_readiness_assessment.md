# 2026-04-26 — Monday Readiness Assessment & Sunday-Night Disconnect Test

## Purpose

Final pre-Monday audit of V11 live readiness. Captures:

1. The IBC + Gateway scheduled-restart configuration
2. A real disconnect that happened tonight (Sunday 21:52 UTC) and how
   the recovery code performed
3. What is now tested in production vs what remains untested
4. Two log issues observed and the decision to fix one but defer the other
5. Concrete failure modes to watch for Monday morning

## Configuration changes tonight

### IBC scheduled restart

`C:\IBC\config.ini`:

```
AutoRestartTime=01:00     # was 22:30
```

Why 01:00 specifically:
- IBKR's daily server reset is at midnight Eastern. Restarting *before*
  the reset (the previous 22:30 setting) means Gateway comes up clean
  but then the midnight reset hits and may leave the connection in an
  unstable state until morning.
- 01:00 EDT = 05:00 UTC, which is 1 hour after the EDT midnight reset
  and 1 hour *before* the ORB Asian range calc fires at 06:00 UTC.
- 00:30 was tried first but Gateway rejected it; 01:00 was accepted.
- DST caveat: in winter (EST), 01:00 local = 06:00 UTC = exactly the
  range calc time. Bump to 00:45 in November when clocks fall back.

### Manual Gateway-side step still required

Gateway also has its own internal Auto-Restart time in
**Configure → Lock and Exit → Auto-Restart**. IBC syncs this when it
restarts Gateway, but to eliminate any race condition the Gateway GUI
should also be set to **01:00**. This is a one-time GUI step the
operator has to do; the agent cannot do it from the file system.

## The Sunday 21:52 UTC disconnect — unplanned production test

While auditing readiness this evening, V11 (running since 20:30 UTC)
hit a real disconnect:

```
21:52:53  ERROR    Failed to connect after 3 attempts
21:52:53  ERROR    Connection lost — waiting 10s
21:53:03  INFO     Reconnecting... (disconnected 63s)
21:53:04  INFO     Connected to IBKR 127.0.0.1:4002
21:53:04  INFO     Re-qualified XAUUSD after reconnect
21:53:06  INFO     Restarted stream for XAUUSD
21:53:06  INFO     Reconciling positions after reconnect (outage=65.9s)...
21:53:06  INFO     reconcile[XAUUSD] -> error
21:53:06  INFO     Reconciliation complete: broker=set(), risk_mgr=set()
21:55:20  INFO     [STATUS] state=IDLE   ← back to normal
```

This is the half-open socket recovery code (committed 642c75f) working
in production, unprompted. 63-second outage, full clean recovery, no
operator intervention. **More valuable than any unit test** because it
proves the integrated reconnect → re-qualify → restart-stream →
reconcile chain holds under real broker conditions.

## Two issues observed and how we handled them

### Issue 1: `reconcile[XAUUSD] -> error` outcome

`v11/v6_orb/ibkr_executor.py:574 reconcile_after_reconnect()` returned
`"error"` because `self.ib.isConnected()` was False at the moment of
the call (line 597-598) — 2 seconds after socket reconnect, ib_insync
was still stabilizing.

**Why this was harmless tonight:** V11 was flat. No position to
re-arm. The portfolio-level reconciliation that ran immediately after
confirmed `broker=set(), risk_mgr=set()` — both flat, consistent.

**Why this could matter Monday:** if the same race happens
post-fill, the SL/TP re-arm step would be skipped. The
naked-position invariant (~60s grace) would still catch it and
force-flatten, but there'd be a window of unprotected exposure.

**Decision:** Do not patch tonight. Adding a retry to the live
reconcile path is a non-trivial change to broker-facing code right
before Monday. The naked-position invariant is the safety net. File
this for the post-Monday fault-drill phase (Phase 5 of the execution
plan, see `2026-04-26_execution_plan_prioritization.md`).

### Issue 2: UnicodeEncodeError on `→` arrow

Windows console codepage `cp1252` cannot encode U+2192. The log file
(UTF-8) wrote it fine — that's why the line appears in the log — but
the console echo raised `UnicodeEncodeError`. Reconciliation logic
itself completed normally.

**Decision:** Fix immediately. One-line change, replaced `→` with `->`
in `v11/live/run_live.py:949` (commit 2468f89). Safe because:
- The .py file is loaded into V11's running memory; editing the file
  on disk does not affect the running process
- The change takes effect on next V11 start, not tonight
- No need to restart V11

Audit confirmed all other unicode arrows in `v11/live/`,
`v11/v6_orb/`, and `v11/execution/` are inside comments only — they
never reach log output. Backtest scripts use `→` in print statements
but those are offline and don't matter for live operation.

## Tested in production tonight (real, not synthetic)

| Behavior | Result |
|---|---|
| Initial connection, contract qualify, bar seed | Worked at 20:30 UTC |
| Daily reset for 2026-04-27, gap history load (8 days) | Worked |
| Weekly close handling (running through Sun 21:00 UTC closeout) | No spurious alerts |
| **Half-open socket recovery** | **Survived real 63s disconnect** |
| Re-qualify contract on reconnect | Worked |
| Restart price stream on reconnect | Worked |
| Position reconciliation (flat) | Worked |
| State machine returns to IDLE | Currently IDLE waiting |
| 523-test suite | Passing |

## Untested — first empirical test Monday morning

| Behavior | Confidence | Failure mode |
|---|---|---|
| `triggerMethod=7` against real bid/ask excursion | Unit-tested only | STP never fires despite price past trigger (the whole reason for last week's failures) |
| SL/TP placement after entry fill | Code present, never executed in paper | Naked position; invariant flattens after ~60s grace |
| Naked-position invariant flatten path | Code present, never executed | Position remains naked indefinitely |
| IBC scheduled restart at 01:00 EDT (05:00 UTC) with V11 watching | Config set, never tested end-to-end | V11 may exit emergency_shutdown if Gateway restart takes longer than reconnect window — wrapper would relaunch |
| Reconcile race after a fill | The `-> error` outcome from tonight's recovery, but with a position open | Skipped re-arm; naked-position invariant catches it |

## Monday morning timeline (UTC)

```
00:00–06:00   Asian session bars accumulate
05:00         IBC restarts Gateway (01:00 EDT)
05:00–05:05   V11 sees disconnect, waits, reconnects, re-seeds
06:00         Range calc fires; ORB state IDLE → RANGE_READY
06:00–08:00   Gap/vol filter; LLM gate (mechanical auto-approve)
08:00         Trade window opens; brackets placed (BUY/SELL STOP @ range)
              ── First real test of triggerMethod=7 ──
First bid/ask
excursion past
a stop         Should fill (this is what was broken all week)
On fill        SL/TP placed via _place_sl_tp (also has triggerMethod=7
              now). First real test of post-fill execution.
16:00         Trade window closes; brackets cancel if no fill
```

## What to watch for and how to react

If at 06:00–06:05 UTC the log shows:
- `ORB: Calculating Asian range (0-6 UTC)` then `Range from IBKR bars: ...` → **good**
- `ORB state: IDLE -> RANGE_READY` → **good**

If at 08:00 UTC the log shows:
- `Brackets placed: BUY=... SELL=...` → **good**
- Anything containing `error`, `failed`, `silent` → investigate

If during the day price visibly trades past a STP and nothing fills →
the trigger method fix didn't work; consult IBKR tick log for whether
LAST trades printed; consider triggerMethod=2 ("last") as next attempt.

If a fill happens and the log shows SL/TP placement errors → the
naked-position invariant should flatten within 60s. Confirm
`Naked-position invariant: flattening` appears.

If IBC restart at 05:00 UTC causes V11 to emergency_shutdown → wrapper
will restart V11 within 30-60s. Should be back well before 06:00 UTC
range calc.

## Decisions deliberately deferred to Monday

- Reconcile retry on `error` outcome (Phase 5 fault-drill discovery)
- Diagnostic improvements to the reconcile pathway
- Any code change to the running session

The principle: a stable session is worth more than any incremental fix
applied at the last minute. We let what's running run.

## Final state going into Monday

- V11 running cleanly since 20:30 UTC, recovered from one real disconnect
- 523 tests passing, last commit 2468f89 (unicode fix, applies on next start only)
- IBC restart configured at 01:00 EDT
- Gateway GUI restart time still needs manual sync to 01:00
- No untracked work in progress
- Three operational unknowns gated on Monday morning: trigger fire,
  SL placement, scheduled restart
