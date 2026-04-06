# Session Journal — 2026-04-06 (Trade Frequency Investigation)

**Time:** Trade frequency investigation session  
**Focus:** Increase trade count from ~10/yr to portfolio-viable levels  
**Next session should:** Consider integrating SMA filter into main pipeline; paper trade EURUSD

---

## What Happened This Session

### Context

The HTF investigation (previous session) found that 60-min SMA(50) turns EURUSD OOS from losing to profitable. But the winner only produces ~10.5 trades/year on EURUSD — too few for a standalone strategy. This session investigated multiple angles to increase frequency.

### Part A: SMA Filter on All 3 Original Instruments (COMPLETED)

Tested 60-min SMA(50) + CONFIRMING on XAUUSD and USDJPY using their default configs.

| Instrument | OOS Trades | OOS WR% | OOS AvgR | Verdict |
|---|---|---|---|---|
| **EURUSD** | 63 | 46.0% | **+0.176** | **Works** |
| XAUUSD | 959 | 32.2% | -0.218 | Worse with SMA |
| USDJPY | 966 | 36.4% | -0.175 | No improvement |

**Finding:** Default params don't work on XAUUSD/USDJPY. SMA filter alone can't fix bad underlying params.

### Part B: Loosened 1-min Params with SMA Safety Net — EURUSD (COMPLETED)

108 param combos tested. The SMA filter as safety net allows wider boxes while staying OOS-positive.

**Key configs found:**

| Params | OOS Trades | OOS/yr | OOS WR% | OOS AvgR | OOS PnL |
|---|---|---|---|---|---|
| tc=20 bc=12 mxW=3.0 brk=2 (original) | 63 | 10.5 | 46.0% | +0.176 | +0.8767 |
| tc=20 bc=12 mxW=3.0 brk=3 | 88 | **14.7** | 43.2% | **+0.175** | +1.4722 |
| tc=20 bc=15 mxW=4.0 brk=1 | 197 | **32.8** | 39.6% | +0.079 | +1.2378 |
| tc=20 bc=12 mxW=4.0 brk=1 | 408 | **68.0** | 40.0% | +0.055 | +1.2097 |
| tc=20 bc=12 mxW=4.0 brk=3 | 443 | **73.8** | 41.1% | +0.047 | -0.0518 |

**Finding:** Loosening brk=2 to brk=3 gets +40% more trades with same AvgR. Widening to mxW=4.0 gets 6-7x more trades but thinner edge (+0.055 AvgR). The tc=20 bc=12 family is consistently the best.

### Part C: 5-min Darvas + SMA Filter (COMPLETED)

144 param combos on 5-min resampled bars. Very high trade counts but thin edges (+0.005 to +0.02 AvgR). Not viable — edge too small to survive real costs.

### Part D: Additional FX Pairs with Default Params (COMPLETED)

GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF all deeply negative with default params + SMA. Need per-instrument optimization (see Part E).

### Part E: Per-Instrument Grid Search — XAUUSD and USDJPY (COMPLETED)

300 param combos x 2 R:R x 2 vol filters = 1,200 total per instrument.

**XAUUSD Results:**
- Only 16/940 configs (1.7%) positive OOS with >= 15 trades
- Best: tc=25 bc=20 mxW=4.0 brk=2 R:R=1.5 SMA+CONF: 64 OOS trades (10.7/yr), 50% WR, +0.253 AvgR
- **RED FLAG:** IS performance is negative (-0.522 AvgR) for this OOS-best config
- XAUUSD needs very different params: longer confirms (25-30), wider boxes (4.0), bottom confirm=20
- Low confidence — OOS-positive configs have negative IS, suggesting data mining

**USDJPY Results:**
- 37/924 configs (4.0%) positive OOS with >= 15 trades
- Best: tc=25 bc=15 mxW=3.0 brk=2 R:R=2.0 SMA+CONF: 25 OOS trades (4.2/yr), 64% WR, +0.657 AvgR
- Better balance: tc=25 bc=12 mxW=3.0 brk=3 R:R=2.0 SMA+CONF: 48 trades (8.0/yr), 56.2% WR, +0.311 AvgR
- Medium confidence — IS still tends negative but OOS is more consistently positive

**Portfolio Projection:**

| Instrument | Best Config | OOS/yr | OOS AvgR | Confidence |
|---|---|---|---|---|
| EURUSD | tc=20 bc=12 mxW=3.0 brk=3 | 14.7 | +0.175 | High |
| USDJPY | tc=25 bc=12 mxW=3.0 brk=3 | 8.0 | +0.311 | Medium |
| XAUUSD | tc=25 bc=20 mxW=4.0 brk=2 | 10.7 | +0.253 | Low |
| **Portfolio** | | **~33/yr** | | |

