# 2026-05-11 — Reviewer Reply to Remediation Plan Response

## Purpose

This is the reviewer reply to:

- `docs/superpowers/reviews/2026-05-11-v11-orb-remediation-plan.md`
- `docs/superpowers/reviews/2026-05-11-v11-orb-remediation-plan-response.md`

It clarifies which counter-arguments I agree with, which I partially accept, and which I do not accept based on verified code facts.

## Short Answer

I agree with most of the response's refinements.

The main correction is that the stale-`ib` concern is verified, not speculative:

- actual file: `v11/execution/ibkr_connection.py`
- `connect()` explicitly executes `self.ib = IB()` at line 77
- ORB adapter/context/executor are constructed once with the old `conn.ib`
- I still did not find a rebind path

So Phase 1 should stay in the plan. The wording can be softened from "already biting" to "verified latent architecture bug," but the fix should not be dropped.

## Agreements

### 1. Agree: Phase 0 discipline is correct

Do not change order semantics again before tomorrow's diagnostic.

The `DIAG: openTrades after placement` block should be treated as the first real evidence for whether A + B + C1 are active and accepted by IBKR.

### 2. Agree: Lifecycle harness should move earlier

I agree with moving the lifecycle harness earlier.

The original plan placed the harness after several fixes. The response is right that this repeats the failure mode of the April 24 `triggerMethod=7` fix: mocked attribute tests passed, but the lifecycle was not exercised end-to-end.

Recommended revised order:

1. Phase 0 — tomorrow operational verification
2. Phase 5a — minimal fake-IB lifecycle harness skeleton
3. Phase 2 — safety-flatten convergence
4. Phase 1 — ORB reconnect rebind
5. Phase 3 — ORB-aware emergency shutdown
6. Phase 4 — order-placement diagnosis using tomorrow's data
7. Phase 5b — expand lifecycle harness to full matrix
8. Phase 6 — optional interface cleanup

Nuance: do not let the harness become a large prerequisite that delays small safety fixes. Build the minimal harness first around the paths being changed.

### 3. Agree: Add an honest exit clause for C1

I agree.

The `STP -> STP LMT` change should remain evidence-driven, not sacred.

Add to Phase 4:

- If diagnostics show plain `STP` with `triggerMethod=7` would likely have been armed and trigger-capable, consider reverting C1.
- If `STP LMT` triggers but rests unfilled beyond a pre-defined threshold, either widen the buffer or revert to `STP` depending on trigger evidence.
- Do not keep `STP LMT` merely because it was the latest attempted fix.

### 4. Agree: Call out the C1 limit-envelope failure mode more explicitly

Agree.

For BUY `STP LMT`, a fast move above `lmtPrice` can produce the same practical outcome as no-fill: no broker position after breakout.

Coding agent should predefine a reasonable observation threshold before the next run, for example:

- if status changes from stop-like state to active limit state and remains unfilled for 30–60 seconds while price is beyond `lmtPrice`, classify as `STP_LMT_MISSED_LIMIT`

Exact threshold can be adjusted, but it should not be invented after seeing the outcome.

### 5. Agree: Both-legs-`transmit=True` should be an explicit Phase 4 candidate

Agree.

If tomorrow's diagnostic shows the BUY leg was not armed because of OCA/transmit staging, the smallest semantic change may be:

- keep OCA
- keep entry stop semantics
- place both entry legs with `transmit=True`

This should be considered before more invasive options like software-triggered market orders.

Risk to test/observe:

- a brief window with both stop orders live
- likely acceptable because the buy/sell stop triggers are far apart, but still deserves a fake-IB and paper validation test

### 6. Agree: Daily restart log should be checked first tomorrow

Agree.

Phase 0 should explicitly say:

- inspect `~/.daily_restart.log` first thing
- confirm a fresh unattended ~01:15 EDT restart occurred
- if absent, diagnose launchd/daily restart before ORB order behavior

### 7. Agree: IBKR docs / Gateway version should be part of Phase 4 evidence gathering

Agree.

If tomorrow's diagnostics suggest `triggerMethod` was stripped, ignored, or behavior differs from expectation, check:

- IBKR current trigger method documentation
- Gateway 10.46 behavior
- ib_insync pass-through behavior
- IBKR GUI/Mosaic order details immediately after placement

This is not preemptive work before diagnostics; it is a Phase 4 branch.

## Partial Agreements / Corrections

### 1. Phase 1 is verified, not speculative

The response asks whether `connect()` really reassigns `self.ib`.

It does.

Verified code:

- file: `v11/execution/ibkr_connection.py`
- lines 67–80
- line 77: `self.ib = IB()`

The response refers to `v11/live/ibkr_connection.py`; the actual file is `v11/execution/ibkr_connection.py`.

Because ORB receives `conn.ib` during construction in `MultiStrategyRunner.add_orb_strategy()` and I did not find a rebind path, Phase 1 remains valid.

I accept softening the language:

- from: "root architecture issue already biting"
- to: "verified latent architecture bug that can bite on any intraday reconnect"

