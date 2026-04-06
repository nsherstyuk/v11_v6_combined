# V11 Design Document — Darvas Box + Volume Imbalance + LLM Filter

**Last updated:** 2026-04-06 ET (multi-strategy architecture)  
**Status:** Research complete. Multi-strategy portfolio designed: Darvas+SMA (EURUSD) + 4H Level Retest (EURUSD) + V6 ORB (XAUUSD). Build phase starting.  
**Related docs:** `docs/PROJECT_STATUS.md` | `docs/journal/2026-04-06_4h_deep_dive.md` | `docs/journal/2026-04-06_multi_strategy_session.md`

---

## 1. System Overview

V11 is a hybrid trading system that combines:

1. **Darvas Box** — rule-based level detection and breakout signals (deterministic)
2. **Volume Imbalance Analysis** — buy/sell flow confirmation (carried from v8)
3. **LLM Filter** — contextual approval layer that evaluates the bigger picture before execution

The key principle: **deterministic signals, intelligent filtering**. The rule-based engine handles "when to look" — the LLM handles "should I actually act?"

---

## 2. Darvas Box — How It Works

### Classic Darvas Box Theory

Nicolas Darvas's method identifies stocks (or any instrument) in consolidation, then trades the breakout:

1. **New High** — price makes a new high
2. **Box Top Forms** — price fails to exceed that high for N consecutive bars → that high becomes the box top
3. **Box Bottom Forms** — the lowest low during the top-formation period becomes the box bottom
4. **Containment** — price stays within the box (between top and bottom)
5. **Breakout** — price closes above box top (long) or below box bottom (short)
6. **Stop Loss** — placed at the box bottom (for longs) or box top (for shorts)

### Adapting Darvas to 1-Minute Bars

Classic Darvas was designed for daily bars. For intraday (1-min) bars, we need to adjust:

| Parameter | Classic (Daily) | Intraday Adaptation | Notes |
|---|---|---|---|
| **Top confirmation bars** | 3 consecutive days without new high | Configurable: 10–30 bars | Too few = noise, too many = late |
| **Minimum box width** | N/A | Min price range (e.g., 0.5× ATR) | Reject micro-boxes that are just tick noise |
| **Maximum box width** | N/A | Max price range (e.g., 5× ATR) | Reject boxes so wide they're meaningless |
| **Minimum box duration** | N/A | Min bars in box (e.g., 20) | Ensures real consolidation, not a single candle |
| **Breakout confirmation** | Close above top | Close above top + optional volume threshold | Reduces false breakouts |

### Darvas Box Parameter Optimization

**The problem:** We don't know the optimal parameters (top confirmation bars, min box width, etc.) for a given instrument and timeframe.

**Approach — Grid search via backtesting:**

```
For each parameter combination:
  top_confirm_bars ∈ [5, 10, 15, 20, 30]
  min_box_width_atr ∈ [0.3, 0.5, 1.0, 1.5]
  max_box_width_atr ∈ [3.0, 5.0, 8.0]
  min_box_duration ∈ [10, 20, 30, 50]
  breakout_confirm ∈ [1, 2, 3]  (bars above box top)

  Run backtester on historical data
  Record: signal count, win rate, avg PnL, max drawdown
  
Select: parameter set with best risk-adjusted return (Sharpe or Calmar)
```

**Walk-forward validation** (same as v8): optimize on window N, test on window N+1, slide forward. This prevents overfitting to a specific period.

**Per-instrument tuning:** XAUUSD, EURUSD, and USDJPY have very different volatility profiles. Parameters should be optimized per instrument, stored in config.

### Hybrid Backtest Approach (Confirmed 2026-04-06)

**Decision:** Use a two-stage hybrid approach to backtesting.

#### The Problem: LLM in Backtest

Grok's calendar/context reasoning is part of the live pipeline, but calling the LLM for every signal during a grid search is:
- **Slow** — API call per signal, rate-limited
- **Expensive** — tokens cost money, grid search produces thousands of signals
- **Non-deterministic** — LLM responses vary between runs, making parameter comparison unreliable

#### Solution: Two-Stage Hybrid

**Stage 1 — Parameter Grid Search (No LLM, Fast, Deterministic)**

Run the Darvas detector + ImbalanceClassifier over historical data with many parameter combinations. No LLM calls. Measures raw signal quality.

```
For each parameter combination:
  → DarvasDetector.add_bar() for all historical bars
  → Record every BreakoutSignal
  → Simulate entry at breakout_price, SL at box boundary, time stop
  → Compute: signal count, win rate, avg R:R, max drawdown, Sharpe
```

Output: ranked parameter sets per instrument.

**Stage 2 — LLM Filter Validation (Best Params Only, Sampled)**

Take the top 1–3 parameter sets from Stage 1. Re-run the backtest, but this time call Grok for every signal with the full `SignalContext` (including timestamp).

```
For each signal in the best parameter set:
  → Build SignalContext (same as live: bars, box, volume, timestamp)
  → Call Grok via GrokFilter.evaluate_signal()
  → Record: approved/rejected, confidence, reasoning, risk_flags
  → Compare filtered vs unfiltered results
```

This answers: **Does Grok's filtering actually improve performance?**

#### What Grok Can Do with Historical Timestamps

