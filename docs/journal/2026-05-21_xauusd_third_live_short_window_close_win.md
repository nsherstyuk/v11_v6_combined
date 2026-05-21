# XAUUSD ORB — third live SHORT, second consecutive winner

**Date:** 2026-05-21
**Result:** SHORT @ 4518.09 → MARKET @ 4512.87, **PnL +$5.22 paper**
(5h33m hold, trade-window-close exit at 12:00 EDT).

## Today's timeline

```
01:00:54  ERROR Connection lost — waiting 10s  (old v11, pre-cron)
01:01:40  ERROR Failed to connect after 3 attempts  (recurring 01:00 ET Gateway flap)
01:16:28  Cron SUCCESS — Gateway PID=70694, v11 PID=70706 (new process)
02:00:02  Range from IBKR bars: 4518.52 - 4570.72 (52.20 wide)
04:00:03  ORB state: IDLE -> RANGE_READY
04:01:04  Entry stops placed:
            BUY  id=101 @ 4570.72 lmt=4575.94
            SELL id=102 @ 4518.52 lmt=4513.30
            (OCA=ORB_XAUUSD_2026-05-21_080102)
04:01:04  ORB state: RANGE_READY -> ORDERS_PLACED
06:26:48  orderStatusEvent: id=102 (SELL) Filled @ 4518.09
06:26:50  SL @ 4570.72, TP @ 4388.02, ENTRY: SHORT @ 4518.09
12:00:06  Trade window closed, closing at market
12:00:06  MARKET: @ 4512.87 | PnL=+5.22
12:00:06  ORB state: IN_TRADE -> DONE_TODAY
```

## Live record (cumulative)

| Date | Dir | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | 5h17m |
| **2026-05-21** | **SHORT** | **4518.09** | **4512.87** | **+$5.22** | **5h33m** |

**Cumulative net: +$17.76** across 3 trades, 2 winners, 1 loser.
Win rate 67% on N=3 (sample too small for inference).

All three live fills have been SHORT direction. All three exited via
**V6 trade-window-close MARKET path** (12:00 EDT) — no SL or TP has
triggered yet in the live era.

## Pattern of non-trade days

Live era 2026-05-12 → 2026-05-21 = 8 trading days, 3 fills:

| Date | Outcome |
|---|---|
| 5/12 | stale-breakout skip |
| 5/13 | SHORT fill (−$7.96) |
| 5/14 | 4h-pending cancel |
| 5/15 | range-too-wide skip |
| 5/18 | 4h-pending cancel |
| 5/19 | SHORT fill (+$20.50) |
| 5/20 | 4h-pending cancel |
| **5/21** | **SHORT fill (+$5.22)** |

**3 fills in 8 days = ~37%** fill rate. Backtest baseline is
~32 trades/year ÷ 252 trading days = ~13% per-day fill rate.
Recent 8 days is running hotter than baseline (likely small-sample
noise — would expect to mean-revert).

## Notable observations

1. **All wins are coming from the SHORT side.** Three SHORTs in
   a row. Backtest 2026 has been mixed-direction so this is
   small-sample noise, but worth tracking. If the next 5 fills
   are also all SHORTs and all winners, that would be a regime
   pattern worth flagging.
2. **4th observed 01:00 ET Gateway auto-restart pattern** — same
   shape as 5/15, 5/16, 5/19. Cron at 01:15 EDT catches it
   reliably. Functionally fine but recurring noise.
3. **No SL or TP has fired live yet.** All three exits are via
   the V6 trade-window-close path. Statistically expected given
   how far away SL/TP land (SL ~$55 from entry, TP ~$130 — 8h
   trade window isn't enough for typical XAUUSD intraday moves
   to reach them). Worth knowing: the live system has not yet
   validated the SL path or the TP path live.
4. **Backtest validates** the current config (per
   `docs/journal/2026-05-19_first_profitable_live_trade_and_backtest_validates.md`).
   Live record so far is consistent with that read.

## What's not happening

- No code/config changes
- No live position currently open
- v11 healthy, sitting in DONE_TODAY until UTC-midnight reset
- Cron tonight 01:15 EDT will cycle as usual

## See also

- `docs/journal/2026-05-13_xauusd_first_live_fill_full_lifecycle.md`
- `docs/journal/2026-05-19_first_profitable_live_trade_and_backtest_validates.md`
- `docs/journal/2026-05-20_max_pending_hours_sweep_keep_mp4.md`
