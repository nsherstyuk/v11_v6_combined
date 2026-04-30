# AUD/NZD Phase 2 — Naive Baseline Backtest: PASS, with discipline caveats

**Date:** 2026-04-30
**Status:** Phase 2 baseline backtest complete. Gate cleared with margin. Phase 3 (filters) is now justified — but the absence of stop-loss modeling means these numbers are an *upper bound* on tradability, not a tradable strategy.
**Script:** `v11/backtest/backtest_audnzd_phase2.py`

---

## 1. The fixed rule (no parameters tuned)

- First half: 22:00–02:00 UTC. midpoint = (max(high) + min(low)) / 2.
- At the 01:59 bar close (the entry bar): dev = close − midpoint.
- If |dev| < 5 pips: skip the night. Phase 1.5 stratification showed the 0–5 pip bin barely beats coin-flip after cost.
- If dev > 0: SELL at bid (mid − spread/2). Reverts toward midpoint.
- If dev < 0: BUY at ask (mid + spread/2).
- Exit at the 05:59 bar close, opposite side.
- Cost: realized per-bar spread at entry + at exit (median 4.1 pips/trade observed).

55.3% of nights produce a trade (922 of 1668 over 2018–2026).

## 2. Headline metrics

| Split | n | WR | AvgR (net) | PF | MaxDD | Total (pips) |
|---|---:|---:|---:|---:|---:|---:|
| ALL | 922 | 70.2% | +14.87 | 3.73 | -188.8 | +13,709 |
| **OOS (2018-23)** | **673** | **70.4%** | **+14.89** | **3.54** | **-188.8** | **+10,022** |
| **IS (2024-26)** | **249** | **69.5%** | **+14.81** | **4.41** | **-68.3** | **+3,687** |

Per-year breakdown (PF range 2.53 – 5.89, every year profitable):

| Year | n | WR | AvgR | PF |
|---|---:|---:|---:|---:|
| 2018 | 114 | 78.9% | +20.4 | 5.12 |
| 2019 | 116 | 69.0% | +12.6 | 2.99 |
| 2020 | 114 | 66.7% | +16.1 | 3.56 |
| 2021 | 98 | 74.5% | +14.1 | 4.28 |
| 2022 | 119 | 66.4% | +11.4 | 2.53 |
| 2023 | 112 | 67.9% | +14.9 | 3.66 |
| 2024 | 109 | 64.2% | +13.2 | 3.33 |
| 2025 | 96 | 71.9% | +13.8 | 5.89 |
| 2026 (YTD) | 44 | 77.3% | +21.1 | 5.72 |

Buy vs sell symmetry: BUY 67.9% WR / +15.08 pips, SELL 72.3% WR / +14.68 pips. No directional bias. Edge is structural, not a hidden carry trade.

## 3. Gate: PASS

The Phase 2 → Phase 3 gate from the original plan was OOS AvgR > 0 with PF > 1.3 after costs. Achieved: AvgR +14.89 pips, PF 3.54. Both criteria cleared by a wide margin on the OOS window alone, and the IS window is even cleaner.

## 4. Why I am not throwing a party

These numbers are the strongest single backtest pass I've seen on this stack since V11 ORB. Three reasons that should temper the read:

### 4a. No stop-loss is modeled
Each trade is held from 02:00 to 06:00 UTC regardless of intra-night excursion. The MaxDD figure (-188 pips, OOS) is the *cumulative equity drawdown*, not the worst single-trade adverse excursion. A losing night could swing -50, -80, even -150 pips against the position before recovering by 06:00. Without intra-night MFE/MAE per night, we cannot:
  - Decide a sane stop level.
  - Know whether the strategy survives a stop at, say, 30 pips (which would convert some current winners to losses if MAE > 30 pips before they reverted).
  - Estimate margin-of-ruin or the realistic R-multiples a broker would impose.

This is the single biggest gap. **Phase 3 must compute per-trade MAE/MFE before any tighter rule is layered on.**