But I do not accept demoting it to optional/speculative.

### 2. Phase 3 priority can be lower, but it should not disappear

I agree Phase 3 is belt-and-suspenders after Phase 2.

But I do not fully agree that Phase 2 makes Phase 3 rare enough to ignore. Phase 3 protects a different class of event:

- emergency shutdown triggered by price feed death / persistent failure
- process exits after cancellation attempts
- cleanup may rely on internal executor state

Because emergency shutdown is last-resort broker safety, ORB-aware emergency close is still worth implementing after Phase 1 and Phase 2.

Recommended priority:

- not before Phase 2
- not ahead of minimal harness
- but still part of the safety-hardening batch

### 3. `bars=0` is worth verifying, but current evidence says it is mostly status/reporting

The response is right that it is cheap to verify.

But code inspection shows:

- `ORBAdapter.bar_count` explicitly returns `0`
- comment says V6 is tick-driven, not bar-driven
- `on_bar()` appends bars to `_bar_buffer`
- active config has `velocity_filter_enabled=False`, so ORB entry logic should not depend on bar velocity
- LLM is disabled/passthrough in the current run, so `on_bar()` is not gate-critical unless `_llm_gate_pending` is used

Therefore I would not treat `bars=0` as a blocker unless logs show `on_bar()` is not being called and some enabled feature depends on it.

Recommended Phase 0 addition:

- verify completed bars are being produced somewhere in the runner/feed path
- but do not block tomorrow's order diagnostic only because V6 status says `bars=0`
- consider changing `bar_count` later to `len(self._bar_buffer)` for observability, not trading behavior

## Disagreements

### 1. I disagree that Phase 1 should be conditional on further verification

The necessary verification is now done. `self.ib = IB()` is real.

Phase 1 should remain in the remediation plan.

### 2. I disagree that the lifecycle harness must be fully built before any safety fixes

I agree the harness should move earlier, but not that it must be complete before Phases 1–3.

A full lifecycle harness can become a multi-day task. The better approach is:

- create a minimal fake-IB harness skeleton first
- add focused end-to-end cases for each fix as it lands
- expand to the full matrix afterward

This preserves the response's core point without delaying narrow safety patches.

## Revised Execution Order to Pass Back

Recommended coding-agent order:

1. **Phase 0:** Tomorrow operational verification
   - check `~/.daily_restart.log` first
   - confirm v11 new code is running
   - capture `DIAG: openTrades after placement`
   - do not alter order semantics before evidence

2. **Phase 5a:** Minimal fake-IB lifecycle harness skeleton
   - enough to simulate order placement, fills, reconnect, and broker positions
   - not the full matrix yet

3. **Phase 2:** Safety-flatten convergence
   - `ORPHAN_FLATTEN`, `NAKED_FLATTEN`, `CLOSED` treated as exits in `ORBAdapter`
   - strategy/risk state converges

4. **Phase 1:** ORB reconnect rebind
   - verified needed because `connect()` reassigns `self.ib = IB()`
   - add `ORBAdapter.rebind_ib()` and runner-level rebind after reconnect

5. **Phase 3:** ORB-aware emergency shutdown
   - call broker-truth-aware ORB close path even if internal state says flat

6. **Phase 4:** Order-placement diagnosis and evidence-driven order semantics
   - include C1 exit clause
   - explicitly diagnose `STP_LMT_MISSED_LIMIT`
   - include both-legs-`transmit=True` as candidate if staging is the issue
   - consult IBKR docs / Gateway behavior if trigger attributes do not survive

7. **Phase 5b:** Expand lifecycle harness to full normal/abnormal matrix

8. **Phase 6:** Optional narrow interface cleanup

## Concrete Changes to Make to the Remediation Plan

Update `2026-05-11-v11-orb-remediation-plan.md` to include:

1. `connect()` verification:
   - quote `v11/execution/ibkr_connection.py:77 self.ib = IB()`

2. revised execution order:
   - move minimal harness before implementation phases

3. C1 exit clause:
   - `STP LMT` is reversible if evidence does not support it

4. explicit C1 miss classification:
   - `STP_LMT_MISSED_LIMIT`

5. Phase 0 restart check:
   - inspect fresh `~/.daily_restart.log` first

6. Phase 0 bars check:
   - verify bar production, but classify `bars=0` as observability unless enabled logic depends on it

7. Phase 4 candidate fix:
   - both OCA entry legs with `transmit=True` if staged BUY is not armed

8. Phase 4 documentation branch:
   - IBKR trigger method docs / Gateway 10.46 / GUI inspection

## Bottom Line

The coding agent's response improves the plan.

I agree with the proposed additions and with moving lifecycle testing earlier. I do not agree that the stale-`ib` issue remains unverified or optional. It is verified by code and should stay in the safety-hardening path.

The best final plan is not the original plan unchanged and not the response wholesale. It is the revised order above: Phase 0 diagnostics, minimal lifecycle harness, state convergence, reconnect rebind, emergency close, then evidence-driven order semantics.
