# Design: ORB LLM Gate

**Date:** 2026-04-07
**Status:** Approved
**Scope:** Add Grok LLM evaluation to V6 ORB strategy before bracket placement

---

## Problem

The V6 ORB strategy is purely mechanical: Asian range calc, velocity filter, bracket placement, wait for fill. It has no contextual awareness. Today's first live trade (SHORT XAUUSD @ 4615.18) entered during a $48 Asian range (3-4x normal) on a tariff-news-driven gap day. An LLM with macro context, session awareness, and recent price action visibility could have flagged this as a low-quality setup.

Darvas and 4H Retest strategies already have LLM gates. ORB is the only strategy without one.

## Decision Point

The LLM gate sits in the ORB adapter's `on_price` flow, **after risk gate, before bracket placement**:

```
IDLE -> range calc -> RANGE_READY -> risk gate -> LLM gate -> velocity check -> ORDERS_PLACED
```

If the LLM rejects: state -> DONE_TODAY, no brackets placed, no exchange interaction.
If the LLM approves: proceed to velocity check and bracket placement as normal.

## LLM Evaluation Context

The LLM receives an `ORBSignalContext` containing:

### Range data
- Asian range: high, low, size (absolute), size as % of price
- Range size relative to recent average: `today_range / avg(last_10_daily_ranges)`
- This tells Grok whether today's range is normal or extreme

### Price action
- Recent 1-min bars: last 360 bars (6 hours, covers full Asian session)
  - Format: timestamp, open, high, low, close, buy_volume, sell_volume, tick_count
- Daily bars: last 10 days
  - Format: date, open, high, low, close
- These give Grok visibility into macro trend, recent momentum, and volatility regime

### Session and timing
- Current UTC hour and session label (ASIAN_CLOSE, LONDON, LONDON_NY_OVERLAP, NY)
- Day of week
- Whether Wednesday (normally skipped by ORB config)

### Instrument
- Instrument name (XAUUSD)
- Current mid price
- Distance from range high and range low (in price and as % of range)

## LLM Response

Uses the existing `LLMResponse` Pydantic model (same as Darvas/Retest):
- `approved`: bool -- should we place brackets?
- `confidence`: int (0-100)
- `reasoning`: str -- why approve/reject
- `risk_flags`: list[str]
- `entry`, `stop`, `target`: float -- **ignored for ORB** (brackets are mechanical)

Confidence threshold: same as global `llm_confidence_threshold` (default 75).

## Timeout and Failure Handling

1. First attempt: standard timeout (default 10s from LiveConfig)
2. On timeout: retry once with 5s timeout
3. On second failure: log warning, **proceed mechanically** (place brackets anyway)

Rationale: the ORB edge exists without the LLM. The LLM is an enhancement filter. Missing a good trade because Grok is slow is worse than occasionally entering an unfiltered trade.

## ORB Prompt Design

System prompt establishes Grok as an ORB trade evaluator for XAUUSD. Key evaluation criteria:

1. **Macro regime**: Is gold trending, ranging, or in a news-driven spike? ORB works best in normal trending conditions, not extreme gap/spike days.
2. **Range quality**: Is today's range size normal relative to recent sessions? Abnormally wide ranges suggest extreme volatility where breakouts are less reliable.
3. **Session dynamics**: Is the breakout likely to have follow-through given the current session? London open tends to extend Asian moves; NY can reverse them.
4. **Directional momentum**: Has price been moving one way for hours? A breakout in the direction of existing momentum has higher follow-through probability.

The prompt explicitly tells Grok it cannot modify entry/stop/target -- only approve or reject.

## Components to Build

### 1. `ORBSignalContext` (new Pydantic model)
- File: `v11/llm/models.py`
- Fields: range_high, range_low, range_size, range_size_pct, range_vs_avg, current_price, distance_from_high, distance_from_low, session, day_of_week, instrument, recent_bars (list of BarData), daily_bars (list of DailyBarData)
- New `DailyBarData` model: date, open, high, low, close

### 2. ORB prompt template
- File: `v11/llm/prompt_templates.py`
- New function: `build_orb_signal_prompt(context_json: str) -> str`
- New constant: `ORB_SYSTEM_PROMPT`

### 3. `evaluate_orb_signal` method on GrokFilter
- File: `v11/llm/grok_filter.py`
- Uses ORB_SYSTEM_PROMPT + build_orb_signal_prompt
- Same return type: FilterDecision
- Same logging pattern (JSON to grok_logs/)

### 4. LLM gate in ORB adapter
- File: `v11/live/orb_adapter.py`
- New method: `_evaluate_orb_signal(now) -> bool`
  - Builds ORBSignalContext from adapter state
  - Calls llm_filter.evaluate_orb_signal()
  - Returns True (approved) or False (rejected)
  - Handles timeout: retry once, then proceed
- Called in `on_price()` when state == RANGE_READY, after risk gate passes
- On rejection: `self._strategy.state = StrategyState.DONE_TODAY`

### 5. Daily bar fetcher
- File: `v11/live/orb_adapter.py`
- Fetch 10 daily bars from IBKR on daily reset (via conn reference)
- Store as `self._daily_bars: list[Bar]`
- Compute `self._avg_daily_range` from last 10 days for range_vs_avg ratio

### 6. LLM filter reference in adapter
- Currently adapter has no reference to the LLM filter
- Add `llm_filter` parameter to ORBAdapter.__init__()
- Wire it in MultiStrategyRunner.add_orb_strategy()
- When llm_filter is None or PassthroughFilter, skip the gate (mechanical mode)

## What Does NOT Change

- V6 frozen code (v11/v6_orb/) -- no modifications
- Range calculation logic
- Velocity filter
- Bracket placement mechanics
- SL/TP computation
- Fill handling
- Risk manager integration
- Darvas/Retest LLM flow (separate context, separate prompts)

## Tests

1. LLM approval -> brackets placed (adapter integration)
2. LLM rejection -> DONE_TODAY, no brackets
3. LLM timeout + retry success -> brackets placed
4. LLM double timeout -> proceed mechanically (brackets placed)
5. ORBSignalContext serialization roundtrip
6. Daily bar fetch and range_vs_avg computation
7. No-LLM mode (PassthroughFilter) -> gate skipped entirely
8. Confidence below threshold -> rejected