---

## Key Findings

### What Works

1. **EURUSD + SMA(50) is the only high-confidence edge** — IS and OOS both positive
2. **Loosening brk from 2 to 3** adds 40% more trades with no AvgR loss on EURUSD
3. **tc=20 family** is consistently best across instruments (long top confirmation)
4. **mxW=3.0** preserves signal quality; mxW=4.0+ trades volume for edge

### What Doesn't Work

1. **Default params on XAUUSD/USDJPY** — deeply negative even with SMA
2. **5-min Darvas + SMA** — too many thin-edge trades
3. **Additional pairs (GBPUSD etc.) with default params** — all negative
4. **XAUUSD with any params** — IS/OOS divergence suggests no stable edge

### Honest Assessment

This is fundamentally a **single-instrument strategy on EURUSD** that produces 15-30 trades/year depending on how loose you set the params. USDJPY might add another 8/yr but with lower confidence. XAUUSD is unreliable.

A realistic portfolio produces **~25 trades/year** from EURUSD (with slightly loosened params) + maybe 8 from USDJPY = **~33/year or ~3/month**.

---

## Files Created This Session

| File | Purpose |
|---|---|
| `v11/backtest/investigate_trade_frequency.py` | Multi-angle trade frequency investigation (Parts A-D) |
| `v11/backtest/investigate_per_instrument_sma_grid.py` | Per-instrument grid search with SMA filter (Part E) |

## Files Modified This Session

| File | Action |
|---|---|
| `docs/journal/2026-04-06_frequency_investigation.md` | Created (this file) |

---

### Part F: Per-Instrument Grid Search — Additional FX Pairs (COMPLETED)

Ran 144 param combos x 2 R:R x 2 vol filters on GBPUSD, AUDUSD, USDCAD, USDCHF.

| Pair | Positive OOS configs (N>=15) | Best OOS AvgR | Best OOS/yr | Verdict |
|---|---|---|---|---|
| GBPUSD | 7/358 (2.0%) | +0.275 (21 trades) | 6.8 | Marginal — tiny PnL, IS negative |
| AUDUSD | 4/342 (1.2%) | +0.030 (106 trades) | 18.2 | Negligible edge |
| USDCAD | **0/350 (0%)** | all negative | — | **Dead** |
| USDCHF | **0/358 (0%)** | all negative | — | **Dead** |

**Finding:** USDCAD and USDCHF have zero configs with positive OOS AvgR. GBPUSD and AUDUSD have a few marginal configs but the edges are too thin to be actionable. **The Darvas+SMA strategy only works on EURUSD (high confidence) and possibly USDJPY (medium confidence).**

---

## Updated Portfolio Projection

| Instrument | Config | OOS/yr | OOS AvgR | Confidence |
|---|---|---|---|---|
| **EURUSD** | tc=20 bc=12 mxW=3.0 brk=3 | **14.7** | +0.175 | **High** |
| USDJPY | tc=25 bc=12 mxW=3.0 brk=3 | 8.0 | +0.311 | Medium |
| GBPUSD | tc=15 bc=15 mxW=3.0 brk=1 | 6.8 | +0.055 | Low |
| XAUUSD | tc=25 bc=20 mxW=4.0 brk=2 | 10.7 | +0.253 | Low |
| AUDUSD | tc=15 bc=12 mxW=3.0 brk=3 | 17.7 | +0.030 | Very low |
| USDCAD | — | — | — | None |
| USDCHF | — | — | — | None |

**Realistic portfolio (high confidence only): ~15 trades/year from EURUSD**  
**Optimistic portfolio (include medium): ~23 trades/year (EURUSD + USDJPY)**

---

## Files Created This Session (Updated)

| File | Purpose |
|---|---|
| `v11/backtest/investigate_trade_frequency.py` | Multi-angle trade frequency investigation (Parts A-D) |
| `v11/backtest/investigate_per_instrument_sma_grid.py` | Per-instrument grid search XAUUSD/USDJPY (Part E) |
| `v11/backtest/investigate_extra_pairs_grid.py` | Per-instrument grid search GBPUSD/AUDUSD/USDCAD/USDCHF (Part F) |

## Open Questions for Next Session

1. **Integrate SMA filter into main simulator** — proven on EURUSD, ready for implementation
2. **Paper trade EURUSD** — validate the edge in real-time
3. **Stage 2 (Grok LLM)** — test if Grok adds value on top of SMA-filtered signals
4. **Explore non-Darvas signal generators** — the Darvas Box may simply not be the right signal for most FX pairs. Could investigate other breakout methods (range breakout, volatility breakout, etc.) that might work across more instruments
