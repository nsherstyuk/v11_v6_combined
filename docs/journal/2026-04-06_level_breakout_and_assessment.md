# Session Journal — 2026-04-06 (Level Breakout Investigation + Critical Assessment)

**Time:** Level breakout investigation + honest project assessment  
**Focus:** Test level breakout strategy, critique what we've done, identify blind spots  
**Next session should:** Test HTF level detection + 1-min entry approach

---

## Level Breakout Investigation Results

### What We Tested

Swing high/low detection on 1-min bars (left_bars=20-60, right_bars=5-20) as S/R levels, with two entry modes (volume confirm, pullback/retest), plus 60-min SMA(50) direction filter and CONFIRMING volume filter.

72 level detection combos in Phase 1, then full sweep on top 5 in Phase 2.

### Result: NOT VIABLE

| Best Config | OOS Trades/yr | OOS WR% | OOS AvgR |
|---|---|---|---|
| lb=40 rb=20 exp=480, vol_confirm, RR=2.0 | 1,477 | 33.9% | +0.003 |
| lb=60 rb=20 exp=480, retest pb=3-60, RR=2.0 | 1,125 | 34.4% | +0.003 |
| **Darvas + SMA(50) baseline** | **10.5** | **46.0%** | **+0.176** |

AvgR of +0.003 is effectively zero — any execution costs wipe it out. The retest mode didn't help. The problem: 1-min swing levels are too abundant and structurally meaningless.

### Why It Failed

Swing highs/lows on 1-min bars represent micro-fluctuations, not real supply/demand zones. With 20-60 bar lookback, hundreds of levels form daily. Even with SMA + volume filters, most breakouts are noise oscillation around trivial price points. The signal-to-noise ratio is fundamentally too low.

### File Created

`v11/backtest/investigate_level_breakout.py` — LevelDetector + BreakoutDetector classes, two-phase parameter sweep

---

## Critical Assessment: Where We Are

### What We've Accomplished

Over 3 sessions, we've systematically investigated:

1. **5 HTF filter approaches** on Darvas (SMA, session, ADX, HTF Darvas, MTF alignment)
2. **Trade frequency optimization** (loosened params, multi-instrument, additional pairs)
3. **Per-instrument grid search** on 8 FX pairs with SMA filter
4. **Level breakout/retest strategy** as alternative signal generator

### What We've Found

**One strategy works: EURUSD Darvas + 60-min SMA(50) + CONFIRMING + Trail10@60, R:R=2.0**
- OOS: 63 trades over 6 years (10.5/yr), 46% WR, +0.176 AvgR
- IS: 24 trades over 2 years (12/yr), 62.5% WR, +0.729 AvgR
- With loosened params (brk=3): 88 OOS trades (14.7/yr), +0.175 AvgR

**Everything else is either marginal or dead:**
- XAUUSD, USDJPY: marginal with optimized params, IS/OOS divergence raises doubts
- GBPUSD, AUDUSD: barely positive, thin edges
- USDCAD, USDCHF: zero positive configs
- Level breakout on 1-min: effectively zero edge
- 5-min Darvas + SMA: thin edge (+0.005-0.02 AvgR)

### Honest Criticisms

**1. Single-instrument dependency.** The entire edge is on EURUSD with specific params. That's fragile. If EURUSD's microstructure changes (new ECB policy, algorithmic competition, liquidity shifts), the edge could disappear. A robust strategy should work across multiple instruments.

**2. Low trade count weakens statistical confidence.** 63 OOS trades over 6 years is not a large sample. The 46% WR could easily be 40% or 52% due to sampling noise. We need 200+ trades to have real confidence in the win rate.

**3. Trailing stop parameters were never independently validated OOS.** Trail10@60 was optimized on IS data in the original backtest session. We've been carrying it forward as "proven" but it was part of the IS-optimized stack. It should be tested independently.

**4. Volume CONFIRMING filter may be proxying for something else.** The CONFIRMING filter was discovered on Darvas signals. It may work because of how Darvas boxes form (breakout of consolidation naturally has volume characteristics), not because volume confirmation is a universal signal.

**5. We haven't tested execution reality.** All results assume entry at bar close, which is unrealistic. Real execution has slippage, partial fills, spread widening during breakouts. The +0.176 AvgR with a 0.1 pip EURUSD spread assumption could degrade significantly.

