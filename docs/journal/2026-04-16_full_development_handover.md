# V11 Full Development Handover — 2026-04-16

**Purpose:** Complete handover document for cross-agent review. Covers all V11 development from project inception (2026-04-05) through 2026-04-16, including the parameter research session.

**Intended audience:** Another AI agent or developer who needs to understand the full project state, recent development history, and pending decisions.

---

## Project Overview

**V11** is a hybrid trading system combining deterministic signal generation with LLM filtering. It runs three strategies across two instruments:

| Strategy | Instrument | Signal Type | Status |
|---|---|---|---|
| Darvas Box Breakout | EURUSD | 1-min bar breakout from consolidation box | ⚠️ Config unvalidated (see Finding 1) |
| 4H Level Retest | EURUSD | Pullback + rebreak at 4H swing levels | ✅ Operational |
| V6 Opening Range Breakout | XAUUSD | Asian range breakout + velocity gate | ⚠️ Velocity filter broken (see Finding 2) |

**Live mode:** Paper trading via IBKR (port 4002). Auto-reconnect and unattended operation implemented.

**Codebase:** `C:\ibkr_grok-_wing_agent\` — 396 tests passing.

---

## Development Timeline

### Phase 1: Research & Backtesting (2026-04-05)

**Ref:** `docs/journal/2026-04-05_backtest_session.md`, `docs/journal/2026-04-05_htf_investigation.md`, `docs/journal/2026-04-05_session.md`

Built backtest infrastructure and ran grid search on EURUSD Darvas parameters.

- **Grid search** — 1,296 parameter combos on EURUSD, XAUUSD, USDJPY
- **Config B discovered** — `tc=20, bc=12, mxW=3.0, brk=2` best balance of quality and quantity
- **OOS failure** — IS 60% WR / +0.570 AvgR → OOS 37% WR / -0.044 AvgR (2018-2023)
- **Volume classification** — CONFIRMING trades 60% WR vs DIVERGENT 41% WR (19pp gap)
- **Trail10@60 validated** — IS AvgR +44% improvement (+0.245 → +0.353)
- **HTF SMA filter** — 60-min SMA(50) + CONFIRMING flipped OOS from -0.044 to **+0.176 AvgR** (documented; now non-reproducible per Finding 1)

**Key decision:** Proceed with LLM filter despite weak mechanical edge — the LLM was always intended to add contextual value.

### Phase 2: V11 Build (2026-04-06)

**Ref:** `docs/journal/2026-04-06_session.md`, `docs/journal/2026-04-06_sma_integration_session.md`, `docs/journal/2026-04-06_level_detector_session.md`, `docs/journal/2026-04-06_retest_detector_session.md`, `docs/journal/2026-04-06_multi_strategy_runner_session.md`

Built all V11 modules in 8 phases:

| Phase | Module | Tests |
|---|---|---|
| 1 | 60-min SMA(50) filter integration | 123 |
| 2 | 4H swing level detector | 123 |
| 3 | Retest detector state machine | 123 |
| 4 | MultiStrategyRunner + RiskManager + LevelRetestEngine | 190 |
| 5 | V6 ORB adapter (frozen V6 code, adapter pattern) | 190 |
| 6 | Tests for new modules | 263 |
| 7 | `run_live.py` multi-strategy entry point | 283 |
| 8 | Critical fixes + trade execution tests | 345 |

**Key architecture decisions:**
- V6 ORB code copied into `v11/v6_orb/` as frozen reference — never modify
- Adapter pattern: `orb_adapter.py` bridges V6 ↔ V11 without touching V6 code
- Shared `TradeManager` for EURUSD (Darvas + 4H share one position)
- `RiskManager` enforces unified daily loss limit across all strategies

**Ref:** `v11/ARCHITECTURE.md` — CENTER vs EDGE module map

### Phase 3: Live Launch & LLM Integration (2026-04-07 to 2026-04-08)

**Ref:** `docs/journal/2026-04-06_live_launch_session.md`, `docs/journal/2026-04-07_orb_llm_gate_session.md`, `docs/journal/2026-04-08_diagnostic_session.md`

- **Live launch** — Python 3.14 compatibility fix, LLM bypass (`--no-llm`), status display
- **ORB LLM gate** — sync OpenAI client (async incompatible with nest_asyncio), `on_bar()` deferred eval, stop=0 for ORB
- **DIVERGENT volume gate** — mechanical rejection of DIVERGENT volume signals (Decision #18)
- **Diagnostic logging** — formation progress, level proximity, pending retests, buffer fill
- **Historical seed** — increased from 8H to 5D so SMA(50) and 4H levels are ready at startup
- **Timeout handling** — catch APITimeoutError, increase to 30s, fallback bypasses confidence threshold

### Phase 4: Decision Feedback Loop (2026-04-08)

**Ref:** `docs/journal/2026-04-08_diagnostic_session.md` (latter half)

- **DecisionLedger** — records LLM decisions with signal context
- **Auto-assessor** — grades past decisions after trade exits (CORRECT/WRONG/MISSED)
- **Bootstrap script** — backfills Grok decision history from IBKR trade data

### Phase 5: Historical Replay Simulator (2026-04-12)

**Ref:** `docs/journal/2026-04-12_code_review_fixes_session.md` (latter half), `docs/superpowers/plans/2026-04-12-historical-replay-simulator.md`

Built 7-module replay system for testing LLM filter performance on historical data:

| Module | Purpose |
|---|---|
| `v11/replay/replay_runner.py` | CLI entry point, orchestrates replay |
| `v11/replay/replay_orb.py` | ReplayORBAdapter + ReplayORBMarketContext |
| `v11/replay/replay_darvas.py` | ReplayDarvasEngine (bar-by-bar simulation) |
| `v11/replay/auto_assessor.py` | Post-trade LLM decision assessment |
| `v11/replay/llm_client.py` | Multi-provider LLM client (OpenAI, DeepSeek, OpenRouter) |
| `v11/replay/replay_tick_logger.py` | Tick logging for replay validation |
| `v11/replay/tick_replayer.py` | Tick-data replay through V11 pipeline |

**Key result:** Replay proved LLM + regime-filtered feedback produces Sharpe 1.77 on XAUUSD (vs 0.40 for LLM alone).

### Phase 6: LLM Filtering Enhancements (2026-04-13)

**Ref:** `docs/journal/2026-04-13_llm_filtering_enhancements.md`, `docs/journal/2026-04-13_llm_model_comparison.md`

- **Expanded ORB price history** — 20 daily bars (was 10), 4-hour bars for 5 days, trend context (SMA slope, streaks, position vs SMA)
- **Regime-filtered feedback** — per-call feedback table filtered by volatility regime (±0.3 tolerance). Falls back to overall record if <3 matches
- **Live auto-assessment** — ORB via `_on_fill → _assess_exit`; Darvas/Retest via `TradeManager.on_trade_closed` callback
- **LLM model comparison** — tested 5 models: DeepSeek V3 winner (70% WR, 3.57 PF, Sharpe 8.84)

**Replay results (XAUUSD, Jan-Apr 2026, DeepSeek V3):**

| Variant | Trades | PnL | Sharpe |
|---|---|---|---|
| Passthrough | 53 | +$117 | 1.14 |
| LLM only | 47 | +$26 | 0.40 |
| LLM + history + unfiltered feedback | 36 | +$41 | 0.90 |
| **LLM + history + regime-filtered feedback** | **35** | **+$78** | **1.77** |

### Phase 7: Code Review Fixes (2026-04-12 to 2026-04-15)

**Ref:** `docs/journal/2026-04-12_code_review_fixes_session.md`, `docs/journal/2026-04-15_code_review_fixes.md`, `docs/journal/2026-04-15_review_fixes_handoff.md`

Two rounds of code review:

**Round 1 (2026-04-12):** 2 critical bugs, 4 important fixes, 20 new tests
- **Critical:** LLM feedback loop injected wrong trade's assessment (indexing bug)
- **Critical:** ORB auto-assessor used exit price instead of entry price for P&L calculation

**Round 2 (2026-04-15):** 13 fixes across 4 priority groups
- Entry time fix (use fill time, not signal time)
- Per-strategy loss limit (separate Darvas/ORB daily limits)
- LLM protocol formalization (structured response parsing)
- Tick logger: strip trailing zeros, fix non-ASCII chars for cp1252

### Phase 8: IBKR Auto-Reconnect & Unattended Operation (2026-04-14)

**Ref:** `docs/journal/2026-04-14_auto_reconnect_session.md`, `docs/journal/2026-04-14_ibkr_auto_reconnect_plan.md`

Built for 24/5 unattended operation:

| Subsystem | Implementation |
|---|---|
| **Connection retry limits** | `MAX_RECONNECT_DURATION=300s`, persistent failure detection |
| **Emergency shutdown** | Log positions, cancel orders, attempt final reconnect, write `emergency_shutdown.json`, exit code 1 |
| **Price feed staleness** | Warn at >60s stale, restart market data at >300s stale |
| **Orphaned position auto-close** | `auto_close_orphans` flag, closes broker positions not tracked by TradeManager |
| **Risk manager broker sync** | Two-level reconciliation: TradeManager per-instrument + RiskManager portfolio-level |
| **Daily reset at 5 PM ET** | Broker session reset using `America/New_York` timezone |
| **Auto-restart wrapper** | `v11/live/start_v11.bat` — restarts on error exit, stops on clean exit |
| **GatewayManager** | `v11/live/gateway_manager.py` — IBC-based Gateway lifecycle management, health monitoring, auto-restart |

**Manual steps remaining:** Install IBC, configure `config.ini`, create Windows Scheduled Task.

### Phase 9: Dashboard & ORB Debug (2026-04-16, morning)

**Ref:** `docs/journal/2026-04-16_dashboard_and_orb_debug_session.md`

- **Streamlit dashboard** — KPIs, P&L chart, price chart, trade log, exit breakdown, strategy status, auto-refresh
- **4H Level detection fix** — `level_left_bars`/`level_right_bars` 10→3 (3×4H=12h sufficient for swing detection)
- **ORB velocity blocking** — discovered velocity 44-52 ticks/min (26-31% of 168 threshold), ORB never places orders
- **Stale breakout deadlock** — V6 only checks stale breakout AFTER velocity passes; added pre-check in adapter
- **ORB diagnostics** — velocity, tick_count_3m, resting order info added to status display

### Phase 10: Parameter Research Session (2026-04-16, afternoon)

**Ref:** `docs/journal/2026-04-16_parameter_research_session_report.md` (summary), plus 5 detailed reports

Executed Tasks 0–4 from the parameter research plan. See **Findings** section below.

---

## Current System State

### What's running

- V11 in paper trading mode via IBKR Gateway
- Auto-restart wrapper (`start_v11.bat`)
- DeepSeek V3 LLM filter (confidence thresholds: 75 Darvas/Retest, 55 ORB)
- 396 tests passing

### What's broken / needs attention

| Issue | Severity | Detail |
|---|---|---|
| **Darvas config unvalidated** | 🔴 High | `mxW=5.0` matches no OOS-tested config; deeply negative OOS |
| **ORB velocity filter broken** | 🔴 High | V11 feed ~60 ticks/min, threshold 168 never met; ORB effectively disabled |
| **Documented OOS edge non-reproducible** | 🟡 Medium | Config B + CONF + SMA now shows -0.114 AvgR OOS (was +0.176) |
| **Gap filter disabled** | 🟡 Medium | Fully implemented, just needs config flag; V6 research showed +4.2pp WR |
| **Trail10@60 not deployed** | 🟢 Low | Research-validated but never ported to production; defer until Darvas fixed |

### Test count by module

Total: **396 tests passing, 0 failing**

---

## Parameter Research Findings (2026-04-16 afternoon)

### Finding 1: Darvas EURUSD_CONFIG is unvalidated and severely suboptimal

**Ref:** `docs/journal/2026-04-16_darvas_param_audit.md`

Current defaults (`tc=15, bc=15, mxW=5.0, brk=3`) were set in the initial commit and never changed. The research-recommended Loosened variant (`tc=20, bc=12, mxW=3.0, brk=3`) was never applied.

**OOS comparison (CONF+SMA+Trail, R:R=2.0):**

| Config | N | WR% | AvgR | PnL | PF |
|---|---|---|---|---|---|
| Config B | 74 | 41.9 | -0.090 | +0.092 | 1.37 |
| **Current Live** | **1067** | **36.5** | **-0.179** | **-1.342** | **0.78** |
| Loosened (recommended) | 101 | 40.6 | -0.103 | +0.204 | 1.69 |

**Critical:** The originally documented OOS edge (+0.176 AvgR for Config B + CONF + SMA) does **not reproduce** on the current dataset. Re-running the same script gives -0.114 AvgR.

**Pending decision:** Which Darvas config to deploy, or whether to disable Darvas entirely.

### Finding 2: ORB velocity filter is broken

**Ref:** `docs/journal/2026-04-16_orb_velocity_recalibration.md`

V11's IBKR feed delivers ~60 ticks/min (snapshot rate). The 168 threshold was calibrated on V6's dedicated feed (~144 ticks/min). **0.0% of minutes exceed 168 on V11's feed.**

**Recommendation:** Use bar-level `tick_count` as velocity proxy. V11's 1-min bars include `tick_count` from IBKR showing real variation (1-933, mean 144). This matches V6's distribution, so threshold 168 would work as designed.

**Implementation:** Modify `v11/live/orb_adapter.py` (EDGE module) only. No V6 code changes.

### Finding 3: Trail10@60 never deployed

**Ref:** `docs/journal/2026-04-16_trail_stop_audit.md`

Exists in 5 research scripts, zero production modules. IS improvement +44% AvgR, OOS improvement +0.119 AvgR but base edge is negative.

**Recommendation:** Defer until Darvas config is resolved. Implementation plan documented if/when approved.

### Finding 4: ORB gap filter fully implemented but disabled

**Ref:** `docs/journal/2026-04-16_orb_gap_filter_audit.md`

V6 research: gap filter improved WR +4.2pp, Avg PnL +$0.82. Implementation is complete (rolling history persistence, IBKR bar fetching, percentile thresholds). Only needs `gap_filter_enabled=True`.

**Recommendation:** Enable after velocity filter is fixed (Finding 2).

### Finding dependencies

```
Finding 1 (Darvas config) → Finding 3 (Trail10@60)
  "Fix config first"         "Only implement trail if edge exists"

