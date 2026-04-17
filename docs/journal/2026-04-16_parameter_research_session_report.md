# Parameter Research Session Report — 2026-04-16

**Purpose:** Handoff document for cross-agent review. Describes methodology, findings, and pending decisions from the parameter research session.

**Author:** Cascade (AI agent)
**Date:** 2026-04-16
**Scope:** Tasks 0–4 from `docs/superpowers/plans/2026-04-16-parameter-research-and-algorithm-improvements.md`

---

## Methodology

### How the research was conducted

1. **Git archaeology** — examined commit history of `v11/config/strategy_config.py` to trace parameter origins
2. **Journal review** — read all journal entries in `docs/journal/` referencing Darvas parameters, OOS validation, velocity thresholds, and gap filters
3. **Live backtest execution** — wrote and ran `v11/backtest/research_darvas_param_audit.py` comparing three Darvas configs across three data windows and four filter stacks
4. **Reproduction verification** — re-ran the original `v11/backtest/investigate_htf_sma.py` to check whether documented OOS numbers still hold
5. **Tick density measurement** — analyzed `data/ticks/XAUUSD/2026-04-16.csv` (73K ticks) and compared to V6 bar-level tick_counts from `nautilus0` data
6. **Codebase audit** — grepped for trail/trailing/tighten logic across all `v11/` Python files and confirmed zero deployment in production modules
7. **Gap filter code review** — traced the full gap filter implementation path through V6 frozen code, adapter, and replay modules

### Constraints observed

- **No CENTER module modifications** — all findings are research-only; no live config or protected module was changed
- **No test regressions** — 396 tests pass after session
- **Research scripts are standalone** — `v11/backtest/research_darvas_param_audit.py` does not import or modify any production module

---

## Finding 1: Darvas EURUSD_CONFIG is unvalidated and severely suboptimal

### What was found

The current `EURUSD_CONFIG` defaults (`top_confirm_bars=15, bottom_confirm_bars=15, max_box_width_atr=5.0, breakout_confirm_bars=3`) were set in the **initial git commit** (`9abf6a9`, 2026-04-06) and **never changed**. They do not match any OOS-tested configuration.

The 2026-04-05 backtest session (`docs/journal/2026-04-05_backtest_session.md`) found that:
- `max_box_width_atr` 3.0→5.0 produces **28× more signals** but dilutes signal quality
- Tight boxes (`mxW=3.0`) preserve volume edge; wide boxes (`mxW=4.0+`) do not
- The recommended config was `tc=20, bc=12, mxW=3.0, brk=3` ("Loosened" variant)

**This recommendation was never applied to `strategy_config.py`.** The research scripts hard-coded Config B via `replace(EURUSD_CONFIG, ...)` but the base config was never updated.

### Measured impact (CONF+SMA+Trail, R:R=2.0)

| Period | Config | N | WR% | AvgR | PnL | PF | MaxDD |
|---|---|---|---|---|---|---|---|
| **OOS 2018-2023** | Config B (tc=20 bc=12 mxW=3.0 brk=2) | 74 | 41.9 | -0.090 | +0.092 | 1.37 | -0.23 |
| | **Current Live (tc=15 bc=15 mxW=5.0 brk=3)** | **1067** | **36.5** | **-0.179** | **-1.342** | **0.78** | **-2.42** |
| | Loosened (tc=20 bc=12 mxW=3.0 brk=3) | 101 | 40.6 | -0.103 | +0.204 | 1.69 | -0.19 |
| **IS 2024-2026** | Config B | 30 | 53.3 | +0.243 | +0.008 | 2.20 | -0.003 |
| | Current Live | 376 | 38.6 | -0.131 | -0.022 | 0.83 | -0.040 |
| | Loosened | 34 | 44.1 | -0.022 | +0.005 | 1.59 | -0.003 |
| **Fresh Jan-Apr 2026** | Config B | 3 | 0.0 | -0.984 | -0.001 | 0.00 | -0.001 |
| | Current Live | 49 | 49.0 | +0.028 | +0.007 | 1.63 | -0.004 |
| | Loosened | 3 | 0.0 | -0.979 | -0.001 | 0.00 | -0.001 |

The current live config is **unambiguously worse** on every OOS and IS metric. It produces 10-14× more trades but all negative AvgR and PF < 1.0.

### Critical: documented OOS numbers do not reproduce

The originally documented OOS result for Config B + CONF + SMA(50) + Trail10@60 was:
- **63 trades, 46% WR, +0.176 AvgR** (from `docs/journal/2026-04-05_htf_investigation.md`)

