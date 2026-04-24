# 2026-04-24 — STP Trigger Method (paper XAU has no `last`)

## Incident

2026-04-24. ORB computed a valid range, placed brackets at
BUY-STOP 4711.24 / SELL-STOP 4687.04 on XAUUSD, and waited.

Between ~09:40 and ~13:40 UTC the live mid traded as high as
~4735.82 — roughly $24 above the BUY trigger — and never filled.
V11's 4-hour window eventually expired; the brackets were cancelled,
and IBKR's post-cancel status produced a misleading "Order was
discarded" log line. Every placement fix from the prior week
(TIF=DAY, orderStatusEvent, reconciliation, half-open sockets,
weekly-close guard) worked exactly as intended. The orders were
alive on IBKR's side for the full four hours — reconciliation
never once flagged them missing.

The orders simply never triggered.

No position held. No money lost. Fifth consecutive day with no fill
despite multiple valid setups.

## Root cause

IBKR paper XAUUSD streams **bid/ask only**. Tick log for the session
confirmed 34,475 ticks captured, **zero** `last` values.

IBKR's default STP `triggerMethod` is `0` — "double last". The stop
fires only when two consecutive `last` trades print past the trigger
price. On paper XAU there are no `last` prints, so the default method
never fires, no matter how far bid/ask moves past the stop.

This is not a reconnect bug, not a placement bug, not a race. The
order is resting on IBKR's books, healthy, and simply waiting for an
event that never arrives on paper.

## Fix landed (commit f6ecf70)

Set `triggerMethod=7` on every STP order the executor places:

- `buy_entry` (BUY-STOP above range) — `v11/v6_orb/ibkr_executor.py`
- `sell_entry` (SELL-STOP below range) — same file
- `sl_order` (post-fill protective stop) — `_place_sl_tp`

`triggerMethod=7` is "last or double bid/ask": when `last` is present
it behaves like the default; when `last` is absent it falls back to
bid/ask. Safe on both paper (where it now works) and live (where
behavior is unchanged because `last` does print).

TP is `orderType="LMT"` and triggers on bid/ask natively — no change
needed.

## Regression tests (v11/tests/test_order_observability.py)

- `test_entry_stops_use_trigger_method_7` — captures both entry
  Orders passed to `placeOrder`, asserts `orderType == "STP"` and
  `triggerMethod == 7`.
- `test_sl_order_uses_trigger_method_7` — drives `_place_sl_tp`
  directly, asserts the single STP (the SL, since TP is LMT) has
  `triggerMethod == 7`.

Full suite: 522 passed.

## Why this wasn't caught earlier

Backtest uses bar-based fills — no trigger method concept exists.
Paper-fill smoke test `v11/live/test_paper_fill.py` uses market
orders, which don't involve a trigger. No prior test exercised a
resting STP against a bid/ask-only feed. The whole week's "nothing
filled" outcome was consistent with either (a) no qualifying breakout,
or (b) every breakout blocked by a velocity/gap filter — both
plausible with how quiet XAU has been. The actual cause was a third
option that never entered the hypothesis space until today's setup
produced an unambiguous $24 excursion with no fill.

## Honest accounting of the week

Every fix landed between 2026-04-20 and 2026-04-23 was necessary:

- **Ghost-order fix (04-20)** — TIF=DAY, orderStatusEvent,
  reconciliation. Without this, a successful trigger would still have
  been silently cancelled by IBKR's paper preset.
- **Live plumbing (04-21)** — price feed, range plausibility check,
  dry-run parity. Without this, the range itself would be wrong.
- **Half-open socket recovery (04-23)** — `force_disconnect`,
  `placement_stuck` tripwire. Without this, a dropped socket would
  leave V11 spinning for hours.
- **Weekly-close guard (04-23)** — prevents ~270 spurious
  `price_feed_dead` restarts across the weekend.

All of those were about order **placement** and system **survival**.
Today's fix is about order **triggering**. It is the piece that
converts "orders are healthy on IBKR" into "orders actually fill."

Placement fixes were necessary but not sufficient. The stack is now
sufficient, pending live validation on Monday's open.

## Followups

- Monday 2026-04-27: watch the first live breakout. Confirm the
  `orderStatus` transitions through `PreSubmitted → Submitted →
  Filled` on a bid/ask-only trigger path.
- Consider extending `test_paper_fill.py` with a resting BUY-STP
  scenario — assert that a bid/ask excursion past the trigger fires
  a fill. This would close the last gap between unit tests and live
  behavior.
