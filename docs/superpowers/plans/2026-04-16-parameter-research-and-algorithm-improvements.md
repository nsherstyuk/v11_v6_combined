# Parameter Research and Algorithm Improvements Plan

> **For agentic workers:** This is a RESEARCH plan, not a pure implementation plan. Each task produces a written report (`docs/journal/YYYY-MM-DD_<task>.md`) with findings, before any live config changes. DO NOT modify live configs (`v11/config/strategy_config.py`, `v11/config/live_config.py`, `v11/live/run_live.py`) without explicit approval AFTER the research report is reviewed.
>
> **Standards to follow:**
> - `@/C:/ibkr_grok-_wing_agent/standards/layer1-research-standards.md` — evidentiary discipline, scope adherence
> - `@/C:/ibkr_grok-_wing_agent/standards/operating-principles-guide-for-agents.md` — center/edge, mismatch surfacing, risk assessment
> - `@/C:/ibkr_grok-_wing_agent/standards/code-review-guide-for-agents.md` — runtime/integration review patterns
> - `@/C:/ibkr_grok-_wing_agent/standards/test-creation-guide-for-agents.md` — test discipline

**Goal:** Validate, re-measure, or improve trading parameters and algorithm settings across the three V11 strategies (Darvas Breakout, 4H Level Retest, V6 ORB) through structured backtest research.

**Context:** A code review on 2026-04-16 (see `@/C:/ibkr_grok-_wing_agent/docs/journal/2026-04-16_dashboard_and_orb_debug_session.md` and the session review results) surfaced that:
1. Some live parameters do not match documented OOS-validated configurations.
2. Some research findings (e.g. Trail10@60 trailing stop) may not be deployed in live.
3. V6 ORB velocity threshold (168 ticks/min) was calibrated on V6's dedicated tick feed; V11's integrated feed may have different tick density.
4. Several parameters were never sensitivity-tested (RR ratio, level merge distance, retest cooldown).

**Scope boundaries:**
- IN: backtests, replays, statistical analysis, written findings, config change RECOMMENDATIONS.
- OUT: modifying live configs, modifying CENTER modules (`darvas_detector.py`, `trade_manager.py`, `retest_detector.py`, `level_detector.py`, `risk_manager.py`), deploying changes. These require explicit human approval AFTER the written findings are reviewed.

**Data dependencies:**
- EURUSD 1-min bars (historical)
- XAUUSD 1-min bars + tick logs (from `data/ticks/XAUUSD/`)
- Existing backtest infrastructure in `@/C:/ibkr_grok-_wing_agent/v11/backtest/`

---

## Task 0 — Read before starting

**Required reading (do this before any task):**

- [ ] `@/C:/ibkr_grok-_wing_agent/docs/PROJECT_STATUS.md` — full context on what's been validated
- [ ] `@/C:/ibkr_grok-_wing_agent/v11/ARCHITECTURE.md` — module boundaries, center/edge map
- [ ] `@/C:/ibkr_grok-_wing_agent/v11/README.md` — overview
- [ ] `@/C:/ibkr_grok-_wing_agent/v11/config/strategy_config.py` — current live parameter values
- [ ] `@/C:/ibkr_grok-_wing_agent/v11/config/live_config.py` — current live config
- [ ] `@/C:/ibkr_grok-_wing_agent/v11/live/run_live.py` — how strategies are wired
- [ ] Existing journal entries for Darvas, ORB, 4H retest research in `@/C:/ibkr_grok-_wing_agent/docs/journal/` — understand what's already been tested and what findings exist

**Output:** a short Markdown summary of what you understood, stored as `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_param_research_kickoff.md`. Include:
- Your understanding of each strategy's current live parameters
- Your understanding of which parameters have been OOS-validated and which haven't
- Any questions that the documentation leaves unresolved

---

## Task 1 — Darvas parameter verification (HIGHEST PRIORITY)

**Problem:** Current live `EURUSD_CONFIG` in `@/C:/ibkr_grok-_wing_agent/v11/config/strategy_config.py`:
```python
top_confirm_bars: int = 15
bottom_confirm_bars: int = 15
min_box_width_atr: float = 0.3
max_box_width_atr: float = 5.0
breakout_confirm_bars: int = 3
min_box_duration: int = 20
```

`PROJECT_STATUS.md` documents these OOS-validated configs:
- **Config B:** `tc=20, bc=12, maxW=3.0, brk=2` → ~10.5 trades/yr OOS, AvgR +0.175
- **Alt:** `tc=15, bc=20, maxW=4.0, brk=2` → ~15 trades/yr OOS
- **Loosened:** `brk=3` variant reaches 14.7/yr at +0.175 AvgR

