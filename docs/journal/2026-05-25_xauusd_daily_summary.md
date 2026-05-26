# XAUUSD ORB daily summary — 2026-05-25

**Outcome:** NO-TRADE (window-close cancel, brackets held the full window
without fill). **First live exercise of the reconnect-rebind +
order-reconcile path under repeated connection churn — 3 reconcile-driven
re-places, all handled cleanly.**

US Memorial Day holiday — markets thin, IBKR gateway connectivity
unusually choppy. Five+ disconnect/reconnect cycles between 03:23 and
09:02 EDT.

## Timeline

```
02:22:10  daily-restart cron fired (late — Mac booted overnight, plist
          ran at first eligible moment instead of the usual 01:15 EDT)
02:22:19  Range from IBKR bars: 4552.08 - 4579.66 (27.58 wide, 72 bars)
03:23:12  IB 1100 disconnect → 2s reconnect cycle
04:00     trade-window open (no state change yet — gap-bar fetch failed
          'Not connected' on first attempt)
04:24:10  Gap filter PASS (retry succeeded). ORB state: IDLE -> RANGE_READY
04:24:12  LLM gate pending (next 1H bar)
04:24:40  IB 1100 disconnect → reconnect cycle
05:25:09  LLM gate eval: passthrough AUTO-APPROVE
05:25:11  Placement attempt → 'Not connected' → forced reconnect (Phase C
          connectivity classifier; consec=1/30)
05:25:15  ORB: rebound to new IBKR connection after reconnect; reconcile
          shows broker flat
05:25:45  Entry stops placed: BUY id=124 @ 4579.66 lmt=4582.42, SELL id=125
          @ 4552.08 lmt=4549.32 (cycle 1)
06:26:10  ORDER RECONCILE: BUY id=124 missing from IBKR openTrades; clearing
06:26:12  Falling back to RANGE_READY for retry
06:26:48  Entry stops re-placed: BUY id=131, SELL id=132 (cycle 2)
07:27:11  IB 1100 disconnect → reconnect cycle
08:00:01  ORDER RECONCILE: BUY id=131 missing; clearing
08:00:05  Placement → 'Not connected' → forced reconnect
08:00:09  Rebind + reconcile (broker flat)
08:00:39  Entry stops re-placed: BUY id=138, SELL id=139 (cycle 3)
08:28:14  IB 1100 disconnect → reconnect cycle
09:01:41  ORDER RECONCILE: BUY id=138 missing; clearing
09:01:45  Placement → 'Not connected' → forced reconnect
09:01:49  Rebind + reconcile (broker flat)
09:02:20  Entry stops re-placed: BUY id=145, SELL id=146 (cycle 4 — final)
12:00:01  Trade window closed, canceling brackets
12:00:03  ORB state: ORDERS_PLACED -> DONE_TODAY
```

No bracket fired. Price stayed inside the 4552.08-4579.66 range from
04:00 through 12:00 EDT.

## What this exercised

**Phase 1 + Phase 2 hardening (landed 2026-05-11) in production, three
times in one day:**
- `_reconcile_orders` detected one OCA leg gone from `openTrades` after
  each disconnect, cleared the executor's tracked IDs, and let the strategy
  fall back to `RANGE_READY`.
- `ORBAdapter.rebind_ib` + `MultiStrategyRunner.rebind_orb_connections`
  fired on each reconnect, swapped the ib+contract, re-subscribed ticks,
  re-hooked events. Logged cleanly each time.
- Phase C connectivity-class placement-error path (`consec=1/30`) caught
  every "Not connected" placement attempt and forced a clean reconnect
  rather than retry-spinning.

Three clean re-places without operator intervention. The recovery
plumbing works.

**Pattern observed:** every reconcile dropped the BUY leg (upside leg,
which never traded today since price stayed in-range from below). Likely
that the BUY-stop tied to the OCA group is the one IBKR de-stages when
the connection drops, while the live SELL-stop (closer to current price,
or just the first half of the OCA pair from IBKR's side) survives. Not
investigated further today — the recovery path handled it.

## Cumulative live record

| Date | Dir | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | 5h17m |
| 2026-05-21 | SHORT | 4518.09 | 4512.87 | +$5.22 | 5h33m |

**Cumulative net: +$17.76 across 3 trades, 2W/1L.** Unchanged.

## Non-trade pattern (live era 2026-05-12 → 2026-05-25)

11 trading days, 3 fills, 8 no-trade days:
- stale-breakout skips: 1 (5/12)
- 4h-pending cancels: 4 (5/14, 5/18, 5/20, 5/22)
- range-too-wide skips: 1 (5/15)
- **window-close cancels (new subtype): 1 (5/25)** — brackets stayed
  staged through full 8h window without fill or 4h-pending trip
- weekends not counted

Today's window-close path is novel: in the four prior 4h-pending days,
the 4h timer started at the first 04:01 placement and tripped at 08:01
once price stayed in-range. Today the timer effectively kept getting
reset by the reconcile re-places (the executor treats each re-place as
a new placement). The most-recent placement at 09:02 would have 4h
expiry at 13:02, past the 12:00 window close — so the V6 window-close
caught it first. Worth verifying that the 4h-pending guard's timer is
intended to reset on reconcile re-place (it appears to be by behavior
today, but I have not audited the code path).

## Open question for follow-up (not blocking)

**4h-pending timer semantics under reconcile re-place.** Today's behavior
suggests the 4h-pending counter resets when the reconcile path re-places
after a connection drop. That is probably fine — the orders genuinely
were re-staged at a new time and price never crossed — but worth
confirming the intent matches the implementation. If unintended, this
would silently extend the effective pending window during connection-
flappy sessions. Filing for later review, not urgent.

## 01:00 ET Gateway auto-restart observation

6th observed cycle. Today is a US holiday so the pattern persisted on a
non-trading-day. Cosmetic noise on this end; 02:22 EDT boot-time
restart caught it.

## Code and config

Unchanged.
