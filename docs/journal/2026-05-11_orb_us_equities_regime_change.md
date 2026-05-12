# US-equity ORB — pre-registered analysis found a regime change, not a tradeable edge

**Date:** 2026-05-11
**Status:** Analysis complete. Recommendation: **do not deploy**.
**Scripts:** `v11/backtest/orb_us_equities_grid.py` (breakout),
`v11/backtest/orb_us_equities_fade_grid.py` (fade — disproved alt hypothesis),
`v11/backtest/orb_us_equities_year_breakdown.py` (regime diagnosis).
**Data:** `tick_vault_data/us_equities/*.csv` — 15 tickers, 29.6M 1-min bars,
2017-12 → 2026-05-08, downloaded via IBKR.

---

## What we did

Ran the pre-registered ORB template from `orb_fx_grid.py`, adapted for US
equity sessions:

- **Range window:** 09:30–10:30 ET (first hour of regular trading)
- **Trade window:** 10:30–15:30 ET
- **RR:** 2.5 (breakout) / 2.0 (fade variant)
- **Range %:** 0.10–2.00% of mid
- **Costs:** $0.03/share round-trip (1¢ slip ×2 + 0.5¢ comm ×2)
- **IS / OOS:** 2024+ / 2018–2023
- **Decision gate:** OOS AvgR_slip ≥ +0.05R AND N/yr ≥ 20

Pre-registered universe: SPY, QQQ, IWM, DIA, AAPL, MSFT, NVDA, META,
AMZN, GOOGL, TSLA, INTC, MU, AMD, TSM.

## A bug found mid-analysis (worth recording)

First-pass results showed WR=8% on breakout (vs 28.6% breakeven for
RR=2.5), which prompted an "anti-selection" hypothesis. Re-reading the
code, the time-exit fallback at 15:30 ET **never fired** — the
`post`-entry window was filtered to ≤15:29:59, then the time-exit
search looked for ≥15:30 bars *inside* `post`. Empty by construction.
Trades that didn't decisively hit target or stop by 15:29 were silently
discarded. The recorded trade set was heavily biased toward decisive
moves; since stops are closer (1× range) than targets (2.5× range),
stops were over-represented and the apparent 8% WR was artifactual.

After fix (read time-exit bars from `day_bars` directly):
- Breakout WR pooled: 8% → 50.1%
- Breakout AvgR pooled: −0.71 → +0.041 (raw) / +0.003 (after costs)
- Three OOS survivors: META, AMZN, TSLA passed the +0.05 AvgR_slip gate

**Rule of thumb to keep:** always be suspicious of strategy results
where WR is much lower than breakeven-WR. Real signals usually cluster
near breakeven; deeply sub-breakeven WRs are most often backtest bugs,
not anti-selection. Same in the other direction — much-above breakeven
WRs are usually look-ahead bias.

## What the survivor pattern actually was

The IS/OOS split made the survivors look real (OOS gate passed) but
something was off — IS performance was *worse* than OOS for all three
survivors. Normally IS overfits and OOS reverts to truth. Here OOS
looked better than IS.

The year-by-year breakdown explained it. META + AMZN + TSLA pooled by year:

```
Year   N    WR    AvgR_slip   PF
----  ---  ----   ---------  ----
2018  414  56.8%   +0.086    1.54   ← passing
2019  517  51.6%   +0.027    1.32
2020  348  54.0%   +0.149    1.72   ← peak
2021  460  52.4%   +0.084    1.43   ← passing
2022  209  52.2%   +0.070    1.28   ← passing
2023  366  54.6%   +0.033    1.19   ← transitioning
2024  405  48.1%   −0.005    1.02   ← edge gone
2025  371  42.6%   −0.062    0.80   ← clearly losing
2026  147  44.9%   −0.015    0.96   ← still losing
```

There was a real, multi-year, structural breakout edge on momentum
mega-caps from 2018 through 2022 (~five consecutive positive years).
It started weakening in 2023. **It has been net-negative every year
since 2024.** The OOS window (2018–2023) captured the live edge; the
IS window (2024+) captured the dead edge.

