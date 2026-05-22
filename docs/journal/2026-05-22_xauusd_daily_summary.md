# XAUUSD ORB daily summary — 2026-05-22

**Outcome:** NO-TRADE (4h-pending cancel).

## Timeline

```
02:00:02  Range from IBKR bars: 4507.41 - 4541.27 (33.86 wide, 72 bars)
04:00:02  ORB state: IDLE -> RANGE_READY
04:01:04  Entry stops placed:
            BUY  id=116 @ 4541.27 lmt=4544.66
            SELL id=117 @ 4507.41 lmt=4504.02
04:01:04  ORB state: RANGE_READY -> ORDERS_PLACED
08:01:03  orderStatusEvent: id=116 (BUY) Cancelled
08:01:04  orderStatusEvent: id=117 (SELL) Cancelled
08:01:04  ORB state: ORDERS_PLACED -> DONE_TODAY
```

Tight 33.86-wide range; price stayed inside the full 4h between
04:00 EDT trade-window-open and 08:01 EDT 4h-pending guard fire.
No fill, no PnL.

## Cumulative live record

| Date | Dir | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | 5h17m |
| 2026-05-21 | SHORT | 4518.09 | 4512.87 | +$5.22 | 5h33m |

**Cumulative net: +$17.76 across 3 trades, 2W/1L.** Unchanged.

## Non-trade pattern (live era 2026-05-12 → 2026-05-22)

10 trading days, 3 fills, 7 no-trade days:
- stale-breakout skips: 1 (5/12)
- 4h-pending cancels: **4** (5/14, 5/18, 5/20, **5/22**)
- range-too-wide skips: 1 (5/15)
- weekends not counted

4h-pending is now the most common no-trade outcome (40% of days).

## Errors today

5th observed 01:00 ET Gateway auto-restart pattern (routine
cosmetic noise; cron at 01:15 EDT caught it as usual). No new
unexplained errors. The Cancelled-entry "ERROR" lines at 08:01
are the expected 4h-pending guard logging.

## Nothing else changed

Code and live config unchanged.
