# GBPCHF SMA50-cross — descriptive research, two pre-registered hypotheses, both rejected

**Date:** 2026-05-15
**Status:** Research complete. No tradeable edge under the
pre-registered conditions. A real structural pattern (mild
mean-reversion at the cross) is documented for future reference,
but isn't large enough to clear round-trip costs.

## Pre-registration

After downloading GBPCHF 1-min MID/BID/ASK from IBKR (2019-01-07 →
2026-05-15, ~1.21M rows MID, see
`docs/journal/2026-05-15_gbpchf_download_complete.md` — pending),
Nick asked whether an observed SMA-cross pattern on TradingView
chart of GBPCHF 5-min bars could be turned into a tradeable rule.

His observation: when 5-min close crosses SMA50, the crossing bar's
range seems to predict the next bar's continuation, and exit
distances scale with that range.

Two pre-registered hypotheses:

1. **H1 — Cross direction continues, range-conditioned.** When the
   5-min close crosses SMA50, the next bar continues in the cross
   direction. Continuation may be stronger when the crossing bar's
   range is large.

2. **H2 — Trend-flip filter improves continuation.** Only consider
   crosses where `bars_since_last_cross >= 24` (~2h of one-side
   dwelling). These should be "real" trend reversals rather than
   chop-zone re-crossings, and should show a meaningfully different
   continuation rate.

Pre-registered split for any tradeable test (not used in this
descriptive phase):
- IS: 2023–2025
- OOS: 2019–2022

Cost model: empirical BID/ASK at trade time, ~2 pip round-trip slip
+ $4 commission for typical size.

## Phase 1 — unconditional descriptive (H1 test)

Script: `v11/backtest/gbpchf_sma_cross_research.py`
Output: `v11/backtest/results/gbpchf_sma_crosses.csv` (20,564 events).

Key numbers:

```
Total events:            20,564 (avg 2,285/year)
Up-crosses:              50.0%
Down-crosses:            50.0%
Next-bar same-direction (continuation):
  All:                   45.8%   ← under 50% baseline
  Up-crosses:            46.2%
  Down-crosses:          45.4%

Crossing bar range (pips):
  p25=2.70  p50=4.35  p75=6.90  p95=13.15

Next-bar MFE-with-thesis (pips):
  p25=0.55  p50=1.35  p75=2.80  p95=7.09
Next-bar MAE-with-thesis (pips):
  p25=0.70  p50=1.60  p75=3.15  p95=7.30

Continuation × crossing-bar-range:
  cross_range  < median (4.35p):  46.5% N=10,225
  cross_range >= median (4.35p):  45.1% N=10,339

Correlation cross_range × signed_followthru: -0.048
```

By year, cont rate is **45–47% every year from 2019 through 2026**.
Pattern is structural, not regime-dependent.

**H1 verdict: rejected.**

- Continuation rate is BELOW 50% baseline (mild mean-reversion at
  the cross), not above. Direction is opposite to Nick's intuition.
- Crossing-bar range does NOT predict follow-through. Correlation
  −0.048 is statistically noise. Splitting by range shows the
  bigger-range bucket is marginally WORSE for continuation.
- MFE/MAE ≈ symmetric. Median MFE-with-thesis 1.35p is below the
  ~2 pip round-trip cost. The unconditional strategy can't beat
  costs even when assuming correct direction.

## Phase 2 — trend-flip filter (H2 test)

Script: `v11/backtest/gbpchf_sma_cross_trendflip.py`
Filter: `bars_since_last_cross >= 24` (~2h).

| Metric | Unconditional | Trend-flip filter |
|---|---|---|
| N | 20,563 | 3,410 (16.6%) |
| Cont rate | 45.8% | **43.8%** (further from 50%) |
| MFE p50 | 1.35p | 1.30p |
| MAE p50 | 1.60p | **1.70p** (worse) |
| MFE/MAE ratio | 0.84 | **0.76** |
| Crossing range p50 | 4.35p | 4.70p |

