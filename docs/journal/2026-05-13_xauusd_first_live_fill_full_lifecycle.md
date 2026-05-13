# XAUUSD ORB — first live fill, full lifecycle validated end-to-end

**Date:** 2026-05-13
**Outcome:** Phase 6 proof-of-life **closed**. The no-fill bug arc that
started 2026-04-24 is fully resolved. A complete ORB lifecycle ran on
paper from cron-fire → range calc → bracket placement → fill → SL/TP
placement → trade-window-close market exit → PnL booking → DONE_TODAY,
with zero errors.

Result for the day: SHORT @ 4687.63 → MARKET exit @ 4695.59,
**PnL −$7.96** (1 share, ~0.17% adverse move). The dollar amount is
irrelevant. The plumbing is what mattered, and the plumbing worked.

## Pre-conditions that came together

1. **Wednesday-enable.** `skip_weekdays=(2,)` → `skip_weekdays=()` change
   shipped yesterday evening; v11 was manually restarted at 20:02 EDT
   2026-05-12 to load the new config into memory before the unattended
   01:15 EDT cron fire (belt-and-suspenders).
2. **Second clean unattended cron fire.** 2026-05-13 01:16:28 EDT cron
   completed SUCCESS. AbandonProcessGroup held for the second time.
3. **No stale breakout.** Price stayed inside the 02:00 EDT range
   (4688.01–4726.99) until the 04:00 EDT trade window opened — the
   first day where the stale-breakout guard did NOT fire.

Together: brackets actually got placed today.

## Full chain

```
2026-05-13 01:16:28  daily-restart cron SUCCESS, v11 PID 74198
2026-05-13 02:00:02  Range from IBKR bars: 4688.01-4726.99 (72 bars)
2026-05-13 02:00:02  Daily range set
2026-05-13 04:00:03  ORB state: IDLE -> RANGE_READY
2026-05-13 04:01:00  ORB: LLM gate passed (passthrough, --no-llm)
2026-05-13 04:01:04  orderStatusEvent: id=36 status=PreSubmitted   ← Fix (B) caught it
2026-05-13 04:01:05  Entry stops placed: BUY id=35 @ 4726.99 lmt=4730.89,
                       SELL id=36 @ 4688.01 lmt=4684.11
                       (OCA=ORB_XAUUSD_2026-05-13_080103)
2026-05-13 04:01:05  DIAG: openTrades after placement:
                       id=35 BUY  STP LMT aux=4726.99 lmt=4730.89 trigger=7
                              tif=DAY transmit=False  oca=ORB_XAUUSD_2026-05-13_080103
                              status=PendingSubmit  filled=0.0/0.0
                       id=36 SELL STP LMT aux=4688.01 lmt=4684.11 trigger=7
                              tif=DAY transmit=True   oca=ORB_XAUUSD_2026-05-13_080103
                              status=PreSubmitted   filled=0.0/1.0
2026-05-13 04:01:05  ORB state: RANGE_READY -> ORDERS_PLACED
2026-05-13 05:22:34  orderStatusEvent: id=36 status=Filled filled=1.0/0.0
2026-05-13 05:22:36  SL/TP placed: SL id=37 @ 4726.99, TP id=38 @ 4590.56
                       (OCA=ORB_EXIT_XAUUSD_2026-05-13_092234)
2026-05-13 05:22:36  ENTRY: SHORT @ 4687.63 | SL=4726.99 TP=4590.56
2026-05-13 05:22:36  RISK: V6_ORB entered XAUUSD (positions: 1/3, trades: 1)
2026-05-13 12:00:01  Trade window closed, closing at market   ← V6 EOD path
2026-05-13 12:00:01  IB 202: Order Canceled × 3                ← OCA + cleanup
2026-05-13 12:00:02  Safety cleanup: cancelled 3 orders for XAUUSD
2026-05-13 12:00:07  Position closed at market (fill=4695.59)
2026-05-13 12:00:07  MARKET: @ 4695.59 | PnL=-7.96
2026-05-13 12:00:07  RISK: V6_ORB exited XAUUSD PnL=$-7.96
2026-05-13 12:00:07  ORB state: IN_TRADE -> DONE_TODAY
```

