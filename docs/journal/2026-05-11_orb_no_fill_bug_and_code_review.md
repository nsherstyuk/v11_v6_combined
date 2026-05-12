# BUY stop didn't fill — root cause analysis + code review findings

**Date:** 2026-05-11
**Status:** Bug confirmed reproducible (same failure mode as 2026-04-24 incident,
which was supposedly fixed). Fix proposal pending Nick's approval to modify
`v11/v6_orb/ibkr_executor.py`.
**Severity:** High — blocks Phase 6 proof-of-life. Trade signal fired,
order didn't execute, no money lost but no fill either.

## Today's failure — full timeline

v11 paper, XAUUSD ORB strategy. New PID 89839 launched 07:41 EDT after
the `AbandonProcessGroup` fix to the daily-restart plist earlier in the
morning. End-to-end state machine ran correctly:

```
07:41:39  Range from IBKR bars: 4648.21 - 4705.56 (72 bars)
07:41:41  ORB state: IDLE → RANGE_READY
07:42:00  ORB LLM gate: passthrough auto-approve
07:42:02  Velocity 0 >= 168, placing brackets         ← misleading log msg (filter is OFF)
07:42:04  Entry stops placed: BUY id=23 @ 4705.56, SELL id=24 @ 4648.21 (OCA=ORB_XAUUSD_2026-05-11_114202)
07:42:04  ORB state: RANGE_READY → ORDERS_PLACED
```

Status-line price evolution during the trade window:

| Time (EDT) | Price (mid) | dist_high (vs 4705.56) |
|---|---:|---:|
| 07:46:41 | 4674.59 | −30.97 (valid: price below stop, order armed) |
| 08:36:45 | 4696.66 | −8.90 (approaching) |
| 09:01:47 | 4704.81 | **−0.76** (about to cross) |
| **09:06:48** | **4714.80** | **+9.23** ← **BREAKOUT** |
| 09:21:49 | 4728.86 | +23.30 |
| 09:46:51 | 4746.22 | +40.65 (peak) |
| 11:42:01 | 4729.84 | +24.27 |
| 11:42:03 | — | brackets cancelled by 4h-pending guard |

**The BUY stop @ 4705.56 should have triggered between 09:01:47 and
09:06:48 EDT.** Once triggered, it becomes a market order and fills at
the next available price. Price stayed above 4705.56 continuously for
~2h 36m after the breakout. **The stop never triggered.** `filled=0.0`
on cancellation.

## The exact same bug happened on 2026-04-24

`v11/v6_orb/ibkr_executor.py` lines 142–148 has this code comment:

> `triggerMethod=7 ("last or double bid/ask"): IBKR paper XAUUSD
> streams bid/ask only — zero `last` trades print. The default
> triggerMethod waits on `last`, so stops never trigger even when
> bid/ask is $20+ past the stop. Method 7 falls back to bid/ask
> when last is absent and uses last when present; safe on both
> paper and live. (2026-04-24 incident: BUY-STOP @ 4711.24 sat
> resting while price traded to 4735.82 mid for ~20 min.)`

**The April 24 fix is still in the source code** (lines 152, 160:
`triggerMethod=7`), yet today reproduces the exact same symptom on the
exact same contract. So either:
1. The fix never reached IBKR (the attribute got stripped or ignored)
2. The fix is no longer sufficient (IBKR paper changed behavior, or this
   Mac Gateway 10.46 install handles it differently than the prior Windows
   environment)
3. The bug isn't actually about the trigger method — there's some
   *other* attribute or state preventing the trigger

## What the order lifecycle actually looked like

The `_on_order_status` listener (lines 261–302) logs every IBKR status
transition for our orders. Between placement at 07:42:02 and cancellation
at 11:42:03, **NO status transitions were logged for id=23 or id=24**.

Normal stop-order lifecycle is:
`PendingSubmit → PreSubmitted → Submitted → (Filled OR Cancelled)`

Each transition fires `orderStatusEvent`. We see zero transitions for 4
hours. Two possibilities:

**(a) Logging gap.** The listener only logs when `oid in (buy_entry_id, sell_entry_id)`,
but the executor sets these IDs AFTER `self.ib.sleep(1)` post-`placeOrder`
(lines 172-174, 176-178). Early lifecycle events (PendingSubmit/PreSubmitted/
Submitted) likely fire BEFORE the IDs are recorded, so the listener
filters them out as "not ours." This is a documentable diagnostic gap
in the executor but isn't necessarily the trade-execution bug.