Grok receives `current_time_utc` in every `SignalContext`. For historical dates, Grok can:
- **Reason about recurring events**: "This is the first Friday of the month → likely NFP day"
- **Identify session context**: "14:30 UTC → London/NY overlap, high liquidity"
- **Flag known risk periods**: "Mid-December → thin holiday liquidity"
- **Assess day-of-week patterns**: "Friday afternoon → position squaring risk"

#### What Grok Cannot Do in Backtest (Same Limitations as Live)

- Know the **actual outcome** of an economic event (no hindsight)
- Know **exact schedules** beyond its training cutoff
- Access **real-time data** (news headlines, live sentiment)

These limitations are identical in live trading, so Stage 2 backtesting accurately reflects live LLM performance on the calendar dimension.

#### Backtest File Structure

```
v11/backtest/
├── data_loader.py        # Load historical bars from CSV or IBKR
├── grid_search.py        # Stage 1: Darvas param optimization (no LLM)
├── llm_validation.py     # Stage 2: Best params + Grok filter
├── simulator.py          # Trade simulation (entry, SL, time stop, PnL)
└── metrics.py            # Win rate, R:R, drawdown, Sharpe, Calmar
```

#### Key Design Decisions for Backtest

| Decision | Choice | Rationale |
|---|---|---|
| SL placement in backtest | Box boundary (bottom for long, top for short) | Structurally meaningful, same as live |
| TP in backtest | None (time stop only) | Let the LLM set TP in Stage 2; Stage 1 uses time stop to measure raw signal quality |
| Spread cost | Per-instrument from StrategyConfig | Realistic cost modeling |
| Walk-forward windows | Optimize on 3 months, test on 1 month, slide | Same approach as v8, prevents overfitting |
| Stage 2 sample | All signals from best param set | Not sampled — full comparison needed for statistical significance |

### Darvas Box — Confirmed Initial Parameters (1-min bars)

These are starting values to be refined via backtesting:

| Parameter | Value | Rationale |
|---|---|---|
| `top_confirm_bars` | 15 | 15 minutes of no new high → box top confirmed |
| `bottom_confirm_bars` | 15 | Same logic for box floor |
| `min_box_width_atr` | 0.3 | Low enough to catch real consolidations, high enough to filter noise |
| `min_box_duration` | 20 | Box must exist 20+ minutes — prevents micro-consolidations |
| `breakout_confirm_bars` | 3 | 3 consecutive bars above box top — balances speed vs false-breakout protection |

### Darvas vs V8 Pivots — Why the Change

| Aspect | V8 Centered Rolling Pivots | Darvas Box |
|---|---|---|
| Level detection | Mathematical: highest high in N-bar window | Structural: price consolidation zone |
| Adapts to volatility | Fixed window → may miss tight or wide consolidations | Box width naturally adapts to what price is doing |
| False breakouts | Level is a single point → any spike triggers | Box requires sustained containment → more conviction |
| Stop placement | ATR-based (arbitrary distance) | Box bottom/top (structurally meaningful) |
| Backtestability | ✅ Fully deterministic | ✅ Fully deterministic |

---

## 3. Volume Imbalance Analysis (From V8)

### What It Does

The `ImbalanceClassifier` from v8 computes the **buy ratio** — the proportion of volume classified as buying vs selling:

```
buy_ratio = buy_volume / (buy_volume + sell_volume)
```

- `buy_ratio > 0.5` → more buying pressure
- `buy_ratio < 0.5` → more selling pressure
- `buy_ratio ≈ 0.5` → balanced / indeterminate

### How It's Used in V11

At each Darvas breakout, we measure volume imbalance:

**For long breakouts (above box top):**
- `buy_ratio >= threshold` → **Confirming** — buyers are driving the breakout
- `buy_ratio < threshold` → **Divergent** — price is up but sellers dominate (possible trap)

**For short breakouts (below box bottom):**
- `buy_ratio <= (1 - threshold)` → **Confirming** — sellers are driving the breakdown
- `buy_ratio > (1 - threshold)` → **Divergent** — price is down but buyers dominate

### Design Decision: Use Imbalance as Signal Enrichment, Not Hard Filter

In v8, imbalance was a hard gate (divergent first break required, matching rebreak required). In v11, we **pass the imbalance data to Grok** and let it decide how much weight to give it. This is because:

1. Grok can reason about context — "imbalance is divergent but it's thin pre-market volume, ignore it"
2. Different instruments may have different imbalance characteristics
3. Avoids the rigidity that made v8's pattern too strict

The imbalance data sent to Grok will include:
- `buy_ratio` at breakout
- `buy_ratio` trend over last N bars (increasing/decreasing)
- `tick_count` quality (was there enough volume to be meaningful?)
- Classification: CONFIRMING / DIVERGENT / INDETERMINATE

---

## 4. LLM Filter Layer — Deep Design

### 4.1 Role of the LLM

The LLM is **not** generating signals. It is **evaluating** signals that the deterministic system has already identified. Its job:

1. **Pattern quality assessment** — "Is this a clean breakout or a choppy mess?"
2. **Higher timeframe context** — "Is this breakout aligned with the daily/4H trend?"
3. **Macro awareness** — "Is there an FOMC meeting in 2 hours that could reverse this?"
4. **Confluence check** — "Is this breakout into major resistance, or into open space?"
5. **Risk/reward assessment** — "Given the box size and ATR, is the R:R worth it?"

