# XAUUSD ORB daily summary — 2026-05-29

**Outcome:** NO-TRADE (stale-breakout skip, UPSIDE). 3rd stale-breakout
of the live era — and the **first upside stale-breakout** (prior two
on 5/12 and 5/27 were downside).

Range 4492.23 – 4520.96 (28.73 wide, normal). At 04:01:02 EDT
trade-window eval, price was **4523.79 — $2.83 above the range high**.
The stale-breakout guard correctly declined to place brackets.

## Timeline

```
00:22:?  IB 1100/2110 disconnect cycle (overnight pattern)
01:00:?  Gateway down — multi-second outage with failed reconnects
         (4th consecutive day of the elevated 01:00 ET pattern)
01:16:27  Cron SUCCESS — Gateway PID=77232, v11 PID=77259 (new process)
02:00:02  Range from IBKR bars: 4492.23 - 4520.96 (28.73 wide, 72 bars)
04:00:02  Range: 4492.23 - 4520.96 (size: 28.73)
04:00:02  ORB state: IDLE -> RANGE_READY
04:01:02  ORB stale breakout: price=4523.79 outside range
          [4492.23-4520.96], skipping (price already outside range
          at eval time)
04:01:02  ORB state: RANGE_READY -> DONE_TODAY
```

No brackets placed. No fill. No PnL. Strategy correctly declined a
setup where the breakout had already happened pre-window.

## Live record (cumulative)

| Date | Dir | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | 5h17m |
| 2026-05-21 | SHORT | 4518.09 | 4512.87 | +$5.22 | 5h33m |
| 2026-05-26 | SHORT | 4522.81 | 4507.49 | +$15.32 | 7h31m |

**Cumulative net: +$33.08** across 4 trades, 3W/1L. Unchanged.

## Non-trade pattern (live era 2026-05-12 → 2026-05-29)

15 trading days, 4 fills, 11 no-trade days:
- **stale-breakout skips: 3 (5/12 down, 5/27 down, 5/29 up)**
- 4h-pending cancels: 4 (5/14, 5/18, 5/20, 5/22)
- range-too-wide skips: 2 (5/15, 5/28)
- window-close cancels: 1 (5/25)
- weekends not counted

Fill rate: 4/15 ≈ **27%** — continuing to mean-revert toward the
~13%/day backtest baseline as no-trade days accumulate.

**4 consecutive no-trade days (5/25 → 5/29), each via a different
skip mechanism.** Mean-reversion in action; the filter system is
exercising its full range of decision branches.

## Notable observations

1. **First UPSIDE stale-breakout in the live era.** The prior two
   stale-breakout skips (5/12, 5/27) were both downside (price already
   below range low). Today's was upside (4523.79 > 4520.96). This
   small data point is useful: it confirms the stale-breakout guard is
   direction-symmetric in practice, not just in code. If only downside
   skips ever fired, that would be one more thing pointing at a
   SHORT-bias in the data. Today is a counter-example. **The
   strategy is not structurally biased toward SHORTs** — the SHORT
   tilt in fills (4/4) appears to be small-sample variance in
   *which* breakouts actually fill, not a structural skew in the
   filter logic.
2. **4th consecutive day of elevated 01:00 ET Gateway disturbance.**
   Same shape as 5/26-5/28. Now confidently a persistent IBKR-side
   change, not random flap. v11 reconnect path continues to handle
   it cleanly; 01:15 EDT cron is the safety net. No action on our
   end yet.
3. **`max_pending expiry` log line still un-exercised in production.**
   4th day pending. Only fires on successful bracket placement;
   today's stale-breakout skip never got there. First verification
   opportunity: the next day brackets actually go down.
4. **4 consecutive no-trade days is normal-ish.** Backtest baseline
   is ~13% fill rate = ~32 trades/yr → average gap between fills
   is ~8 trading days. So 4 in a row is not yet a concern — just
   the system running closer to baseline pacing now that the early
   hot stretch has cooled.
5. **No code or config changes.** Continued resistance to tinkering
   on N=4 fills. Backtest already validated the current config across
   16 years.

## Weekend

Markets close after today. No trade events expected Sat/Sun. v11 will
stay running through the weekend (no shutdown planned this time
unlike 5/23). Monday 6/1: normal 01:15 EDT cron, normal trade window.

## What's not happening

- No code/config changes today
- No live position opened
- v11 healthy, sitting in DONE_TODAY until UTC-midnight reset
- Cron tonight 01:15 EDT will cycle as usual

## See also

- `docs/journal/2026-05-27_xauusd_daily_summary.md` — prior
  downside stale-breakout skip
- `docs/journal/2026-05-28_xauusd_daily_summary.md` — yesterday's
  range-too-wide skip and earlier observations on the recurring
  01:00 ET Gateway pattern
- `docs/journal/2026-05-26_xauusd_daily_summary.md` — most recent
  fill (4th live SHORT win, +$15.32)