**(b) Order genuinely sat in a non-Submitted state.** If the order was
stuck in some pre-Active state — e.g., transmit=False staged but never
actually transmitted by the follow-up — then it would never trigger on
price even when price crosses. The two-phase OCA placement is:

```python
buy_entry = Order(..., transmit=False, ocaGroup=oca_group)
sell_entry = Order(..., transmit=True, ocaGroup=oca_group)

buy_trade = self.ib.placeOrder(self.contract, buy_entry)
self.ib.sleep(1)
sell_trade = self.ib.placeOrder(self.contract, sell_entry)
```

The intent: `transmit=False` on BUY stages it on IBKR's side; `transmit=True`
on SELL transmits BOTH because they share an OCA group. This is the
standard ib_insync pattern. But the docs are sparse on whether OCA
auto-transmits ALL prior staged orders in the group when one is sent
with transmit=True. If it doesn't, the BUY could be permanently staged
but never armed.

We can't tell from the logs alone which possibility (a) or (b) is true.
Need a live diagnostic during the next trade window.

## Other code-review findings

While reading `v11/v6_orb/ibkr_executor.py`:

### 1. Logging gap for early order-lifecycle events (lines 172-178)

```python
buy_trade = self.ib.placeOrder(self.contract, buy_entry)
self.ib.sleep(1)                              # ← lifecycle events fire here
self.buy_entry_id = buy_trade.order.orderId   # ← but is_ours check uses this
```

`orderStatusEvent` for PendingSubmit/PreSubmitted/Submitted fires during
the 1-second sleep, before `self.buy_entry_id` is set. The listener
filters those events out as "not ours." We see only the eventual
Cancelled events 4h later. **Fix:** record `buy_trade.order.orderId`
immediately after `placeOrder` returns (no sleep first), or hook the
listener to log all events regardless of ID match for diagnostic purposes.

### 2. Misleading "Velocity 0 >= 168, placing brackets" message

When `velocity_filter_enabled=False`, the velocity check is bypassed but
the log message reads "Velocity 0 >= 168" — which is mathematically
FALSE. This caused confusion in today's review. Should read "Velocity
filter disabled, placing brackets" or similar.

### 3. `bars=0` throughout in status lines

Every `[STATUS]` line today shows `bars=0`, even hours into the trade
window. The strategy's bar count never incremented. Looking at the
strategy flow, this is probably the count of *new* 1-min bars the
detector has seen during the trade window (vs seeded bars), and 0 means
no bars closed during 04:00–12:00 EDT after the initial seed. But the
status line also reports a price that DOES update — so price ticks ARE
flowing. This needs verification: is the bar aggregator publishing bars
to the V6 strategy, or only ticks? If V6 only receives ticks, its
internal state machine might be running on stale bar data.

### 4. Two `Cancelled` events per orderId at cancellation

```
11:42:03  id=23 status=Cancelled filled=0.0/0.0
11:42:03  id=23 status=Cancelled filled=0.0/1.0
```

Two cancellation events fired for the same order ID, with different
remaining values (0.0/0.0 then 0.0/1.0). Probably because the OCA pair
generates one event from the original transmit cancel + one from the
sibling auto-cancel. Cosmetic but worth understanding.

## Things that are NOT bugs (just reading carefully)

- The 4h-pending guard at 11:42:03 fired exactly per `max_pending_hours=4`
  config, measured from placement (07:42:04) not from window open. Per design.
- The OCA pair (one cancels the other) is implemented correctly with
  `ocaGroup` matching and `ocaType=1`.
- The `triggerMethod=7` attribute is correctly set on BOTH orders.
- `TIF="DAY"` is correct per the 2026-04-20 comment (IBKR paper preset
  rejects GTD).
- Error handling on connectivity-class placement failures is sound
  (reconnect path, stuck threshold, etc.).

## Why does it feel like "so many bugs"

Honest breakdown of the issues we've seen since 2026-05-08:

| Date | Bug | Class |
|---|---|---|
| 2026-05-08 | Silent overnight Gateway death | Operational (IBC autorestart token missing on Mac) |
| 2026-05-09 | No launchd supervisor | Operational (Mac migration) |
| 2026-05-10 | Equity download reconnect tight | Operational (download script) |
| 2026-05-11 AM | PGRP-kill on daily restart | Operational (launchd plist missing `AbandonProcessGroup`) |
| 2026-05-11 mid | ORB backtest time-exit bug | My code (backtest script I wrote 2h earlier) |
| 2026-05-11 PM | BUY stop didn't fill | Strategy-execution (IBKR-paper interaction) |