Year-by-year delta (trend-flip cont% − unconditional cont%):

```
2019 -1.7%   2023 -3.6%
2020 -3.6%   2024 -0.6%
2021 -5.0%   2025 -0.4%
2022 -2.0%   2026 +4.6% (partial year, N=190)
```

Seven of eight years negative — the filter consistently selects
events that mean-revert *more* than the unconditional set.

Sub-buckets worth noting:

- **Sydney down-crosses:** cont 30.8%, N=300. 19-point anti-
  continuation skew. Likely a thin-session artifact (wider spreads,
  sparse 5-min bars), not a tradeable signal.
- **NY up-crosses:** cont 39.0%, N=480. 11-point anti-continuation
  in the most-traded session. Structurally interesting but won't
  clear cost.

**H2 verdict: rejected.** The filter does NOT improve continuation;
it surfaces stronger mean-reversion. Genuine trend-flip events on
this pair tend to fade back, not continue.

## What's structurally true on this pair

Two pre-registered tests, both rejected, paint a coherent picture:

> On GBPCHF 5-min bars, SMA50 crosses exhibit a mild but persistent
> anti-continuation bias (45.8% cont unconditional, 43.8% on
> trend-flip subset). The next bar is more likely than chance to
> fade back toward the SMA. The signal is stable across 8 years and
> meaningfully different from 50%. **It is too small relative to
> round-trip cost to trade as a direct edge.**

Worth keeping in mind for any future strategy that touches this
pair: an SMA50 cross in the opposite direction within the last bar
is a small head wind, not a tail wind.

## Why we stopped here

- Two pre-registered hypotheses tested, neither produced tradeable
  edge.
- The descriptive output across 20K events is honest and stable —
  there's no "rescue" by re-tuning the conditions that wouldn't be
  curve-fitting noise.
- Trying 15-min instead would be de-facto multiple-comparison
  (searching for the timeframe where this works). If 15-min is
  worth a look later, it would be a separate pre-registration, not
  a continuation of this one.

## What's still useful from this work

1. **The dataset.** `tick_vault_data/fx/GBPCHF_{MIDPOINT,BID,ASK}.csv`
   (~3.6M rows total, 2019-01 → 2026-05) is on disk and reusable
   for any future GBPCHF strategy idea.
2. **The cross-event dump.** `v11/backtest/results/gbpchf_sma_crosses.csv`
   has 20K labeled events with full feature set (range, session,
   bars-since, MFE, MAE, etc.). Useful input for any related
   analysis without needing to re-derive.
3. **The structural observation.** Persistent ~46% / 44%
   anti-continuation is a fingerprint of the pair worth knowing.
4. **The two scripts.** Pattern is reusable for other pairs / other
   moving-average parameters / other timeframes if a fresh
   pre-registration motivates them.

## Process notes for future analyses on this pair

1. The two-phase descriptive-then-conditional approach worked
   cleanly. Phase 1 told us the simple hypothesis fails; Phase 2
   told us the cleanest refinement fails further. Three hours of
   work in total, clean record.
2. Cost reality came in BEFORE PnL modeling: median MFE was below
   round-trip cost even at the descriptive level. That's the right
   place to discover "this can't trade" — not after building a
   full simulator.
3. Pre-registration prevented the obvious next step (search other
   thresholds, other timeframes, other filters) from becoming a
   knob-twisting exercise.

## See also

- `tick_vault_data/fx/_download.log` — empirical download trail
- `v11/backtest/download_gbpchf_sequential.py` — downloader (pair-
  agnostic with one-line changes)
- `v11/backtest/gbpchf_sma_cross_research.py` — Phase 1 script
- `v11/backtest/gbpchf_sma_cross_trendflip.py` — Phase 2 script
- `v11/backtest/results/gbpchf_sma_crosses.csv` — event dump
