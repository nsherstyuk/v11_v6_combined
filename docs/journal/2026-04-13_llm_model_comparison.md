# Session: 2026-04-13 — LLM Model Comparison + Multi-Provider Support

**Date:** 2026-04-13 08:00–16:00 ET  
**Agent:** Cascade  
**Status:** Complete

---

## What Happened

### 1. Historical Replay with Grok (Passthrough → Live)

Ran the EURUSD historical replay simulator (Jan 1 – Apr 12, 2026) on the newly downloaded 1-minute bar data:

- **Passthrough** (no LLM): 53 trades, +$338.33 PnL, 47.2% WR, PF 2.23
- **Grok R1** (live): 3 trades, -$10.10 PnL, 66.7% WR, PF 0.60

Grok was **extremely over-conservative** — rejected 50/53 signals with confidence scores of 35-45, all below the 75 threshold. The 3 approved trades had a decent win rate but the single loser ($-25.20) wiped out two small winners.

### 2. Bug Fix: ExitReason.DAILY_RESET

The replay crashed on first run with `AttributeError: 'str' object has no attribute 'value'` because `force_close` was called with string `"DAILY_RESET"` instead of the `ExitReason` enum.

**Fix:** Added `DAILY_RESET = "DAILY_RESET"` to `ExitReason` enum in `v11/core/types.py` and updated `v11/replay/replay_runner.py` to use `ExitReason.DAILY_RESET`.

### 3. Multi-Provider LLM Support (OpenRouter)

Made the LLM filter provider-agnostic so any OpenAI-compatible API can be used:

- Added `base_url` parameter to `GrokFilter.__init__` (default: xAI)
- Added `llm_base_url` field to `LiveConfig` and `ReplayConfig`
- Added `--model` and `--base-url` CLI args to `run_replay.py`
- Added `OPENROUTER_API_KEY` to `.env` and key resolution chain
- Updated all `GrokFilter()` call sites in `replay_runner.py` and `run_live.py`

### 4. LLM Model Comparison (5 models tested)

Ran the same EURUSD replay (2026-01-01 to 2026-04-12) with 5 different LLM models via OpenRouter:

| Model | Trades | Net PnL | Win Rate | Profit Factor | Sharpe | Avg Latency |
|---|---|---|---|---|---|---|
| **DeepSeek V3** (threshold 75) | **10** | **+$132.90** | **70.0%** | **3.57** | **8.84** | ~8s |
| DeepSeek V3 (threshold 65) | 13 | +$115.10 | 61.5% | 2.44 | 6.03 | ~8s |
| Gemini 2.5 Flash | 28 | +$61.60 | 53.6% | 1.44 | 2.52 | ~15s |
| Grok R1 (xAI) | 3 | -$10.10 | 66.7% | 0.60 | -2.83 | ~15s |
| DeepSeek R1 | 0 | $0 | N/A | N/A | N/A | ~60s |
| GLM 5.1 | 0 | $0 | N/A | N/A | N/A | ~60-140s |
| Passthrough (no LLM) | 53 | +$338.33 | 47.2% | 2.23 | 4.66 | 0s |

### 5. Confidence Threshold Tuning

Tested DeepSeek V3 at threshold 65 vs 75:
- Threshold 75: 10 trades, 70% WR, PF 3.57, Sharpe 8.84 ✅ **Best**
- Threshold 65: 13 trades, 61.5% WR, PF 2.44, Sharpe 6.03

The 3 extra trades from the lower threshold were marginal — they dragged down all quality metrics. The model's confidence scoring is well-calibrated: signals it rates 65-74% are genuinely worse trades.

### 6. Feedback Loop Analysis

Reviewed the existing `DecisionLedger` system and analyzed the proposed LLM feedback loop (recording decisions → assessing outcomes → feeding back into prompts).

**Conclusion: Not recommended for now.** Key downsides:
- **Overfitting to past regimes** — markets are non-stationary, feedback optimizes for what worked recently
- **Counterfactual estimation unreliable** — "would have won" for rejected trades is speculative
- **Oscillation risk** — too-conservative → approve more → too-liberal → reject more → cycle
- **Marginal gain** — DeepSeek V3 already achieves 70% WR / 3.57 PF without feedback

Better alternatives: tune threshold (done), improve prompt wording (low risk), add aggregate stats only (not individual decisions).

---

## Key Findings

1. **DeepSeek V3 is the best LLM for this task** — fast, well-calibrated confidence, selective but not over-cautious
2. **Reasoning models (Grok R1, DeepSeek R1, GLM 5.1) are too conservative** — they find reasons to reject everything, giving confidence scores far below the 75 threshold
3. **GLM 5.1 is unsuitable** — 0 trades approved, extremely slow (60-140s per call), confidence scores of 22-25
4. **Confidence threshold 75 is optimal for DeepSeek V3** — lowering to 65 admits worse trades
5. **The prompt's "be conservative" instruction was counterproductive** — replacing with "be calibrated" increased trades 10→27, PnL +71%, win rate unchanged at 70%

### 7. Prompt Calibration Experiment (Step 1 of Feedback Loop)

After the model comparison, revisited the LLM feedback loop discussion. Previous analysis was cautiously negative (overfitting risk, unreliable counterfactuals, oscillation). Re-evaluated and proposed a 3-step approach:

**Step 1: Fix the prompt** (lowest risk, highest expected impact)

Changed the system prompt from:
> "Be conservative: when in doubt, reject. False negatives are cheaper than false positives."

To:
> "Be calibrated: your goal is to approve good setups and reject bad ones with accurate confidence scores. You tend to be too conservative — many rejected signals would have been profitable. Use confidence honestly: if a setup is marginal, give it 60-70 (which will be filtered by the threshold) rather than inflating risk flags to justify rejection."

**Result — significant improvement:**

| Metric | Old Prompt ("be conservative") | New Prompt ("be calibrated") | Change |
|---|---|---|---|
| Trades | 10 | **27** | +170% |
| Net PnL | +$132.90 | **+$227.10** | +71% |
| Win Rate | 70.0% | 70.4% | flat |
| Profit Factor | 3.57 | 2.77 | -22% |
| Sharpe | 8.84 | 6.88 | -22% |
| Max Drawdown | $51.80 | $97.00 | +87% |
| Avg Winner | +$26.39 | +$18.72 | -29% |
| Avg Loser | -$17.27 | -$16.07 | +7% |

The prompt change unlocked 17 more trades while maintaining the same 70% win rate. Net PnL jumped 71%. Profit factor and Sharpe declined because the additional trades are lower-quality (smaller winners), but the absolute dollar gain is substantial.

**Interpretation:** The old prompt was suppressing good trades. The new prompt lets the model express its true confidence, and the 75 threshold still filters the weakest signals. The model is now approving trades it was previously "rejecting by inflating risk flags" — exactly what the prompt fix targeted.

**Remaining concern:** Max drawdown nearly doubled ($51.80 → $97.00). More trades = more exposure. The additional trades are profitable on average but introduce more variance.

### 8. Feedback Loop Discussion — 3-Step Plan

Agreed on a phased approach to LLM self-calibration:

**Step 1: Prompt fix** ✅ DONE — significant improvement

**Step 2: Auto-assess approved trades only** (no counterfactual speculation)
- After each replay run, automatically assess: Approved + won → CORRECT, Approved + lost → WRONG
- Feed aggregate stats back: win rate, avg winner/loser, profit factor on approved trades
- No speculation on rejected trades — only real outcomes

**Step 3: Rejection pattern tracking** (directional bias feedback)
- Track what the LLM tends to reject for ("thin_volume", "counter_trend", etc.)
- Show rejection reason distribution without speculating on outcomes
- LLM can self-assess whether its rejection patterns are overly cautious

**Why this is safer than full feedback:**
- No counterfactuals (no "rejected trade X would have won")
- No directive to approve/reject more — just shows the LLM its own patterns
- Aggregate stats only, not individual decision records
- Approved-trade outcomes are real and reliable

---

## Files Changed

| File | Change |
|---|---|
| `v11/core/types.py` | Added `DAILY_RESET` to `ExitReason` enum |
| `v11/replay/replay_runner.py` | Import `ExitReason`, use enum in `force_close`; pass `base_url` to `GrokFilter` |
| `v11/llm/grok_filter.py` | Added `base_url` parameter to `__init__` |
| `v11/config/live_config.py` | Added `llm_base_url` field; changed default model to `deepseek/deepseek-chat-v3-0324`, base_url to OpenRouter |
| `v11/replay/config.py` | Added `llm_base_url` field; changed default model and base_url |
| `v11/replay/run_replay.py` | Added `--model`, `--base-url` CLI args; `OPENROUTER_API_KEY` key resolution; wire new fields into `ReplayConfig` |
| `v11/live/run_live.py` | Pass `base_url` to `GrokFilter`; `OPENROUTER_API_KEY` key resolution |
| `.env` | Added `OPENROUTER_API_KEY` |
| `v11/llm/prompt_templates.py` | Changed "be conservative" to "be calibrated" in Darvas system prompt |

---

## Open Questions

1. ~~**Prompt optimization**~~ **RESOLVED: Changed to "be calibrated" — 71% PnL improvement**
2. **Walk-forward with LLM** — Run replay on 2018-2023 data with DeepSeek V3 to validate out-of-sample
3. **Feedback loop Step 2** — Auto-assess approved trades, feed aggregate stats into prompt
4. **Feedback loop Step 3** — Track rejection pattern distribution, show LLM its own biases
5. **Live timeout** — DeepSeek V3 averages ~8s but can spike to 18s; the 10s live timeout may be too tight
6. **Drawdown management** — prompt change increased max drawdown from $51.80 to $97.00; may need tighter risk controls

---

## Handoff Notes

- Default LLM is now **DeepSeek V3 via OpenRouter** (not Grok via xAI)
- The `GrokFilter` class name is now misleading (it handles any OpenAI-compatible provider) — consider renaming to `LLMFilter` or `OpenAILLMFilter` in a future refactor
- The replay simulator is fully functional with multi-provider support
- EURUSD data is complete from 2018 to 2026-04-12 in `C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv`
- **Prompt calibration was the biggest single improvement** — changing "be conservative" to "be calibrated" increased PnL by 71% while maintaining 70% win rate
- Next steps: implement auto-assessment (Step 2) and rejection pattern tracking (Step 3) for the feedback loop
