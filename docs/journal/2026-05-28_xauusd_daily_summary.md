# XAUUSD ORB daily summary — 2026-05-28

**Outcome:** NO-TRADE (range-too-wide skip). 2nd range-too-wide of the
live era (first was 2026-05-15).

Asian range 4366.57 – 4458.90 = **92.33 points (2.09%)** — well above
the `max_range_size` threshold. Strategy correctly declined: a wide
overnight range implies trend momentum the breakout system isn't
sized to handle.

## Timeline

```
00:21:36  IB 1100/2110 disconnect cycle, partial recovery
00:22:43  Reconnected to IBKR — re-qualified XAUUSD
01:00:15  Gateway down — 68.8s outage (multiple failed reconnect cycles)
01:01:21  Reconnected, rebind + reconcile clean (broker flat)
01:15:04  Cron SIGTERM — clean shutdown
01:16:27  Cron SUCCESS — Gateway PID=11297, v11 PID=11326 (new process)
02:00:02  Range from IBKR bars: 4366.57 - 4458.90 (92.33 wide, 72 bars)
04:00:03  Range too wide (2.09%) — exceeds max_range_size
04:00:03  Range invalid (size: 92.33), done
04:00:03  ORB state: IDLE -> DONE_TODAY
```

No brackets placed. No fill. No PnL. Strategy never entered the trade
window because the range filter rejected the setup at window-open.

## Live record (cumulative)

| Date | Dir | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | 5h17m |
| 2026-05-21 | SHORT | 4518.09 | 4512.87 | +$5.22 | 5h33m |
| 2026-05-26 | SHORT | 4522.81 | 4507.49 | +$15.32 | 7h31m |

**Cumulative net: +$33.08** across 4 trades, 3W/1L. Unchanged.

## Non-trade pattern (live era 2026-05-12 → 2026-05-28)

14 trading days, 4 fills, 10 no-trade days:
- stale-breakout skips: 2 (5/12, 5/27)
- 4h-pending cancels: 4 (5/14, 5/18, 5/20, 5/22)
- **range-too-wide skips: 2 (5/15, 5/28)**
- window-close cancels: 1 (5/25)
- weekends not counted

Fill rate: 4/14 ≈ **29%** — converging from the recent hot stretch
back toward the ~13%/day backtest baseline. **3-day no-trade streak
exercises 3 different skip reasons** (5/25 window-close, 5/27 stale,
5/28 wide-range) — the filter system is working as designed.

## Notable observations

1. **3rd consecutive day of elevated 01:00 ET Gateway disturbance.**
   Today: 68.8s outage with multiple failed reconnect attempts
   (5/26: 76s, 5/27: 76s, 5/28: 69s). The pattern is now consistent
   enough to call a trend rather than a one-off — IBKR appears to
   have changed its overnight restart behavior in the past week.
   Functionally still handled: v11 reconnect eventually succeeds,
   Phase 1 rebind fires, reconcile sees broker flat, and the 01:15
   EDT cron cycles everything anyway. Nothing to fix on our end yet;
   the 01:15 cron is the safety net and it works.
2. **Wide overnight range = trend day skipped.** Gold moved $92 in
   the 6h Asian session — that's a strong overnight directional
   move. The ORB strategy is designed for *reversion / breakout
   from balance*, not for trend continuation. Skipping wide-range
   days protects against entering against ongoing momentum.
3. **3rd consecutive no-trade day** — the live system is
   mean-reverting toward the backtest fill-rate baseline. After the
   2.5× hot stretch earlier in the live era, this is *constructive*
   noise, not a problem.
4. **New `max_pending expiry` log line still not exercised in
   production.** Only fires on successful bracket placement; today's
   range-too-wide skip never got there. 3rd day pending. First
   verification opportunity: the next day brackets actually go down.
5. **No code or config changes.** Resist the temptation to widen
   `max_range_size` to capture days like today. Backtest already
   validated the current threshold across 16 years.

## What's not happening

- No code/config changes today
- No live position opened
- v11 healthy, sitting in DONE_TODAY until UTC-midnight reset
- Cron tonight 01:15 EDT will cycle as usual

## See also

- `docs/journal/2026-05-27_xauusd_daily_summary.md` — yesterday's
  stale-breakout skip and earlier observations of the escalated
  01:00 ET Gateway pattern
- `docs/journal/2026-05-26_xauusd_daily_summary.md` — most recent
  fill (4th live SHORT win)