### 4.2 What the LLM Receives

When a Darvas breakout fires, we package:

```json
{
  "signal": {
    "type": "DARVAS_BREAKOUT",
    "direction": "long",
    "box_top": 2045.50,
    "box_bottom": 2038.20,
    "box_duration_bars": 45,
    "breakout_price": 2046.10,
    "atr": 1.85
  },
  "volume_analysis": {
    "buy_ratio_at_breakout": 0.62,
    "buy_ratio_trend_20bar": "increasing",
    "tick_quality": "HIGH",
    "classification": "CONFIRMING"
  },
  "recent_bars": [...last 200 1-min bars...],
  "daily_bars": [...last 30 daily bars (if available)...],
  "current_time_utc": "2026-03-31T14:35:00Z",
  "instrument": "XAUUSD",
  "session": "LONDON_NY_OVERLAP"
}
```

### 4.3 What the LLM Returns

```json
{
  "approved": true,
  "confidence": 78,
  "entry": 2046.10,
  "stop": 2037.80,
  "target": 2058.00,
  "reasoning": "Clean breakout above 45-bar consolidation. Buy ratio confirms institutional buying. Daily trend is bullish (higher highs). No major economic events in next 2 hours. Box bottom provides clear structural stop. R:R = 1:1.6.",
  "risk_flags": []
}
```

### 4.4 Which LLM? Key Parameters That Matter

This is a critical decision. The LLM for this application needs specific characteristics:

| Parameter | Why It Matters | Ideal |
|---|---|---|
| **Latency** | Signal fires → LLM must respond fast. 10+ seconds = price moves away | < 3 seconds response time |
| **JSON reliability** | Must return valid, parseable JSON every time | Supports `response_format: json_object` |
| **Reasoning quality** | Must correctly interpret chart patterns, volume data, macro context | Strong on quantitative reasoning |
| **Context window** | 200 bars × ~50 tokens/bar + daily bars + prompt = ~15K-20K tokens | >= 32K tokens |
| **Cost per call** | ~2-5 calls/day = low volume, but matters over months | < $0.01 per call ideal |
| **Knowledge cutoff** | Needs to know economic calendar, recent macro events | More recent = better |
| **Temperature control** | Need deterministic-ish responses for consistency | Supports temperature=0 or low |
| **Hallucination rate** | Cannot hallucinate "FOMC today" when there isn't one | Lower = better |

### 4.5 LLM Candidates Comparison

| Model | Latency | JSON | Reasoning | Context | Cost/call* | Notes |
|---|---|---|---|---|---|---|
| **Grok 4-1 Fast** | ~2-3s | ✅ json_object | Strong | 131K | ~$0.002 | Current choice. Fast, cheap, good reasoning |
| **Grok 4-1** (full) | ~5-8s | ✅ | Stronger | 131K | ~$0.005 | Better reasoning but slower |
| **GPT-4o** | ~2-4s | ✅ json_object | Strong | 128K | ~$0.005 | Well-tested, reliable JSON |
| **GPT-4o-mini** | ~1-2s | ✅ json_object | Good | 128K | ~$0.001 | Fastest, cheapest, slightly less reasoning |
| **Claude Sonnet 4** | ~3-5s | ✅ | Very strong | 200K | ~$0.005 | Best reasoning, larger context |
| **Claude Haiku** | ~1-2s | ✅ | Good | 200K | ~$0.001 | Fast and cheap, good for filtering |
| **Gemini 2.5 Flash** | ~1-3s | ✅ | Good | 1M | ~$0.001 | Huge context, fast |

*Estimated cost per typical 15K-token request + 500-token response

### 4.6 Decision: Grok (Confirmed), Designed for Swappability

**Primary:** Grok 4-1 Fast — confirmed by user. API key already available, fast, cheap, JSON mode works well.

**Design principle:** Abstract the LLM behind an interface so swapping models requires changing one config value:

```python
class LLMFilter(Protocol):
    async def evaluate_signal(self, context: SignalContext) -> FilterDecision: ...
```

Implementations: `GrokFilter`, `OpenAIFilter`, `ClaudeFilter` — all share the same interface. This lets you A/B test models later without changing any other code.

### 4.7 What ELSE Can the LLM Do?

Beyond basic signal approval, the LLM can provide value in several ways:

#### A. Higher Timeframe Trend Analysis

**How:** Send daily bars (last 30 days) alongside the 1-min signal. Ask:
- "Is the daily trend aligned with this breakout direction?"
- "Are we near a major daily support/resistance level?"
- "Is price in the upper/lower range of the daily ATR?"

**Value:** A 1-min long breakout against a strong daily downtrend has lower probability. The LLM can weigh this.

#### B. Session/Time-of-Day Context

**How:** Include current UTC time and session label (Asian, London, NY, overlap).

**What LLM knows:**
- London/NY overlap (13:00-17:00 UTC) has highest FX volume → breakouts more reliable
- Asian session (00:00-08:00 UTC) is quieter → more false breakouts on EURUSD
- Gold (XAUUSD) moves most during London open and US data releases

#### C. Economic Calendar Awareness