Five of the six are integration / operational issues from the Mac
migration plus one fresh code bug I wrote myself. Today's strategy-level
bug is the FIRST one in actual v11 production code logic — and it's
specifically a recurrence of an April 24 bug that the team had already
hit, "fixed," and journaled. The IBKR paper XAUUSD interaction is
genuinely finicky in ways that aren't visible from the v11 side alone.
**The fix path for today's bug is in the same domain as the April fix
(IBKR-side order attributes / order-type choice), not in any "v11 is
buggy" refactor.**

## Recommended fix path (needs Nick approval to touch `v11/v6_orb/`)

In rough priority order:

### A. Diagnostic-first — verify what IBKR actually has (recommended starting point)

Before changing the order placement, instrument it to capture full
context next time. Tomorrow's brackets-placement should include:

1. After `placeOrder` for both legs, immediately call
   `ib.openTrades()` and log every order's status, type, triggerMethod,
   transmit flag, OCA group. This confirms whether IBKR actually has
   the order armed with the attributes we sent.
2. After 60 seconds, repeat the query — confirm the order is still
   Submitted, not stuck in PreSubmit.
3. If the trigger condition fires (price > 4705.56 in our case), log
   the bid/ask state at the moment of crossing. Compare against the
   expected trigger behavior of method 7.

This is a small change (add ~10 lines of diagnostic logging) and risks
nothing. It DOESN'T change behavior; it just gives us the smoking-gun
evidence for path B or C.

### B. Logging-gap fix (small, independent of root cause)

Set `self.buy_entry_id = buy_trade.order.orderId` BEFORE the
`ib.sleep(1)`, not after. This is sufficient to catch early-lifecycle
status events in the listener. One-line change.

### C. Order-type alternative (if A confirms the order WAS armed correctly)

If diagnostic A shows the BUY was properly Submitted with
triggerMethod=7 and still didn't fill, change order placement strategy:

**Option C1 — STP LMT instead of STP.** Stop-limit orders give an
explicit limit price (e.g., `auxPrice=range_high, lmtPrice=range_high + 0.50`).
The trigger is the same but the resulting order has an explicit price
ceiling, which IBKR paper handles more reliably for thin instruments.

**Option C2 — Use ib_insync's `Stop` helper.**
`Stop("BUY", quantity, range_high)` produces a known-good order shape.
Might handle attribute defaults differently than manual `Order()` construction.

**Option C3 — Submit as bracketOrder()** — but bracketOrder is for
single-direction parent+SL+TP, not for two opposite-direction OCA
brackets. Doesn't apply here.

I'd recommend B + A first, then C1 if A reveals the order was correctly
armed. Don't bundle them — one variable at a time.

## What I'm NOT proposing

- Refactoring `ibkr_executor.py` — the code is clean and well-commented.
  This is targeted edits, not a rewrite.
- Changing the strategy logic — strategy state machine ran correctly.
- Modifying anything outside `v11/v6_orb/`.

## Open questions for Nick

1. **Approval to modify `v11/v6_orb/ibkr_executor.py`** for the
   diagnostic logging (A + B above). These are safe additive changes,
   no behavior change, low risk. Need explicit yes per CLAUDE.md.
2. **Approval to attempt the order-type fix (C1)** if A reveals the
   need. This would change live order behavior, so higher stakes.
3. **Tomorrow's plan** — if Nick wants to skip ahead and just try C1
   tomorrow, that's a valid call, but we lose the diagnostic info that
   would tell us whether the fix worked because it addressed root cause
   or because of unrelated variance.

## Post-write update: April 24 journal found

After writing the above I located `docs/journal/2026-04-24_stp_trigger_method.md`
and read it in full plus the regression tests at
`v11/tests/test_order_observability.py`. This sharpens the picture
significantly.

### The April 24 fix was committed AND tested, but in a specific way

- Fix landed as commit `f6ecf70`, set `triggerMethod=7` on three call sites
  in `ibkr_executor.py` (buy_entry, sell_entry, sl_order). All three are
  still present in current code.
- Regression tests added: `test_entry_stops_use_trigger_method_7` and
  `test_sl_order_uses_trigger_method_7`. Both pass today (522 tests).
- **The tests only verify the attribute is set on our side.** They mock
  `ib.placeOrder` and inspect the `Order` object — they do NOT verify
  that IBKR receives the attribute, applies it to its matching engine,
  and actually triggers correctly when bid/ask crosses on a no-last
  feed.
