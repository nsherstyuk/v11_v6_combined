# ORB Backtest Reconciliation — One Canonical Script

**Date:** 2026-04-18
**Outcome:** `investigate_orb_xauusd.py` kept as the single canonical ORB backtest. `backtest_orb_xauusd.py` and `backtest_orb_optimize.py` deleted.

---

## Context

After the ORB hardening session (2026-04-16), a new `backtest_orb_xauusd.py` was written the next day by the Cascade agent, parallel to the existing `investigate_orb_xauusd.py`. They produced inconsistent numbers and different verdicts on regime concentration, creating uncertainty about which was the reference.

Before paper-trade fills can be meaningfully compared against "backtest expectations," the project needs one canonical backtest script.

## The two scripts

| Attribute | `investigate_orb_xauusd.py` (kept) | `backtest_orb_xauusd.py` (deleted) |
|---|---|---|
| Strategy engine | Real V6 ORBStrategy via ReplayORBAdapter | Real V6 ORBStrategy via custom BacktestMarketContext |
| Gap filter | Pre-computed rolling-percentile lookup, clean no-lookahead | On-the-fly rolling percentile, equivalent in principle |
| Unit of reporting | **R (pnl / range_size)** — normalizes across regimes | **Dollars per lot** — conflates edge with volatility |
| OOS/IS split | Explicit 2018-2023 OOS vs 2024+ IS | All years together; year-by-year only |
| Config variants | velocity ON/OFF, gap ON/OFF, Wed include, slippage sweep, direction breakdown | RR sensitivity, slippage, BE, gap toggle, exit reason |
| Stress-test variants | 5 main + slippage sweep | 6 scenarios |

## The "2025 concentration" mirage

Cascade's script reported: **"2025 carries $557 of $943 total P&L (59%). Without 2025, only $55/year."**

Running the canonical script with the IS year-by-year breakdown added today, velocity=OFF gap=ON:

| Year | N | WR% | AvgR | Regime |
|---|---|---|---|---|
| 2018 | 53 | 47.2% | +0.116 | OOS |
| 2019 | 42 | 50.0% | +0.101 | OOS |
| 2020 | 53 | 56.6% | +0.207 | OOS |
| 2021 | 54 | 35.2% | +0.212 | OOS |
| **2022** | **67** | **53.7%** | **+0.267** | OOS (best) |
| 2023 | 46 | 54.3% | +0.154 | OOS |
| 2024 | 60 | 43.3% | +0.127 | IS |
| 2025 | 53 | 58.5% | +0.122 | IS |
| 2026* | 8 | 25.0% | −0.439 | IS (4 months) |

*2026 partial year — 8 trades is below statistical threshold for anything.

**In R terms, 8 of 9 years are positive and 2025 contributes ~9.5% of total R (6.47R of ~68R).** The best year was 2022. There is no single-year concentration.

### Why dollars said 59% and R says 9.5%

Gold was ~$1,200 in 2018, ~$4,800 in 2025 — a 4× price level change with proportionally larger absolute ranges. With fixed-1-lot sizing (which both backtests use), a 2025 winning trade captures ~$20 vs ~$5 for a 2018 trade. The strategy's edge (`pnl / range_size`) is equivalent; the scaling just inflates 2025's dollar contribution.

**Cascade's 59% was a position-sizing artifact, not strategy fragility.** If paper trading used fixed % of account risk per trade (standard industry practice), dollar P&L would distribute more like the R view.

## Decision

**Canonical script: `v11/backtest/investigate_orb_xauusd.py`**

Reasons:
1. R reporting is invariant to gold's price level; dollars aren't
2. Explicit OOS/IS split baked into summary tables — critical for honest evaluation
3. Has stress-test variants (slippage, velocity, Wednesday) that the deleted script didn't
4. Pre-computed gap lookup is conceptually cleaner (zero ambiguity about lookahead)

Also added today: year-by-year breakdown now covers IS years, so the regime-concentration question is answerable from this script alone (no need for a parallel implementation to check).

**Deleted scripts:**
- `v11/backtest/backtest_orb_xauusd.py` — duplicate
- `v11/backtest/backtest_orb_optimize.py` — parameter sweeps that were never completed and duplicated infrastructure. If future parameter sweeps are needed, add them as variants inside `investigate_orb_xauusd.py` rather than a parallel file.

## Canonical numbers (2018-2023 OOS)

| Config | N | WR% | AvgR | PF | MaxDD |
|---|---|---|---|---|---|
| velocity=ON, gap=OFF | 530 | 44.3% | +0.055 | 1.13 | 13.5R |
| velocity=ON, gap=ON | 296 | 48.0% | +0.126 | 1.33 | 9.4R |
| velocity=OFF, gap=OFF | 583 | 43.9% | +0.091 | 1.20 | 13.0R |
| **velocity=OFF, gap=ON** | **315** | **49.5%** | **+0.183** | **1.48** | **6.5R** |
| velocity=ON, gap=ON, Wed=include | 380 | 46.1% | +0.083 | 1.21 | 13.7R |

**Best config:** `velocity=OFF, gap=ON, skip Wednesday`. +0.183 AvgR OOS. 8 of 9 years positive. Edge survives 0.3pt/side slippage.

These numbers are what paper trade fills should be compared against.

## Paper-trade comparison thresholds

Once 20+ paper trades are collected (~4-5 months), compare against:

- **Win rate:** expect 45-55% (point estimate 49.5%). Below 40% or above 60% → investigate.
- **AvgR:** expect +0.05 to +0.25. Below 0 for 10+ trades → something is off.
- **Trade frequency:** expect ~1/week. 0 trades for 10+ non-holiday days → strategy or filter is broken.
- **Fill slippage:** expect median <0.2pt. Consistently >0.3pt → real-world cost higher than backtest assumed.