The live values (`mxW=5.0` in particular) **match no validated configuration**. This is a behavioral mismatch per operating principles §Mismatch surfacing.

**Subtasks:**

- [ ] **1.1 Archaeology** — search git history and `docs/journal/` for when and why `EURUSD_CONFIG` was set to its current values. Was there a session that validated `mxW=5.0` but wasn't recorded in `PROJECT_STATUS.md`?

- [ ] **1.2 Reproduce the documented OOS results** — run `@/C:/ibkr_grok-_wing_agent/v11/backtest/run_backtest.py` (or equivalent) on EURUSD with **Config B** (`tc=20, bc=12, mxW=3.0, brk=2`) + SMA(50) + CONFIRMING filter, on the same OOS date range referenced in PROJECT_STATUS. Verify the documented numbers (10.5 trades/yr, AvgR +0.175) reproduce.

- [ ] **1.3 Measure the currently-deployed config** — same OOS range, current live params (`tc=15, bc=15, mxW=5.0, brk=3`). Report trades/yr, win rate, AvgR, PF, max drawdown.

- [ ] **1.4 Extend to fresh data** — run both configs on the most recent 3 months of EURUSD data that was NOT part of the original OOS window. This is true out-of-sample for both.

- [ ] **1.5 Written report** at `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_darvas_param_audit.md`:
  - Historical timeline of parameter choices
  - Reproduction numbers for all configs tested
  - Fresh-data results
  - **Recommendation** for live config (with confidence level)
  - List of risks if the recommendation is wrong

**Handoff to human:** review the report. Human decides whether to change `EURUSD_CONFIG` and approves the specific change.

**Example report skeleton:**
```markdown
# Darvas Parameter Audit — YYYY-MM-DD

## Archaeology findings
[What I found in git history and journals about why current params are what they are]

## Reproduction of documented results
| Config | Trades/yr | WR | AvgR | PF | MaxDD | Source |
|--------|-----------|-----|------|-----|-------|--------|
| Config B (tc=20, bc=12, mxW=3.0, brk=2) | X | X% | +X | X | $X | PROJECT_STATUS.md |
| My reproduction | X | X% | +X | X | $X | my run |

## Current-live config measurement
[Numbers for tc=15, bc=15, mxW=5.0, brk=3]

## Fresh-data results (last 3 months)
[Same configs on unseen data]

## Recommendation
[Which config to deploy and why. Include risk assessment.]
```

---

## Task 2 — V6 ORB velocity threshold recalibration for V11

**Problem:** V6's velocity threshold `168 ticks/min` was the P50 of tick rates observed in V6's dedicated tick feed during ORB breakouts. V11's integrated ib_insync tick feed may produce different tick densities. On 2026-04-16, a quiet XAUUSD day saw velocity sit well below threshold all day; ORB entered no trades. We don't know whether:
- (a) V11 tick density is materially lower than V6 (calibration issue)
- (b) The day genuinely didn't meet the threshold (correctly rejecting)
- (c) Threshold is fine on active days but too tight on quiet ones

**Subtasks:**

- [ ] **2.1 Measure V11 tick density on XAUUSD** — use logs from `@/C:/ibkr_grok-_wing_agent/data/ticks/XAUUSD/` to measure actual ticks/min distribution throughout the trading day. Compare to V6's documented distribution (search `v11/v6_orb/research/` or equivalent for the original measurement).

- [ ] **2.2 Historical replay** — use `@/C:/ibkr_grok-_wing_agent/v11/backtest/` or the replay simulator at `@/C:/ibkr_grok-_wing_agent/docs/superpowers/plans/2026-04-15-tick-logging-replay.md` to replay ORB over all available XAUUSD data with multiple velocity thresholds: `[0 (disabled), 50, 100, 168, 250]`.

- [ ] **2.3 Metrics per threshold:**
  - Trades per year
  - Win rate
  - Avg R
  - Profit factor
  - Max drawdown
  - Days where a breakout happened in range but was rejected by velocity (opportunity cost)

- [ ] **2.4 Written report** at `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_orb_velocity_recalibration.md`:
  - Observed V11 tick distribution vs. V6
  - Performance at each threshold
  - **Recommendation** for V11 threshold
  - Note: if recommended threshold differs from V6, also document whether to update the V6 config in `@/C:/ibkr_grok-_wing_agent/v11/live/run_live.py:XAUUSD_ORB_CONFIG` or add a V11-specific override

---

## Task 3 — Trail10@60 trailing stop: deployment audit + implementation