**How:** Two approaches:
1. **LLM's training knowledge** — Grok knows the general FOMC/NFP/CPI schedule patterns. Not specific dates but can reason about "first Friday of month = NFP risk"
2. **External calendar feed** (future enhancement) — Pull today's economic events from an API (e.g., ForexFactory, Investing.com) and include in the prompt: "Events today: FOMC Minutes at 14:00 UTC, Initial Jobless Claims at 12:30 UTC"

**Value:** Avoid entering 5 minutes before NFP when volatility will spike unpredictably.

#### D. Pattern Quality Scoring

**How:** Send the last 200 bars and ask the LLM to assess:
- "Is this consolidation clean (tight range, clear walls) or messy (overlapping wicks, no clear structure)?"
- "How many times has this level been tested? More tests = weaker level"
- "Is volume declining during consolidation (bullish) or increasing (distribution)?"

**Value:** Not all Darvas boxes are equal. A clean 50-bar consolidation with declining volume is higher quality than a choppy 15-bar range.

#### E. Multi-Signal Correlation

**How:** If multiple instruments have signals at the same time, send all of them:
- "XAUUSD long breakout AND EURUSD long breakout — is this a USD weakness move?"
- "XAUUSD long breakout BUT USDJPY also breaking up — conflicting signals, reduce confidence"

**Value:** Cross-instrument confirmation or contradiction.

#### F. Post-Trade Review (Learning Loop)

**How:** After each trade closes, send the result to the LLM:
- "You approved this long at 2046.10. It hit the stop at 2037.80. Here's what happened in the bars after entry. What went wrong? Should we adjust any filters?"

**Value:** The LLM doesn't "learn" in the ML sense, but it can generate insights that you (the human) can use to refine parameters or add new filters. This is a **human-in-the-loop learning cycle**, not autonomous adaptation.

---

## 5. Data Flow — Complete Picture

```
IBKR Live Stream
       ↓
  BarAggregator (from v8)
  Ticks → 1-min OHLCV bars with buy/sell classification
       ↓
  RollingBuffer (from v8)
  Maintains last N bars in memory
       ↓
  DarvasDetector (NEW)
  - Tracks new highs/lows
  - Forms box top (N bars without new high)
  - Forms box bottom (lowest low during top formation)
  - Detects breakout (close above top or below bottom)
  - Most bars: no signal → O(1) work, no LLM cost
       ↓
  On breakout signal:
       ↓
  ImbalanceClassifier (from v8)
  - Computes buy_ratio at breakout
  - Classifies: CONFIRMING / DIVERGENT / INDETERMINATE
       ↓
  SignalContext packager
  - Last 200 bars (1-min)
  - Last 30 daily bars (if available)
  - Darvas box parameters
  - Imbalance data
  - Current time, session, instrument info
       ↓
  LLM Filter (Grok / swappable)
  - Evaluates bigger picture
  - Returns: approved + confidence + entry/stop/target + reasoning
  - ~2-3 second latency
       ↓
  If approved AND confidence >= threshold:
       ↓
  TradeManager (from v8)
  - Market entry order
  - SL stop order (at box bottom or LLM-suggested stop)
  - Fill tracking + slippage computation
  - Commission tracking
  - Position reconciliation
       ↓
  Position Monitoring (from v8)
  - SL hit → close position, log trade
  - Time stop → close position, log trade
  - Shutdown → close position, cancel orders
  - Daily limits → force close if exceeded
       ↓
  Trade CSV Logger (from v8)
  - Records all trades with fills, PnL, commissions, exit reason
```

---

## 6. Project Structure

```
C:\ibkr_grok-_wing_agent\v11\
├── config/
│   ├── strategy_config.py       # DarvasConfig (frozen): box params, imbalance params
│   └── live_config.py           # IBKR connection, LLM settings, safety limits
├── core/
│   ├── types.py                 # Bar, DarvasBox, BreakoutSignal, FilterDecision
│   ├── darvas_detector.py       # Darvas box formation + breakout detection
│   └── imbalance_classifier.py  # Buy/sell volume ratio (reuse from v8)
├── llm/
│   ├── base.py                  # LLMFilter protocol (interface)
│   ├── grok_filter.py           # Grok implementation
│   ├── prompt_templates.py      # Signal evaluation prompt
│   └── models.py                # Pydantic: SignalContext, FilterDecision
├── execution/
│   ├── ibkr_connection.py       # IBKR connection manager (from v8)
│   ├── trade_manager.py         # Bracket orders, fills, cleanup (from v8)
│   └── bar_aggregator.py        # Tick → bar aggregation (from v8)
├── live/
│   ├── live_engine.py           # RollingBuffer + orchestration
│   ├── run_live.py              # Main loop
│   └── watchdog.py              # Auto-restart (from v8)
├── backtest/
│   ├── runner.py                # Replay historical bars through DarvasDetector
│   ├── run_backtest.py          # CLI entry point
│   └── param_sweep.py           # Grid search for Darvas parameters
├── tests/
│   ├── test_darvas.py           # Darvas box formation, breakout detection
│   ├── test_imbalance.py        # Volume classification (reuse from v8)
│   ├── test_trade_manager.py    # Bracket orders, position reconciliation
│   └── test_llm_filter.py       # Schema validation, approval logic
├── ARCHITECTURE.md              # Center/edge documentation
├── requirements.txt
└── README.md

C:\ibkr_grok-_wing_agent\grok_logs\   # Shared across projects
└── YYYY-MM-DD_HHMMSS_{instrument}_{direction}.json  # Every LLM request/response pair
```

