# max_pending_hours sweep — KEEP MP4 (current live)

**Date:** 2026-05-20
**Status:** Pre-registered 4-way sweep complete. All variants are
within sampling noise of each other on OOS. MP4 (current live)
ties or wins on every secondary criterion. No live change.

## Why this was tested

Today (2026-05-20) the 4h-pending guard fired at 08:01 EDT,
cancelling both bracket orders. Price then crossed the BUY trigger
(4509.02) at 10:22 EDT — ~2h21m AFTER the cancel — and rallied to
4552.54 before the 12:00 EDT trade-window-close. Estimated phantom
fill PnL: ~+$20 paper that the strategy refused to take.

Nick reasonably asked: **is the 4h cutoff costing us trades that
would have been profitable?**

Pre-registered test:
`docs/superpowers/specs/2026-05-20-max-pending-hours-sweep.md`.
Locked BEFORE viewing any numbers: 4-cell sweep
{MP4, MP6, MP8, MP12} with 5 explicit decision conditions.

## Results

```
cell    OOS_AvgR   OOS_N/yr   EARLY_AvgR   OOS_pos_yrs   ALL_AvgR
MP4      +0.0853      34.7      -0.0556         5/6      +0.0285
MP6      +0.0892      39.8      -0.0704         4/6      +0.0111
MP8      +0.0857      41.3      -0.0727         5/6      +0.0094
MP12     +0.0857      41.3      -0.0727         5/6      +0.0094
```

Best alternative: MP6 at OOS AvgR_slip = +0.0892.
Δ vs MP4: +0.0039R.

Pre-registered conditions for live change (winner MP6):

| # | Condition | Value | Result |
|---|---|---|---|
| 1 | OOS AvgR ≥ +0.05 | +0.0892 | PASS |
| 2 | Δ vs MP4 ≥ +0.030 | +0.0039 | **FAIL** |
| 3 | OOS N/yr ≥ 20 | 39.8 | PASS |
| 4 | OOS positive years ≥ 4/6 | 4 | PASS |
| 5 | EARLY AvgR ≥ MP4_EARLY − 0.10 | −0.0704 (vs MP4 −0.0556) | PASS |

**Condition 2 fails.** Per the locked rules, this means KEEP MP4.

## What the data actually says

The 4-cell spread on OOS is **0.0039R** (MP4 → MP6). For reference,
typical year-to-year noise on a single OOS cell is ~0.10R. The
spread across all four cells (0.004R) is **2.5% of one year's
typical noise** — overwhelmingly likely to be statistical artifact,
not real signal.

Subtle but real findings:

1. **Longer max_pending_hours produces MORE trades but the SAME
   expectancy per trade.** MP4 → MP12 goes 208 → 248 OOS trades
   (+19%) with per-trade AvgR essentially unchanged. The "extra"
   trades are not lower-quality, but they're not higher-quality
   either. Pure volume increase.
2. **MP4 has the best EARLY-period AvgR.** −0.0556 vs −0.0727 for
   the others. In the pre-2018 unfavorable regime, the 4h cutoff
   was actively helping by removing late-day chop-zone whipsaws.
   If a similar regime returns, MP4 is the least-bad version.
3. **2026 YTD shows mild degradation with longer windows.** MP4
   YTD is −0.025; MP6/MP8/MP12 are −0.075 to −0.082. Tiny N (15-28)
   so noise dominates, but the direction is consistent: this year
   has not favored late-window trades.
4. **MP8 and MP12 are identical** because trade-window-length is 8h
   (08:00–16:00 UTC), so MP12 is functionally "no cutoff" and gives
   the same trades as MP8.

## Today specifically — what it tells us

Today's 4h-pending cancel cost a ~$20 phantom win. **Historically
this kind of late-window-trigger event doesn't dominate.** Sometimes
the missed trade would have been a winner (today). Sometimes it
would have been a loser. On average, the two cancel out and per-trade
expectancy stays the same.

This is a "right ex-ante, wrong ex-post" data point. Normal in
trading. No reason to change the rule based on one outcome.

## What stays the same

- Live config `XAUUSD_ORB_CONFIG.max_pending_hours` stays at 4.
- v11 live continues as-is.
- The pre-registered discipline (locked thresholds, multi-comparison
  aware) prevented this from becoming a curve-fit drift.

## What's still genuinely worth thinking about

The strategy refused a ~$20 trade today. Per backtest, this kind of
event happens roughly N/yr × (MP6_N - MP4_N) / N/yr = ~5 times per
year (the extra trades MP6 takes that MP4 doesn't, of which ~46%
would be winners). Roughly 2-3 extra winners + 2-3 extra losers per
year, netting to ~0 expectancy. The 4h cutoff is doing what the
strategy thesis says it should.

If we ever revisit this question, the right separate research would
be: **conditional gating** — e.g., "extend max_pending_hours only
when crossing-bar conditions look like a real late-day setup, not
chop." That's harder to define and pre-register; not worth pursuing
unless there's a specific structural reason.

## Artifacts kept

- `docs/superpowers/specs/2026-05-20-max-pending-hours-sweep.md` —
  pre-registration record
- `v11/backtest/orb_xauusd_mph_sweep.py` — sweep script

## See also

- `docs/journal/2026-05-19_first_profitable_live_trade_and_backtest_validates.md`
  — the prior journal validating the live config
- `v11/backtest/orb_xauusd.py` — the single-cell backtest the sweep
  was derived from
