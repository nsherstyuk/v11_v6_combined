# Volume Imbalance & Price Reversal Research

**Date:** 2026-04-16  
**Author:** Cascade + User brainstorming session  
**Status:** Complete — no actionable standalone edge found

---

## 1. Research Question

**Can tick-level volume imbalance (buy vs sell volume) predict price reversals in FX and gold?**

Specifically:
- Does a shift in volume balance from buy-dominant to sell-dominant (or vice versa) **precede** a price reversal?
- Or does volume imbalance **follow** price moves (lagging/confirming)?
- Are extreme imbalance readings (one side dominating) a sign of **exhaustion** (contrarian signal)?
- Does **divergence** (price going one way, imbalance going the other) predict reversals?
- Do any of these effects get **stronger on higher timeframes** (5min, 15min, 60min)?

---

## 2. Data Source

### 2.1 Files

All data comes from Nautilus-formatted 1-minute CSV files at `C:\nautilus0\data\1m_csv\`:

| Pair | File | Rows | Date Range |
|---|---|---|---|
| GBPUSD | `gbpusd_1m_tick.csv` | 3,058,333 | 2018-01-01 → 2026-03-23 |
| USDJPY | `usdjpy_1m_tick.csv` | ~3M | 2018-01-01 → 2026-03 |
| USDCAD | `usdcad_1m_tick.csv` | ~3M | 2018-01-01 → 2026-03 |
| USDCHF | `usdchf_1m_tick.csv` | ~3M | 2018-01-01 → 2026-03 |
| AUDUSD | `audusd_1m_tick.csv` | ~3M | 2018-01-01 → 2026-03 |
| NZDUSD | `nzdusd_1m_tick.csv` | ~3M | 2018-01-01 → 2026-03 |
| XAUUSD | `xauusd_1m_tick.csv` | 2,876,848 | 2018-01-01 → 2026-02-25 |

EURUSD was excluded per user instruction (data integrity concerns).

### 2.2 Columns per bar

Each 1-minute bar contains:

| Column | Type | Description |
|---|---|---|
| `timestamp` | datetime | Bar start time (UTC) |
| `open` | float | First tick price in the minute |
| `high` | float | Highest tick price |
| `low` | float | Lowest tick price |
| `close` | float | Last tick price |
| `tick_count` | int | Number of ticks in the minute |
| `avg_spread` | float | Average bid-ask spread across ticks |
| `max_spread` | float | Maximum bid-ask spread across ticks |
| `vol_imbalance` | float | **sell_volume − buy_volume** (positive = net selling pressure) |
| `buy_volume` | float | Volume classified as aggressive buying (up-ticks) |
| `sell_volume` | float | Volume classified as aggressive selling (down-ticks) |
| `total_volume` | float | buy_volume + sell_volume |
| `buy_ratio` | float | **buy_volume / total_volume** (0.5 = balanced, >0.5 = buy-dominant) |

### 2.3 How volume is classified (tick rule)

FX markets do not provide trade direction flags. The **tick rule** is used to classify each tick:

- **Tick price goes UP** → classified as aggressive **buy** (someone hit the ask)
- **Tick price goes DOWN** → classified as aggressive **sell** (someone hit the bid)
- **Tick unchanged** → not classified (or split)

This is the standard method for estimating order flow direction in OTC markets. It's an approximation — some up-ticks are actually limit orders being lifted, not true aggressive buys — but it's the best available without exchange-level trade flags.

### 2.4 Sign convention

**Important:** `vol_imbalance` uses a counter-intuitive sign convention:
- **Positive** vol_imbalance = sell_volume > buy_volume = **selling pressure**
- **Negative** vol_imbalance = buy_volume > sell_volume = **buying pressure**

`buy_ratio` is more intuitive:
- **> 0.5** = buy-dominant (more buying pressure)
- **< 0.5** = sell-dominant (more selling pressure)
- **= 0.5** = balanced

---

## 3. Methodology

### 3.1 Derived features

From the raw columns, the following features were computed:

| Feature | Formula | Purpose |
|---|---|---|
| `price_chg_pips` | `(close − prev_close) × pip_multiplier` | Price change in pips/points |
| `direction` | `sign(price_chg_pips)` | +1 up, −1 down, 0 flat |
| `reversal` | direction ≠ prev_direction AND prev_direction ≠ 0 | Bar where price changed direction |
| `reversal_up` | reversal AND direction = +1 | Was falling, now rising |
| `reversal_down` | reversal AND direction = −1 | Was rising, now falling |
| `buy_ratio_chg` | `buy_ratio − prev_buy_ratio` | Change in balance this bar |
| `balance_flip` | buy_ratio crossed 0.5 | Shift from buy-dominant to sell-dominant or vice versa |
| `delta` | Cumulative sum of vol_imbalance, reset daily | Session-level order flow (cumulative delta) |

Pip multipliers: GBPUSD/USDCAD/USDCHF/AUDUSD/NZDUSD = 10,000; USDJPY = 100; XAUUSD = 100 (points).

### 3.2 Divergence calculation

Divergence detects when price and cumulative delta (order flow) are moving in opposite directions:

- **Bullish divergence:** Price SMA(10) is declining (price falling) BUT delta SMA(10) is also declining (negative delta = net buying pressure accumulating). *Interpretation: price is dropping but buyers are quietly accumulating underneath — potential bottom.*
- **Bearish divergence:** Price SMA(10) is rising (price rising) BUT delta SMA(10) is also rising (positive delta = net selling pressure accumulating). *Interpretation: price is rising but sellers are quietly distributing into the rally — potential top.*

Note on sign: because `vol_imbalance` is positive when selling dominates, a rising delta means cumulative selling, and a falling delta means cumulative buying.

### 3.3 Higher timeframe aggregation

1-minute bars were aggregated into 5min, 15min, and 60min bars using pandas `resample()`:

- **OHLC:** standard aggregation (first open, max high, min low, last close)
- **Volumes:** sum of buy_volume, sell_volume, total_volume, tick_count
- **buy_ratio:** recalculated as `sum(buy_volume) / sum(total_volume)` from the aggregated volumes
- **vol_imbalance:** recalculated as `sum(sell_volume) − sum(buy_volume)` from the aggregated volumes

This preserves the correct volume ratios — simply averaging buy_ratio across bars would be mathematically wrong because it would give equal weight to low-volume and high-volume bars.

### 3.4 Look-ahead analysis

For each signal (e.g., "buy_ratio just went above 0.80"), the analysis looks **forward** N bars and measures:
- **Average future return** (cumulative price change in pips over N bars)
- **Win rate** (percentage of times the return was in the expected direction)
- **Sample size** (number of occurrences)

Look-ahead windows tested: 1, 2, 3, 5, 10, 20 bars (at each timeframe).

This is a **point-in-time** analysis — at each bar we ask "if we entered here based on this signal, what would happen?" No overlapping positions, no compounding.

### 3.5 Quintile analysis

Bars are ranked by `vol_imbalance` and split into 5 equal groups (quintiles):
- **Q1** = most buying pressure (most negative vol_imbalance)
- **Q5** = most selling pressure (most positive vol_imbalance)

The average future return for each quintile is computed. If volume imbalance has directional predictive power, Q1 should predict up-moves and Q5 should predict down-moves. The **spread** (Q1 return − Q5 return) measures the total separation.

### 3.6 Statistical thresholds

Results with sample size < 100 are marked as `n/a` and excluded. This prevents drawing conclusions from tiny samples that could be noise.

---

## 4. Results

### 4.1 Q1: Does buy_ratio change PRECEDE price reversal?

**Method:** At each bar, check if buy_ratio went up or down. Then look ahead N bars and measure the reversal rate. Compare to the baseline reversal rate (~50.9%).

**GBPUSD results (representative, all pairs similar):**

| Look-ahead | Baseline rev rate | Rev after buy_ratio UP | Rev after buy_ratio DOWN | Rev-UP after br UP | Rev-DOWN after br DOWN |
|---|---|---|---|---|---|
| 1 bar | 50.92% | 50.91% | 50.93% | 21.26% | 21.27% |
| 3 bars | 50.92% | 50.89% | 50.92% | 23.92% | 23.93% |
| 5 bars | 50.92% | 50.89% | 50.92% | 23.92% | 23.93% |
| 10 bars | 50.92% | 50.87% | 50.94% | 23.94% | 23.96% |

**Conclusion:** No predictive power. Reversal rates after buy_ratio changes are indistinguishable from baseline. A shift in volume balance does NOT predict that price will reverse.

### 4.2 Q2: Does price reversal PRECEDE buy_ratio change?

**Method:** At each reversal bar, look ahead N bars and measure the average change in buy_ratio. Compare to non-reversal bars.

**GBPUSD results:**

| Look-ahead | After rev-UP, br chg | After rev-DOWN, br chg |
|---|---|---|
| 1 bar | **−0.00476** | **+0.00489** |
| 2 bars | −0.00147 | +0.00142 |
| 3 bars | −0.00048 | +0.00064 |
| 5 bars | −0.00019 | +0.00009 |
| 10 bars | +0.00007 | −0.00002 |

**Conclusion:** Volume imbalance **follows** price moves (lagging). After price reverses UP, buy_ratio actually *decreases* (more selling appears). After price reverses DOWN, buy_ratio *increases* (more buying appears). This is **liquidity provision** / mean-reversion flow — market makers and contrarians step in to provide the other side after a move. The effect decays within 3–5 bars and is essentially zero by 10 bars.

This is the key finding about **causality direction**: volume imbalance is a consequence of price moves, not a cause.

### 4.3 Q3: Balance flip (buy_ratio crosses 0.5)

**Method:** Identify bars where buy_ratio crossed the 0.5 threshold (flipped from buy-dominant to sell-dominant or vice versa). Measure future returns.

**GBPUSD results:**

| Look-ahead | Flip→buy dominant ret | WR | Flip→sell dominant ret | WR |
|---|---|---|---|---|
| 1 bar | −0.014 pips | 47.7% | +0.005 pips | 48.7% |
| 5 bars | −0.024 pips | 49.6% | +0.012 pips | 50.3% |
| 20 bars | −0.032 pips | 49.8% | +0.016 pips | 50.1% |

**Conclusion:** No edge. 31% of bars are flips — far too frequent to carry information. The direction is actually *backwards* (flip to buy-dominant → slightly negative returns), likely because the flip is a lagging indicator that captures the tail end of a move.

### 4.4 Q5: Extreme buy_ratio as exhaustion signal

**Method:** Identify bars where buy_ratio exceeds extreme thresholds (>0.70, >0.80 for buy exhaustion; <0.30, <0.20 for sell exhaustion). Measure future returns.

**All pairs, 1-min, 5-bar ahead:**

| Pair | BUY ret (>0.80) | BUY WR | SELL ret (<0.20) | SELL WR | N_buy | N_sell |
|---|---|---|---|---|---|---|
| **NZDUSD** | −0.121 | 47.9% | **+0.290** | **57.0%** | 3,402 | 2,643 |
| **USDCAD** | −0.268 | 45.0% | +0.085 | **54.7%** | 2,823 | 3,235 |
| **USDCHF** | −0.241 | 43.6% | +0.088 | 50.9% | 8,011 | 11,036 |
| **AUDUSD** | −0.039 | 49.2% | +0.052 | 53.5% | 4,694 | 5,082 |
| USDJPY | −0.078 | 46.7% | +0.002 | 51.1% | 5,028 | 4,779 |
| GBPUSD | −0.074 | 48.3% | +0.096 | 52.1% | 45,563 | 44,624 |
| XAUUSD | −1.561 | 49.6% | −0.588 | 49.7% | 26,143 | 32,158 |

**Conclusion:** Exhaustion pattern is **real but tiny** for FX pairs. When one side is extremely dominant (>80% of volume), the move tends to reverse:

- **Sell exhaustion** (buy_ratio < 0.20, too much selling) → price bounces UP. Strongest on NZDUSD (57% WR, +0.29 pips) and USDCAD (54.7% WR).
- **Buy exhaustion** (buy_ratio > 0.80, too much buying) → price reverses DOWN. Strongest on USDCAD (45% WR, −0.268 pips) and USDCHF (43.6% WR).

However, the effect sizes are sub-pip for FX pairs. With typical spreads of 0.8–1.5 pips, these edges are entirely consumed by transaction costs.

**XAUUSD shows NO exhaustion pattern** — both sides produce negative returns at the 0.80 threshold. Gold's volume classification behaves differently (commodity vs FX pair).

The exhaustion pattern is **asymmetric**: sell-side exhaustion is consistently stronger than buy-side exhaustion across FX pairs. This may be because panic selling creates more genuine climactic volume than FOMO buying.

### 4.5 Q6: vol_imbalance quintile analysis

**Method:** Rank bars by vol_imbalance into 5 equal groups. Measure average future return per group.

**All pairs, 1-min, 5-bar ahead:**

| Pair | Correlation | Q1 (buy pressure) | Q5 (sell pressure) | Spread (Q1−Q5) |
|---|---|---|---|---|
| **XAUUSD** | +0.0032 | −0.393 pts | **+1.484 pts** | −1.876 pts |
| GBPUSD | −0.0007 | +0.050 pips | −0.057 pips | +0.107 pips |
| NZDUSD | +0.0014 | −0.030 pips | +0.024 pips | −0.054 pips |
| USDCHF | +0.0001 | +0.010 pips | −0.020 pips | +0.030 pips |
| USDCAD | +0.0002 | −0.016 pips | +0.013 pips | −0.029 pips |
| AUDUSD | +0.0006 | +0.003 pips | −0.014 pips | +0.018 pips |
| USDJPY | −0.0007 | +0.007 pips | +0.005 pips | +0.002 pips |

**Conclusion:** For FX pairs, the quintile relationship is noise — correlations are essentially zero (−0.0007 to +0.0014), and spreads are 0.002–0.107 pips. At normal (non-extreme) imbalance levels, there is no directional information.

**XAUUSD** shows a large spread (1.88 pts) but **reversed**: the most-sell-pressure quintile predicts UP-moves (+1.48 pts). This is the same exhaustion pattern from Q5 measured differently — at the extremes, selling pressure is contrarian (climactic selling → bounce).

### 4.6 Q8: Price/Delta divergence

**Method:** Detect when price trend and cumulative delta trend diverge over 10-bar windows. Measure future returns.

**All pairs, 1-min, 30-bar ahead:**

| Pair | Bullish div ret | Bullish div WR | Bearish div ret | Bearish div WR |
|---|---|---|---|---|
| **XAUUSD** | **+4.54 pts** | **51.9%** | +6.66 pts | 49.2% |
| GBPUSD | +0.112 pips | 51.7% | −0.089 pips | 50.8% |
| USDJPY | +0.093 pips | 51.8% | +0.003 pips | 49.5% |
| USDCAD | +0.084 pips | 50.7% | −0.036 pips | 50.8% |
| USDCHF | +0.057 pips | 51.1% | −0.084 pips | 50.7% |
| AUDUSD | +0.028 pips | 51.6% | −0.046 pips | 50.2% |
| NZDUSD | +0.016 pips | 50.5% | −0.066 pips | 50.6% |

**Conclusion:** **Bullish divergence is consistent across ALL pairs** — always positive return, always >50% WR. This is the most robust finding in the entire study. When price is dropping but buying pressure is accumulating underneath (smart money absorbing selling), price tends to eventually reverse up.

However, magnitudes are sub-pip for FX (0.016–0.112 pips over 30 bars = 30 minutes). Not tradeable standalone.

**XAUUSD** bullish divergence: +4.54 pts, 51.9% WR. But bearish divergence also predicts up-moves (+6.66 pts) — both types predict upward movement. This is contaminated by the strong 2018–2026 gold uptrend, not a genuine directional signal.

---

## 5. Higher Timeframe Results

### 5.1 Aggregation method

1-minute bars were aggregated into 5min, 15min, and 60min using pandas `resample()`. Volumes were summed (not averaged), and buy_ratio/vol_imbalance were recalculated from the summed volumes to preserve correct ratios.

### 5.2 Q5: Extreme exhaustion across timeframes

**Sell exhaustion (buy_ratio < 0.20), 3-bar ahead:**

| Pair | 5min ret / WR | 15min ret / WR | 60min ret / WR |
|---|---|---|---|
| **NZDUSD** | **+0.353 / 55.4%** (N=316) | n/a | n/a |
| **USDJPY** | +0.047 / 53.2% (N=643) | −0.419 / 51.9% (N=181) | n/a |
| **AUDUSD** | +0.027 / 51.0% (N=498) | **+1.336 / 60.4%** (N=101) | n/a |
| GBPUSD | −0.103 / 50.2% (N=4,406) | −0.670 / 50.8% (N=776) | n/a |
| XAUUSD | −2.227 / 50.1% (N=3,344) | −12.668 / 48.1% (N=616) | n/a |

**Buy exhaustion (buy_ratio > 0.80), 3-bar ahead:**

| Pair | 5min ret / WR | 15min ret / WR | 60min ret / WR |
|---|---|---|---|
| **NZDUSD** | −0.077 / 51.5% (N=452) | **+1.366 / 56.5%** (N=131) | n/a |
| USDCHF | −0.461 / 43.2% (N=923) | −0.813 / 40.3% (N=191) | n/a |
| USDCAD | −0.538 / 45.2% (N=372) | n/a | n/a |
| XAUUSD | −8.976 / 49.5% (N=2,383) | +2.419 / 49.5% (N=374) | n/a |

**Key observation:** The exhaustion signal is **inconsistent across timeframes**. A pair that shows exhaustion at 5min may not show it at 15min, and vice versa. This inconsistency suggests the signal is overfitting to noise in small samples rather than reflecting a stable phenomenon. At higher timeframes, sample sizes drop dramatically (many cells have N < 200 or are n/a entirely), making results unreliable.

### 5.3 Q8: Divergence across timeframes

**Bullish divergence, 3-bar ahead:**

| Pair | 5min ret / WR | 15min ret / WR | 60min ret / WR |
|---|---|---|---|
| **XAUUSD** | **+3.43 / 51.7%** (N=141k) | **+6.31 / 51.8%** (N=47k) | −14.79 / 52.2% (N=12k) |
| USDJPY | +0.029 / 51.5% | +0.088 / 51.9% | −0.269 / 52.8% |
| GBPUSD | +0.036 / 51.1% | −0.058 / 50.6% | −0.091 / 51.1% |
| USDCAD | +0.060 / 50.5% | +0.105 / 50.6% | −0.389 / 49.6% |

**Key observation:** Bullish divergence is consistent at 5min (positive return, >50% WR for all pairs) but **deteriorates at higher timeframes**. At 60min, most FX pairs show negative returns after bullish divergence. The signal works at short horizons but does not persist — it's a microstructure effect, not a macro trend.

### 5.4 Best signals per timeframe

| Timeframe | Best sell-exhaustion | Best bull-divergence |
|---|---|---|
| **5min** | USDCAD 55.5% WR (+0.135 pips, N=665) | XAUUSD 52.0% WR (+6.1 pts, N=141k) |
| **15min** | AUDUSD 54.8% WR (+0.413 pips, N=786) | USDJPY 52.3% WR (+0.128 pips, N=47k) |
| **60min** | AUDUSD 58.4% WR (+0.799 pips, N=267) | USDJPY 52.8% WR (−0.269 pips, N=13k) |

**Note:** The 60min AUDUSD 58.4% WR is on only N=267 samples — too small to be statistically reliable.

---

## 6. Existing Indicators Based on Tick Volume Imbalance

### 6.1 Delta / Cumulative Delta

The most widely used tick-imbalance indicator. Delta = buy_volume − sell_volume per bar. Cumulative delta = running sum over a session. Displayed on **footprint charts** (Market Delta, Bookmap, Sierra Chart). Used by discretionary traders to see "who's hitting whom."

Our Q7 showed cumulative delta has **no directional predictive value** at 1-min resolution for GBPUSD (±0.01 pips).

### 6.2 Order Flow Imbalance (OFI)

Academic measure from Cont, Kukanov & Stoikov (2014). Uses **changes in best bid/ask sizes** from the L2 order book, not tick-classified volume. OFI has been shown to have stronger predictive power than tick-based measures because it captures **limit order placement** (intent) rather than **execution** (result).

Our data does not contain L2 order book information — only tick-classified volume. OFI would require a different data source.

### 6.3 Volume Delta / Bid-Ask Volume Ratio

Essentially our `buy_ratio`. Commonly used as a **confirming filter** in trading systems, not a standalone signal. Our analysis confirms why: it's confirming (Q2), not predictive (Q1).

### 6.4 Klinger Volume Oscillator

Uses "volume force" (buy/sell classification based on typical price vs prev bar) smoothed with EMAs, then compares short EMA vs long EMA for crossovers. Conceptually similar to our SMA crossover analysis (Q4), which showed no edge.

### 6.5 Delta Divergence

Price makes new high but cumulative delta doesn't (bearish), or price makes new low but cumulative delta doesn't (bullish). This is exactly our Q8 analysis, which showed the strongest (but still marginal) signal.

### 6.6 VWAP Delta

Difference between current price and volume-weighted average price. Different concept — anchored to intraday benchmark rather than order flow direction. Not directly comparable.

---

## 7. Conclusions

### 7.1 Volume imbalance is lagging, not leading

The core finding across all 7 pairs and all timeframes:

```
Price move  →  Volume imbalance shifts (lagging, confirming)
Volume imbalance shifts  →  Next price move (NO predictive power)
```

This is consistent with market microstructure theory. In liquid FX markets:
1. Aggressive orders (market buys/sells) *move* price
2. The tick rule *classifies* those orders after the fact
3. By the time you see "buy_ratio went up," the price has already moved up
4. The next move is determined by new information, not by what just happened

### 7.2 Exhaustion at extremes is real but tiny

When buy_ratio reaches extreme levels (>0.80 or <0.20), there is a slight contrarian edge — the move is exhausting. This is the **climactic volume** pattern known in classical technical analysis.

- Best on **NZDUSD** (57% WR on sell exhaustion at 1-min) and **USDCAD** (54.7% WR)
- But the edge is 0.08–0.29 pips — smaller than typical FX spreads
- The pattern is **asymmetric**: sell-side exhaustion is consistently stronger than buy-side exhaustion

### 7.3 Bullish divergence is the most robust signal

Price dropping + buying pressure accumulating → slight tendency for price to reverse up. This is consistent across all 7 pairs at 5min. But the effect is <0.15 pips for FX — not tradeable standalone.

### 7.4 Higher timeframes do NOT strengthen the signal

Aggregation from 1-min to 5/15/60-min does not improve the edge. Reasons:
- Volume imbalance is already a per-bar aggregate — smoothing just reduces observations
- The signal is genuinely weak, not buried in noise
- Higher TF reduces sample sizes dramatically, making results unreliable

### 7.5 XAUUSD is different but contaminated by trend

Gold shows the largest absolute effects (4–12 pts on divergence) but:
- Both divergence types predict upward moves → contaminated by 2018–2026 uptrend
- The quintile spread is reversed (sell pressure → price up) → exhaustion at larger scale
- No exhaustion pattern at 0.80 threshold for either side

### 7.6 No standalone strategy edge exists in this data

The volume imbalance information in tick-classified 1-minute FX data does not contain a tradeable standalone edge at any timeframe from 1-min to 60-min. The effects are either:
- Too small relative to spread (FX pairs)
- Contaminated by trend (XAUUSD)
- On too-small samples to trust (higher TF extremes)
- Inconsistent across timeframes (overfitting to noise)

---

## 8. Potential Future Directions

If this line of research is to be pursued further, the more promising directions would be:

1. **Use as a filter, not a signal.** Example: add an exhaustion filter to an existing strategy — "don't take the breakout long if buy_ratio was >0.80 in the last 5 bars." This could avoid bad entries without requiring the signal to be tradeable on its own.

2. **Real L2 order book data.** The academic OFI (Order Flow Imbalance) literature uses changes in bid/ask sizes at the best bid/ask levels, which captures *intent* (limit order placement) rather than *result* (execution). This has shown stronger predictive power in equities and futures markets. Would require a different data source (e.g., futures order book from CME).

3. **Futures volume instead of FX tick volume.** FX spot volume is tick-count-based and estimated. CME currency futures have real exchange-reported volume with trade direction flags, which would be more reliable for imbalance analysis.

4. **Intraday seasonality.** Volume imbalance may behave differently at different times of day (London open vs NY afternoon vs Asian session). The current analysis treats all bars equally.

5. **Machine learning on volume features.** Rather than testing individual signals, a ML model could detect non-linear combinations of volume features that have predictive power. But given the near-zero linear correlations, non-linear effects are unlikely to be strong.

---

## 9. Scripts

The following scripts were used for this research:

| Script | Purpose |
|---|---|
| `v11/backtest/research_volume_imbalance.py` | Full Q1-Q8 analysis on GBPUSD 1-min |
| `v11/backtest/research_vi_q1q3.py` | Q1-Q3 re-run for GBPUSD (captured truncated output) |
| `v11/backtest/research_vi_all_pairs.py` | Q1-Q8 across all 7 pairs at 1-min |
| `v11/backtest/research_vi_htf.py` | Q5/Q6/Q8 across all 7 pairs at 5min/15min/60min |