---

## 7. Center vs Edge Elements (V11)

### Center (protect — changes require explicit approval)

| Element | Why | Location |
|---|---|---|
| Darvas box breakout rules | Defines when signals fire. Wrong logic = bad trades or missed signals | `darvas_detector.py` |
| Imbalance classification | Confirms/denies breakout quality. Wrong threshold = filter failure | `imbalance_classifier.py` |
| Trade execution + bracket orders | Real money. Entry + SL must be atomic | `trade_manager.py` |
| Position reconciliation | Prevents orphaned positions or double entries | `trade_manager.py` |
| LLM response schema | Contract between LLM output and execution. Invalid = silent misbehavior | `llm/models.py` |
| Safety limits | Daily trade cap, daily loss limit, confidence threshold | `live_config.py` |
| Fill tracking + SL management | Ensures positions have stops, tracks actual vs expected fills | `trade_manager.py` |

### Edge (move freely)

| Element | Why | Location |
|---|---|---|
| LLM prompt text | Wording can change without affecting signal logic or execution | `prompt_templates.py` |
| LLM model choice | Swappable behind interface. Any model that returns valid JSON works | `live_config.py` |
| Logging format | Cosmetic | Various |
| Bar count for LLM context | How many bars to send (100 vs 200 vs 500) — doesn't affect signals | `grok_filter.py` |
| Daily bar fetching | Optional enrichment for LLM. Missing = slightly less context, no crash | `live_engine.py` |
| CSV trade log format | Reporting only | `run_live.py` |
| Watchdog restart params | Operational tuning | `watchdog.py` |

---

## 8. Resolved Decisions

