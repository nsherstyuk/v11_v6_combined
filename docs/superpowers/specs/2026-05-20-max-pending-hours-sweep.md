# XAUUSD ORB max_pending_hours sweep — pre-registration

**Date locked:** 2026-05-20 (after seeing today's 4h-pending cancel and
the empirical observation that price would have triggered the BUY
~2h after the cancel)

## Hypothesis

The live `XAUUSD_ORB_CONFIG.max_pending_hours = 4` may be discarding
profitable late-trade-window breakouts. Specifically: today
(2026-05-20) brackets were cancelled at 08:01 EDT, then price
crossed the BUY trigger at 10:22 EDT (~2h21m after cancel) and
rallied to peak before the 12:00 EDT trade-window-close MARKET
exit — a phantom ~+$20 win that the strategy refused.

If this is the typical pattern over 16 years, a longer
`max_pending_hours` value would empirically beat the current 4-hour
choice on OOS.

If today is the exception (i.e., the 4-hour cutoff correctly avoids
*more* losing late-breakouts than profitable ones, on average), the
4-hour choice will dominate.

## Variants to test (locked)

All other parameters from `v11/live/run_live.py:XAUUSD_ORB_CONFIG`
held constant. Only `max_pending_hours` varies:

- **MP4**  (current live): max_pending_hours = 4
- **MP6**:                  max_pending_hours = 6
- **MP8**:                  max_pending_hours = 8
- **MP12** (effectively no cutoff): max_pending_hours = 12

Trade window is `trade_start_hour=8, trade_end_hour=16` UTC = 8
hours total. MP12 ≥ trade-window-length so it never fires; the
V6 trade-window-close MARKET exit becomes the only exit.

## Data

`tick_vault_data/xauusd/XAUUSD_MIDPOINT.csv` — 2.52M bars,
2010-06-08 → 2026-05-18 (the same data used in
`docs/journal/2026-05-19_first_profitable_live_trade_and_backtest_validates.md`).

## Period split (same as prior XAUUSD backtest)

- EARLY 2010-2017 — holdout, not part of gate
- **OOS 2018-2023 — gate window**
- IS 2024-now

## Decision rules

This is a **4-way parameter search**, so naive winner-takes-all is
biased toward declaring noise as signal. Required for the winner
to motivate a live change:

1. Winner's **OOS AvgR_slip ≥ +0.05** (the original gate threshold)
2. Winner's **OOS AvgR_slip ≥ MP4_OOS_AvgR_slip + 0.030** (i.e.,
   the new value must be meaningfully better than current, not
   just nominally better)
3. Winner's **OOS N/yr ≥ 20**
4. **Year-by-year regime stability**: winner positive in at
   least 4 of 6 OOS years
5. Winner's **EARLY AvgR_slip not catastrophically worse than
   MP4's EARLY AvgR_slip** (max regression -0.10R from MP4 EARLY)

If all 5 conditions met → propose live change with explicit Nick
OK. If any condition fails → keep MP4. Either way, journal the
result.

## What we will NOT do

- Test additional values mid-run (e.g., "MP5 might be even better")
- Tune a different parameter "while we're at it"
- Drop the "± vs MP4" discipline if MP4 happens to be slightly
  positive

## Cost model

Same as prior XAUUSD backtest: $0.30 RT slip+spread (per-side
slippage_pts = 0.15 in price units).

## Multiple-comparison note

We test 4 cells. A naive p<0.05 winner across 4 cells has
~20% chance of being a noise artifact. The +0.030 buffer above
MP4 is the practical discount: roughly 60% of typical 1-year-of-trades
AvgR_slip standard error on this dataset, so the winner must clear
~1 standard error above MP4 to be taken seriously. Not a formal
Bonferroni correction but in the same spirit.

## Pre-registered before any number is observed.
