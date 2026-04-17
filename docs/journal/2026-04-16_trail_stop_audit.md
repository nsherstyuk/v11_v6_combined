# Trail10@60 Trailing Stop Audit — 2026-04-16

**Task:** Task 3 from parameter research plan — audit Trail10@60 deployment status and scope.

---

## 3.1 Audit Results

### Where Trail10@60 exists in the codebase

| Location | Type | Trail Logic | Default | Notes |
|---|---|---|---|---|
| `v11/backtest/htf_utils.py:285-387` | Shared utility | ✅ Full implementation | `tighten_mode="trail"`, `tighten_after_bars=60`, `trail_lookback=10` | Canonical implementation used by all research scripts |
| `v11/backtest/htf_utils.py:392-428` | Pipeline wrapper | ✅ Via `simulate_trades()` | `tighten_mode="trail"` | Defaults to Trail10@60 |
| `v11/backtest/oos_validation.py:22-103` | Standalone script | ✅ Local copy | `tighten_after_bars=60`, `trail_lookback=10` | Duplicated from htf_utils, slightly different API |
| `v11/backtest/analyze_combined.py:24-130` | Standalone script | ✅ Local copy | Multiple variants tested | Original research script |
| `v11/backtest/analyze_trailing_sl.py` | Standalone script | ✅ Local copy | Tests all variants | SL tightening investigation |
| `v11/backtest/research_darvas_param_audit.py` | Research script | ✅ Local copy | `trail_enabled=False` by default | Created today for Task 1 |

### Where Trail10@60 does NOT exist

| Location | Type | Trail Logic | Notes |
|---|---|---|---|
| `v11/backtest/simulator.py` | Production backtest engine | ❌ None | `simulate_trade()` has only fixed SL + TP + time stop |
| `v11/execution/trade_manager.py` | Live trade manager | ❌ None | `check_exit()` has only fixed SL + TP + time stop |
| `v11/live/live_engine.py` | Live engine | ❌ None | Delegates to TradeManager |
| `v11/live/multi_strategy_runner.py` | Multi-strategy runner | ❌ None | Delegates to TradeManager |

### Research that validated Trail10@60

**Source:** `docs/journal/2026-04-05_backtest_session.md` Phase 7

Tested on EURUSD Config B, IS 2024-2026, R:R=2.0:

| Variant | WR% | AvgR | PnL |
|---|---|---|---|
| Baseline (no tighten) | 44.2% | +0.245 | +1.3154 |
| BE after 60 bars | 37.2% | +0.294 | +1.4464 |
| Lock 50% after 60 bars | 57.0% | +0.322 | +1.6114 |
| **Trail 10-bar after 60 bars** | 51.2% | **+0.353** | **+1.8069** |

**IS improvement:** AvgR +44% (+0.245 → +0.353), PnL +37%. Time stops eliminated.

**OOS validation:** The OOS validation script (`oos_validation.py`) runs Trail10@60 by default. The documented OOS result (+0.176 AvgR) included Trail10@60. However, as documented in the Task 1 audit, this OOS result does **not reproduce** on the current dataset (actual: -0.114 AvgR).

### Was Trail10@60 ever deployed?

**No.** It has never been in any production code path (neither backtest framework nor live engine). It exists only in standalone research scripts.

---

## 3.2 Scope Decision

**Current state:** Trail10@60 is in research scripts only, not in the production backtest engine or live pipeline.

**Required path (per plan):** Implement in backtest first → re-validate → port to live.

### Implementation plan (requires approval before execution)

#### Step 1: Add trail parameters to `StrategyConfig`

```python
# In v11/config/strategy_config.py (CENTER module — requires approval)
# Add after retest_rr_ratio:

# Trailing stop management (see V11_DESIGN.md §9 Phase 7)
trail_enabled: bool = False           # enable trailing stop after activation
trail_activation_bars: int = 60       # bars after entry before trail activates
trail_lookback_bars: int = 10         # bars of swing low/high for trail offset
```

