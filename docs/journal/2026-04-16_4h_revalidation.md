# 4H Level Retest Re-Validation — 2026-04-16

**Status: NO-GO — Edge has evaporated on current dataset**

**Script run:** `v11/backtest/investigate_4h_levels_deep.py`
**Dataset:** EURUSD, full history (3,069,832 bars), OOS = 2018-2023

---

## Key Numbers: Documented vs Current

| Metric | Documented (2026-04-06) | Current Run | Delta |
|---|---|---|---|
| OOS trades/yr | 22.3 | 29.7 | +7.4 |
| OOS WR% | 39.6% | 34.8% | -4.8pp |
| OOS AvgR | **+0.135** | **-0.365** | **-0.500** |
| IS AvgR | +0.230 | -0.144 | -0.374 |

The documented OOS edge does not reproduce. The swing is -0.500 AvgR on the key metric.

---

## Full OOS Results (Retest pb=10-30, the documented best config)

From current run:
- OOS N: 178 trades total, 29.7/yr
- OOS WR: 34.8%
- OOS AvgR: **-0.365**

---

## Broader Results — All Negative

Every configuration tested produced negative OOS AvgR:

| Config | OOS AvgR |
|---|---|
| Direct SL=0.3 RR=2.0 CONF (baseline) | -0.178 |
| Retest pb=10-30 | -0.365 |
| Retest pb=3-30 | -0.393 |
| Retest pb=5-60 | -0.366 |
| Direct NoAsian | -0.169 (best) |
| R:R=3.0 | -0.160 (best R:R variant) |

No filter stack, R:R, or retest window produces positive OOS AvgR.

---

## Year-by-Year OOS (direct, SMA+CONF, RR=2.0)

| Year | N | WR% | AvgR |
|---|---|---|---|
| 2018 | 109 | 31.2% | -0.089 |
| 2019 | 163 | 32.5% | -0.227 |
| 2020 | 147 | 38.8% | -0.295 |
| 2021 | 109 | 42.2% | -0.117 |
| 2022 | 147 | 44.9% | -0.122 |
| 2023 | 172 | 34.9% | -0.155 |

Zero positive OOS years.

---

## Decision

**Option C is unviable.** The plan's decision gate states:

> If OOS AvgR is negative or near zero: The edge has evaporated, same as Darvas. STOP. We're in Option D territory.

Both strategies that were supposed to survive Option C (ORB + 4H Level Retest) now have evidence problems:
- ORB: velocity fix applied, edge claim is based on V6 research that hasn't been re-validated on V11's bar data
- 4H Level Retest: OOS edge does not reproduce on current EURUSD dataset

**Escalating to user. Do not proceed to Task 4.**

---

## Root Cause Hypothesis

Same pattern as Darvas: the underlying EURUSD CSV data has been updated/extended since the original research (2026-04-06). The 2018-2023 OOS period contains the same data, but something changed. Either:
1. Data was corrected/adjusted retroactively
2. The original research had a lookahead bias that wasn't caught
3. The IS/OOS split was defined differently in the original script

This is a data integrity question that needs investigation before any algorithm re-selection.