**Problem:** Research documented a Trail10@60 SL management rule (trail stop 10 bars behind price, activated 60 bars after entry) improved Darvas AvgR by +44%. Code review on 2026-04-16 found no trailing-stop logic in `@/C:/ibkr_grok-_wing_agent/v11/execution/trade_manager.py:check_exit()`. Need to verify whether it was ever deployed, and if not, implement it behind a feature flag.

**Subtasks:**

- [ ] **3.1 Audit** — grep for "trail", "Trail", "trailing" across `v11/` and `docs/journal/`. Find:
  - The research session that validated Trail10@60
  - Whether it was ever implemented in any code path
  - Whether it's currently active in backtest but not live (or neither)

- [ ] **3.2 Scope decision** — if Trail10@60 is already in backtest, the task is to port it to `trade_manager.py`. If not in either, the task is to implement it in backtest first, re-validate, THEN port to live.

- [ ] **3.3 Implementation sketch** (if approved after audit):
  - Add to `StrategyConfig`: `trail_enabled: bool = False`, `trail_activation_bars: int = 60`, `trail_offset_bars: int = 10` (or ATR-based)
  - Add to `TradeManager.check_exit()` a trailing-stop update branch BEFORE the SL check
  - Trailing stop updates should modify `self.stop_price` AND cancel/replace the broker SL order
  - **CENTER warning:** this modifies `trade_manager.py` (a marked center module). Requires explicit approval with a plan showing the exact diff before implementing.

- [ ] **3.4 Test plan:**
  - Unit tests: trail activation after N bars, trail updates on new highs/lows, trail does not move backwards
  - Backtest regression: feature OFF reproduces current results exactly; feature ON reproduces the +44% AvgR improvement
  - Paper dry-run: verify broker SL order is cancelled+replaced correctly

- [ ] **3.5 Written report** at `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_trail_stop_audit.md`

---

## Task 4 — ORB gap filter audit

**Problem:** `gap_filter_enabled: bool = False` in `XAUUSD_ORB_CONFIG`. Research showed gap-filtered ORB improved WR by 4pp and AvgPnL by +$0.82. Why is it disabled?

**Subtasks:**

- [ ] **4.1 Archaeology** — find the original research and any later session that decided to disable it

- [ ] **4.2 Backtest comparison** — enabled vs. disabled on recent XAUUSD data, 3-month window + full-year window

- [ ] **4.3 Recommendation** with written report

---

## Task 5 — 4H Retest parameter sensitivity

**Current values** in `EURUSD_CONFIG`:
```python
retest_min_pullback_bars: int = 10     # 1-min bars
retest_max_pullback_bars: int = 30     # 1-min bars
retest_cooldown_bars: int = 60         # 1-min bars (1h)
retest_sl_atr_offset: float = 0.3
retest_rr_ratio: float = 2.0
level_merge_distance: float = 0.00005  # 0.5 pips EURUSD
```

**Subtasks:**

- [ ] **5.1 Sensitivity grid** (one variable at a time, hold others at current defaults):
  - `cooldown_bars`: {60, 240, 720} (1h, 4h, 12h)
  - `level_merge_distance`: {0.00005, 0.0002, 0.0005} (0.5, 2, 5 pips)
  - `retest_rr_ratio`: {1.5, 2.0, 2.5, 3.0}
  - `retest_sl_atr_offset`: {0.2, 0.3, 0.5, 0.75}

- [ ] **5.2 Metrics per config:** trades/yr, WR, AvgR, PF, MaxDD

- [ ] **5.3 Top 3 combinations** — run the best single-variable choices together as a combined config, compare to baseline

- [ ] **5.4 Written report** with recommendation at `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_retest_param_sensitivity.md`

---

## Task 6 — RR ratio sensitivity across all strategies

**Current values:**
- Darvas: implicit (SL at box edge, TP varies per signal)
- 4H Retest: `retest_rr_ratio = 2.0`
- ORB: `rr_ratio = 2.5`

**Subtasks:**

- [ ] **6.1 Grid:** {1.5, 2.0, 2.5, 3.0} per strategy

- [ ] **6.2 Metrics per (strategy, RR):** expectancy, WR, AvgR, max drawdown

- [ ] **6.3 Joint optimization** — is the same RR optimal for all three? Or per-strategy?

- [ ] **6.4 Written report** at `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_rr_sensitivity.md`

---

## Task 7 — LLM confidence threshold re-validation for ORB

**Problem:** Live `orb_confidence_threshold = 55` was chosen because "mechanical edge exists." But regime-filtered feedback research (which improved Sharpe 0.90→1.77) was validated at threshold 75. Current 55 is 20 points below where research was conducted.

**Subtasks:**

- [ ] **7.1 Replay** — re-run the LLM filtering research on ORB with thresholds `{50, 55, 65, 75, 85}` using DeepSeek V3 + regime-filtered feedback

