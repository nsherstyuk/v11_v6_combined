# FX Strategy Brainstorming Retrospective

**Date**: 2026-04-17
**Purpose**: Document all strategies explored, tested, and their outcomes

---

## Background

The starting point was an underperforming **carry + momentum blend** on FX pairs. The goal: find a profitable, implementable FX trading strategy. Below is every algorithm we explored, in chronological order.

---

## 1. Carry + Momentum Blend (Baseline)

**Idea**: Go long high-yielding currencies (AUD, NZD) funded by low-yielding ones (JPY, CHF). Add momentum overlay (12-month return) to avoid catching falling knives.

**Implementation**: Daily rebalancing, 10% portfolio vol target, carry/momentum weights.

**Result**: Underperforming. The carry trade has been compressed since 2022 rate convergence. Momentum in FX is weak on daily timeframe.

**Verdict**: ❌ Not viable as standalone. Led to search for alternatives.

---

## 2. Session-Based Mean Reversion

**Idea**: FX has predictable intraday patterns driven by session opens (Asian, London, NY). Fade these patterns.

### 2a. Asian Range Fade
**Logic**: Price tends to revert to the Asian session range after London open. If London pushes outside the Asian range, fade it back in.

**Result**: Marginal edge, heavily dependent on spread costs. The fade only works in low-vol regimes, which are exactly when the profit per trade is smallest.

**Verdict**: ❌ Not viable standalone.

### 2b. London Gap Fill
**Logic**: Gap between previous NY close and London open tends to fill within the first 1-2 hours of London trading. Fade the gap.

**Result**: Tested across 7 FX pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCAD, USDCHF). Gap fill rate ~55-60% but average fill is small. After costs, edge disappears.

**Verdict**: ❌ No reliable edge. See `backtest_london_gap.py`.

### 2c. NY VWAP Drift
**Logic**: Price tends to drift toward VWAP during NY session. Fade deviations from VWAP.

**Result**: Data quality issues — tick_count as volume proxy was unreliable for FX (no real volume data). Could not validate.

**Verdict**: ⚠️ Inconclusive due to data. Unlikely to work — FX has no centralized volume.

---

## 3. Statistical Arbitrage / Pairs Trading

**Idea**: Find cointegrated FX pairs (e.g., AUDUSD/NZDUSD). Trade the spread when it deviates from equilibrium. Spread is mean-reverting by construction if cointegrated.

### 3a. Daily Pairs Trading
**Implementation**: Z-score entry at ±2.0, exit at 0.0, stop at ±4.0, 20-day lookback. Tested on AUDUSD/NZDUSD.

**Result**: Appeared profitable on daily data, but **look-ahead bias** was suspected (using same-day close for both entry and exit). Also, execution at next-day open added slippage.

**Verdict**: ⚠️ Suspicious results. Needed intraday validation.

### 3b. Intraday Pairs Validation
**Implementation**: Same logic on 1-minute data with realistic execution (next bar fill). AUDUSD/NZDUSD 2018-2026.

**Result**: Edge **collapsed** with realistic execution. The spread mean-reverts slowly (half-life ~5-10 days), but intraday noise and costs eat the profit. Z_EXIT=0.5 helped slightly but not enough.

**Verdict**: ❌ Intraday pairs trading on FX doesn't work. Spread moves too slowly relative to costs.

### 3c. Half-Life & Cointegration Scan
**Implementation**: Scanned all 30 FX pair combinations. Measured:
- **Half-life** of mean-reversion (how fast spread returns to mean)
- **ADF test** (cointegration significance)
- **Hurst exponent** (<0.5 = mean-reverting)
- Backtested top pairs with lookback matched to half-life

**Result**: No pair showed reliable cointegration. AUDUSD/NZDUSD had the best stats (Hurst ~0.45, half-life ~8 days) but:
- ADF p-value often > 0.05 (not statistically cointegrated)
- Cointegration breaks down in out-of-sample periods
- Structural regime shifts (RBA vs RBNZ divergence) destroy the relationship

**Verdict**: ❌ FX pairs are NOT reliably cointegrated. Statistical arbitrage requires structural relationships (like equity sector pairs) that FX doesn't have.

---

## 4. London Gap Fade (Multi-Pair)

**Idea**: Proper multi-pair backtest of the London open gap fade, with gap size filters and multiple hold durations.

**Implementation**: 7 FX pairs, 1-minute data. Gap = London open price vs previous NY close. Fade direction = trade toward the gap fill. Tested hold durations of 30min, 1h, 2h, 4h. Gap size filters.

**Result**: 
- Gap fill rate: 55-60% across pairs
- Average fill: 2-5 pips
- Cost per round trip: 0.4 pips
- After costs: **break-even at best**
- Gap size filter didn't help — larger gaps fill less often, smaller gaps have less profit

**Verdict**: ❌ No edge after costs. See `backtest_london_gap.py`.

---

## 5. FX Options (Explained, Not Implemented)

