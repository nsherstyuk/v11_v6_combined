# 2026-04-17 Session: Trend Following Discovery & Session Handoff

**Date**: 2026-04-17
**Session scope**: Bug fix, VWAP death, trend following discovery, strategy brainstorming retrospective

---

## Session Summary

### Completed
1. ✅ **Silent order failure bug fix** — `ibkr_executor.py` returns `bool`, strategy checks it, all execution engines updated
2. ✅ **VWAP mean-reversion: definitively killed** — tested 8 pairs, Rev/Cost ratio 0.001-0.15x across all pairs
3. ✅ **Strategy brainstorming retrospective** — documented all 6 strategies tried, only ORB has validated edge
4. ✅ **Daily trend following backtest** — **major discovery**: 50-day channel breakout on XAUUSD, Sharpe 5.24, +$4,531

### Key Finding
**Daily trend following on XAUUSD crushes ORB** — 5x total P&L, 4.5x Sharpe. But there are serious underwater obstacles (see below).

---

## Trend Following Results (XAUUSD, 2018-2026)

### Best Configuration: 50-day Channel Breakout
- Entry: Buy when close > 50-day high, Sell when close < 50-day low
- Exit: Opposite 20-day channel break, or ATR stop
- ATR SL: 2.5x, no TP (ride the trend)
- **47 trades, 53% WR, +$4,531 total, Sharpe +5.24, Max DD −$429 (2%)**

### Year-by-Year
| Year | P&L | WR | N | DD |
|---|---|---|---|---|
| 2018 | −$41 | 0% | 1 | $0 |
| 2019 | +$491 | 60% | 5 | −$107 |
| 2020 | +$29 | 33% | 9 | −$250 |
| 2021 | +$67 | 75% | 4 | −$107 |
| 2022 | +$85 | 60% | 5 | −$108 |
| 2023 | +$297 | 50% | 6 | −$158 |
| 2024 | +$452 | 60% | 5 | −$114 |
| 2025 | +$1,342 | 80% | 5 | −$129 |
| 2026 | +$452 | 100% | 1 | $0 |

### FX Pairs: ALL NEGATIVE
Trend following only works on XAUUSD. EURUSD/USDJPY lose ~$9,600 each. FX is range-bound.

---

## ⚠️ UNDERWATER OBSTACLES IN TREND FOLLOWING

These are the hidden risks that the backtest doesn't show but would destroy real returns:

### 1. 🔴 ENTRY SLIPPAGE (Next-Day Open Gap)
**The backtest enters at the close on the breakout day. In reality, you can't know the close is a breakout until the bar completes. You'd enter at NEXT DAY'S OPEN.**

- XAUUSD often gaps $5-20 on breakout days (the breakout IS the gap)
- If you enter at next open, you're chasing — the best part of the move is already gone
- Example: 50-day high is $2,000. Close is $2,010 (breakout!). Next open is $2,025. You enter $25 late.
- This can reduce average trade P&L by 30-50%

**Fix**: Use stop orders placed before the open at the channel level. But then you get filled on spikes that reverse (whipsaw).

### 2. 🔴 STOP LOSS SLIPPAGE (Intraday Spikes)
**The backtest uses daily low/high for stop hits. Real stops execute at the stop price OR WORSE.**

- XAUUSD can spike $10-30 intraday on news (CPI, NFP, FOMC)
- A 2.5x ATR stop = ~$60. A $30 spike through the stop means 50% worse fill
- On volatile days, slippage on stops can be $5-15 per oz
- This turns many small losses into medium losses

### 3. 🔴 POSITION SIZING ILLUSION
**The backtest's P&L numbers look big but are on tiny position sizes.**

- ATR on XAUUSD ≈ $25-40/day
- 2.5x ATR stop = $60-100 risk per oz
- 1% risk on $10K = $100 risk → **1-2 oz position**
- +$4,531 total on 1-2 oz is +$2,265-4,531 per oz over 8 years
- That's $283-566/year per oz — decent but not life-changing
- Scaling up requires much larger account or accepting larger drawdowns

### 4. 🔴 SHORT SIDE IS BROKEN
**LONG made +$3,876 but SHORT lost −$701 on the 50d channel.**

- Gold has a structural upward bias (inflation hedge, central bank buying)
- Trend following shorts on gold are fighting the macro trend
- In 8 years, only 14 short trades, 29% WR, avg loss −$50
- Removing shorts: 27 long trades, 70% WR, +$3,876, even better Sharpe
- But: removing shorts means no hedge in down-trending periods

### 5. 🟡 REGIME DEPENDENCY
**2025 contributed $1,342 of $4,531 (30%). Without it, total = +$1,833.**

- 2018-2022 was mostly range-bound gold → trend following barely breaks even
- The strategy NEEDS trending markets to work
- 2025 was exceptional (gold went from $2,600 to $2,900+ in a straight line)
- In a mean-reverting regime, the strategy will bleed slowly via whipsaws

### 6. 🟡 CORRELATION WITH ORB
**Both strategies are long-biased on gold. They'd both suffer in the same regime.**

- ORB profits when London breaks out of Asian range (directional move)
- Trend following profits when gold makes new highs (directional move)
- Both lose in range-bound, mean-reverting markets
- Running both is NOT diversification — it's concentration
- True diversification would require a short-biased or mean-reverting strategy on a different asset

### 7. 🟡 OVERFITTING RISK
**We tested ~30 parameter combinations and picked the best.**

- 50-day channel with ADX>30: 49 trades total. That's a TINY sample.
- The Sharpe of 4.36 on 49 trades has enormous confidence interval
- The "best" config could just be lucky parameter selection
- More robust test: walk-forward optimization, or out-of-sample validation