| # | Question | Decision | Date | Notes |
|---|---|---|---|---|
| 1 | Instrument scope | **XAUUSD + EURUSD + USDJPY** from day one | 2026-04-05 | All three pairs supported at launch |
| 2 | Darvas initial parameters | See §2 "Confirmed Initial Parameters" table | 2026-04-05 | top=15, bottom=15, width=0.3 ATR, duration=20, confirm=3 — tunable via backtest |
| 3 | LLM confidence threshold | **75** | 2026-04-05 | Higher than swing agent (70) because 1-min bars produce more noise; tunable |
| 4 | Economic calendar | **Start with Grok training knowledge**, add external API later if insufficient | 2026-04-05 | IBKR has no calendar API; external options: TradingEconomics, FCS API |
| 5 | Short trades | **Both long and short** from start | 2026-04-05 | Enabled for all instruments |
| 6 | LLM choice | **Grok 4-1 Fast** (confirmed) | 2026-04-05 | Designed for swappability via LLMFilter protocol |
| 7 | Project location | **`C:\ibkr_grok-_wing_agent\v11\`** | 2026-04-05 | Confirmed |
| 8 | Backtest approach | **Two-stage hybrid** (see §2 "Hybrid Backtest Approach") | 2026-04-06 | Stage 1: grid search without LLM (fast, deterministic). Stage 2: best params + Grok filter (validates LLM value-add) |
| 9 | Multiple boxes | **One box at a time** (current implementation) | 2026-04-06 | Resolved during build — detector resets to SEEKING_TOP after breakout. Nested boxes deferred to future iteration |
| 10 | Box invalidation | **Price above confirmed top during bottom formation → restart** | 2026-04-06 | Resolved during build — see `darvas_detector.py _process_confirming_bottom()` |
| 11 | Entry drift / slippage ceiling | **Abort trade if price drifts > 0.5 ATR during LLM latency** | 2026-04-06 | Implemented in `live_engine.py _handle_signal()`. Param: `max_entry_drift_atr` in LiveConfig |
| 12 | HTF direction filter | **60-min SMA(50) direction alignment** | 2026-04-05 | Only LONG when price > 60-min SMA(50), SHORT when price < SMA. Turns OOS from -0.044 AvgR to +0.176 AvgR. See §10. |
| 13 | MTF box alignment | **Not viable in current form — too restrictive** | 2026-04-05 | Macro box rarely active at micro breakout time. SMA filter achieves same goal more simply. |
| 14 | Multi-strategy integration | **Separate strategy engines, shared IBKR + risk manager** | 2026-04-06 | V6 ORB (XAUUSD) + V11 Darvas (EURUSD) + V11 4H Retest (EURUSD). Single process, one IBKR connection, unified daily loss limit. V6 code untouched — adapter pattern. |
| 15 | 4H Level Retest | **pb=10-30 retest mode, lb=10 rb=10, exp=72h, merge=0.5 pips** | 2026-04-06 | Nearly triples AvgR from +0.049 to +0.135 OOS. SL at level ± 0.3 ATR. See §12. |
| 16 | LLM role | **Optional enhancement, not required for core edge** | 2026-04-06 | Mechanical system is profitable without Grok. LLM adds value for news filtering and pattern quality, but core edge is SMA + volume + structural signals. |

---

## 9. External Review Findings (2026-04-06)

An external LLM review of the design docs raised three critiques. Assessment and actions below.

### Critique #1: Latency Gap (🔴 Critical — FIXED)

**Problem:** Grok takes 2–3 seconds to respond. On XAUUSD 1-min bars, price can move $2–$4 during that time. By the time Grok returns `approved: true`, the breakout price may be stale.

**Response:** Valid. Implemented a **slippage ceiling** (`max_entry_drift_atr = 0.5` in LiveConfig).

**How it works:**
1. Signal fires → breakout_price recorded
2. Grok is called (2–3 sec latency)
3. Grok approves → check `abs(current_price - breakout_price)` vs `0.5 × ATR`
4. If drift exceeds ceiling → **trade aborted**, logged as `ENTRY DRIFT ABORT`

**Location:** `live_engine.py _handle_signal()`, after LLM approval check, before `enter_trade()`.

**Why 0.5 ATR?** It allows for normal post-breakout follow-through (expected) while catching runaway moves where the risk/reward has already degraded. Tunable in LiveConfig.

### Critique #2: Calendar Hallucination (🟡 Valid — Deferred to Pre-Live)

**Problem:** Grok knows the *general schedule* of FOMC, NFP, CPI but doesn't know about moved meetings, surprise announcements, or flash events.

**Response:** Valid concern. Already scoped as Decision #4: "Start with Grok training knowledge, add external API later."

**Assessment:**
- For **paper trading + backtesting**: Grok's approximate knowledge is sufficient
- For **live trading with real money**: external calendar feed should be wired up first
- Candidate APIs: ForexFactory (scraping), TradingEconomics, FCS API, AlphaVantage
- The `SignalContext` schema can be extended with an `upcoming_events` field

**Priority:** Must be done before transitioning from paper to live.

### Critique #3: Zoomed-In Bias (🟡 Valid — Partially Addressed)

**Problem:** 200 × 1-min bars = ~3.3 hours. Grok can't see that a bullish 1-min breakout is hitting a 4H resistance or Daily 200-EMA.

**Response:** Valid. The schema already has `daily_bars: Optional[List[BarData]]` in `SignalContext` — designed for exactly this. The data source isn't wired up yet.

**What's needed:**
1. Fetch daily OHLC bars from IBKR historical data API (or external source)
2. Optionally compute key levels (200-EMA, major S/R) and include as summary fields
3. Wire into `_build_signal_context()` in `live_engine.py`

**Priority:** Should be done before live trading. The schema is ready; only the data pipe is missing.

### Critique Assessment: Watchlist Strategy (Noted — Out of Scope)

The reviewer noted the Swing Agent's static watchlist as the "biggest bottleneck for ROI." This applies to the Swing Agent (Project 1), not V11. V11 trades pre-defined FX/commodity pairs, not equities. Noted but no action needed for V11.

---

---

## 10. HTF Direction Filter — DECIDED, Ready to Integrate (2026-04-05)

### The Problem

The 1-min Darvas breakout system achieved strong in-sample results but failed out-of-sample. The core issue: 1-minute consolidations don't carry structural significance — a 15-minute "box" could just be a lunch break, not meaningful supply/demand.

### The Solution: 60-min SMA(50) Direction Filter

**Rule:** Only take LONG breakouts when the current price is above the 60-minute SMA(50). Only take SHORT breakouts when below.

**Why it works:** A 60-min SMA(50) represents ~50 hours (~2 trading days) of price history. If a 1-min long breakout fires but the medium-term trend is down, it's likely a counter-trend fake-out. The SMA filter eliminates these.

**Look-ahead prevention:** The SMA value used is from the **previous completed** 60-min bar, not the current in-progress bar. This is critical — using the current bar's SMA would leak future information.

### OOS-Validated Results (EURUSD Config B + CONFIRMING + Trail10@60 + R:R=2.0)

| Config | Period | Trades | WR% | AvgR | PnL |
|---|---|---|---|---|---|
| Without SMA filter | IS 2024-2026 | 40 | 60.0% | +0.570 | +1.2760 |
| Without SMA filter | OOS 2018-2023 | 127 | 37.0% | -0.044 | -0.1877 |
| **With 60-min SMA(50)** | **IS 2024-2026** | **24** | **62.5%** | **+0.729** | **+1.3141** |
| **With 60-min SMA(50)** | **OOS 2018-2023** | **63** | **46.0%** | **+0.176** | **+0.8767** |

### Full Investigation (5 Approaches Tested)

| # | Approach | OOS Result | Verdict |
|---|---|---|---|
| 1 | **HTF SMA direction filter** | +0.176 AvgR, 46% WR | **WINNER — integrate into pipeline** |
| 2 | Darvas on 5-min bars | +0.188 AvgR, 56.5% WR (23 trades) | Promising alternative |
| 3 | Session/time-of-day filter | +0.091 AvgR Asian, -0.226 London | Modest, combine with SMA |
| 4 | ADX trend strength | +0.042 AvgR (DI alignment only) | Weaker than SMA |
| 5 | MTF box alignment | No combo reached 15 OOS trades | Too restrictive, abandoned |

### Implementation Plan

**For backtest (`simulator.py`):**
- Compute 60-min resampled bars from input bars
- Compute SMA(50) on the resampled bars
- At each signal, look up the previous completed 60-min bar's SMA
- If signal direction doesn't align with price vs SMA → skip signal

**For live (`live_engine.py`):**
- Maintain a rolling buffer of 60-min bars (resample from 1-min stream)
- Compute SMA(50) incrementally
- Check alignment before sending signal to LLM

**New config params (to add to StrategyConfig):**
- `htf_sma_bar_minutes: int = 60` — HTF bar period for SMA computation
- `htf_sma_period: int = 50` — SMA lookback in HTF bars
- `htf_sma_enabled: bool = True` — enable/disable the filter

### Utility Module

All HTF computation functions are in `v11/backtest/htf_utils.py`:
- `resample_bars()`, `resample_sessions()` — bar resampling
- `compute_sma()` — SMA computation
- `compute_adx()` — ADX computation (for future use)
- `build_htf_lookup()`, `get_htf_value_at()` — O(1) lookup with look-ahead prevention
- `collect_signals()`, `simulate_trades()`, `compute_stats()` — shared analysis pipelines

Investigation scripts in `v11/backtest/investigate_*.py`. Full plan in `docs/HTF_INVESTIGATION_PLAN.md`.

---

## 11. Multi-Strategy Portfolio Architecture (2026-04-06)

### The Vision

Instead of a single signal generator, V11 runs **three independent strategies across two instruments** from one process. This diversifies across signal types (consolidation breakout vs level retest vs range breakout) and instruments (EURUSD + XAUUSD).

### Architecture Diagram

```
MultiStrategyRunner
│
├── Shared Infrastructure
│   ├── IBKRConnection (one gateway, multi-instrument: EURUSD + XAUUSD)
│   ├── RiskManager
│   │   ├── Per-strategy daily loss limit
│   │   ├── Combined portfolio daily loss limit
│   │   └── Max simultaneous positions (1 per instrument)
│   └── TradeLogger (unified CSV, all strategies)
│
├── EURUSD Pipeline (shared data feed)
│   ├── BarAggregator (tick → 1-min bars)
│   ├── ImbalanceClassifier (volume flow)
│   ├── HTF SMA Computer (60-min SMA(50), incremental)
│   │
│   ├── Strategy A: DarvasBreakout
│   │   ├── DarvasDetector (tc=20, bc=12, mxW=3.0, brk=3)
│   │   ├── Signal filter: SMA direction + CONFIRMING volume
│   │   ├── SL: box boundary, TP: entry + risk × 2.0
│   │   ├── Trail10@60 SL management
│   │   └── TradeManager (bracket orders)
│   │
│   └── Strategy B: 4HLevelRetest
│       ├── SwingLevelDetector (4H bars, lb=10, rb=10)
│       ├── Level tracker (72h expiry, 0.5-pip merge)
│       ├── Retest detector (break → pb 10-30 bars → rebreak)
│       ├── Signal filter: SMA direction + CONFIRMING volume
│       ├── SL: level ± 0.3 ATR, TP: entry + risk × 2.0
│       └── TradeManager (bracket orders)
│
└── XAUUSD Pipeline (separate data feed)
    └── Strategy C: V6 ORBAdapter
        ├── ORBStrategy (imported from v6, untouched)
        ├── LiveMarketContext (velocity, Asian range, gap)
        └── IBKRExecutionEngine (XAUUSD contract)
