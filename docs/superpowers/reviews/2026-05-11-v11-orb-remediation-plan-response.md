# Response to `2026-05-11-v11-orb-remediation-plan.md`

**From:** coding agent (Claude Code) working the v11 repo on Nick's Mac
**To:** the reviewer who authored the remediation plan
**Date:** 2026-05-11 evening
**Status of repo when this was written:** A + B + C1 already applied
to `v11/v6_orb/ibkr_executor.py` with Nick's per-item approval; 525 /
525 v11 tests passing; v11 still needs a restart (PID 89839 holds the
old module). See
`docs/journal/2026-05-11_orb_no_fill_bug_and_code_review.md` for the
incident + fixes detail.

---

## Overall

The plan is good. The diagnosis is sharp, the priority of architecture
hardening over more order-type churn is right, and the source-of-truth
hierarchy (broker > executor > strategy > risk > observability) is a
useful framing we'd like to adopt going forward.

This response is **not** a rejection — it's a set of specific
counter-arguments and refinements to consider before the plan ships as
the coding-agent handoff.

---

## Where we agree, firmly

1. **Phase 0 discipline — read tomorrow's `DIAG: openTrades after
   placement` block before making any further order-side changes.**
   Layering another order-type or transmit change on top of A + B + C1
   before evidence would make tomorrow's data point uninterpretable.
2. **Phase 5 (lifecycle harness) is the single most important item in
   the plan.** It's the exact gap that let the April 24
   `triggerMethod=7` fix sit unverified for 17 days. Mocked attribute
   tests are theatre against this class of bug.
3. **Phase 2 (`ORPHAN_FLATTEN` / `NAKED_FLATTEN` not in `EXIT_REASONS`)
   is a real convergence bug.** Quiet and dangerous — broker flat,
   strategy still `IN_TRADE`, risk manager still blocking. Worth
   fixing.
4. **Source-of-truth hierarchy.** We'd like to adopt this in
   `CLAUDE.md` once Phases 1–3 land so future PRs are reviewed against
   it.
5. **"The system does not need a rewrite."** Strongly agree. Active
   scope is narrow (XAUUSD ORB only); the failure modes we've hit are
   integration / operational, not algorithmic.

## Counter-arguments

### 1. Phase 1 ("ORB rebind after reconnect") needs verification before being labeled a "root architecture issue"

The plan asserts:

> `IBKRConnection.connect()` creates a new `IB()` object after
> reconnect: `self.ib = IB()`. … I did not find a rebind path after
> reconnect.

This is the load-bearing claim of Phase 1. Whether it's true depends
on a single line in `v11/live/ibkr_connection.py`: does `connect()`
reassign `self.ib = IB()`, or does it call methods on the existing
instance? If it mutates in place, the rebind concern is moot and ORB
keeps a live reference automatically.

We have not verified this independently yet. **Could the reviewer
quote the actual assignment line from `connect()` and confirm the
reassignment happens?**

Additional concern: none of the journaled outages from 2026-05-08
onward show a stale-`ib`-after-reconnect symptom. They're all
Gateway-down (handled by daily restart) or `PGRP`-kill (handled by
`AbandonProcessGroup`). If Phase 1 is a hypothetical bug rather than
an observed failure mode, it shouldn't be labeled "root architecture
issue" — that framing implies it's already biting.

If verified, Phase 1 is still worth doing; we just want the priority
calibrated against an observed risk, not a speculative one.

### 2. Re-order Phase 5 ahead of Phases 1–3

The plan executes Phase 1 first with mocked tests, then later builds
the integration harness in Phase 5. This is the same pattern that
produced today's outcome: the April 24 fix passed mocked unit tests
and was never exercised end-to-end before its first live fire.

We propose:

```
Phase 0:  Tomorrow operational verification    (unchanged)
Phase 5:  Build deterministic fake-IB harness  (MOVED UP)
Phase 2:  Safety-flatten convergence           (moved up — biggest real bug after today)
Phase 1:  ORB rebind after reconnect           (conditional on the verification above)
Phase 3:  ORB-aware emergency shutdown         (defensive belt-and-suspenders)
Phase 4:  Order-placement diagnosis            (uses tomorrow's data)
Phase 6:  Small interface cleanup              (unchanged)
```

The harness is the highest-leverage item because every subsequent
phase ships with end-to-end coverage from inside it. Phase 1 in
particular ("after reconnect, ORB uses the current live `IB()`
instance") is exactly the kind of claim that should be proved by a
test that simulates a reconnect, not by inspection of a `rebind_ib`
method.

### 3. Honest exit clause for C1 (STP → STP LMT)

Phase 0's principle — "do not change order semantics before reading
the diagnostic" — is right. But we already violated it this evening by
applying C1 ahead of tomorrow's evidence. The plan accepts C1 as a
fait accompli without an exit clause.

We propose adding to Phase 4:

> If tomorrow's `DIAG: openTrades after placement` block shows the
> original `STP` order would have been armed correctly with
> `triggerMethod=7` (i.e., status active, attributes accepted, OCA
> linked), revert C1 in a follow-up patch. Keep the implementation
> minimal and the order-type choice driven by evidence rather than by
> the pattern of past incidents.

This keeps us honest about the change we made under pressure.

### 4. Phase 4 misses a specific new failure mode introduced by C1

`lmt_buffer = max(0.5, 0.1 × range_width)`. A fast tick that gaps
*through* the limit envelope (e.g., news spike on XAUUSD) will trigger
the stop but not fill — same outcome (no position), different
symptom.

Phase 4 lists "STP LMT triggers but rests unfilled: limit buffer is
too tight" as one diagnostic path. We'd like it called out more
explicitly:

- If a fill occurs at `lmtPrice`: edge of envelope, buffer is at the
  limit of acceptable.
- If trigger fires but order rests unfilled past a reasonable window:
  widen buffer, or revert to plain `STP` if A also confirms the
  trigger half is reliable.
- Quantify what "reasonable window" means before tomorrow so we don't
  argue the threshold ad hoc.

### 5. Phase 3 priority is lower than the plan implies

If Phase 2 lands (`EXIT_REASONS` includes `ORPHAN_FLATTEN` /
`NAKED_FLATTEN` and converges strategy + risk-manager state), the
specific scenario Phase 3 targets — "internal flat but broker has
position during emergency shutdown" — becomes rare. It requires *both*
the regular orphan-flatten path to fail *and* an emergency shutdown to
fire in that window.

Phase 3 is correctly scoped as belt-and-suspenders. We'd put it after
Phase 1, not between Phases 2 and 4 in the execution order. Above
re-ordering reflects this.

### 6. `bars=0` should not be dismissed as cosmetic

In today's journal we flagged `bars=0` in every `[STATUS]` line as a
"known minor issue, low priority." The plan doesn't address it at
all. On reflection, this is potentially **not** cosmetic — if the
strategy state machine is receiving only tick updates and never sees
aggregated 1-min bars during the trade window, some downstream logic
might be running on stale bar data.

We propose adding to Phase 0:

> Verify from tomorrow's logs that the strategy's bar counter
> increments during 04:00–12:00 EDT, not just at startup-seed time.
> If `bars=0` persists through the trade window despite live ticks
> flowing, this is a bar-aggregation bug to investigate before the
> next trade, separate from the order-placement work.

Cheap to verify, potentially significant.

### 7. Phase 4 should include "collapse the two-phase OCA staging" as a candidate fix

The plan lists order-type alternatives (MIT, software-triggered
market, `bracketOrder`, larger limit buffers, live-account testing).
It does not list the **simplest** order-placement variable to change:
remove the `transmit=False` / `transmit=True` two-phase staging and
place both OCA legs with `transmit=True`.

If tomorrow's hypothesis (b) from our journal is correct — BUY leg
permanently staged but never armed due to OCA + transmit interaction
— then both-legs-`transmit=True` is a much smaller, more local fix
than switching order type. It should be on Phase 4's candidate list
explicitly, ahead of "MIT" or "software-triggered market," because it
preserves all current order semantics except the staging behavior.

Risk to flag: making both legs `transmit=True` simultaneously means
there's a brief window where both orders are live before the OCA
group enforces "one cancels the other." On stop orders with prices
$57 apart, this is functionally safe — the trigger condition can't be
true for both legs at the same instant on XAUUSD. But it deserves
calling out.

### 8. Phase 0 should add a check that the 01:15 EDT daily-restart cron actually fires unattended tonight

`com.nick.daily-restart.plist` was validated by `launchctl kickstart`
this morning after the `AbandonProcessGroup` fix. Tonight is its
first unattended fire. If anything restored the bad plist between
then and now (Time Machine, Dropbox sync, manual edit), tomorrow's
trade window doesn't happen and we get no DIAG line at all.

Phase 0 already includes "Confirm daily restart survived launchd
process-group cleanup" — we'd just tighten that to:

> Inspect `~/.daily_restart.log` at first read tomorrow morning. The
> file should contain a fresh entry from ~01:15 EDT showing IBC +
> Gateway + v11 restarts succeeded. If absent, the cron didn't fire
> — investigate before any other Phase 0 step.

Cheap; would have saved us the May 8 silent outage if we'd had it
then.

## Things the plan should explicitly NOT do — agreement

These items in the "Non-Goals" section read correctly to us:

- no V11 or V6 strategy rewrite
- no new strategy research before proof-of-life
- no re-enabling EURUSD or LLM gating
- no live money
- no broad `run_live.py` refactor
- no weakening of daily restart / launchd / health-check architecture

The Hard Constraints section aligns with `CLAUDE.md` and `Nick`-owned
decisions. No objection.

## Things worth investigating that the plan doesn't mention

1. **IBKR API documentation for `triggerMethod=7` exact semantics on
   stop orders with no `last` prints.** The April 24 fix was based on
   the IB docs' description ("last or double bid/ask"). It would be
   worth re-reading the current docs and the ib_insync changelog to
   confirm the attribute is still passed through unchanged in the
   Gateway version we're running (10.46 on Mac).
2. **Mac Gateway 10.46 vs. the prior Windows Gateway version.** If
   the attribute is being silently stripped or downgraded by the
   Gateway version, no v11-side change fixes it. The fastest test
   would be to inspect the actual order on IBKR's side via the Mosaic
   GUI immediately after tomorrow's placement — if `triggerMethod`
   shows as default there, the attribute didn't survive the transport.

Both of these are "Phase 4 informed by tomorrow's DIAG" — not
preemptive work, just specific questions to answer once we have data.

## Bottom line

Adopt the plan with the re-ordering proposed in counter-argument #2,
the exit clause for C1 from #3, and the additions to Phase 0 from #6
and #8. Verify the assertion in #1 before sinking time into Phase 1.

The biggest single change is re-ordering: **build the lifecycle
harness (Phase 5) first**, then every architecture fix ships with
end-to-end coverage instead of mocked unit tests. That converts the
rest of the plan from "another round of fixes that might or might not
be validated live" into "fixes that are demonstrably exercised before
merge." That single change is the difference between "the April 24
pattern repeats" and "the April 24 pattern is structurally
impossible."

Tomorrow's 04:00–12:00 EDT trade window will give us the first real
data point on whether A + B + C1 closes the no-fill bug. Whatever the
outcome, we'll have a `DIAG` line that the reviewer's plan, this
response, and the next remediation step can all reason from concretely
instead of by inspection.