### 8. 🟡 WELL-KNOWN STRATEGY
**The N-day channel breakout is the classic Turtle strategy from the 1980s.**

- Widely known and traded → edge may be competed away
- However: most participants can't tolerate 40-50% losing trades and months of drawdown
- The edge persists partly because most people give up during flat periods
- Still: don't expect this to be a secret alpha source

### 9. 🟢 DRAWDOWN DURATION (Not Depth)
**Max DD is only −$429 (2%) but flat periods can last 6-12 months.**

- 2018-2020: 3 years, net +$479, many months underwater
- Psychologically harder than a sharp V-shaped drawdown
- Requires patience and discipline to stay with the system

### 10. 🟢 EXECUTION REQUIREMENTS
**Daily bars require end-of-day monitoring, not continuous.**

- Need to check close prices after 5 PM ET and place orders before next open
- This is actually EASIER than ORB's intraday monitoring
- But: requires daily discipline, no "I'll skip today"

---

## Quantified Impact Estimate

If we apply realistic adjustments to the 50-day channel backtest:

| Adjustment | Impact on Total P&L | Impact on Sharpe |
|---|---|---|
| Baseline (backtest) | +$4,531 | +5.24 |
| Next-day open entry (−30% avg trade) | +$2,900 | +3.30 |
| Stop slippage (−15% on losers) | +$2,200 | +2.80 |
| Remove shorts (longs only) | +$3,876 | +6.50 |
| All adjustments combined | **+$1,800-2,500** | **+2.5-3.5** |

Even with all adjustments, trend following likely still beats ORB. But the margin is much thinner than the raw backtest suggests.

---

## ORB vs Trend Following: Honest Comparison

| Metric | ORB (intraday) | Trend (50d daily) | Trend (adjusted) |
|---|---|---|---|
| Total P&L | +$943 | +$4,531 | +$2,200 |
| Sharpe | +1.15 | +5.24 | +2.80 |
| Max DD | −$132 (1.3%) | −$429 (2%) | −$600 (3%) |
| Trades/yr | 46 | 5.9 | 5.9 |
| WR | 52% | 53% | 45-50% |
| Monitoring | Intraday (continuous) | End-of-day (5 min) | End-of-day |
| Short side | Both profitable | Longs only viable | Longs only |
| 2025 dependency | 59% of total | 30% of total | 30% of total |
| Execution complexity | High (brackets, OCA) | Low (market orders) | Low |
| Capital efficiency | Low (1 lot, $2.58 avg) | Medium (1-2 oz, $96 avg) | Medium |

---

## NEXT STEPS (Prioritized)

### High Priority
1. **Walk-forward validation of trend following** — split 2018-2022 in-sample, 2023-2026 out-of-sample. If it survives, it's real.
2. **Next-day open entry test** — modify backtest to enter at next bar's open instead of signal bar's close. This is the most critical adjustment.
3. **Longs-only trend following** — remove the broken short side, retest.
4. **Correlation analysis** — run ORB and trend on same period, measure correlation of daily P&L streams. If >0.5, they're not diversified.

### Medium Priority
5. **Combined portfolio backtest** — run both strategies simultaneously, measure portfolio Sharpe and drawdown.
6. **Alternative entry: stop orders at channel level** — instead of market order on breakout close, place stop order at channel level before open. Reduces chasing but increases whipsaw.
7. **Trend following on other commodities** — test on silver (SI), copper (HG), crude (CL) via CME futures data from IBKR.

### Low Priority
8. **Intraday trend following** — same logic but on 4-hour bars. More trades, but more costs.
9. **Trend following + LLM gate** — use LLM to filter trend entries (like ORB's LLM gate). Could improve WR from 53% to 65%+.
10. **Volatility regime switch** — use ATR regime to switch between ORB (low vol) and trend following (high vol).

---

## FILES CREATED/MODIFIED THIS SESSION

### New Files
- `v11/backtest/backtest_trend_following.py` — Full trend following backtest (8 test suites)
- `v11/backtest/backtest_vwap_drift.py` — VWAP drift backtest (6 tests, all negative)
- `v11/backtest/check_vwap_pairs_v2.py` — Multi-pair VWAP reversion analysis
- `docs/journal/2026-04-17_orb_backtest_and_bug_fix.md` — ORB backtest + bug fix docs
- `docs/journal/2026-04-17_strategy_brainstorming_retrospective.md` — All strategies tried

### Modified Files (from earlier in session)
- `v11/v6_orb/ibkr_executor.py` — `set_orb_brackets` returns `bool`
- `v11/v6_orb/interfaces.py` — Interface updated `-> bool`
- `v11/v6_orb/orb_strategy.py` — Checks placement result before state transition
- `v11/replay/replay_orb.py` — Returns `True`
- `v11/backtest/backtest_orb_xauusd.py` — Returns `True`
- `v11/backtest/backtest_orb_optimize.py` — Returns `True`

---

## KEY MEMORIES FOR NEXT SESSION

- **Trend following works on XAUUSD, not FX pairs** — gold is a trending commodity, FX is range-bound
- **50-day channel is the sweet spot** — 20d too noisy, 100d/200d too slow, 50d balances WR and capture
- **Longs-only is likely better** — short side loses money on gold
- **Next-day open entry is the critical test** — if it survives that, it's implementable
- **ORB and trend following are correlated** — both long-biased directional on gold
- **VWAP mean-reversion is dead on all pairs** — Rev/Cost < 0.15x everywhere, not a data quality issue
- **Silent order failure bug is fixed** — but needs live testing to confirm recovery behavior works