Total: 6h 37m hold, single SHORT, 1 share, paper.

## What got validated for the first time live

| Fix / path | First live exercise |
|---|---|
| (A) DIAG `openTrades` block after placement | ✅ Block printed, attributes correct |
| (B) entry IDs set before `ib.sleep(1)` | ✅ PreSubmitted hook fired at 04:01:04, BEFORE the explicit "Entry stops placed" log at 04:01:05 — meaning the listener saw the early event because the ID was already recorded |
| (C1) STP → STP LMT entry orders | ✅ Both legs accepted, SELL leg filled at 05:22:34 |
| `triggerMethod=7` (the original 2026-04-24 fix) | ✅ IBKR accepted it (DIAG confirmed) — finally validated end-to-end after sitting unverified in code for 19 days |
| Two-phase OCA (transmit=False / transmit=True) | ✅ SELL transmit=True activated both legs; OCA pair canceled cleanly when SELL filled |
| Post-fill SL/TP placement | ✅ Placed within 2s of fill |
| V6 trade-window-close MARKET exit | ✅ Strategy correctly fired `Trade window closed, closing at market` at 12:00:01 EDT (this path existed but had never been observed live — I previously thought V6 had no IN_TRADE time-exit; I was wrong) |
| MARKET exit reason → ORBAdapter._on_fill | ✅ Existing EXIT_REASONS path |
| PnL booked through RiskManager | ✅ Combined PnL −$7.96, positions: 0/3 |
| Final state IN_TRADE → DONE_TODAY | ✅ |

## What's still untested live

The Phase 1/2/3 hardening shipped 2026-05-11 evening:

- **Phase 1 (reconnect rebind)** — no reconnect happened today.
- **Phase 2 (safety-flatten convergence)** — never triggered.
- **Phase 3 (emergency_close)** — never triggered.

These remain unit-test-validated but production-untested. They are
the tail-risk paths; we'd only see them if something specific
fails (Gateway flap, broker-internal disagreement, persistent
connectivity failure). Don't try to force them — wait for natural
exercise.

## Correction to yesterday's writeup

In `docs/journal/2026-05-12_overnight_watch_and_stale_breakout_skip.md`
I wrote that V6's `_handle_in_trade` has no time-exit. **Today's behavior
proves that wrong** — at 12:00:01 EDT (16:00 UTC = `trade_end_hour`),
V6 emitted `Trade window closed, closing at market` and the executor
closed the position. The time-exit IS implemented; I just hadn't found
the code path. Worth re-reading `v11/v6_orb/orb_strategy.py` to map
the exact branch for future reference.

## Net assessment update for unattended paper

This was the test day. The system is operationally capable of
unattended paper runs:

- Two consecutive clean cron fires (5/12, 5/13).
- Order placement → fill → SL/TP → exit lifecycle complete.
- All A+B+C1 + Apr-24 trigger fix paths exercised.
- No errors, no warnings, no manual intervention needed.

Still worth observing in production over the next 1–2 weeks:
- TP fill (today was MARKET exit at window close, not TP).
- SL fill (loss path under SL hit, not window-close).
- A natural reconnect event during the trade window.
- A multi-day cron streak without skipping.

See `docs/PROJECT_STATUS.md` "Current state" for the live status board.

## Files written / data captured

- `~/.v11_trade_today.log` — per-probe trail of today's trade (2026-05-13 07:02 EDT → 12:00:07 EDT)
- `~/.v11_paper.log` — full v11 log; today's entries from 01:15:52 onward
- `~/.daily_restart.log` — cron fire record (01:15:05–01:16:28 EDT)

## Next forward action

Write the morning health probe (`~/.v11_morning_health.log`
one-liner) per yesterday's plan. Trade has closed → unblocked.