```

### What's Shared vs Independent

| Layer | Shared? | Notes |
|---|---|---|
| IBKR connection | **Shared** | One IB Gateway, multiple contracts |
| Risk manager | **Shared** | Combined daily loss limit |
| Trade logging | **Shared** | Unified CSV for all strategies |
| Bar aggregation | **Per-instrument** | EURUSD and XAUUSD have separate feeds |
| SMA computation | **Per-instrument** | EURUSD only for now |
| Volume classifier | **Per-instrument** | EURUSD only |
| Signal generation | **Per-strategy** | Each strategy has its own detector |
| State machine | **Per-strategy** | No cross-strategy state |
| Trade management | **Per-strategy** | Independent bracket orders |

### Conflict Resolution

- **Same instrument, two signals at once:** EURUSD could have Darvas and 4H signals simultaneously. Rule: max 1 position per instrument. First signal to fire gets the slot; second is rejected until first trade closes.
- **Cross-instrument positions:** EURUSD and XAUUSD can be open simultaneously. Risk manager tracks combined exposure.
- **Daily loss limit:** If combined losses across all strategies exceed the limit, all strategies pause for the day.

### V6 ORB Adapter Design

V6 code lives in `C:\nautilus0\v6_orb_refactor\` and must not be modified. The adapter:

1. Imports `ORBStrategy`, `LiveMarketContext`, `IBKRExecutionEngine` from v6
2. Connects v6's `IBKRExecutionEngine` to the shared `IBKRConnection`
3. Feeds XAUUSD ticks from the shared connection into v6's `LiveMarketContext`
4. Registers v6's fill callbacks with the shared `RiskManager` and `TradeLogger`
5. Handles daily reset (`strategy.reset_for_new_day()`) from the runner's clock

The adapter is thin — it translates between V11's event model and V6's tick-driven interface without touching V6 internals.

---

## 12. 4H Swing Level Detector — Design (2026-04-06)

### What It Does

Detects significant support/resistance levels from 4-hour price data, then monitors 1-minute bars for breakout → pullback → rebreak patterns at those levels.

### Level Detection

**Input:** 4H OHLC bars (resampled from 1-min stream in live, pre-computed in backtest).

**Swing detection algorithm:**
- A **swing high** is a bar whose high is higher than the highs of `lb` bars before it AND `rb` bars after it
- A **swing low** is a bar whose low is lower than the lows of `lb` bars before it AND `rb` bars after it
- Parameters: `lb=10` (left bars), `rb=10` (right bars) — requires 10 bars (~40 hours) on each side

**Level management:**
- Each swing high/low becomes a **level** with a timestamp
- Levels expire after `expiry_hours=72` (3 days) — old levels lose relevance
- Levels within `merge_distance=0.5 pips` (0.00005) of each other are merged — prevents duplicate levels from similar swings
- Active levels are stored in a list, pruned on each new 4H bar

### Retest Detection (The Breakthrough)

Direct level breakouts are low quality (+0.049 AvgR OOS). The key insight: **retests nearly triple the edge** because they confirm the level matters.

**Retest state machine per level:**

```
WATCHING → BROKEN → RETESTING → REBREAK (entry signal)
                  ↘ EXPIRED (timeout)
