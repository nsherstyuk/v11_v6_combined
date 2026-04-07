# Project Status — All Trading Systems

**Last updated:** 2026-04-07 ET (Phase 5 build: V6 ORB adapter integrated into MultiStrategyRunner)  
**Author:** Cascade (AI pair programmer)

---

## Documentation Structure

All project documentation lives in `C:\ibkr_grok-_wing_agent\docs\`:

```
docs/
├── PROJECT_STATUS.md      ← YOU ARE HERE (living document, updated each session)
├── V11_DESIGN.md          ← V11 architecture, LLM strategy, Darvas params (living document)
└── journal/
    └── 2026-03-30_session.md  ← Session handoff (immutable after session ends)
    └── 2026-04-05_session.md  ← Design decisions finalized
    └── 2026-04-06_session.md  ← Initial build complete (all modules + 51 tests passing)
    └── 2026-04-05_backtest_session.md  ← Backtest Stage 1: grid search + OOS validation
    └── 2026-04-05_htf_investigation.md ← HTF investigation: 5 approaches tested, SMA filter wins
    └── 2026-04-06_frequency_investigation.md ← Trade frequency: multi-instrument + loosened params
    └── 2026-04-06_level_breakout_and_assessment.md ← Level breakout (failed) + critical assessment
    └── 2026-04-06_4h_deep_dive.md ← 4H level retest: breakthrough finding + strategy architecture
    └── 2026-04-06_multi_strategy_session.md ← V6 ORB review + multi-strategy architecture
    └── 2026-04-06_sma_integration_session.md ← Phase 1: SMA filter integrated into simulator + live engine
    └── 2026-04-06_level_detector_session.md ← Phase 2: 4H swing level detector built + 23 tests
    └── 2026-04-06_retest_detector_session.md ← Phase 3: retest detector state machine + 27 tests
    └── 2026-04-06_multi_strategy_runner_session.md ← Phase 4: MultiStrategyRunner + RiskManager + LevelRetestEngine + 40 tests
    └── 2026-04-07_orb_adapter_session.md ← Phase 5: V6 ORB adapter + 27 tests
    └── ...future sessions...