- [ ] **7.2 Metrics per threshold:** trades/yr, WR, AvgR, Sharpe, rejection pattern distribution (how many trades rejected at each threshold)

- [ ] **7.3 Written report** at `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_orb_confidence_threshold.md`

---

## Task 8b — ATR implementation mismatch between Darvas and 4H Retest

**Problem:** `@/C:/ibkr_grok-_wing_agent/v11/core/darvas_detector.py:372-392` and `@/C:/ibkr_grok-_wing_agent/v11/live/level_retest_engine.py:438-456` both compute an ATR with the same name (`_atr`, EMA of true range) but differ in two ways:

1. **Bar 1 handling:**
   - Darvas: uses `bar.high - bar.low` as the first true range.
   - LevelRetest: skips bar 1 entirely (just sets `_prev_close`).
2. **Seeding:**
   - Darvas: SMA for first `atr_period` bars, then EMA.
   - LevelRetest: pure EMA from bar 2 onward.

In live trading with ~5 days of seed data these converge closely, but they are not numerically identical. A simple refactor that picks one formula and applies it to both strategies is a silent behavior change, not a cleanup.

**Subtasks:**

- [ ] **8b.1 Measure the numerical difference** on matched 1-min EURUSD windows: feed the same bars to both ATR implementations and plot the difference over time. Quantify the typical divergence in ATR units and in "ATR multiples" (since SL/TP offsets are scaled by ATR).

- [ ] **8b.2 Decide which formula is correct** — reference standard Wilder ATR? Simple EMA? Reasoned backtest comparison on one strategy (e.g. run Darvas with the LevelRetest formula, compare results).

- [ ] **8b.3 If chosen, centralize** — extract to `@/C:/ibkr_grok-_wing_agent/v11/core/atr.py`, replace both sites. **CENTER warning:** both files are marked center; requires explicit human approval with the numeric delta from 8b.1 documented.

- [ ] **8b.4 Written report** at `@/C:/ibkr_grok-_wing_agent/docs/journal/YYYY-MM-DD_atr_mismatch.md`.

---

## Task 8 — Economic calendar context for LLM (EXPLORATORY)

**Problem:** LLM filter sees ATR regime but not macro calendar events. On ECB/NFP/FOMC days the market behaves fundamentally differently and the LLM has no signal about which day it is.

**Subtasks:**

- [ ] **8.1 Feasibility** — identify free/cheap economic calendar APIs (e.g. investpy, forexfactory scraper, Trading Economics API). Decide whether real-time integration is viable.

- [ ] **8.2 Historical correlation** — using a historical calendar dataset, measure whether past trade outcomes differ materially on high-impact news days vs. normal days. If no difference, the feature isn't worth adding.

- [ ] **8.3 If worthwhile, prototype** — add an optional `macro_events_today` field to `ORBSignalContext` and `SignalContext`, populate from calendar lookup, run A/B backtest with/without.

- [ ] **8.4 Written report.** This task is EXPLORATORY — a null result (no correlation, not worth adding) is a valid outcome.

---

## Execution order (recommended)

1. **Task 0** (read) — always first
2. **Task 1** (Darvas audit) — highest priority, blocks clean backtesting of other items
3. **Task 3** (Trail10@60 audit) — if documented edge exists and isn't deployed, highest ROI
4. **Task 2** (ORB velocity) — unblocks ORB trading on quiet days
5. **Task 4** (ORB gap filter) — quick win if research supports enabling
6. **Task 5** (Retest sensitivity) — lower priority, improvement-oriented
7. **Task 6** (RR sensitivity) — lower priority
8. **Task 7** (ORB confidence threshold) — confirmatory
9. **Task 8** (Calendar) — exploratory, do last or skip

---

## What NOT to do

- Do NOT modify live configs until the corresponding research report has been reviewed and approved by the human partner.
- Do NOT modify CENTER modules (`darvas_detector.py`, `trade_manager.py`, `retest_detector.py`, `level_detector.py`, `risk_manager.py`) without an explicit implementation plan approved separately.
- Do NOT delete or weaken existing tests.
- Do NOT run optimization grids beyond the specified parameter ranges (avoids overfitting via over-searching).
- Do NOT mix research findings with code changes in the same commit/session.

---

## Handoff checklist for each task

When a research task is complete, deliver:

- [ ] Written report in `@/C:/ibkr_grok-_wing_agent/docs/journal/`
- [ ] Raw backtest output files (CSV/JSON) referenced from the report
- [ ] Clear recommendation with confidence level
- [ ] Risks if the recommendation is followed and turns out wrong
- [ ] Explicit statement: "This report does NOT modify any live code. Awaiting human review."