Default `trail_enabled=False` ensures zero regression — existing behavior unchanged until explicitly enabled.

#### Step 2: Add trail logic to `simulator.py`

Add trailing stop to `simulate_trade()` in `v11/backtest/simulator.py`, behind the `config.trail_enabled` flag. The logic should match `htf_utils.py:simulate_with_trailing()` exactly.

This is an **EDGE** module (backtest infrastructure, not CENTER).

#### Step 3: Re-validate with full pipeline

Run the full Darvas backtest with `trail_enabled=True` on both IS and OOS data, with SMA + CONFIRMING filter. Compare to the research script results to verify the implementation is correct.

#### Step 4: Add trail logic to `trade_manager.py` (CENTER — requires approval)

This is the critical step. `trade_manager.py` is a CENTER module. The implementation must:

1. Track `bars_held` (already done via `entry_bar_index`)
2. After `trail_activation_bars`, compute swing low/high from recent bars
3. Update `self.stop_price` to the new trail level
4. **Cancel and replace the broker SL order** — this is the hardest part
5. Log the trail update for audit

**Risk:** Cancel/replace of broker orders introduces a timing window where the position has no stop. If the cancel succeeds but the replace fails, the position is unprotected. Mitigation:
- Use IBKR's "cancel/replace" API which is atomic on the broker side
- If replace fails, immediately re-submit the original SL as a safety fallback
- Add a `stop_order_confirmed` flag and check it every bar

#### Step 5: Unit tests

- Trail activation after N bars (no activation before)
- Trail updates on new highs/lows (long: trail follows swing low up; short: follows swing high down)
- Trail never moves backwards (ratchet only)
- Trail at breakeven when trade is barely profitable
- Feature OFF reproduces current results exactly
- Feature ON reproduces research +44% AvgR improvement

---

## 3.3 Key Concern: OOS Validation Gap

The original Trail10@60 research was IS-only (2024-2026). The OOS validation script uses it, but we now know the OOS edge is thin (-0.114 AvgR with trail vs -0.209 without trail on current data for Config B + CONF + SMA).

**Trail10@60 OOS impact** (from today's research run, OOS 2018-2023, Config B + CONF + SMA):

| Variant | N | WR% | AvgR | PnL | PF |
|---|---|---|---|---|---|
| CONF+SMA (no trail) | 74 | 35.1 | -0.209 | +0.0879 | 1.34 |
| CONF+SMA+Trail | 74 | 41.9 | -0.090 | +0.0918 | 1.37 |

Trail improves AvgR by +0.119 and WR by +6.8pp on OOS. The improvement is real but the base edge is negative. Trail converts some time-stop losses into smaller SL_TIGHT losses, which helps but doesn't make the strategy profitable on its own.

---

## 3.4 Recommendation

**Defer Trail10@60 implementation** until the Darvas config issue (Task 1) is resolved.

### Rationale

1. **No point adding trail to a losing strategy.** The current live config (mxW=5.0) is deeply negative OOS. Trail would marginally improve it but not fix the fundamental problem.
2. **Config B + SMA is marginally negative OOS** (-0.090 AvgR with trail). Trail helps (+0.119 vs no-trail) but the strategy still isn't profitable OOS.
3. **Implementation risk is high.** Modifying `trade_manager.py` (CENTER) to add cancel/replace broker orders introduces a safety-critical code path. This should only be done if the strategy has a clear edge.
4. **The right sequence is:** Fix Darvas config → validate OOS edge → if edge exists, add trail → if no edge, consider disabling Darvas.

### If/when approved

The implementation sketch in §3.2 above is complete and ready to execute. The key design decisions are:
- `trail_enabled: bool = False` — safe default, no regression
- Trail only ratchets forward (never moves SL away from entry)
- Cancel/replace broker SL order with atomic fallback
- Full unit test coverage before any live deployment

---

*This report does NOT modify any live code. Awaiting human review.*

**Related:** `docs/journal/2026-04-16_darvas_param_audit.md` (Task 1 findings)
