# XAUUSD ORB — fourth live SHORT, third consecutive winner

**Date:** 2026-05-26
**Result:** SHORT @ 4522.81 → MARKET @ 4507.49, **PnL +$15.32 paper**
(7h31m hold, trade-window-close exit at 12:00 EDT).

## Today's timeline

```
00:17:21  IB 1100 disconnect (overnight churn from 5/25 carried into 5/26)
00:17:23  IB 2110 TWS↔server broken
00:17:44  IB 1100 disconnect (second wave)
00:17:47  Connected to IBKR — re-qualified XAUUSD, re-subscribed
00:18:18  IB 1102 connectivity restored
01:15:05  Cron SIGTERM — clean shutdown of prior v11 (PID 3403)
01:16:28  Cron SUCCESS — Gateway PID=10403, v11 PID=10417 (new process)
02:00:04  Range from IBKR bars: 4523.14 - 4560.32 (37.18 wide, 72 bars)
04:00:03  Gap filter PASS (vol=0.000301, range=0.466)
04:00:03  ORB state: IDLE -> RANGE_READY
04:01:01  ORB LLM passthrough APPROVED (mechanical)
04:01:05  Entry stops placed:
            BUY  id=128 @ 4560.32 lmt=4564.04
            SELL id=129 @ 4523.14 lmt=4519.42
            (OCA=ORB_XAUUSD_2026-05-26_080103)
04:01:05  ORB state: RANGE_READY -> ORDERS_PLACED
04:29:23  orderStatusEvent: id=129 (SELL) Filled
04:29:24  SL @ 4560.32, TP @ 4430.19 placed (OCA exit pair)
          ENTRY: SHORT @ 4522.81 | SL=4560.32 TP=4430.19
12:00:00  Trade window closed, closing at market
12:00:06  MARKET: @ 4507.49 | PnL=+15.32
12:00:06  ORB state: IN_TRADE -> DONE_TODAY
```

Fill landed only **28 minutes after trade-window open** — fastest live
fill so far (prior fastest was 5/19 at 1h17m). Held 7h31m to the V6
window-close MARKET exit.

## Live record (cumulative)

| Date | Dir | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | 5h17m |
| 2026-05-21 | SHORT | 4518.09 | 4512.87 | +$5.22 | 5h33m |
| **2026-05-26** | **SHORT** | **4522.81** | **4507.49** | **+$15.32** | **7h31m** |

**Cumulative net: +$33.08** across 4 trades, 3 winners, 1 loser.
Win rate 75% on N=4 (still sample too small for inference).

**All four live fills have been SHORT direction.** All four exited via
**V6 trade-window-close MARKET path** at 12:00 EDT — no SL or TP has
triggered yet in the live era. Pattern called out on 5/21; now four in
a row. Still small-sample, but the SHORT-bias is becoming a thing worth
tracking.

## Pattern of non-trade days

Live era 2026-05-12 → 2026-05-26 = 12 trading days, 4 fills:

| Date | Outcome |
|---|---|
| 5/12 | stale-breakout skip |
| 5/13 | SHORT fill (−$7.96) |
| 5/14 | 4h-pending cancel |
| 5/15 | range-too-wide skip |
| 5/18 | 4h-pending cancel |
| 5/19 | SHORT fill (+$20.50) |
| 5/20 | 4h-pending cancel |
| 5/21 | SHORT fill (+$5.22) |
| 5/22 | 4h-pending cancel |
| 5/25 | window-close cancel (Memorial Day, 4 reconcile cycles, no fill) |
| **5/26** | **SHORT fill (+$15.32)** |

**4 fills in 12 days = ~33%** fill rate. Backtest baseline is ~13%/day
(~32 trades/yr ÷ 252 days). Live is still running hot vs baseline —
~2.5× expected — likely small-sample variance.

## Notable observations

1. **All wins are coming from the SHORT side.** Now 4 SHORTs in a row,
   3 of them winners. Backtest is direction-symmetric, so this is
   small-sample noise — but worth tracking. If the next 4 fills are
   also all SHORTs and majority winners, that would be a regime
   pattern worth investigating.
2. **No SL or TP has fired live yet.** All 4 exits via V6 window-close.
   Confirms what we saw on 5/21 — SL ~$38 from entry / TP ~$93 are
   farther than an 8h window typically reaches on XAUUSD. The live
   system still has not validated the SL path or the TP path. Worth
   knowing if either ever fires in anger.
3. **7th observed 01:00 ET Gateway disturbance pattern**, expressed
   differently today: the wave started at 00:17 EDT (~43min early)
   rather than the usual 01:00 EDT, with TWS↔server `IB 2110` errors
   mixed in. Cron at 01:15 EDT caught and cycled everything cleanly.
4. **Today's v11 is running the pre-e4b87eb code.** The new
   `max_pending expiry` log line landed in master at ~13:00 EDT, after
   today's 01:16 EDT cron-up. v11 PID 10417 still running the old
   compiled module. The new log line will appear from tomorrow's
   01:15 EDT cron cycle onward — first verification opportunity.
5. **Window-close cancellation noise.** At 12:00 EDT during the
   MARKET-close path, IBKR emitted `Error 202 (Order Canceled —
   discarded)` and `Error 161 (Cancel attempted when not in
   cancellable state)` on the SL/TP legs. This is expected: the
   MARKET-close fills the position, the OCA SL+TP pair then becomes
   un-cancellable, and the safety-cleanup pass cancels everything
   anyway. Functional. Same shape as prior window-close exits.

## What's not happening

- No code/config changes (today)
- No live position currently open (closed at 12:00:06 EDT)
- v11 healthy, sitting in DONE_TODAY until UTC-midnight reset
- Cron tonight 01:15 EDT will cycle as usual — and **will pick up the
  new e4b87eb code** (max_pending expiry log line + reconcile-re-place
  regression test)

## See also

- `docs/journal/2026-05-25_xauusd_daily_summary.md` — yesterday's
  4-cycle reconcile day that motivated e4b87eb
- `docs/journal/2026-05-21_xauusd_third_live_short_window_close_win.md`
- `docs/journal/2026-05-19_first_profitable_live_trade_and_backtest_validates.md`