```

1. **WATCHING:** Level is active. Monitor 1-min bars.
2. **BROKEN:** 1-min bar closes beyond the level. Record break time. **Do NOT enter.**
3. **RETESTING:** Price pulls back toward the level (crosses back within `pullback_zone`). Timer starts.
   - Must happen within `min_pullback_bars=10` to `max_pullback_bars=30` after break
4. **REBREAK:** 1-min bar closes beyond the level again. **This is the entry signal.**
   - Must happen within the `max_pullback_bars` window from the initial break
5. **EXPIRED:** If pullback or rebreak doesn't happen in time, level state resets.

### Parameters

| Parameter | Value | Rationale |
|---|---|---|
| `lb` / `rb` | 10 | Swing detection: 10 bars (~40h) on each side = significant level |
| `expiry_hours` | 72 | 3 days — beyond this, levels lose market memory |
| `merge_distance` | 0.00005 (0.5 pips) | Prevents duplicate levels from similar swings |
| `min_pullback_bars` | 10 | Wait at least 10 min for pullback (avoid immediate noise) |
| `max_pullback_bars` | 30 | Max 30 min for full break → rebreak cycle |
| `cooldown_bars` | 60 | After entry at a level, ignore that level for 60 bars |
| `sl_atr_offset` | 0.3 | SL placed 0.3 ATR beyond the level |
| `rr_ratio` | 2.0 | TP at entry + risk × 2.0 |

### SL/TP Calculation

```python
# For a LONG rebreak (price breaks above level, pulls back, rebreaks up)
entry = rebreak_bar.close
sl = level_price - sl_atr_offset * atr  # Below the level
risk = entry - sl
tp = entry + risk * rr_ratio

# For a SHORT rebreak (price breaks below level, pulls back, rebreaks down)
entry = rebreak_bar.close
sl = level_price + sl_atr_offset * atr  # Above the level
risk = sl - entry
tp = entry - risk * rr_ratio
```

### Integration with Shared Pipeline

The 4H Level strategy shares infrastructure with Darvas:
- Same 1-min bar feed from `BarAggregator`
- Same `ImbalanceClassifier` for CONFIRMING volume check
- Same `HTF SMA Computer` for direction alignment
- Same `TradeManager` for bracket orders on EURUSD

The `SwingLevelDetector` processes 4H bars (resampled from 1-min) and maintains the level list. The `RetestDetector` monitors 1-min bars against active levels and emits entry signals when the full break → pullback → rebreak pattern completes.

### Module Structure (To Build)

```
v11/core/
├── level_detector.py      # SwingLevelDetector: 4H level detection + management
├── retest_detector.py     # RetestDetector: break → pullback → rebreak state machine
```

### OOS-Validated Results

| Config | OOS/yr | OOS WR% | OOS AvgR | IS AvgR |
|---|---|---|---|---|
| Direct (no retest) + SMA + CONF | 108.7 | 40.2% | +0.049 | +0.008 |
| **Retest pb=10-30 + SMA + CONF** | **22.3** | **39.6%** | **+0.135** | **+0.230** |

Year-by-year OOS (direct mode, 4 of 6 years positive):
- 2018: -0.048, 2019: +0.089, 2020: -0.107, 2021: +0.159, 2022: +0.151, 2023: +0.085

---

### Still Open (Technical)

- ~~Integrate SMA filter into main simulator and live engine~~ → **✅ COMPLETE** (Phase 1 done: `core/htf_sma_filter.py` + `simulator.py` + `live_engine.py` + 22 tests)
- ~~Test SMA filter on XAUUSD and USDJPY~~ → **Resolved**: EURUSD is the only viable instrument for V11 signals
- ~~Build 4H level detector module~~ → **✅ COMPLETE** (Phase 2 done: `core/level_detector.py` + `SwingLevel`/`LevelType` types + 23 tests)
- ~~Build retest detector module~~ → **✅ COMPLETE** (Phase 3 done: `core/retest_detector.py` + `RetestSignal`/`RetestState` types + 27 tests)
- **Build MultiStrategyRunner** — design in §11, code not yet written (Phase 4)
- **V6 ORB adapter** — import from nautilus0 or copy? Decision needed (Phase 5)
- **Combined risk management** — how to handle simultaneous XAUUSD + EURUSD positions
- **Daily bar context for LLM** (Critique #3) — SignalContext supports daily bars but no data source wired up. Priority: before live trading
- **External economic calendar API** (Critique #2) — Grok's training knowledge is approximate. Priority: before live trading with real money