Finding 2 (Velocity) → Finding 4 (Gap filter)
  "Fix velocity first"    "Gap filter is moot while velocity blocks all trades"
```

---

## Document Reference Guide

### Essential reading (start here)

| # | Document | Path | Why |
|---|---|---|---|
| 1 | **Project Status** | `docs/PROJECT_STATUS.md` | Living document — current state of all projects, build roadmap, open questions |
| 2 | **V11 Design** | `docs/V11_DESIGN.md` | Architecture, Darvas theory, LLM strategy, parameter optimization history |
| 3 | **V11 Architecture** | `v11/ARCHITECTURE.md` | CENTER vs EDGE module boundaries — what needs approval to change |
| 4 | **Operating Principles** | `standards/operating-principles-guide-for-agents.md` | Rules: surface mismatches, protect CENTER modules, don't silently resolve |
| 5 | **Parameter Research Plan** | `docs/superpowers/plans/2026-04-16-parameter-research-and-algorithm-improvements.md` | Defines all tasks, subtasks, acceptance criteria |

### Research history (how current parameters were chosen)

| # | Document | Path | Key Content |
|---|---|---|---|
| 6 | **Backtest Session** | `docs/journal/2026-04-05_backtest_session.md` | Config B discovery, OOS failure, Trail10@60 validation, volume classification |
| 7 | **HTF Investigation** | `docs/journal/2026-04-05_htf_investigation.md` | SMA(50) filter validation, documented +0.176 OOS AvgR (now non-reproducible) |
| 8 | **Frequency Investigation** | `docs/journal/2026-04-06_frequency_investigation.md` | Loosened variant recommendation, multi-instrument portfolio projection |
| 9 | **4H Deep Dive** | `docs/journal/2026-04-06_4h_deep_dive.md` | 4H level retest breakthrough, EURUSD-only viability |

### Build & integration sessions

| # | Document | Path | Key Content |
|---|---|---|---|
| 10 | **Initial Build** | `docs/journal/2026-04-06_session.md` | 8-phase build, 345 tests |
| 11 | **SMA Integration** | `docs/journal/2026-04-06_sma_integration_session.md` | Phase 1: SMA filter into simulator + live |
| 12 | **Level Detector** | `docs/journal/2026-04-06_level_detector_session.md` | Phase 2: 4H swing level detector |
| 13 | **Retest Detector** | `docs/journal/2026-04-06_retest_detector_session.md` | Phase 3: retest state machine |
| 14 | **Multi-Strategy** | `docs/journal/2026-04-06_multi_strategy_runner_session.md` | Phase 4: runner + risk manager |
| 15 | **ORB Adapter** | `docs/journal/2026-04-07_orb_adapter_session.md` | Phase 5: V6 ORB wiring |
| 16 | **Live Launch** | `docs/journal/2026-04-06_live_launch_session.md` | Py3.14 fix, LLM bypass, Phase 7-9 |
| 17 | **ORB LLM Gate** | `docs/journal/2026-04-07_orb_llm_gate_session.md` | Sync client fix, deferred eval |

### LLM & feedback loop

| # | Document | Path | Key Content |
|---|---|---|---|
| 18 | **Diagnostic Session** | `docs/journal/2026-04-08_diagnostic_session.md` | Grok fixes, diagnostic logging, 5D seed |
| 19 | **LLM Enhancements** | `docs/journal/2026-04-13_llm_filtering_enhancements.md` | Regime-filtered feedback, expanded history, auto-assessment |
| 20 | **LLM Model Comparison** | `docs/journal/2026-04-13_llm_model_comparison.md` | 5 models tested, DeepSeek V3 winner |

### Reliability & operations

| # | Document | Path | Key Content |
|---|---|---|---|
| 21 | **Code Review Fixes** | `docs/journal/2026-04-12_code_review_fixes_session.md` | 2 critical bugs, 4 important fixes |
| 22 | **Review Fixes Handoff** | `docs/journal/2026-04-15_review_fixes_handoff.md` | 13 fixes across 4 priority groups |
| 23 | **Auto-Reconnect Plan** | `docs/journal/2026-04-14_ibkr_auto_reconnect_plan.md` | Phase A-C research and plan |
| 24 | **Auto-Reconnect Session** | `docs/journal/2026-04-14_auto_reconnect_session.md` | Phase B implemented |
| 25 | **Dashboard & ORB Debug** | `docs/journal/2026-04-16_dashboard_and_orb_debug_session.md` | Streamlit, 4H fix, velocity blocking |

### Parameter research (this session)

| # | Document | Path | Key Content |
|---|---|---|---|
| 26 | **Research Kickoff** | `docs/journal/2026-04-16_param_research_kickoff.md` | Parameter inventory, OOS status, unresolved questions |
| 27 | **Darvas Audit** | `docs/journal/2026-04-16_darvas_param_audit.md` | Finding 1: config archaeology + backtest comparison |
| 28 | **Velocity Recalibration** | `docs/journal/2026-04-16_orb_velocity_recalibration.md` | Finding 2: tick density + fix proposal |
| 29 | **Trail Stop Audit** | `docs/journal/2026-04-16_trail_stop_audit.md` | Finding 3: deployment status + implementation plan |
| 30 | **Gap Filter Audit** | `docs/journal/2026-04-16_orb_gap_filter_audit.md` | Finding 4: why disabled + enable recommendation |
| 31 | **Session Report** | `docs/journal/2026-04-16_parameter_research_session_report.md` | Summary of all 4 findings |

### Key source files

| # | File | Path | Why |
|---|---|---|---|
| 32 | **Strategy Config** | `v11/config/strategy_config.py` | Darvas defaults — what needs changing (CENTER) |
| 33 | **Live Config** | `v11/config/live_config.py` | LLM thresholds, safety limits, instrument configs |
| 34 | **ORB Live Config** | `v11/live/run_live.py:125-154` | `XAUUSD_ORB_CONFIG` — velocity=168, gap disabled |
| 35 | **V6 ORB Config** | `v11/v6_orb/config.py` | Frozen V6 defaults (velocity=200, gap=False) |
| 36 | **Trade Manager** | `v11/execution/trade_manager.py` | No trailing stop — confirms Finding 3 (CENTER) |
| 37 | **ORB Adapter** | `v11/live/orb_adapter.py` | Velocity diagnostics, stale breakout guard (EDGE) |
| 38 | **Live Market Context** | `v11/v6_orb/live_context.py` | Velocity calculation, gap filter, rolling history |
| 39 | **Simulator** | `v11/backtest/simulator.py` | Production backtest — no trail logic |
| 40 | **OOS Validation** | `v11/backtest/oos_validation.py` | Original Config B validation script |
| 41 | **HTF Utils** | `v11/backtest/htf_utils.py` | Trail10@60 implementation (research only) |

---

## Pending Decisions (requires human review)

### High priority

- [ ] **Darvas config** — Deploy Loosened variant (`tc=20, bc=12, mxW=3.0, brk=3`), Config B, or disable Darvas entirely?
- [ ] **Data integrity** — The non-reproducible OOS result raises questions about the EURUSD CSV data. Has it been modified? Should we pin the dataset?
- [ ] **ORB velocity fix** — Approve bar-level velocity proxy implementation in `orb_adapter.py`?

### Medium priority

- [ ] **Gap filter** — Enable after velocity fix? Or enable now as independent improvement?
- [ ] **Velocity threshold** — Keep at 168 (now compatible with bar-level metric) or recalibrate?
- [ ] **XAUUSD/USDJPY impact** — Changing `StrategyConfig` class defaults affects these instruments too. Override only `EURUSD_CONFIG`?

### Low priority

- [ ] **Trail10@60** — Implement after Darvas config is resolved? Or permanently defer?
- [ ] **LLM feedback Step 3** — Track rejection pattern distribution, show LLM its own biases
- [ ] **IBC installation** — Manual step for Gateway auto-restart (see `python -m v11.live.gateway_manager --setup`)
- [ ] **Heartbeat file (Phase C)** — Write `heartbeat.json` every 5 min for external monitoring

---

## Git History (chronological)

```
9abf6a9  2026-04-06  Initial commit: full project state
6dc6ab6  2026-04-06  Phase 1-3: SMA filter, 4H level detector, retest detector
657cbc4  2026-04-06  Phase 4+5: MultiStrategyRunner, RiskManager, ORB Adapter
903b0b7  2026-04-06  Live launch: Py3.14 fix, LLM bypass, Phase 7-9
568d979  2026-04-07  Decision #18: DIVERGENT volume mechanical gate
85f7cd0  2026-04-07  Diagnostic logging: detector state, box/level/SMA detail
071d488  2026-04-07  Fix ORB LLM gate: sync client, on_bar() deferred eval
c0e799b  2026-04-08  Fix Grok: AsyncOpenAI → sync OpenAI (nest_asyncio compat)
855670a  2026-04-08  Increase historical seed to 5D
0e0b813  2026-04-08  Rich diagnostic logging
813bfb7  2026-04-08  Fix timeout handling: APITimeoutError, 30s timeout
a31acb3  2026-04-08  Decision feedback loop: ledger, assessment, prompt injection
ba94672  2026-04-08  Fix bootstrap_ledger.py: Python 3.14 asyncio patch
c3d8667  2026-04-12  Historical Replay Simulator: 7 modules, 30 tests
4a973d2  2026-04-13  LLM filtering code review: 2 critical bugs, 4 fixes, 20 tests
c7779f3  2026-04-14  Fix LLM too conservative: clear poisoned ledger, strengthen prompts
2849ff8  2026-04-14  Enhanced status log with trade proximity and LLM history
dbf9507  2026-04-14  IBKR auto-reconnect research + implementation plan
85ee0b2  2026-04-14  Fix: extend startup connection patience for Gateway cold start
c10ac54  2026-04-15  Tick logging and replay design spec
7b087fe  2026-04-15  Add tick_logging config fields to LiveConfig
df8ac71  2026-04-15  Add TickLogger: records raw IBKR ticks to CSV
795fe2c  2026-04-15  Hook TickLogger into run_live.py poll loop
8914174  2026-04-15  Add load_ticks(): merge-sorted tick CSV reader
f184607  2026-04-15  Add TickReplayer + run_tick_replay CLI
ae94c28  2026-04-15  Fix: replace non-ASCII arrow in tick logging (cp1252 compat)
f3cde01  2026-04-15  Fix: strip trailing zeros from tick CSV float fields
e9f228a  2026-04-15  Code review fixes: 13 fixes across 4 priority groups (374 tests)
376ec15  2026-04-16  Review: entry_time fix, per-strategy loss limit, LLM protocol
```

Note: Parameter research session (2026-04-16 afternoon) produced journal entries and a research script but **no git commits** — findings are research-only pending human review.

---

*End of handover document. All findings are research-only. No production code or CENTER modules were modified. 396 tests passing.*
