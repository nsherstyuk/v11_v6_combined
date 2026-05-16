# USDJPY ORB — pre-registered single-parameter run, decision gate FAIL

**Date:** 2026-05-16
**Status:** Pre-registered hypothesis rejected on the gate. Year-by-year
breakdown reveals strong regime-structured behavior worth recording.
No deployment, no re-tuning, no Variant B in this session.

## Pre-registration

Per `docs/superpowers/specs/2026-05-15-usdjpy-orb-preregistration.md`,
locked 2026-05-15 before any data was loaded:

> Variant A — Asian-range, London/NY-break — mirrors XAUUSD ORB on
> USDJPY. Range 0-6 UTC, trade 8-16 UTC, RR=2.5, range_pct 0.05%-0.50%,
> 1.5 pip RT cost. IS 2023-2025, OOS 2019-2022. Gate:
> OOS AvgR_slip ≥ +0.05R AND N/yr ≥ 20.

## Data

`tick_vault_data/fx/USDJPY_MIDPOINT.csv` — 3,762,207 1-min bars,
2005-03-09 → 2026-04-29. Downloaded from IBKR over ~26h elapsed
(2026-05-15 10:10 EDT → 2026-05-16 12:13 EDT). Empirical BID/ASK also
on disk (`USDJPY_BID.csv`, `USDJPY_ASK.csv`) for future cost-modeling
refinement but not used in this descriptive single-cost run.

## Result

Script: `v11/backtest/orb_usdjpy.py`
Output: `v11/backtest/results/usdjpy_orb_trades.csv` (948 trades).

```
                              N    WR     AvgR_slip   PF   MaxDD
EARLY (2005-2018, holdout)  589  44.7%   -0.008     0.98  38.70
OOS   (2019-2022, gate)     170  44.1%   -0.019     0.95  12.10
IS    (2023-2025)           158  53.2%   +0.237     1.70   8.21
ALL                         948  46.1%   +0.035     1.08  38.70
```

Decision gate (PRE-REGISTERED):
- OOS AvgR_slip = **−0.019** < +0.050 threshold → **FAIL**
- OOS N/yr = 42.5 > 20 → PASS
- Overall: **FAIL — null result**

## Year-by-year detail (with slippage)

```
year     N    WR%    AvgR     PF   segment
2005    46   47.8   +0.099   1.25  early
2006    51   37.3   −0.177   0.62  early
2007    50   38.0   −0.054   0.88  early
2008    29   41.4   −0.228   0.61  early
2009    31   41.9   −0.131   0.72  early
2010    43   39.5   −0.070   0.85  early
2011    51   41.2   −0.133   0.74  early
2012    47   40.4   +0.002   1.00  early
2013    33   51.5   +0.219   1.62  early
2014    40   52.5   +0.184   1.44  early
2015    43   46.5   +0.029   1.07  early
2016    31   58.1   +0.090   1.31  early
2017    46   52.2   +0.286   1.76  early
2018    48   43.8   −0.184   0.62  early
─────────────────────────────────────────────
2019    48   37.5   −0.136   0.73  OOS
2020    46   39.1   +0.094   1.23  OOS
2021    37   40.5   −0.230   0.53  OOS
2022    39   61.5   +0.192   1.71  OOS
─────────────────────────────────────────────
2023    45   55.6   +0.249   1.73  IS
2024    61   49.2   +0.131   1.32  IS
2025    52   55.8   +0.350   2.33  IS
2026    31   48.4   +0.125   1.32  early(YTD)
```

## What's structurally true

1. **The unconditional gate fails.** Pre-registered window says no edge.
2. **The year-by-year shows clear regime structure**, not pure noise:
   - 2005-2011: 7 mostly-negative years (low yen vol, BOJ ZIRP era)
   - 2012-2017: 6 mostly-positive years (Abenomics-period regime)
   - 2018-2021: 4 mostly-negative years (low vol, range-bound)
   - 2022-2026 YTD: 5 consecutive positive years (BOJ rate
     normalization + intervention era, USDJPY 130-160 trading range)
3. **The 2022+ regime is structurally similar in pattern to 2013-2017**
   — periods of high yen volatility produce positive ORB outcomes;
   periods of compressed yen volatility produce negative ones.

## What this rules out (this session)

- Deploying USDJPY ORB as it stands. No.
- Re-tuning rr_ratio, range bounds, or trade window to "rescue" the
  gate. That's curve-fitting on the data we just saw.
- Re-splitting IS/OOS to declare a win. Same problem.
- Testing Variant B (pre-Tokyo range, Tokyo break) as a substitute.
  Multiple-comparison hazard.

## What's still honestly tractable (separate pre-registration on
a separate day, if pursued)

The regime structure is the real finding. Two ways it might be
formalized into an honest test:

1. **Vol-conditional USDJPY ORB.** Pre-register a single VIX-equivalent
   (USDJPY 20-day realized vol) threshold that gates trading. Test
   strictly on the 2022-2026 regime as the new "IS" with a pre-2022
   walk-forward OOS check (different from today's IS/OOS).
2. **BOJ-policy-conditional USDJPY ORB.** Pre-register a binary
   "policy regime" indicator (rates above floor, intervention threat
   active, etc.) and gate on it. More specific, harder to construct,
   easier to interpret.

Neither is "let's tune." Both require defining the regime indicator
BEFORE looking at the cell-by-cell results.

## What I am NOT recommending

- Running the regime-conditional version in this session. That's
  research that needs its own pre-registration document, on a
  separate day, treating today's findings as background context
  rather than the test target.

## What v11 should do operationally

Nothing. The current live system (XAUUSD ORB) is unaffected. The
USDJPY downloader is gone (clean exit). Hourly poll cron `4f0af042`
cancelled. v11 continues as the single live strategy with no second
strategy on deck.

## Artifacts kept

- `v11/backtest/download_usdjpy_sequential.py` — downloader (reusable
  pattern for other JPY pairs if a fresh pre-registration motivates)
- `v11/backtest/orb_usdjpy.py` — backtest script (locked parameters
  per pre-registration)
- `v11/backtest/results/usdjpy_orb_trades.csv` — 948 trades, full
  feature set, gitignored but regeneratable
- `tick_vault_data/fx/USDJPY_*.csv` — 11.14M rows of 1-min data,
  21 years, MID + BID + ASK
- `docs/superpowers/specs/2026-05-15-usdjpy-orb-preregistration.md`
  — pre-registration record (what was promised, what was tested)

## See also

- `docs/journal/2026-05-11_orb_us_equities_regime_change.md` — prior
  ORB research arc that hit the inverse regime pattern (edge died
  2024+ on US equities)
- `docs/journal/2026-05-15_gbpchf_sma50_cross_research.md` — prior
  research arc on GBPCHF with two pre-registered nulls
- `docs/journal/2026-05-13_xauusd_first_live_fill_full_lifecycle.md`
  — the working strategy (XAUUSD) that motivated the ORB-generalization
  hypothesis tested here
