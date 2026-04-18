# 2026-04-17 Session: Trend Following V2 — Reality Check

**Date**: 2026-04-17
**Session scope**: Next-day open entry test, longs-only mode, walk-forward validation, regime filter, buy-and-hold comparison

---

## Executive Summary

The V1 handoff document estimated that realistic adjustments (next-day open entry, removing shorts, stop slippage) would reduce trend following P&L from +$4,531 to +$1,800-2,500. **This was wrong.** The two biggest feared obstacles turned out differently:

1. **Next-day open entry costs ~1%, not 30-50%** — XAUUSD doesn't gap meaningfully on breakout days
2. **Longs-only IMPROVES results** — but this is largely because gold tripled in the sample period

The realistic P&L is actually **+$4,063, Sharpe 8.83** — better than the raw backtest. However, this is misleading because **52% of the edge is just "being long gold during a 322% rally."** The true trading edge over buy-and-hold is drawdown control, not alpha.

---

## Test Results

### Test 1: Next-Day Open Entry — THE #1 FEARED OBSTACLE WAS WRONG

| Config | Signal-Bar Close | Next-Day Open | P&L Change |
|---|---|---|---|
| 20d channel | +$2,573 | +$2,591 | -0.7% |
| 50d channel | +$3,175 | +$3,212 | -1.2% |
| 50d + ADX>=30 | +$2,496 | +$2,488 | +0.3% |
| 20d + ADX>=20 | +$3,363 | +$3,412 | -1.5% |

**Why the original estimate was wrong**: The handoff assumed XAUUSD gaps $5-20 on breakout days. The data shows the mean gap is **$0.25** (+0.01% of price, +0.01x ATR). The gap is essentially random — 48% favorable, 52% adverse. XAUUSD trades 23 hours/day with very little overnight gap.

### Test 2: Longs-Only — Massive Improvement (But See Caveat)

| Config | Both Directions | Longs Only | Change |
|---|---|---|---|
| 20d channel | +$2,591, Sharpe 2.28 | +$5,389, Sharpe 5.61 | 2x P&L |
| 50d channel | +$3,212, Sharpe 5.43 | +$4,063, Sharpe 8.83 | +26% P&L |
| 50d + ADX>=30 | +$2,488, Sharpe 6.27 | +$2,727, Sharpe 8.07 | +10% P&L |
| 20d + ADX>=20 | +$3,412, Sharpe 3.40 | +$5,173, Sharpe 6.20 | +52% P&L |

Removing shorts is the single biggest improvement. WR jumps from ~45-56% to 57-70%.

**CRITICAL CAVEAT**: Longs-only is so good because gold went from $1,222 to $5,166 (+322%) in this sample. We have NO bear market data. In a 2011-2015 style gold crash ($1,900 -> $1,050), longs-only would bleed badly — repeatedly buying breakouts that fail, with no shorts to hedge.

### Test 3: Best Realistic Estimate

**50d channel, next-day open, longs-only**: +$4,063, Sharpe 8.83, MaxDD -$147 (1.0%)

This is 128% of the raw backtest (+$3,175 both directions, signal-bar close). Longs-only more than compensates for the tiny entry slippage.

Year-by-year (realistic):
| Year | P&L | WR | N | DD |
|---|---|---|---|---|
| 2018 | -$41 | 0% | 1 | $0 |
| 2019 | +$697 | 100% | 3 | $0 |
| 2020 | +$251 | 50% | 6 | -$147 |
| 2021 | +$106 | 100% | 2 | $0 |
| 2022 | +$126 | 50% | 2 | $0 |
| 2023 | +$407 | 67% | 3 | -$7 |
| 2024 | +$599 | 75% | 4 | -$122 |
| 2025 | +$1,434 | 80% | 5 | -$137 |
| 2026 | +$482 | 100% | 1 | $0 |

### Test 4: Walk-Forward Validation — Edge Survives OOS

| Config | IS Sharpe (2018-2022) | OOS Sharpe (2023-2026) | WFE |
|---|---|---|---|
| 20d channel | +2.65 | +9.88 | 373% |
| 50d channel | +8.14 | +10.43 | 128% |
| 50d + ADX>=30 | +6.03 | +10.08 | 167% |
| 20d + ADX>=20 | +2.99 | +10.85 | 364% |

All configs survive OOS. WFE > 100% everywhere — OOS actually performs better than IS. This is because 2023-2026 had strong gold trends.

**But**: The OOS period is also a gold bull market. The walk-forward validates that the strategy works in trending gold markets, but doesn't test it in a bear market.

### Test 6: Entry Gap Analysis

Breakout day gap statistics (174 long signals):
- Mean gap: +$0.25 (+0.01% of price)
- Median gap: $0.00
- Gap vs ATR: +0.01x (negligible)
- Favorable (gap < 0): 48% of signals
- Adverse (gap > 0): 52% of signals
- Worst gap: +$20.32 (one outlier)
- Best gap: -$7.31

The gap is essentially random noise. XAUUSD's 23-hour trading day means there's very little overnight gap to exploit or suffer from.

### Test 7: Stop-Order Entry vs Market Order

| Method | N | WR | Total | Sharpe | MaxDD |
|---|---|---|---|---|---|
| Stop-order at 50d high | 32 | 68.8% | +$4,393 | 8.02 | -$252 |
| Market order at next open | 27 | 70.4% | +$4,063 | 8.83 | -$147 |
| Signal-bar close (raw) | 27 | 70.4% | +$4,077 | 8.85 | -$147 |