```

### Rules

1. **PROJECT_STATUS.md** — Living document. Updated at the end of every session. Shows current state of ALL projects. Start here to get oriented.
2. **V11_DESIGN.md** — Living document. Updated as design evolves. Contains architecture, LLM strategy, Darvas parameters, center/edge map, open questions.
3. **journal/YYYY-MM-DD_session.md** — Immutable after session ends. One file per session. Contains: what happened, decisions made, files changed, open questions, handoff notes. Read chronologically to understand project history.

### How to Start a New Session

1. Read `docs/PROJECT_STATUS.md` for current state
2. Read the latest `docs/journal/YYYY-MM-DD_session.md` for handoff context
3. Read `docs/V11_DESIGN.md` if working on v11
4. Resolve any open questions listed in the journal before coding

### Folder Roles

| Folder | Role |
|---|---|
| `C:\ibkr_grok-_wing_agent\` | **Home base** — all active development, docs, v11 project |
| `C:\nautilus0\v8_confirmed_rebreak\` | **Reference only** — completed v8 project, read but don't modify |
| `C:\nautilus0\v6_orb_refactor\` | **Reference only** — completed v6 ORB project, read but don't modify |

---

## Project 1: IBKR + Grok Swing Trading Agent

**Location:** `C:\ibkr_grok-_wing_agent\`  
**Status:** ✅ Operational (paper mode, sleeping until Monday 7:00 AM ET)  
**Purpose:** Automated swing trading agent for US equities using Grok LLM for trade decisions.

### What It Does

1. Connects to IBKR (paper account, port 4002)
2. Pulls 5-day hourly bars + live snapshot for a fixed watchlist of US stocks
3. Sends market data to Grok (`grok-4-1-fast-reasoning`) every 15 minutes
4. Grok returns JSON trade recommendations (BUY only, with entry/stop/target/confidence)
5. RiskManager validates each recommendation (per-trade risk, daily loss limit, confidence gate, stop validation)
6. Passing trades are placed as LimitOrders on IBKR during Regular Trading Hours only

### Architecture

```
main.py          — Core loop: connect → fetch data → ask Grok → risk check → place orders
config.py        — Centralized config (env vars, risk constants, STRATEGY_RULES prompt)
models.py        — Pydantic schemas: TradeRecommendation, GrokDecision
portfolio.py     — PortfolioTracker stub (edge element, logging only)
utils/logger.py  — Rotating file + console logger
```

### Key Parameters

| Parameter | Value | Source |
|---|---|---|
| Watchlist | EEIQ, SGML, UGRO, ANET | User-defined (config / .env) |
| Risk per trade | 1% of account | `MAX_RISK_PER_TRADE = 0.01` |
| Daily loss limit | 3% of account | `DAILY_LOSS_LIMIT = 0.03` |
| Confidence threshold | 70 | `CONFIDENCE_THRESHOLD = 70` |
| Grok model | grok-4-1-fast-reasoning | config.py |
| Market hours | Pre-market 7:00 AM, RTH 9:30–4:00 PM ET | main.py |
| Loop interval | 15 minutes | config.py |

### Limitations

- **Watchlist is static** — user-defined, not dynamically screened
- **No position monitoring** — PortfolioTracker is a stub (no bracket orders, no SL management after entry)
- **LimitOrder only** — no stop-loss orders placed on IBKR after entry
- **No trade exit logic** — once a LimitOrder fills, there's no automated exit management
- **Grok picks all trades** — no rule-based pre-screening; LLM is both screener and analyst

---

## Project 2: V8 Confirmed Rebreak (Reference Only)

**Location:** `C:\nautilus0\v8_confirmed_rebreak\` (READ ONLY — do not modify)  
**Status:** ✅ Complete (proven backtest results, live-tested)  
**Purpose:** Rule-based trading system for FX/commodities (primarily XAUUSD) based on pivot breakout → divergent imbalance → pullback → rebreak → matching imbalance pattern.

### Verified Backtest Results

| Metric | Value |
|---|---|
| Trades | 1,150 |
| PnL | +$1,863.40 |
| Win Rate | 57.4% |
| Avg Hold | 58.1 bars (1-min bars) |
| Exit Reasons | TIME_STOP: 1,055 / SL: 95 |

### What's Good (Carry Forward to V11)

- **Trade management** — bracket orders (entry → SL stop), fill tracking, commission tracking, IBKR-verified PnL, slippage computation, position reconciliation, cleanup on shutdown
- **IBKRConnection** — heartbeat, reconnection, error categorization, mid-price, tick rounding
- **BarAggregator** — tick → 1-min bar aggregation with buy/sell classification
- **ImbalanceClassifier** — volume flow analysis (divergence/matching detection)
- **Safety limits** — daily trade count + daily loss limits → force close
- **Watchdog** — auto-restart with rate limiting
- **Config separation** — strategy params (frozen) vs broker/environment params

### What's Weak (Replace in V11)

- **Pivot detection** (centered rolling max/min) — levels were too noisy or missed meaningful consolidation zones
- **Pattern too rigid** — break → divergence → pullback → rebreak → match is a very specific 5-step state machine
- **No LLM layer** — purely mechanical, no contextual awareness
- **No higher timeframe analysis** — operates only on 1-min bars

---

## Project 3: V11 (Multi-Strategy Portfolio — Build Phase)

**Location:** `C:\ibkr_grok-_wing_agent\v11\`  
**Status:** 🔨 Build Phase 5 complete (V6 ORB adapter). Three strategies across two instruments: EURUSD Darvas+SMA (~15/yr), EURUSD 4H Level Retest (~22/yr), XAUUSD ORB (from v6, ~150/yr). Combined ~187 trades/yr. Next: Phase 7 (run_live.py entry point + paper trading).  
**Purpose:** Multi-strategy portfolio combining rule-based breakouts + volume imbalance + optional Grok LLM filter for FX/commodities.

### What We've Learned (Honest Summary)

Over 4 investigation sessions, we tested **10+ signal/filter approaches** across **8 FX instruments** with hundreds of thousands of parameter combinations. The core finding:

**Higher-timeframe context is everything.** 1-min signals alone are noise. The value comes from aligning 1-min entries with higher-timeframe direction (SMA) or structure (4H levels).

### Two OOS-Validated Strategies on EURUSD

| Strategy | OOS Trades/yr | OOS WR% | OOS AvgR | Total R/yr | Confidence |
|---|---|---|---|---|---|
| **Darvas + SMA(50) + CONF** | 10.5-14.7 | 43-46% | +0.175 | ~2R | **High** (IS+OOS aligned) |
| **4H Level Breaks + SMA(50) + CONF** | 108.7 | 40.2% | +0.049 | ~5.3R | **Medium** (thin edge, needs more work) |

The Darvas strategy has higher per-trade quality. The 4H level strategy has 10x more trades and ~3x total R/year, but the edge is thin enough that execution costs matter.

### What Failed (and Why)

| Approach | Result | Why It Failed |
|---|---|---|
| 1-min Darvas without SMA | OOS -0.044 AvgR | No directional context, trades counter-trend |
| 1-min swing level breakouts | OOS +0.003 AvgR (zero) | Levels too abundant, structurally meaningless |
| 1H swing levels + 1-min entry | OOS ~0 AvgR | Still too many levels, not selective enough |
| Previous day H/L | OOS -0.008 to -0.028 | Too well-known, priced in |
| MTF Darvas box alignment | <15 trades | Too restrictive, boxes rarely active simultaneously |
| ADX threshold filter | No filtering effect | ADX always above 15 at breakout time |
| XAUUSD/USDJPY/GBPUSD/AUDUSD/USDCAD/USDCHF | Negative or marginal | Strategy is EURUSD-specific with current signals |

### Key Principle Discovered

**Signal quality correlates with timeframe:** 1-min levels are noise, 1H levels are marginal, 4H levels show real signal, and the SMA(50) on 60-min bars (representing ~2 days of trend) is the strongest filter. The pattern is consistent: the longer the timeframe of the reference, the more meaningful the signal.

### Core Components

| Component | Role | Validated? |
|---|---|---|
| 60-min SMA(50) direction filter | Trend alignment | **Yes — proven OOS on EURUSD** |
| CONFIRMING volume filter | Flow confirmation | **Yes — 19-31pt WR gap IS, helps OOS** |
| Darvas Box breakout | Consolidation breakout signal | **Yes on EURUSD with SMA** (regime-dependent alone) |
| 4H swing level breakout | S/R level breakout signal | **Promising — under investigation** |
| Trail10@60 SL management | Risk management | **Yes IS — not independently OOS validated** |
| Grok LLM filter | Contextual judgment | **Not yet tested (Stage 2)** |

### Key Decisions (Finalized 2026-04-05)

| Decision | Value |
|---|---|
| Instruments | XAUUSD + EURUSD + USDJPY (all from day one) |
| LLM | Grok 4-1 Fast (swappable via protocol) |
| Confidence threshold | 75 |
| Short trades | Both long and short enabled |
| Darvas params | Optimized per instrument via grid search (see Backtest Results below) |
| Economic calendar | Grok training knowledge initially; external API later |

### Build Status (2026-04-06)

| Module | File | Status |
|---|---|---|
| Core types | `v11/core/types.py` | ✅ Complete |
| Darvas detector (CENTER) | `v11/core/darvas_detector.py` | ✅ Complete, 15 tests |
| Imbalance classifier | `v11/core/imbalance_classifier.py` | ✅ Ported from v8, 14 tests |
| LLM protocol | `v11/llm/base.py` | ✅ Complete |
| LLM models (CENTER) | `v11/llm/models.py` | ✅ Complete, 12 tests |
| Grok filter | `v11/llm/grok_filter.py` | ✅ Complete |
| IBKR connection | `v11/execution/ibkr_connection.py` | ✅ Ported from v8 |
| Bar aggregator | `v11/execution/bar_aggregator.py` | ✅ Ported from v8, 10 tests |
| Trade manager (CENTER) | `v11/execution/trade_manager.py` | ✅ Complete |
| Live engine | `v11/live/live_engine.py` | ✅ Complete |
| Entry point | `v11/live/run_live.py` | ✅ Complete |
| Config | `v11/config/` | ✅ Complete |
| Tests | `v11/tests/` | ✅ 190 tests, all passing |
| Data loader | `v11/backtest/data_loader.py` | ✅ Complete |
| Simulator | `v11/backtest/simulator.py` | ✅ Complete |
| Metrics | `v11/backtest/metrics.py` | ✅ Complete |
| Grid search | `v11/backtest/grid_search.py` | ✅ Complete |
| HTF utilities | `v11/backtest/htf_utils.py` | ✅ Complete (resampling, SMA, ADX, shared pipelines) |
| Session filter investigation | `v11/backtest/investigate_session_filter.py` | ✅ Complete |
| HTF SMA filter (CENTER) | `v11/core/htf_sma_filter.py` | ✅ Complete, 22 tests |
| 4H Swing Level Detector | `v11/core/level_detector.py` | ✅ Complete, 23 tests |
| Retest Detector (CENTER) | `v11/core/retest_detector.py` | ✅ Complete, 27 tests |
| RiskManager | `v11/live/risk_manager.py` | ✅ Complete, 17 tests |
| LevelRetestEngine | `v11/live/level_retest_engine.py` | ✅ Complete, 8 tests |
| MultiStrategyRunner | `v11/live/multi_strategy_runner.py` | ✅ Complete, 15 tests |
| V6 ORB package | `v11/v6_orb/` | ✅ Frozen V6 copies (strategy, context, executor) |
| ORB Adapter | `v11/live/orb_adapter.py` | ✅ Complete, 27 tests |
| HTF SMA investigation | `v11/backtest/investigate_htf_sma.py` | ✅ Complete |
| ADX filter investigation | `v11/backtest/investigate_adx_filter.py` | ✅ Complete |
| HTF Darvas investigation | `v11/backtest/investigate_htf_darvas.py` | ✅ Complete |
| MTF alignment investigation | `v11/backtest/investigate_mtf_alignment.py` | ✅ Complete |
| Trade frequency investigation | `v11/backtest/investigate_trade_frequency.py` | ✅ Complete |
| Per-instrument SMA grid | `v11/backtest/investigate_per_instrument_sma_grid.py` | ✅ Complete |
| Extra pairs grid search | `v11/backtest/investigate_extra_pairs_grid.py` | ✅ Complete |
| Level breakout investigation | `v11/backtest/investigate_level_breakout.py` | ✅ Complete (1-min levels not viable) |
| HTF levels investigation | `v11/backtest/investigate_htf_levels.py` | ✅ Complete (4H levels promising) |

### Backtest Stage 1 Results (2026-04-05)

**Data:** 1-min bars from `C:\nautilus0\data\1m_csv\`. XAUUSD (761K bars 2024-2026), EURUSD (705K bars), USDJPY (820K bars).

#### Best In-Sample Results (2024-2026)

| Instrument | R:R | Sharpe | WR% | PF | Trades | Best Params |
|---|---|---|---|---|---|---|
| EURUSD | 2.0 | 0.72 | 45.9% | 1.29 | 148 | tc=15, bc=20, maxW=4.0, brk=2 |
| EURUSD | 2.0 | 0.60 | 44.2% | 1.80 | 86 | tc=20, bc=12, maxW=3.0, brk=2 |
| XAUUSD | 2.0 | 0.19 | 38.9% | 1.74 | 36 | tc=10, bc=20, maxW=3.0, brk=3 |
| USDJPY | 2.0 | 0.12 | 42.5% | 1.32 | 106 | tc=20, bc=20, maxW=4.0, brk=3 |

#### Volume Classification — Confirmed Edge

CONFIRMING trades consistently outperform DIVERGENT across all instruments:
- **XAUUSD:** CONFIRMING 56% WR vs DIVERGENT 25% WR (31pt gap)
- **EURUSD Config B:** CONFIRMING 60% WR vs DIVERGENT 41% WR (19pt gap)
- **USDJPY:** CONFIRMING 51% WR vs DIVERGENT 42% WR (9pt gap)

#### SL Tightening — Improves Returns

Trail 10-bar after 60 bars (1 hour) is the best SL management variant:
- Baseline AvgR=+0.245 → Trail10@60 AvgR=+0.353 (+44% improvement)
- Time stops eliminated (11 → 1)

#### Best Combined Stack (EURUSD, IS 2024-2026)

CONFIRMING + R:R=2.0 + Trail10@60: **40 trades, 60% WR, +0.570 AvgR**

#### ⚠️ OUT-OF-SAMPLE VALIDATION — ORIGINALLY FAILED, NOW FIXED WITH HTF SMA FILTER

| Config | Period | Type | Trades | WR% | AvgR | PnL |
|---|---|---|---|---|---|---|
| Baseline (no HTF filter) | 2018-2023 | **OOS** | 127 | 37.0% | -0.044 | -0.1877 |
| Baseline (no HTF filter) | 2024-2026 | IS | 40 | 60.0% | +0.570 | +1.2760 |
| **+ 60-min SMA(50) filter** | **2018-2023** | **OOS** | **63** | **46.0%** | **+0.176** | **+0.8767** |
| **+ 60-min SMA(50) filter** | **2024-2026** | **IS** | **24** | **62.5%** | **+0.729** | **+1.3141** |

**The 60-min SMA(50) direction filter turns OOS from losing to profitable.** Only take LONG breakouts when price > 60-min SMA(50), SHORT when price < SMA. This was the single strongest finding from the HTF investigation.

### Complete Investigation Log

#### Session 1 — Backtest Stage 1 (2026-04-05 evening)
Built backtester (data_loader, simulator, metrics, grid_search). Ran grid search on 3 instruments. Best IS: EURUSD Config B (tc=20, bc=12, mxW=3.0, brk=2) 60% WR, +0.570 AvgR. **OOS validation failed:** 37% WR, -0.044 AvgR.
- Journal: `docs/journal/2026-04-05_backtest_session.md`

#### Session 2 — HTF Filter Investigation (2026-04-05 late)
Tested 5 HTF filter approaches to fix OOS failure:

| # | Approach | OOS Result | Verdict |
|---|---|---|---|
| 1 | **60-min SMA(50) direction** | **+0.176 AvgR, 46% WR** | **WINNER** |
| 2 | Darvas on 5-min bars | +0.188 AvgR, 56.5% WR (23 trades) | Promising |
| 3 | Session/time-of-day filter | +0.091 AvgR (Asian best) | Modest |
| 4 | ADX trend strength | +0.042 AvgR (DI alignment) | Weaker than SMA |
| 5 | MTF box alignment | <15 trades | Too restrictive |

- Journal: `docs/journal/2026-04-05_htf_investigation.md`

#### Session 3 — Trade Frequency (2026-04-06)
Investigated increasing trade count from ~10/yr:

| Angle | Result |
|---|---|
| SMA on XAUUSD/USDJPY (default params) | Both negative — SMA can't fix bad params |
| Loosened EURUSD params + SMA | brk=3 → 14.7/yr (+0.175 AvgR); mxW=4.0 → 68/yr (+0.055) |
| 5-min Darvas + SMA | Thin edge (+0.005-0.02), not viable |
| Per-instrument grid XAUUSD | 16/940 positive OOS — IS/OOS diverge (low confidence) |
| Per-instrument grid USDJPY | 37/924 positive — best 48 trades/yr +0.311 AvgR (medium confidence) |
| Per-instrument grid GBPUSD/AUDUSD | Marginal (2-4 positive configs each) |
| Per-instrument grid USDCAD/USDCHF | **Zero positive configs** |

**Conclusion:** EURUSD is the primary instrument. USDJPY possible secondary.
- Journal: `docs/journal/2026-04-06_frequency_investigation.md`

#### Session 4 — Level Breakout & HTF Levels (2026-04-06)
Tested alternative signal generators:

| Approach | OOS/yr | OOS AvgR | Verdict |
|---|---|---|---|
| 1-min swing level breakout | 1,477 | +0.003 (zero) | Dead — too many weak levels |
| 1H swing levels + 1-min entry | ~2,500 | ~0 | Still too many |
| **4H swing levels + 1-min entry** | **108.7** | **+0.049** | **Promising — 3x total R/yr vs Darvas** |
| Previous day H/L | 2,180 | -0.008 | Dead — too well-known |

- Journal: `docs/journal/2026-04-06_level_breakout_and_assessment.md`

#### Session 5 — 4H Level Deep Dive (2026-04-06 late)
Deep investigation of 4H level strategy: SL tightness, retest mode, session filter, R:R, volume filter, year-by-year.

**Retest mode is the breakthrough finding:**

| Config | OOS/yr | OOS WR% | OOS AvgR | IS AvgR | Total R/yr |
|---|---|---|---|---|---|
| Direct (baseline) | 108.7 | 40.2% | +0.049 | +0.008 | ~5.3R |
| **Retest pb=10-30** | **22.3** | **39.6%** | **+0.135** | **+0.230** | **~3.0R** |
| Retest pb=5-30 | 24.0 | 37.5% | +0.108 | +0.269 | ~2.6R |
| Retest pb=3-120 | 30.5 | 37.2% | +0.098 | +0.149 | ~3.0R |

Other findings: SL offset barely matters (4H level IS the stop); No-Asian filter helps (+0.062 vs +0.049); R:R=2.0 is sweet spot; CONFIRMING volume adds value (+0.049 vs +0.014 ALL).

Year-by-year OOS (direct mode): 2018 worst (-0.048), 2021-2022 best (+0.15). 4 of 6 OOS years positive.

**Combined portfolio projection:**

| Strategy | OOS/yr | OOS AvgR | Total R/yr |
|---|---|---|---|
| Darvas + SMA | 14.7 | +0.175 | ~2.6R |
| 4H Level Retest + SMA | 22.3 | +0.135 | ~3.0R |
| **COMBINED** | **~37** | **~+0.155** | **~5.6R** |

- Journal: `docs/journal/2026-04-06_4h_deep_dive.md`
- Script: `v11/backtest/investigate_4h_levels_deep.py`

### Backtest Artifacts

**Grid search CSVs** (project root): `v11_grid_*.csv` (12 files, multiple instruments/R:R)

**Stage 1 analysis scripts** (`v11/backtest/analyze_*.py`): volume, funnel, ATR SL/TP, trailing SL, combined, exits, OOS validation

**Investigation scripts** (`v11/backtest/investigate_*.py`):

| Script | What It Tests |
|---|---|
| `htf_utils.py` | Shared utilities (resampling, SMA, ADX, simulation, stats) |
| `investigate_session_filter.py` | Session/time-of-day filter |
| `investigate_htf_sma.py` | 60-min SMA direction filter |
| `investigate_adx_filter.py` | ADX trend strength + DI alignment |
| `investigate_htf_darvas.py` | Darvas on 5/15-min bars |
| `investigate_mtf_alignment.py` | Multi-timeframe box alignment |
| `investigate_trade_frequency.py` | Trade count: loosened params, multi-instrument, extra pairs |
| `investigate_per_instrument_sma_grid.py` | Per-instrument grid search XAUUSD/USDJPY |
| `investigate_extra_pairs_grid.py` | Per-instrument grid search GBPUSD/AUDUSD/USDCAD/USDCHF |
| `investigate_level_breakout.py` | 1-min swing level breakout/retest |
| `investigate_htf_levels.py` | HTF (1H/4H) swing levels + daily H/L + 1-min entry |
| `investigate_4h_levels_deep.py` | 4H levels deep dive: retest, SL, sessions, year-by-year |
| `investigate_4h_retest_all_pairs.py` | 4H level retest on all 7 FX pairs |

Plans: `docs/HTF_INVESTIGATION_PLAN.md`

### 4H Level Retest — Multi-Instrument Results (2026-04-06)

Tested best 4H retest config on all 7 available FX pairs:

| Instrument | Best Config | OOS/yr | OOS WR% | OOS AvgR | IS AvgR | Viable? |
|---|---|---|---|---|---|---|
| **EURUSD** | Retest pb=10-30 CONF RR=2.0 | **22.3** | **39.6%** | **+0.135** | **+0.230** | **YES** |
| XAUUSD | Retest pb=10-30 CONF RR=1.5 | 26.7 | 38.1% | -0.452 | -0.243 | NO |
| USDJPY | Retest pb=10-30 CONF RR=2.0 | 17.8 | 34.6% | -0.446 | -0.470 | NO |
| GBPUSD | Retest pb=5-30 CONF RR=2.0 | 29.0 | 35.6% | -0.330 | -0.800 | NO |
| AUDUSD | Retest pb=5-30 CONF RR=2.0 | 23.8 | 37.8% | -0.411 | -0.566 | NO |
| USDCAD | Retest pb=10-30 CONF RR=2.0 | 25.0 | 34.0% | -0.484 | -0.212 | NO |
| USDCHF | Retest pb=5-60 CONF RR=2.0 | 33.8 | 30.5% | -0.883 | -0.921 | NO |

**The 4H level retest strategy, like Darvas, is EURUSD-only.** All other pairs are deeply negative in both IS and OOS. This is not a parameter problem — the strategy fundamentally doesn't work on these pairs with these signals.

**Why EURUSD is unique:** EURUSD is the world's most liquid FX pair (~28% of daily volume). Its price action is cleaner, levels are more respected by institutional flow, and breakouts follow through more reliably. Other pairs have wider spreads (relatively), more erratic price action around levels, and less consistent volume patterns.

### Honest Criticisms (Updated After 6 Sessions)

1. **Single-instrument** — both strategies only validated on EURUSD (confirmed across 7 pairs)
2. **Trade count still modest** — combined portfolio ~37 trades/yr is better but not high-frequency
3. **Trailing stop not independently OOS-validated** — Trail10@60 was IS-optimized
4. **4H retest edge thin in some years** — 2018 and 2023 are negative OOS years
5. **No execution reality testing** — slippage, spread widening, partial fills not modeled
6. **SMA is the dominant alpha source** — both strategies depend heavily on it

### Recommended Strategy Architecture — Multi-Strategy Portfolio

Based on all research + V6 ORB review, the system is a **three-signal portfolio across two instruments**:

**Signal 1 — Darvas Box Breakout (high quality, low frequency)**
- EURUSD, 1-min bars, Config B (tc=20, bc=12, mxW=3.0, brk=3)
- 60-min SMA(50) direction filter
- CONFIRMING volume filter
- Trail10@60 SL management
- R:R=2.0
- Expected: ~15 trades/yr, +0.175 AvgR, ~2.6R/yr

**Signal 2 — 4H Level Retest (moderate quality, higher frequency)**
- EURUSD, 4H swing levels (lb=10, rb=10, exp=72h, merge=0.5 pips)
- Break → pullback (min 10 bars) → rebreak (max 30 bars)
- 60-min SMA(50) direction filter
- CONFIRMING volume filter
- SL at level ± 0.3 ATR
- R:R=2.0
- Expected: ~22 trades/yr, +0.135 AvgR, ~3.0R/yr

**Signal 3 — Opening Range Breakout (from V6 ORB, high frequency)**
- XAUUSD, 1-min bars
- Asian range (00:00-06:00 UTC) defines consolidation zone
- Gap filter (06:00-08:00 volatility > P50) screens bad days
- Velocity filter gates bracket placement during trade window (08:00-16:00)
- Bracket orders at range high/low, SL at opposite boundary
- R:R=2.5, breakeven after N hours
- Expected: ~150 trades/yr, 50.6% WR, $1.52/trade

**Integration Architecture:**

```
MultiStrategyRunner (single process)
├── IBKRConnection (shared, multi-instrument)
├── RiskManager (combined daily loss limit across all positions)
├── Trade CSV Logger (unified log)
│
├── V6 ORBStrategy (XAUUSD) — untouched, proven code
│   ├── LiveMarketContext (velocity, Asian range, gap metrics)
│   └── IBKRExecutionEngine (XAUUSD contract)
│
├── V11 DarvasStrategy (EURUSD)
│   ├── DarvasDetector + SMA(50) + CONFIRMING filter
│   └── TradeManager (EURUSD contract)
│
└── V11 4HLevelStrategy (EURUSD)
    ├── SwingLevelDetector + Retest logic + SMA(50) + CONFIRMING filter
    └── TradeManager (EURUSD contract, shared with Darvas)
