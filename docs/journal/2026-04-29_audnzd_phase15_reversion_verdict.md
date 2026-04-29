# AUD/NZD Phase 1.5 — Direct Mean-Reversion Test: PASS (preliminary)

**Date:** 2026-04-29
**Status:** Phase 1.5 complete on partial data (2018-01 → 2022-06). All three pass criteria met. Full-data revalidation still required before Phase 2.
**Script:** `v11/backtest/investigate_audnzd_reversion.py`

---

## 1. Why Phase 1.5 exists

Phase 1 (`investigate_audnzd_eda.py`) tested whether the 22:00–06:00 UTC window is *quiet* — it's not (quiet/busy abs-return ratio = 0.89, well above the 0.70 threshold; median spread 2.14 pips, marginal). Phase 1 read as a fail.

But the actual hypothesis isn't "quiet" — it's "mean-reverting." Those are different claims. A window can be active *and* reverting. Phase 1.5 tests the reversion claim directly, not via activity proxies.

## 2. Test design

For each night with sufficient coverage (≥120 bars in each half):
- First half: 22:00–02:00 UTC. Midpoint = (max(high) + min(low)) / 2 over the first half.
- `dev_02` = close at 02:00 UTC − midpoint (rubber-band stretch)
- `move_06` = close at 06:00 UTC − close at 02:00 UTC (what happens next)
- Reversion ⇒ corr(dev_02, move_06) < 0; price moves opposite to its stretch direction.

Round-trip spread cost budgeted at 4 pips (≈ 2 pip spread × 2 sides).

## 3. Results (886 nights, 2018-01 → 2022-06)

| Metric | Value | Threshold | Status |
|---|---:|---|:---:|
| Pearson corr(dev_02, move_06) | **-0.498** | < -0.10 | PASS |
| Hit rate moving toward midpoint | **65.6%** | > 55% | PASS |
| Mean net result after 4-pip cost | **+7.67 pips/night** | > 0 | PASS |
| % nights net-profitable | 60.3% | — | — |

### Stratification by stretch magnitude
| Stretch (pips) | n | mean_revert (pips) | hit rate |
|---|---:|---:|---:|
| 0–5 | 398 | 2.29 | 54.5% |
| 5–10 | 290 | 11.01 | 67.6% |
| 10–15 | 112 | 25.71 | 82.1% |
| 15–20 | 48 | 25.78 | 85.4% |
| 20–30 | 28 | 51.56 | 92.9% |
| 30–50 | 8 | 69.51 | 100.0% |
| 50+ | 2 | 56.95 | 50.0% |

Monotonic strengthening with stretch (until n collapses at 50+). Tradeable threshold (mean_revert > 4 pips) starts at the 5-pip stretch bin.

### Year-by-year stability
| Year | n | corr | hit rate | mean_revert (pips) |
|---|---:|---:|---:|---:|
| 2018 | 192 | -0.585 | 70.8% | 15.29 |
| 2019 | 207 | -0.430 | 65.7% | 11.90 |
| 2020 | 193 | -0.507 | 62.2% | 11.27 |
| 2021 | 208 | -0.571 | 63.9% | 9.48 |
| 2022 | 86 | -0.559 | 65.1% | 9.21 |

No year breaks the pattern. 2020 (covid) is the weakest by hit rate but still well above 55%.

## 4. Why Phase 1 disagreed

Phase 1 measured **activity** (mean |return| per minute). Phase 1.5 measures **structure** (does price return to mid). The window is active *and* reverting — Gemini's framing conflated the two, and so did Phase 1's pass criteria. Reversion is the real edge; quiet was a red herring.

## 5. Caveats — do not get excited yet

1. **Partial data.** CSV ends 2022-06; the V11 IS window (2024+) is unseen. Full download still in progress (task `bia00z9df`). Re-run Phase 1.5 on complete data before any Phase 2 work.
2. **4-pip cost is optimistic.** Phase 1 measured median 22-06 UTC spread at 2.14 pips → realized round-trip ≈ 4.3 pips. Edge survives but tighter than the table suggests.
3. **Close-to-close only.** Phase 1.5 doesn't model intra-night drawdown. A 65% hit rate is meaningless if the 35% of losing nights have unbounded MFE-against. Phase 2 must measure MAE/MFE per night to size stops realistically.
4. **No execution cost beyond spread.** Slippage, partial fills, weekend gaps, Sunday open — none modeled.
5. **Selection bias.** The 22:00 anchor + ≥120 bar requirement drops nights with thin data. If those drops correlate with regime, results are biased. Coverage breakdown not yet checked against the dropped set.

## 6. Decision

- **Go ahead and commit.** Phase 1.5 is a real, falsifiable result that flips the Phase 1 verdict on its head.
- **Do not start Phase 2** until full data lands and Phase 1.5 re-runs cleanly on 2018–2026.
- **When Phase 2 starts**, the entry rule should target the ≥5 pip stretch bin (where mean_revert > spread cost), not all nights. Roughly 55% of nights qualify.

## 7. Output artifacts

- `v11/backtest/investigate_audnzd_reversion.py` — script
- `v11/backtest/audnzd_eda_output/reversion_nights.csv` — per-night data for inspection
- This journal — verdict + caveats
