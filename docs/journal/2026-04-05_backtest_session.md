# Session Journal — 2026-04-05 (Evening Backtest Session)

**Time:** ~5:30 PM – 8:30 PM ET  
**Focus:** Backtest Stage 1 — Grid search, parameter optimization, and out-of-sample validation  
**Next session should:** Address OOS failure — re-optimize on earlier data, try 5-min bars, or proceed to Stage 2 (Grok LLM filter)

---

## What Happened This Session

### Phase 1: Built Backtest Stage 1 Modules (COMPLETED)

Created four modules in `v11/backtest/`:

1. **`data_loader.py`** (~155 lines) — Loads 1-min CSVs from `C:\nautilus0\data\1m_csv\` into Bar objects. Handles EURUSD's extra index column. Supports date range filtering and session splitting (30-min gap).

2. **`simulator.py`** (~197 lines) — Simulates trade lifecycle: entry at breakout price, SL at box boundary, TP at risk × R:R ratio, time stop at 120 bars (2 hours). SL checked before TP each bar (conservative). Orchestrates DarvasDetector + ImbalanceClassifier per session.

3. **`metrics.py`** (~240 lines) — Computes performance stats: win rate, PnL, avg R, profit factor, max drawdown, Sharpe ratio, Calmar ratio, exit reason breakdown, volume classification breakdown, direction breakdown. Exports to pandas DataFrame.

4. **`grid_search.py`** (~279 lines) — Builds parameter grids, runs backtests sequentially or in parallel, saves results to CSV. Two predefined grids: QUICK (144 combos) and DEFAULT (1,296 combos).

### Phase 2: Initial Grid Search (COMPLETED)

Ran QUICK grid (144 combos) on XAUUSD, EURUSD, USDJPY with 2024-2026 data at R:R=2.0 and R:R=1.5.

**Initial results (R:R=2.0):**

| Instrument | Best Sharpe | WR% | PF | Trades | Best Params |
|---|---|---|---|---|---|
| EURUSD | 0.56 | 58.3% | 2.34 | 12 | tc=20, bc=20, maxW=3.0, brk=2 |
| XAUUSD | 0.19 | 38.9% | 1.74 | 36 | tc=10, bc=20, maxW=3.0, brk=3 |
| USDJPY | -0.05 | 42.6% | 0.98 | 190 | tc=20, bc=10, maxW=3.0, brk=2 |

Key observation: EURUSD best but only 12 trades. USDJPY not viable (no positive Sharpe combos).

### Phase 3: Signal Funnel Analysis (COMPLETED)

Investigated why trade counts are so low. Traced the full pipeline:

```
EURUSD: 705,474 bars → 10,110 tops confirmed → 12 boxes formed → 12 signals
```

**Bottleneck: box formation (top → box = 0.1% conversion).** Once a box forms, 97-100% produce a signal.

**Parameter sensitivity — what moves the needle:**
- `max_box_width_atr` (3.0→5.0) = **28× more signals** (dominant factor)
- `bottom_confirm_bars` (20→5) = **58× more signals**
- `min_box_duration`, `breakout_confirm_bars`, `min_box_width_atr` = **no effect** on trade count

### Phase 4: Loosening Grid Search (COMPLETED)

Ran targeted grid varying `max_box_width_atr` [3.0, 4.0, 5.0] and `bottom_confirm_bars` [10, 12, 15, 20].

**Key discovery:** EURUSD with `maxW=4.0` unlocked 148 trades (12× more) at Sharpe=0.72. Config B (`tc=20, bc=12, maxW=3.0`) gave 86 trades at Sharpe=0.60, PF=1.80 — best balance of quality and quantity.

### Phase 5: Volume Classification Analysis (COMPLETED)

Analyzed CONFIRMING vs DIVERGENT vs INDETERMINATE trade outcomes for all EURUSD configs.

**Result: CONFIRMING volume is a real edge.**

Config B (tc=20, bc=12, maxW=3.0, R:R=1.5):
- CONFIRMING: 40 trades, **60% WR**, AvgR=+0.395, **91% of total PnL**
- DIVERGENT: 22 trades, 41% WR, AvgR=+0.043
- Gap: 19 percentage points in win rate

Config A (tc=15, bc=20, maxW=4.0) — volume filter did NOT differentiate (46% vs 47% WR). Wider boxes dilute the volume signal.

**Conclusion:** Tight boxes (maxW=3.0) preserve volume signal predictive power. Wide boxes (maxW=4.0) give more trades but weaker signal.

### Phase 6: ATR-Based vs Box-Based SL/TP (COMPLETED)

Tested all combinations of SL source (box, ATR×1.0/1.5/2.0) and TP source (R:R, ATR×1.5/2.0/3.0).

**Result: Box-based SL wins decisively.**
- Box SL (avg 43 pips) outperforms all ATR SL variants
- ATR SL at 1.0×/1.5× = death (57-59 SL hits out of 86 trades — too tight, post-breakout pullbacks clip them)
- ATR SL at 2.0× is second best but still worse than box (no structural meaning)
- ATR-based TP gives high WR (78% for atr_1.5) but tiny AvgR (+0.096) — small wins, same big losses
- **Best combo remains: SL=box, TP=R:R**

### Phase 7: SL Tightening Investigation (COMPLETED)

Tested SL management variants after N bars:
- Breakeven (move SL to entry)
- Lock (move SL to entry + X% of unrealized)
- Trail (move SL to recent swing low/high)

At tighten thresholds: 30, 45, 60, 90 bars.

**Results (EURUSD Config B, R:R=2.0):**

| Variant | WR% | AvgR | PnL |
|---|---|---|---|
| Baseline (no tighten) | 44.2% | +0.245 | +1.3154 |
| BE after 60 bars | 37.2% | +0.294 | +1.4464 |
| Lock 50% after 60 bars | **57.0%** | +0.322 | +1.6114 |
| **Trail 10-bar after 60 bars** | 51.2% | **+0.353** | **+1.8069** |

**60 bars (1 hour) is the sweet spot.** 30/45 bar variants tighten too early and catch post-breakout pullbacks. Trail10@60 gives best AvgR, Lock50@60 gives best WR.

### Phase 8: Combined Stack Analysis (COMPLETED)

Tested all combinations of volume filter × R:R × SL management.

**Best combined result (EURUSD Config B, IS 2024-2026):**

CONFIRMING + R:R=2.0 + Trail10@60:
- **40 trades, 60% WR, +0.570 AvgR, +1.2760 PnL**
- Improvement over baseline: WR +16pts, AvgR +133%, time stops eliminated
- Long WR: 69.6%, Short WR: 47.1%

Alternative "safe" choice: CONFIRMING + R:R=2.0 + Lock50@60:
- **40 trades, 67.5% WR, +0.505 AvgR**

Including INDETERMINATE trades (NO-DIVERGENT filter) diluted the edge: WR dropped from 60% → 50%, AvgR from +0.570 → +0.346.

### Phase 9: Out-of-Sample Validation (COMPLETED — ⚠️ FAILED)

Tested the best config (CONFIRMING + R:R=2.0 + Trail10@60) on 2018-2023 data (never seen during optimization).

**Year-by-year OOS results:**

| Year | Trades | WR% | AvgR | PnL |
|---|---|---|---|---|
| 2018 | 14 | 35.7% | +0.028 | -0.0423 |
| 2019 | 25 | 36.0% | +0.028 | -0.1340 |
| 2020 | 23 | 34.8% | -0.090 | +0.0399 |
| 2021 | 21 | 23.8% | -0.302 | -0.1661 |
| 2022 | 18 | 22.2% | -0.234 | +0.3452 |
| 2023 | 25 | 32.0% | -0.099 | -0.3105 |
| **ALL OOS** | **127** | **37.0%** | **-0.044** | **-0.1877** |
| ALL IS (2024-2026) | 40 | 60.0% | +0.570 | +1.2760 |

**The edge does NOT hold out-of-sample.** No OOS year exceeds 36% WR. The volume filter and tightening reduce losses (CONF+Trail is least bad OOS) but the core Darvas signal is not profitable on 2018-2023 data with these params.

### Phase 10: Look-Ahead Bias Audit (COMPLETED — CLEAN)

Audited the entire pipeline for future-leaking:
- **DarvasDetector:** processes bars one at a time, incremental ATR — CLEAN
- **ImbalanceClassifier:** rolling buffer, backward-looking — CLEAN
- **Signal generation:** fires on Nth consecutive close, only past data — CLEAN
- **Trade simulation:** starts at bars[i+1] after signal — CLEAN
- **SL/TP check:** SL before TP each bar (conservative) — CLEAN
- **Minor issue:** entry at breakout bar close instead of next bar open (mildly optimistic)
- **No structural look-ahead bias found**

---

## Key Findings Summary

### What Works (In-Sample)

1. **Volume classification is a real filter** — CONFIRMING trades consistently outperform DIVERGENT (19-31pt WR gap)
2. **SL tightening after 1 hour adds value** — Trail10@60 improves AvgR by +44%
3. **Box-based SL > ATR-based SL** — structural levels are respected by price; arbitrary ATR lines aren't
4. **R:R=2.0 > R:R=1.5** for AvgR (but R:R=1.5 gives higher WR)
5. **Exit reasons are clean** — all TP exits are wins, all SL exits are losses, time stops are breakeven noise
6. **Tight boxes (maxW=3.0) preserve signal quality** — wider boxes dilute volume edge

### What Doesn't Work

1. **In-sample params don't transfer to OOS data** — likely overfit to 2024-2026 regime
2. **USDJPY Darvas** — no positive Sharpe at any param combo
3. **XAUUSD** — marginally positive (Sharpe=0.19) but only 4/144 combos positive
4. **INDETERMINATE volume trades** — look good in isolation but dilute edge when combined with other improvements

### Honest Assessment

The Darvas + volume + tightening stack is well-engineered and the individual components (volume filter, trail stop) show genuine value. But **the core Darvas breakout signal on 1-min FX bars does not have a durable, regime-independent edge** at the params we tested. The 2024-2026 results may reflect a specific market regime (trending EURUSD, high volatility post-COVID normalization) rather than a structural market inefficiency.

---

## Files Created This Session

| File | Purpose |
|---|---|
| `v11/backtest/data_loader.py` | Load 1-min CSVs into Bar objects |
| `v11/backtest/simulator.py` | Trade simulation engine |
| `v11/backtest/metrics.py` | Performance metrics computation |
| `v11/backtest/grid_search.py` | Parameter grid search engine |
| `v11/backtest/analyze_volume.py` | Volume classification analysis (all instruments) |
| `v11/backtest/analyze_volume_eurusd.py` | Volume analysis for EURUSD high-trade configs |
| `v11/backtest/analyze_funnel.py` | Signal funnel bottleneck diagnostics |
| `v11/backtest/analyze_atr_sltp.py` | ATR vs box SL/TP comparison |
| `v11/backtest/analyze_trailing_sl.py` | SL tightening variants analysis |
| `v11/backtest/analyze_combined.py` | Combined filter + tightening analysis |
| `v11/backtest/analyze_exits_eurusd.py` | Exit reason breakdown |
| `v11/backtest/oos_validation.py` | Out-of-sample walk-forward test |
| `v11/backtest/run_loosening_grid.py` | Loosened parameter grid runner |
| `v11_grid_*.csv` (multiple) | Grid search results (project root) |

## Files Modified This Session

| File | Action |
|---|---|
| `docs/PROJECT_STATUS.md` | Updated with full backtest results and OOS findings |

---

## Wish List (Deferred Implementation)

1. **Implement SL tightening into main `simulator.py`** — Trail10@60 and Lock50@60 as options
2. **Fix entry price** — use `bars_after[0].open` instead of `signal.breakout_price` (minor optimism bias)
3. **Combine CONFIRMING filter as a gating option** in `run_backtest()`

---

## Open Questions for Next Session

1. **Re-optimize on 2018-2021, test on 2022-2026?** — Would different params work across regimes, or is 1-min Darvas fundamentally regime-dependent?
2. **Try 5-min or 15-min bars?** — 1-min may be too noisy for Darvas; longer timeframes could give cleaner consolidation zones.
3. **Proceed directly to Stage 2 (Grok LLM)?** — The LLM filter was always intended to add contextual value that pure mechanics can't. Maybe the mechanical edge is weak by design and Grok is the missing piece.
4. **Investigate what changed in 2024-2026 EURUSD** — Was there a volatility or trend regime shift that made Darvas work better? ATR profiles, trending vs ranging?
5. **Try the strategy on other instruments** — GBPUSD, AUDUSD, USDCAD data is available in `C:\nautilus0\data\1m_csv\`.

---

## What NOT to Do

- **Don't modify v8** — reference only at `C:\nautilus0\`
- **Don't trust in-sample results without OOS validation** — this session proved why
- **Don't change CENTER modules without explicit approval**
- **Don't delete analysis scripts** — they document the investigation trail
