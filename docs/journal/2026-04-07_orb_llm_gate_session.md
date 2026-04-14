# Session: ORB LLM Gate
**Date:** 2026-04-07

## What was built
Added Grok LLM evaluation gate to the V6 ORB strategy, gating bracket placement on contextual approval. The gate evaluates macro regime, session dynamics, range quality, and recent price action before allowing ORB brackets to be placed.

### New/Modified files
- **`v11/llm/models.py`** -- Added `DailyBarData` and `ORBSignalContext` Pydantic models
- **`v11/llm/prompt_templates.py`** -- Added `ORB_SYSTEM_PROMPT` and `build_orb_signal_prompt()`
- **`v11/llm/grok_filter.py`** -- Added `evaluate_orb_signal()` with retry+mechanical fallback
- **`v11/llm/passthrough_filter.py`** -- Added `evaluate_orb_signal()` auto-approve for --no-llm mode
- **`v11/live/orb_adapter.py`** -- Added `_evaluate_orb_signal()`, LLM gate in on_price flow, daily bars storage
- **`v11/live/multi_strategy_runner.py`** -- Passes `llm_filter` and `llm_confidence_threshold` to ORBAdapter
- **`v11/live/run_live.py`** -- Fetches 10 daily bars from IBKR for ORB LLM context
- **`v11/tests/test_orb_llm_gate.py`** -- 10 new tests for the ORB LLM gate
- **`v11/tests/test_llm_models.py`** -- 4 new tests for ORB models and prompt

## Design decisions
1. **Gate at RANGE_READY, before brackets** -- No exchange interaction on rejected signals. Matches Darvas/Retest pattern.
2. **Retry once on timeout, then proceed mechanically** -- ORB edge exists without LLM. Better to enter unfiltered than miss valid trades due to API latency.
3. **evaluate_orb_signal is separate from evaluate_signal** -- Different context (ORBSignalContext vs SignalContext), different prompt (ORB_SYSTEM_PROMPT), different fallback behavior (mechanical vs reject).
4. **Daily bars fetched at startup** -- 10 daily bars from IBKR, stored on adapter, used for range_vs_avg computation.
5. **Gate runs once per day** -- `_llm_evaluated_today` flag prevents repeated calls on each tick.

## Test results
- **277 total tests passing** (263 previous + 14 new)
- Zero regressions

## How to use
- With LLM: `python -m v11.live.run_live --live` (requires XAI_API_KEY in .env)
- Without LLM: `python -m v11.live.run_live --live --no-llm` (PassthroughFilter auto-approves)

## Next session should
- Paper trade with LLM enabled for 2-3 days, compare ORB outcomes
- Review Grok's reasoning in grok_logs/ JSON files
- Consider adding recent 1-min bars to ORBSignalContext (currently empty list)
- Consider wiring daily bar refresh on daily reset (currently only at startup)