```

**Design principle:** Separate strategy engines, shared infrastructure. V6 ORB code stays untouched. Each strategy has its own state machine and signal logic.

**LLM (Grok) Layer — Optional Enhancement, Not Required:**
The mechanical system is profitable without Grok. The LLM could add value by:
- Filtering out signals near major news events
- Assessing pattern quality (clean vs messy breakout)
- Adding higher-timeframe context beyond what SMA captures
- But the core edge is mechanical — LLM is a refinement, not the foundation

**Combined Expected Performance:**

| Strategy | Instrument | Trades/yr | AvgR | R/yr |
|---|---|---|---|---|
| Darvas + SMA | EURUSD | ~15 | +0.175 | ~2.6 |
| 4H Level Retest | EURUSD | ~22 | +0.135 | ~3.0 |
| V6 ORB | XAUUSD | ~150 | ~+0.05 | ~7.5 |
| **COMBINED** | **2 instruments** | **~187** | | **~13R** |

At 1% risk per trade: ~13% annual return before compounding. Diversified across instruments and signal types.

### Build Roadmap

| Phase | Task | Status |
|---|---|---|
| 1 | Integrate 60-min SMA(50) filter into `simulator.py` and `live_engine.py` | ✅ Complete |
| 2 | Build 4H swing level detector module (`v11/core/level_detector.py`) | ✅ Complete |
| 3 | Build retest detection logic (pb=10-30 window) | ✅ Complete |
| 4 | Build `MultiStrategyRunner` orchestrator (shared IBKR + risk) | ✅ Complete |
| 5 | Wire V6 ORB into the runner (adapter, don't modify v6) | 🔲 Pending |
| 6 | Write tests for new modules (level detector, retest, runner) | 🔲 Pending |
| 7 | Paper trade EURUSD (Darvas + 4H) + XAUUSD (ORB) | 🔲 Pending |
| 8 | Stage 2: Test Grok LLM as optional enhancement | 🔲 Future |

### Open Questions

1. **Walk-forward validation** — train on rolling windows instead of fixed IS/OOS split
2. **Execution simulation** — model slippage, spread widening, partial fills
3. **V6 ORB adapter** — should we copy v6 code into v11, or import from nautilus0?
4. **Combined risk management** — how to handle simultaneous XAUUSD + EURUSD positions against one daily loss limit

### Full Design

See `docs/V11_DESIGN.md` for detailed architecture, LLM strategy, Darvas parameters, and backtest approach.  
See `v11/ARCHITECTURE.md` for center/edge map and module boundaries.

---

## Project 4: V6 ORB Refactor (Reference Only — To Be Integrated)

**Location:** `C:\nautilus0\v6_orb_refactor\` (READ ONLY — do not modify)  
**Status:** ✅ Complete (backtested, architecture verified, ready to wire into multi-strategy runner)  
**Purpose:** Opening Range Breakout strategy for XAUUSD using Asian session consolidation + velocity filter + gap filter.

### How It Works

1. Asian session (00:00-06:00 UTC) defines the consolidation range (high/low)
2. Gap filter (06:00-08:00 UTC) checks pre-trade volatility — skips quiet days
3. During trade window (08:00-16:00 UTC), velocity filter monitors tick speed
4. When velocity exceeds threshold → OCA bracket orders placed at range high (long) and range low (short)
5. Entry fill → SL at opposite range boundary, TP at range boundary + R:R × range size
6. Breakeven rule: move SL to entry after N hours
7. EOD close: any open position closed at end of trade window

### Architecture (Excellent — Environment-Agnostic)

```
ORBStrategy (pure state machine, no IBKR knowledge)
  ↔ MarketContext (ABC) → HistoricalMarketContext / LiveMarketContext
  ↔ ExecutionEngine (ABC) → SimExecutionEngine / IBKRExecutionEngine
  ↔ Runner (orchestrator) → BacktestRunner / LiveRunner