### 4b. Spread is realized, but slippage is not
We cross the spread at both ends — that's the right first-order cost model. But:
  - Market orders at 02:00 UTC may slip a tick or two beyond bid/ask, especially around RBA/RBNZ events.
  - Sunday-night opens have unstable spreads (Phase 1 EDA showed the 21:00 UTC rollover bar at p95 = 17.5 pips). Some of those wash through into the 22:00 bar on volatile weekends.
  - Big-print AUS/NZ economic releases (CPI, employment) can blow out spreads to 10+ pips during the holding window. We use the avg_spread at entry/exit, not the rolling spread *during* the holding period.

### 4c. The numbers are *too* clean
70% WR with PF 3+ across a 9-year window with zero parameter tuning is very rare. Possible explanations:
  - **Real edge.** AUDNZD is a thin pair where Asian-session liquidity providers genuinely overshoot mid; the reversion is structural.
  - **Hidden look-ahead.** I checked: midpoint uses max(high)/min(low) over [22:00, 01:59], all observable by 01:59 close. dev is computed at 01:59 close and the trade enters at the same bar's bid/ask. No future bar is consulted.
  - **Survivorship in the data.** Dukascopy data has no survivorship — AUDNZD didn't get delisted. Spread data is realized historical, not stitched.
  - **Wrong pip size.** 1e-4 is correct for AUDNZD (verified against EURUSD config and the 5-decimal pipette convention).
  - **Selection bias from the coverage filter.** Drops nights with <120 bars per half (~22% of weekdays). If those drops correlate with high-volatility regimes the edge might evaporate on full coverage.

Of these, 4a and 4c-survivorship-of-coverage are the ones that could meaningfully swing the verdict. Both go on the Phase 3 punch-list.

## 5. What Phase 3 must do, in order

1. **MAE/MFE per trade.** For each of the 922 trades, compute the worst adverse excursion and best favorable excursion within the 02:00–06:00 holding window. Distribution + worst-case will tell us what stop level is feasible. *No new rule yet — just measurement.*
2. **Stop-loss sweep.** Apply a fixed-pip stop and sweep 10, 20, 30, 50, 100 pips. Report n_stopped, change in WR/AvgR/PF/MaxDD. Pick the stop level that retains the most edge with bounded per-trade loss.
3. **Coverage stress test.** Re-run on nights with <120 bar coverage relaxed, see if the metrics survive.
4. **Range filter.** Trade only when the 22:00–02:00 UTC range is between the 25th and 75th percentile of historical (Phase 1 eyeballed this; Phase 3 measures it).
5. **News blackout.** Drop nights when scheduled high-impact AU/NZ events fall in the 22:00–06:00 window. Forex-factory archive for 2018–2026 — needs a loader.
6. **Day-of-week.** Drop the worst day if statistically meaningful. Phase 1 showed weak-to-no DOW effect, so likely a no-op.

Each Phase 3 filter must improve OOS, not just IS. Drop any filter whose only contribution is overfitting the recent window.

## 6. What Phase 4 (live spike) needs from Phase 3

Only proceed if Phase 3 OOS PF > 1.3 *and* per-trade worst-case loss is bounded by a stop *and* news-day exclusion is implemented. The current numbers are PF > 3 — there is plenty of room to lose to filters and still clear the live gate. But the unfiltered baseline is not what gets paper-traded.

Live integration would slot AUDNZD as a sibling to V6_ORB:
  - Reuse the existing risk manager for sizing.
  - Reuse the IB connection / heartbeat infrastructure.
  - Trade window is 22:00–06:00 UTC (the boring hours for the ORB stack — no scheduling conflict).
  - One open position max; AUDNZD overnight sits in margin overnight regardless.
  - Reduce risk per trade to 0.25R until the strategy proves itself in paper for 2 months.

## 7. Output artifacts

- `v11/backtest/backtest_audnzd_phase2.py` — the script
- `v11/backtest/audnzd_eda_output/phase2_trades.csv` — per-trade ledger
- `v11/backtest/audnzd_eda_output/phase2_baseline.txt` — full run log
- This journal — verdict + Phase 3 punch list

## 8. Decision

- Commit Phase 2 results.
- Stop autonomous progression here. Phase 3 starts with measurement (MAE/MFE distribution), not a new rule. That is a meaningful design step deserving a fresh decision: do we do MAE/MFE first, or jump to a fixed-stop sweep? Default: MAE/MFE first, because the right stop level falls out of that distribution rather than being guessed.