**Idea**: Instead of directional FX trading, sell volatility premium via options:
- **Straddles/strangles**: Sell both call and put, profit if price stays in range
- **Volatility Risk Premium (VRP)**: Implied vol typically exceeds realized vol → selling options is +EV
- **Delta hedging**: Dynamically hedge to isolate vol premium

**Pros**:
- Structural edge (VRP is well-documented)
- Benefits from range-bound markets (which FX often is)
- Defined risk with proper structure

**Cons**:
- Requires IBKR options permissions (not available on CASH pairs)
- Complex execution (delta hedging, rolling, assignment risk)
- Tail risk: black swan events can wipe months of premium
- Need OTC FX options or futures options (not available via IBKR CASH)

**Verdict**: ⚠️ Theoretically attractive but **not implementable** with current IBKR setup. Would need futures account + options permissions.

---

## 6. Opening Range Breakout (ORB) — Current Focus

**Idea**: The Asian session (00:00-06:00 UTC) creates a range. When London opens and price breaks out of this range with momentum, follow the breakout. Classic ORB strategy adapted for XAUUSD.

**Implementation**: V6 ORBStrategy with:
- Asian range: 00:00-06:00 UTC
- Trade window: 08:00-16:00 UTC
- OCA bracket orders (buy stop above range high, sell stop below range low)
- RR ratio: 2.5:1
- Gap filter: skip days with low pre-market activity
- Skip Wednesday (low-quality day)
- LLM gate for signal quality filtering

### Backtest Results (2018-2026, XAUUSD)

| Metric | Value |
|---|---|
| Trades | 366 (~46/year) |
| Win rate | 51.6% |
| Total P&L | +$943 (no slip) / +$724 ($0.20 slip) |
| Sharpe | +1.15 (raw) / +0.88 (with slip) |
| Max DD | −$132 |

**Strengths**:
- Positive edge across most years
- Gap filter significantly improves quality (Sharpe +49%)
- Survives realistic slippage up to $0.50/leg
- Long and short both profitable

**Weaknesses**:
- 2025 carries the total (exceptional year)
- Without 2025: only ~$55/year on 1 lot
- Most profit from EOD exits, not TP hits
- Edge is thin at realistic costs

**Verdict**: ✅ **The only strategy with a validated edge.** Worth running live with LLM gating. See full results in `2026-04-17_orb_backtest_and_bug_fix.md`.

### LLM Gating (from previous session, Jan-Apr 2026 only)

| Config | Trades | P&L | Sharpe |
|---|---|---|---|
| Passthrough | 53 | +$117 | 1.14 |
| LLM only | 47 | +$26 | 0.40 |
| LLM + history + regime feedback | 35 | +$78 | **1.77** |

Regime-filtered LLM feedback **doubled Sharpe** on the 3-month sample. Needs validation on full 8-year dataset.

---

## Summary Table

| # | Strategy | Edge? | Implementable? | Status |
|---|---|---|---|---|
| 1 | Carry + Momentum | Marginal | ✅ | Underperforming |
| 2a | Asian Range Fade | Marginal | ✅ | ❌ Costs eat edge |
| 2b | London Gap Fill | Weak | ✅ | ❌ No edge after costs |
| 2c | NY VWAP Drift | Unknown | ⚠️ | ❌ No FX volume data |
| 3a | Daily Pairs Trading | Illusory | ✅ | ❌ Look-ahead bias |
| 3b | Intraday Pairs | None | ✅ | ❌ Edge collapses |
| 3c | Cointegration Scan | None | ✅ | ❌ FX not cointegrated |
| 4 | Multi-Pair Gap Fade | None | ✅ | ❌ Break-even after costs |
| 5 | FX Options (VRP) | Theoretical | ❌ | ⚠️ No options access |
| **6** | **ORB** | **Real** | **✅** | **✅ Live, validated** |

---

## Key Lessons

1. **FX is hard** — most "patterns" are either noise or too small to cover costs
2. **Mean reversion on FX doesn't work** — spreads aren't stationary, cointegration breaks
3. **Session patterns are real but tiny** — gap fills happen but profits are < costs
4. **Breakout strategies have the best profile** — asymmetric payoffs (small SL, large TP) survive costs better than mean-reversion (many small wins, rare large losses)
5. **Filters are everything** — ORB without gap filter is mediocre; with it, it's viable
6. **LLM gating adds real value** — regime-filtered feedback doubles Sharpe
7. **Execution reliability matters** — the silent order failure bug could have cost many winning days

---

## Files

- `v11/backtest/backtest_carry_momentum.py` — Carry + momentum backtest
- `v11/backtest/backtest_pair_statarb.py` — Daily pairs trading
- `v11/backtest/backtest_pairs_intraday.py` — Intraday pairs validation
- `v11/backtest/backtest_pairs_halflife.py` — Cointegration scan + backtest
- `v11/backtest/backtest_london_gap.py` — Multi-pair London gap fade
- `v11/backtest/backtest_orb_xauusd.py` — Full ORB backtest (6 test suites)
- `v11/backtest/backtest_orb_optimize.py` — ORB optimization sweep (pending run)