```

Same strategy code runs in backtest and live. No `if dry_run` branches. Clean separation.

### Verified Results (XAUUSD)

| Config | Trades | WR% | Avg PnL | Total PnL |
|---|---|---|---|---|
| No gap filter | 1,613 | 46.4% | $0.70 | $1,137 |
| **Gap filter (vol > P50)** | **780** | **50.6%** | **$1.52** | **$1,187** |

### Key Parameters

| Parameter | Value |
|---|---|
| Instrument | XAUUSD |
| Range window | 00:00-06:00 UTC (Asian session) |
| Trade window | 08:00-16:00 UTC |
| R:R ratio | 2.5 |
| Velocity lookback | 3 minutes |
| Velocity threshold | 200 ticks/min |
| Gap filter | Enabled (P50 volatility) |
| Spread model | $0.30 per side |

### Integration Plan

V6 ORB will be wired into the V11 `MultiStrategyRunner` via an adapter layer:
- V6 code stays untouched in `C:\nautilus0\v6_orb_refactor\`
- Adapter wraps `ORBStrategy` + `LiveMarketContext` + `IBKRExecutionEngine`
- Shares the IBKR connection and risk manager with V11 strategies
- XAUUSD contract managed separately from EURUSD

---

## Standards Documents

**Location:** `C:\ibkr_grok-_wing_agent\standards\`

Three standards govern all development:

1. **operating-principles-guide-for-agents.md** — Center/edge protection, deep modules, risk assessment before acting, mismatch surfacing, handoff with evidence
2. **layer1-research-standards.md** — Epistemic discipline, confidence from checkable conditions, surface assumptions, authority order
3. **test-creation-guide-for-agents.md** — Tests from intent + regression (not implementation), two-phase (spec then code), coverage by design decisions, no tautological tests

---

## Environment

- **OS:** Windows
- **Python:** 3.14.3
- **IBKR:** IB Gateway on port 4002 (paper trading)
- **Grok API:** xAI via openai.AsyncOpenAI, model `grok-4-1-fast-reasoning`
- **Key packages:** ib_async (swing agent), ib_insync (v8/v11), pydantic, pandas, numpy, pytest