Stop-order gets more fills (32 vs 27) but includes 5 whipsaw entries where price spikes through the channel level then reverses. Market order at next open is cleaner — fewer trades, higher WR, better Sharpe, lower drawdown.

### Test 8: Regime Filter (SMA200 / SMA50)

| 50d channel Config | N | WR | Total | Sharpe | MaxDD |
|---|---|---|---|---|---|
| Longs-only (no filter) | 27 | 70.4% | +$4,063 | 8.83 | -$147 |
| Longs-only + SMA200 | 26 | 73.1% | +$3,990 | 8.94 | -$186 |
| Longs-only + SMA50 | 27 | 70.4% | +$4,063 | 8.83 | -$147 |

Regime filter has minimal impact because **gold was above its 200d SMA 77% of the time** in this sample. The filter barely activates.

Days below 200d SMA by year:
| Year | Days Below 200d SMA | % |
|---|---|---|
| 2018 | 42/61 | 69% |
| 2019 | 0/312 | 0% |
| 2020 | 21/312 | 7% |
| 2021 | 225/306 | 74% |
| 2022 | 171/308 | 56% |
| 2023 | 59/308 | 19% |
| 2024 | 0/313 | 0% |
| 2025 | 0/311 | 0% |
| 2026 | 0/48 | 0% |

Even in 2021 (74% below 200d SMA), longs-only was profitable (+$106). Gold didn't crash, it consolidated. We've never seen a true bear market in this data.

### Test 9: Buy & Hold Comparison — THE KEY REALITY CHECK

| | Trend Following (50d, longs-only) | Buy & Hold Gold (2 oz) |
|---|---|---|
| Total P&L | +$4,063 | **+$7,887** |
| Max DD | -$147 (1.0%) | **-$1,619 (21.4%)** |
| Time in market | 36% (823/2279 days) | 100% |
| Gold price move | $1,222 -> $5,166 (+322%) | same |

Trend following captures only **52% of buy-and-hold return**. The "edge" is mostly just "being long gold during a 322% rally."

The real value-add is **risk control**:
- 9% of buy-and-hold's drawdown
- 36% time at risk (flat 64% of the time)
- Capital is available for other strategies when flat

But this is a **risk management edge, not an alpha edge**. If you just want gold exposure, buy and hold is simpler and more profitable.

### Test 10: Regime Filter Walk-Forward

SMA200 filter survives OOS on all configs (WFE 124-487%). But same caveat — the filter barely activates in this sample.

---

## Revised Assessment

### What Changed from V1 Handoff

| Obstacle | V1 Estimate | V2 Finding | Impact |
|---|---|---|---|
| Next-day open entry | 30-50% P&L reduction | ~1% reduction | **Was wrong** — XAUUSD gaps are negligible |
| Short side broken | Removing shorts reduces P&L | Removing shorts IMPROVES P&L by 26% | **Was wrong** — but sample-dependent |
| Position sizing illusion | 1-2 oz on $10K | Same — still valid | Unchanged |
| Stop slippage on news | $5-15/oz | Not tested directly | Still valid |
| Regime dependency | 2025 = 30% of total | 2025 = 35% of total | Still valid |
| Overfitting risk | 49 trades, tiny sample | 27 trades longs-only | Still valid (even smaller sample) |
| Correlated with ORB | Both long-biased | Same — still valid | Unchanged |

### The Two Honest Views

**Optimistic view**:
- Trend following on XAUUSD with 50d channel, next-day open, longs-only: Sharpe 8.83, MaxDD 1.0%
- Walk-forward validates: OOS Sharpe 10.43, WFE 128%
- Next-day open entry is nearly free
- Much better risk-adjusted returns than buy-and-hold
- Only 36% time in market — capital available for other strategies

**Pessimistic view**:
- 52% of the "edge" is just being long gold during a 322% rally
- We have ZERO bear market data — data starts 2018
- Longs-only would get destroyed in a 2011-2015 style gold crash
- 27 trades over 8 years is a tiny sample
- Regime filter (SMA200) is the right idea but untestable with this data
- Correlated with ORB — running both = concentration, not diversification

### Decision Framework

**Worth implementing IF**:
- You believe gold will continue to trend upward (structural thesis: inflation hedge, central bank buying, de-dollarization)
- You want gold exposure with better drawdown control than buy-and-hold
- You can tolerate 6-12 month flat periods
- You use the SMA200 regime filter as a safety net for bear markets

**NOT worth implementing IF**:
- You're looking for alpha uncorrelated with buy-and-hold
- You want diversification from ORB (both are long-biased directional on gold)
- You're concerned about the lack of bear market data
- You can't tolerate the possibility that the "edge" is mostly beta

---

## Recommended Implementation (If Proceeding)

1. **Config**: 50d channel, longs-only, next-day open entry, SMA200 regime filter
2. **Entry**: After daily close, check if close > 50d high AND close > 200d SMA. If yes, buy at next day's open.
3. **Exit**: ATR 2.5x trailing stop, or close < 20d low
4. **Position size**: 1% risk per trade (1-2 oz on $10K)
5. **Monitoring**: End-of-day only (5 min/day), much easier than ORB

---

## FILES

- `v11/backtest/backtest_trend_following_v2.py` — V2 backtest with all new tests
- `v11/backtest/backtest_trend_following.py` — original V1 backtest
- `v11/backtest/output_v2.txt` — full output from V2 run
- `docs/journal/2026-04-17_trend_following_and_session_handoff.md` — V1 handoff (superseded by this document)