Re-running the **same script** (`investigate_htf_sma.py`) on the **current dataset**:
- **73 trades, 41.1% WR, -0.114 AvgR**

The OOS date range (2018-2023) is the same. The discrepancy likely results from the underlying CSV data being updated/extended since the original research. **The documented +0.176 AvgR OOS edge does not exist on the current dataset.**

### Recommendation

Change `EURUSD_CONFIG` to the Loosened variant (`tc=20, bc=12, mxW=3.0, brk=3`). This is the best available config but the edge is thin (OOS AvgR = -0.103). An alternative is to disable Darvas entirely and rely on 4H Level Retest + ORB only.

### Pending decision

- [ ] Which Darvas config to deploy (Loosened, Config B, or disable)
- [ ] Whether the non-reproducible OOS result invalidates prior Darvas research
- [ ] Whether to override only `EURUSD_CONFIG` or change `StrategyConfig` class defaults (which also affects XAUUSD and USDJPY configs)

### Detailed report

`docs/journal/2026-04-16_darvas_param_audit.md`

---

## Finding 2: ORB velocity filter is broken — ORB is effectively disabled

### What was found

The ORB velocity threshold (`168 ticks/min`) was calibrated on V6's dedicated tick feed, which captured actual market ticks at ~144 ticks/min (P50). V11's `ib_insync` tick subscription delivers **~60 ticks/min consistently** — this is IBKR's snapshot rate (1 tick/second), not actual market activity.

**Measured tick density:**

| Feed | Source | Ticks/min (mean) | Ticks/min (P50) | % of minutes ≥ 168 |
|---|---|---|---|---|
| V6 (nautilus0 bar data) | Dedicated tick collector | 143.9 | 112.0 | **30.7%** |
| V11 (ib_insync live) | IBKR snapshot stream | 59.5 | 60.0 | **0.0%** |

The velocity threshold of 168 is **never met** on V11's feed. On 2026-04-16, velocity was 44-52 ticks/min all day — ORB sat in RANGE_READY for 2.5+ hours, then the stale breakout check skipped the day.

### Root cause

V6's `LiveMarketContext.get_velocity()` counts ticks in the rolling buffer and divides by lookback minutes. On V11's feed, this always returns ~60 regardless of actual market activity. The feed lacks the variance needed for the velocity filter to differentiate active from quiet markets.

### Recommendation

Use **bar-level `tick_count`** as the velocity proxy instead of the raw tick stream. V11's 1-min bars include a `tick_count` field from IBKR that shows real variation (1-933 ticks/bar, mean 144). This matches V6's original data distribution, so the threshold of 168 would work as designed.

Implementation: modify `v11/live/orb_adapter.py` (EDGE module) to compute velocity from the bar aggregator's buffer instead of the tick stream. No changes to V6's frozen code.

### Pending decision

- [ ] Approve bar-level velocity proxy implementation
- [ ] Whether to keep threshold at 168 or recalibrate
- [ ] Whether this is a prerequisite for enabling the gap filter

### Detailed report

`docs/journal/2026-04-16_orb_velocity_recalibration.md`

---

## Finding 3: Trail10@60 trailing stop was never deployed

### What was found

The Trail10@60 rule (trail stop 10 bars behind price, activated 60 bars after entry) exists in **five research scripts** but in **zero production modules**:

| Module | Trail Logic | Status |
|---|---|---|
| `v11/backtest/htf_utils.py` | ✅ Full implementation | Research utility, default=trail |
| `v11/backtest/oos_validation.py` | ✅ Local copy | Research script |
| `v11/backtest/analyze_combined.py` | ✅ Local copy | Research script |
| `v11/backtest/analyze_trailing_sl.py` | ✅ Local copy | Research script |
| `v11/backtest/simulator.py` | ❌ None | Production backtest engine |
| `v11/execution/trade_manager.py` | ❌ None | Live trade manager |

The original IS research showed Trail10@60 improved AvgR by +44% (from +0.245 to +0.353) on Config B. On OOS with the current dataset, trail improves AvgR by +0.119 (from -0.209 to -0.090) but the base edge is still negative.

### Recommendation

**Defer Trail10@60 implementation** until the Darvas config issue (Finding 1) is resolved. No point adding trail to a strategy with thin/negative OOS edge. If Darvas is fixed and shows a clear OOS edge, the implementation plan is documented in the trail audit report.

### Pending decision