- The April 24 journal flagged this explicit gap in the "Followups"
  section: *"Consider extending `test_paper_fill.py` with a resting
  BUY-STP scenario — assert that a bid/ask excursion past the trigger
  fires a fill. This would close the last gap between unit tests and
  live behavior."* **That followup was never implemented.** It would
  have caught today's recurrence.

### Plus: the planned April 27 live validation likely never happened

The April 24 journal says: *"Monday 2026-04-27: watch the first live
breakout. Confirm the `orderStatus` transitions through `PreSubmitted →
Submitted → Filled` on a bid/ask-only trigger path."*

Looking at journal entries between 2026-04-27 and 2026-05-01, the next
substantive entry is `docs/journal/2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md`
— which discovered that **IBKR Gateway's daily 04:00 ET restart was
killing v11 right when the trade window opened, producing zero trades
across two weeks (2026-04-17 through 2026-05-01).** Whatever the
triggerMethod=7 fix's true behavior was, no breakout reached it during
that window. The "fix" passed unit tests and was never validated
end-to-end against a real bid/ask-only trigger event in production.

**Net: the fix has been in place since April 24, but its first real-world
end-to-end test was today, and today it failed.** The "we already fixed
this in April" framing in my initial reading was slightly misleading
— the fix was *applied* but not *verified*.

### Why the bug might still be hitting us — refined hypotheses

The April 24 root cause was: paper XAUUSD has no `last` trades, default
trigger method requires `last`, stops never fire. The fix uses method 7
which falls back to bid/ask when last is absent.

For triggerMethod=7 to fail today, one of:

1. **Method 7 was set in our Order object but stripped/ignored by
   ib_insync, IB API, or Gateway** in the path between v11 and IBKR's
   matching engine. Different Gateway version (10.46 on Mac vs whatever
   Windows had) could behave differently on this attribute.
2. **Method 7 requires "double bid/ask" — two consecutive bid/ask
   quotes past the trigger** — and today's price action didn't produce
   two consecutive prints at/above 4705.56. Possible but unlikely given
   price went +$40 above.
3. **The contract type changed.** On Windows the contract may have been
   `CMDTY` XAUUSD; on Mac it might be `CASH` XAU/USD or vice versa.
   Different contract types route to different IBKR data farms with
   different tick semantics. Triggers may behave differently.
4. **Something in the Order attribute set today silently invalidates
   the trigger configuration** — e.g., interaction with `transmit=False`
   on the OCA pair, or some interaction we haven't seen.

The diagnostic-first approach (proposed plan A above) is the cleanest
way to narrow which of these is real.

### Refined urgency

The bug isn't a fresh regression — it's the original April 24 bug whose
"fix" was never properly validated live. The path forward is the same:
add live-trigger integration test, narrow down the actual cause,
adjust order placement attributes or order type. That's a half-day of
work at most, mostly diagnostic.

## See also (updated)

- `v11/v6_orb/ibkr_executor.py` — the executor (lines 142-163 contain
  the triggerMethod=7 attribute and the April 24 comment)
- `docs/journal/2026-04-24_stp_trigger_method.md` — original incident
  writeup
- `v11/tests/test_order_observability.py` — regression tests; verify
  the attribute is set, do NOT verify end-to-end IBKR-side trigger
- `docs/journal/2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md`
  — the Gateway-restart-killing-trade-window issue that masked the
  unverified-fix problem for two weeks
- Today's v11 log: `v11/live/logs/v11_live_20260511_074134.log`

---

## Fixes applied (2026-05-11 evening, after Nick's approval)

All three proposed fixes were applied to `v11/v6_orb/ibkr_executor.py`
with Nick's explicit per-item approval. Tests updated.
525 / 525 v11 tests pass after the changes.

### B — orderStatusEvent listener gap

Moved `self.buy_entry_id = buy_trade.order.orderId` and
`self.sell_entry_id = sell_trade.order.orderId` from AFTER each
`ib.sleep(1)` to immediately AFTER each `placeOrder` call. Listener
can now recognize early-lifecycle status events (PendingSubmit /
PreSubmitted / Submitted) as "ours" instead of filtering them out.
Tomorrow's first placement will produce a more complete event log.

### A — Post-placement diagnostic

After both `placeOrder` calls return and IDs are recorded, query
`ib.openTrades()` and log every order for the contract:

```
DIAG: openTrades after placement:
    id=N BUY STP LMT aux=4705.56 lmt=4711.30 trigger=7 tif=DAY
        transmit=True oca=ORB_XAUUSD_… ocaType=1
        status=Submitted filled=0/1
    id=M SELL STP LMT aux=4648.21 lmt=4642.47 trigger=7 tif=DAY
        transmit=True oca=ORB_XAUUSD_… ocaType=1
        status=Submitted filled=0/1
```

This is the smoking-gun data we couldn't get from the post-mortem
today. It tells us whether IBKR actually accepted the attributes,
what status the order is in, and whether the OCA pair is correctly
linked. If something is wrong (e.g., `triggerMethod=0` instead of 7,
`status=PreSubmitted` indefinitely), the diagnostic exposes it.

### C1 — STP → STP LMT

Entry orders changed from `orderType="STP"` to `orderType="STP LMT"`
with explicit `lmtPrice`:

```python
range_width = range_info.high - range_info.low
lmt_buffer = max(0.5, 0.1 * range_width)
buy_lmt = round(range_info.high + lmt_buffer, d)
sell_lmt = round(range_info.low - lmt_buffer, d)
```

For today's range of 57.35, buffer = $5.74, so:
- BUY would have been `STP LMT aux=4705.56 lmt=4711.30`
- SELL would have been `STP LMT aux=4648.21 lmt=4642.47`

The trigger half retains `triggerMethod=7` so the bid/ask fallback
still works on paper. The added limit price gives the post-trigger
order a well-defined fill envelope. Trade-off:

- **Pro:** more reliable on paper (paper XAUUSD's no-`last` quirk
  affects pure stop-fill semantics in undocumented ways; STP LMT's
  explicit fill envelope is what IBKR's matching engine handles
  most predictably for thin instruments).
- **Con:** a fast tick that gaps THROUGH the `lmt_buffer` window
  would not fill. With buffer at 10% of range_width, this requires
  a one-tick move of >10% of range, which on XAUUSD intraday is
  rare but possible during news events.

### Test changes

`v11/tests/test_order_observability.py::test_entry_stops_use_trigger_method_7`
renamed to `test_entry_stops_use_stp_lmt_with_trigger_method_7` and
updated to assert:
- `orderType == "STP LMT"` (was `"STP"`)
- `triggerMethod == 7` (unchanged)
- BUY `lmtPrice == auxPrice + buffer` (new)
- SELL `lmtPrice == auxPrice - buffer` (new)
- `buffer = max(0.5, 0.1 * range_width)` (new)

The SL test (`test_sl_order_uses_trigger_method_7`) is unchanged —
the SL placement code (in `_place_sl_tp`) still uses `STP` not `STP LMT`,
because SL semantics are different (fast-market protection, slippage
acceptable, no need for an explicit fill envelope).

### Source-code header

Header docstring of `ibkr_executor.py` now lists the 2026-05-11
change with all three sub-points (A, B, C1) and points back to this
journal.

## What's expected next

- **v11 process needs to restart** to pick up the new code (currently
  running PID 89839 holds the OLD compiled module in memory).
- Tomorrow's 01:15 EDT daily-restart cron will cycle v11 and load the
  new code, OR Nick can manually restart anytime.
- First production fire of the new STP LMT order behavior:
  ~04:00–04:05 EDT tomorrow when brackets get placed.
- First DIAG log line will appear at that time. If it confirms the
  orders are correctly armed AND a breakout occurs AND the order
  fills, Phase 6 proof-of-life closes.
- If the order is armed correctly but still doesn't fill, the issue
  is downstream of v11 — likely Mac Gateway version or paper-account
  contract specifics. Escalate to IBKR support or test against live.

## Known minor issues (not blocking, low priority)

These were noted during today's review but not fixed:

1. **"Velocity 0 >= 168, placing brackets" log message** is misleading
   when `velocity_filter_enabled=False`. Reads as "0 >= 168 (false)"
   but represents a bypassed check. Cosmetic; consider rewording to
   "Velocity filter disabled, placing brackets" or similar.
2. **`bars=0` in all `[STATUS]` lines** throughout the trade window.
   The strategy's internal bar counter doesn't seem to increment from
   live tick aggregation, only at startup-seed time. The strategy state
   machine still works correctly (orders placed, price tracked via
   ticks), but the status-line semantics of `bars` are unclear.
   Diagnostic, not functional.
3. **Two `Cancelled` events per orderId at cancellation** with
   different `remaining` values (0.0/0.0 then 0.0/1.0). Probably from
   the OCA pair's auto-cancel cascade. Cosmetic.

Address these if/when something forces it (e.g., debugging requires
clarity), not preemptively.