**6. The SMA filter is doing most of the heavy lifting.** Without SMA, OOS AvgR is -0.044. With SMA, it's +0.176. The "Darvas + volume" part contributes some selectivity but the SMA direction alignment is the primary edge. This raises the question: would a simpler signal (just "buy when above SMA, sell when below") with good risk management work similarly?

**7. Session-level reset may discard important information.** We reset all detectors between sessions (30-min gap). But the most important S/R levels in FX persist across sessions — yesterday's high, this week's open, etc. Our approach can't see these.

---

## What We Might Be Missing

### A. HTF Level Detection + 1-min Entry (User's Suggestion — PROMISING)

**The idea:** Detect swing highs/lows on 1-hour or 4-hour bars (where they represent real institutional levels that market remembers), then trade the 1-min breakout of those levels.

**Why this is different from what we tested:**
- Our level breakout used 1-min swings → too many weak levels
- HTF swings (1H/4H) represent hours/days of price memory → genuinely significant
- Entry on 1-min gives precise timing (tight SL = better R:R)
- SL at the HTF level is structurally meaningful

**Why this is different from our failed MTF alignment:**
- MTF alignment required two ACTIVE Darvas boxes simultaneously → too restrictive
- This only requires HTF levels to EXIST (they persist) and 1-min price to CROSS them → much more frequent

**This could combine the best of both worlds:**
- Signal significance from HTF (like daily chart trading)
- Entry precision from LTF (like scalping)
- SMA direction filter (proven)
- Volume confirmation (potentially valuable at level breaks)

**Implementation would be:**
1. Resample 1-min to 1H or 4H bars
2. Detect swing highs/lows on HTF bars (left=10-20, right=5-10)
3. These levels PERSIST across sessions (unlike Darvas boxes)
4. On 1-min: when close crosses an HTF level → check SMA + volume → enter
5. SL: at the HTF level ± buffer
6. TP: risk × R:R

### B. The "Simple Trend Following" Question

If the SMA is doing most of the work, what about a simpler approach?
- Enter LONG when 1-min close crosses above 60-min SMA(50), SHORT when below
- Or: enter LONG on pullback to SMA during uptrend
- This would have massive trade count but thin edge (similar to level breakout)
- Worth testing to understand if the Darvas "consolidation breakout" part actually adds value

### C. Daily/Weekly Key Levels

Instead of detecting levels algorithmically, use fixed key levels:
- Previous day high/low
- Previous week high/low  
- Round numbers (1.0800, 1.0900, etc.)
- These are universally watched by traders and create genuine order flow

### D. Regime Filtering

The strategy might work in trending months and fail in ranging months. Explicitly modeling regime (via monthly ADX, or monthly ATR trend) could help avoid the worst drawdown periods.

### E. The LLM Question

We've been trying to build a fully mechanical edge. But V11 was designed as a **hybrid** system — mechanical signals + LLM judgment. Maybe the mechanical edge is intentionally thin, and Grok's contextual analysis (higher timeframe, calendar, pattern quality) is meant to supply the missing alpha. We haven't tested Stage 2 yet.

---

## My Recommendation: Priority Order

1. **Test HTF levels + 1-min entry** — the user's suggestion. Detect levels on 1H/4H, trade breaks on 1-min. This has the best theoretical foundation and we haven't tested it.

2. **Proceed to Stage 2 (Grok LLM)** — test whether Grok can improve the Darvas signals we already have. Even small WR improvement (46% → 52%) significantly impacts profitability.

3. **Daily key levels** — previous day high/low are universally significant. Quick to test.

4. **Regime filter** — reduce drawdowns by avoiding ranging markets. Could improve risk-adjusted returns even if not more trades.

---

## Files Created

| File | Purpose |
|---|---|
| `v11/backtest/investigate_level_breakout.py` | Level breakout/retest investigation (1-min levels — not viable) |
| `v11/backtest/investigate_extra_pairs_grid.py` | Per-instrument SMA grid on GBPUSD/AUDUSD/USDCAD/USDCHF |

## Files Modified

| File | Action |
|---|---|
| `docs/PROJECT_STATUS.md` | Updated build status, journal reference |
| `docs/journal/2026-04-06_frequency_investigation.md` | Updated with Parts E-F results |
| `docs/journal/2026-04-06_level_breakout_and_assessment.md` | Created (this file) |