This isn't a 2020-2021-COVID-only artifact (which was my hypothesis
going into the breakdown). It's a longer-running regime that
encompassed the 2018-2022 momentum era and ended sometime in 2023.
Possible drivers: rate cycle, post-AI-mania consolidation, post-meme
retail behavior, vol-of-vol compression. We can speculate but can't
test causally.

## The pooled aggregate confirms the regime story

All 15 tickers pooled by year (15,078 OOS trades):
- Pre-2024: mostly near-breakeven with one positive year (2022 +0.063)
- 2024: +0.006
- 2025: −0.008
- 2026 YTD: −0.010

The edge wasn't hiding anywhere in the broader universe. The mega-cap
subset (META/AMZN/TSLA) was where the real action was. And it's gone.

## Fade variant (the alternative hypothesis)

Tested ORB-fade (short the up-break, long the down-break, target the
opposite side of range) with RR=2.0 — same trigger, opposite direction.
Pre-registered as the natural counter-hypothesis to "anti-selection."

After the time-exit bug fix:
- Fade aggregate OOS: WR 39.5%, AvgR_slip −0.130, PF 0.77
- **Zero survivors** across all 15 tickers
- Fade is clearly losing — confirmed by both IS and OOS, both with and without costs

Both directions of the strategy are losing money in the current regime.
Fade was a meaningfully different hypothesis (different RR, different
target distance), and it failed too. Don't pursue.

## Recommendation

**Don't deploy ORB-breakout to live or paper trading on this universe**,
including the gate-passing survivors. The last 24 months of data are
the most relevant for forward trading, and they're negative across
every cell.

Specifically:
- Don't add META/AMZN/TSLA ORB-breakout to v11. The OOS-survivor gate
  is misleading without regime context.
- Don't tune parameters to "recover" recent performance — that's
  curve-fitting on a regime that may not recur. The research-discipline
  principle here is firm.
- Don't broaden the universe hoping to find a healthier ticker. The
  aggregate is at zero; adding tickers averages to zero too.

## What the dataset is still good for

The 15-ticker, 29.6M-row 1-min dataset remains valuable as:
1. A **baseline** for comparing future equity strategies.
2. Reference data for analyses that aren't strict ORB (e.g., volume
   profile, premarket-gap-fade, end-of-day trends).
3. Validation surface for any v11 module that needs equity-bar inputs.

It just doesn't reveal a tradeable edge under the pre-registered ORB
template.

## What might be worth a fresh round (each requires a *separate*
   pre-registration — NOT to be tuned on this dataset)

1. **Different range window:** first 5-min or 15-min instead of 60-min.
   Smaller universe to start. Pre-register a clean 2×2 grid (range × RR)
   and report all cells.
2. **Regime-conditional strategy:** ORB only when realized volatility
   is in a specific regime. Adds one parameter; needs honest
   pre-registration to avoid curve-fitting on the 2018-2022 vol regime.
3. **Sector-rotation overlay:** ORB on the strongest-trending sector
   ETF (rolling 60-day momentum). Different question.

None of these are obvious next steps. The most useful conclusion is
that **XAUUSD ORB (with its actual validated edge) is the right thing
to focus on for live paper trading**, not US-equity ORB.

## Process notes for future analyses

1. **Always include a time-exit (or no-exit) sanity check.** Bugs in
   exit logic produce backtest results that look like signal but are
   artifact. The 8%-WR ratio I almost reported with confidence was
   entirely from a 4-line code bug.
2. **The year-by-year breakdown is essential** for any IS/OOS split
   that uses a single chronological cutoff. A clean IS-passes-OOS-fails
   or IS-fails-OOS-passes can hide a regime boundary at the split point.
   Future analyses should default to year-by-year as well as IS/OOS.
3. **Be skeptical of "OOS looks better than IS."** It's not rare for
   data periods to differ structurally; the question is always "what
   changed and is it persistent."

## See also

- `v11/backtest/orb_fx_grid.py` — the source template that this work
  mirrors. Same conclusion on FX: no exploitable edge on most majors.
- `docs/superpowers/reviews/2026-05-10-project-direction-review.md` —
  P2 priority that "the currently promising stock direction is
  pre-registered US equity / ETF ORB research." This journal entry
  records the outcome of that research.
- `docs/PROJECT_STATUS.md` "Current state" — should reference this
  finding when the equity-ORB research line gets retired.