- [ ] Whether to implement trail after Darvas config is resolved
- [ ] Whether trail should go into `simulator.py` (backtest) first, then `trade_manager.py` (live)
- [ ] Approval to modify `trade_manager.py` (CENTER module) when the time comes

### Detailed report

`docs/journal/2026-04-16_trail_stop_audit.md`

---

## Finding 4: ORB gap filter is fully implemented but disabled for no documented reason

### What was found

The gap filter (skip days where 06:00-08:00 UTC pre-trade volatility is below P50) is **fully implemented** across V6's frozen code, the adapter, and the live context — including rolling history persistence to disk. It is disabled only by `gap_filter_enabled=False` in the config.

V6's original backtest showed:

| Config | Trades | WR% | Avg PnL | Total PnL |
|---|---|---|---|---|
| No gap filter | 1,613 | 46.4% | $0.70 | $1,137 |
| **Gap filter (vol > P50)** | **780** | **50.6%** | **$1.52** | **$1,187** |

No journal entry or commit message explains why it was disabled. The most likely explanation: conservative default during initial V11 integration, never re-enabled.

The gap filter uses bar-level data (1-min bars during 06:00-08:00 UTC), so it is **not affected by the V11 tick feed issue** (Finding 2). It works correctly on V11's feed.

### Recommendation

Enable the gap filter by setting `gap_filter_enabled=True` in `XAUUSD_ORB_CONFIG`. This is a config-only change — no code modifications needed. However, it should be done **after** fixing the velocity filter (Finding 2), since the velocity filter currently blocks all ORB trades regardless.

### Pending decision

- [ ] Approve enabling gap filter
- [ ] Whether to fix velocity first or enable both simultaneously
- [ ] Monitoring plan for gap filter effectiveness on V11

### Detailed report

`docs/journal/2026-04-16_orb_gap_filter_audit.md`

---

## Dependency Graph Between Findings

```
Finding 1 (Darvas config) ──→ Finding 3 (Trail10@60)
  "Fix config first"              "Only implement trail if edge exists"

Finding 2 (Velocity filter) ──→ Finding 4 (Gap filter)
  "Fix velocity first"             "Gap filter is moot while velocity blocks all trades"
```

The recommended implementation order:
1. Fix Darvas config (Finding 1) — highest priority, current config is deeply negative
2. Fix ORB velocity (Finding 2) — ORB is currently disabled
3. Enable gap filter (Finding 4) — simple config change after velocity is fixed
4. Consider Trail10@60 (Finding 3) — only if Darvas shows clear OOS edge after config fix

---

## Files Created This Session

| File | Purpose |
|---|---|
| `docs/journal/2026-04-16_param_research_kickoff.md` | Task 0: parameter inventory and unresolved questions |
| `docs/journal/2026-04-16_darvas_param_audit.md` | Task 1: Darvas config archaeology + backtest comparison |
| `docs/journal/2026-04-16_trail_stop_audit.md` | Task 3: Trail10@60 deployment audit + implementation plan |
| `docs/journal/2026-04-16_orb_velocity_recalibration.md` | Task 2: V11 vs V6 tick density + velocity fix proposal |
| `docs/journal/2026-04-16_orb_gap_filter_audit.md` | Task 4: gap filter archaeology + enable recommendation |
| `v11/backtest/research_darvas_param_audit.py` | Research script: 3 configs × 3 periods × 4 filter stacks |
| `v11/backtest/research_output.txt` | Raw backtest output from research script |

## Files NOT Modified

No production code, CENTER modules, or live configs were modified. The only new files are journal entries and a standalone research script. 396 tests continue to pass.

---

## Open Questions for Reviewer

1. **Darvas viability** — Given that no config shows positive OOS AvgR on the current dataset, should Darvas be disabled entirely? Or is the Loosened variant's positive OOS PnL (+0.204) sufficient to continue?

2. **Data integrity** — The non-reproducibility of the original OOS result raises questions about the EURUSD CSV data. Has the data been modified since the original research? Should we pin the dataset version?

3. **Velocity fix scope** — The bar-level velocity proxy requires modifying `orb_adapter.py`. Is this acceptable as an EDGE change, or does it need CENTER-level review since it changes how the V6 strategy interacts with V11's data?

4. **Gap filter threshold** — The P50 threshold was validated on V6's data. Should it be re-validated on V11's bar data, or is the original validation sufficient?

5. **XAUUSD and USDJPY impact** — Changing `StrategyConfig` class defaults would also affect these instruments. Should overrides be added for them, or should only `EURUSD_CONFIG` be explicitly overridden?
