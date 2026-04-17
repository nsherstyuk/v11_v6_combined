# Parameter Research Kickoff — 2026-04-16

**Purpose:** Summary of current live parameters, OOS validation status, and unresolved questions before starting the parameter research plan.

---

## 1. Current Live Parameters — EURUSD (Darvas + 4H Retest)

### Darvas Box Parameters (`EURUSD_CONFIG` in `v11/config/strategy_config.py`)

| Parameter | Live Value | OOS-Validated Config B | Alt Config | Loosened Variant |
|---|---|---|---|---|
| `top_confirm_bars` | **15** | 20 | 15 | 20 |
| `bottom_confirm_bars` | **15** | 12 | 20 | 12 |
| `max_box_width_atr` | **5.0** | 3.0 | 4.0 | 3.0 |
| `breakout_confirm_bars` | **3** | 2 | 2 | 3 |
| `min_box_width_atr` | 0.3 | 0.3 | 0.3 | 0.3 |
| `min_box_duration` | 20 | 20 | 20 | 20 |

**⚠️ BEHAVIORAL MISMATCH:** The live config (`tc=15, bc=15, mxW=5.0, brk=3`) matches **no documented OOS-validated configuration**. In particular, `mxW=5.0` is wider than any tested variant. This is the highest-priority item to resolve.

### 4H Retest Parameters (same `EURUSD_CONFIG`)

| Parameter | Live Value | OOS-Validated? |
|---|---|---|
| `retest_min_pullback_bars` | 10 | Yes (pb=10-30 was best) |
| `retest_max_pullback_bars` | 30 | Yes |
| `retest_cooldown_bars` | 60 | **No** — never sensitivity-tested |
| `retest_sl_atr_offset` | 0.3 | **No** — never sensitivity-tested |
| `retest_rr_ratio` | 2.0 | Yes (R:R=2.0 was sweet spot) |
| `level_merge_distance` | 0.00005 | **No** — never sensitivity-tested |
| `level_left_bars` | 3 | Recently changed from 10 → 3 (2026-04-16 fix) |
| `level_right_bars` | 3 | Recently changed from 10 → 3 (2026-04-16 fix) |

### Other Darvas/Retest Parameters

| Parameter | Live Value | Notes |
|---|---|---|
| `htf_sma_enabled` | True | OOS-validated (turns OOS from -0.044 to +0.176 AvgR) |
| `htf_sma_period` | 50 | OOS-validated |
| `imbalance_window` | 3 | IS-validated (19-31pt WR gap) |
| `divergence_threshold` | 0.50 | IS-validated |
| `max_hold_bars` | 120 | 2 hours at 1-min |
| `atr_period` | 60 | — |

---

## 2. Current Live Parameters — XAUUSD (ORB)

### V6 ORB Config (`XAUUSD_ORB_CONFIG` in `v11/live/run_live.py`)

| Parameter | Live Value | OOS-Validated? | Notes |
|---|---|---|---|
| `velocity_threshold` | **168.0** | Yes (P50 from V6 research) | **⚠️ Calibrated on V6's dedicated tick feed; V11's integrated feed may differ** |
| `velocity_filter_enabled` | True | Yes | |
| `velocity_lookback_minutes` | 3 | Yes | |
| `gap_filter_enabled` | **False** | **No — research showed +4pp WR, +$0.82 AvgPnL** | Why disabled? |
| `gap_vol_percentile` | 50.0 | Yes (P50 threshold) | |
| `rr_ratio` | 2.5 | Yes | |
| `skip_weekdays` | (2,) | Wednesday skip | |

### LLM Parameters (`LiveConfig` in `v11/config/live_config.py`)

| Parameter | Live Value | OOS-Validated? | Notes |
|---|---|---|---|
| `llm_confidence_threshold` | 75 | Yes (validated for Darvas/Retest) | |
| `orb_confidence_threshold` | **55** | **No** | Research was at threshold 75. Current 55 is 20 points below. |
| `llm_model` | deepseek/deepseek-chat-v3-0324 | Yes (DeepSeek V3 winner) | |
| `llm_timeout_seconds` | 10.0 | — | DeepSeek V3 averages ~8s but spikes to 18s |

---

## 3. OOS Validation Status Summary

### What HAS been OOS-validated (2018-2023, never touched during optimization):

1. **Darvas Config B** (`tc=20, bc=12, mxW=3.0, brk=2`): 63 trades, 46% WR, +0.176 AvgR OOS
2. **60-min SMA(50) direction filter**: turns OOS from losing to profitable
3. **CONFIRMING volume filter**: 19-31pt WR gap IS, helps OOS
4. **4H Level Retest** (`pb=10-30, CONF, RR=2.0`): 22.3/yr, 39.6% WR, +0.135 AvgR OOS
5. **DeepSeek V3 + regime-filtered feedback**: Sharpe 1.77 on XAUUSD replay

### What has NOT been OOS-validated:

1. **Current live EURUSD_CONFIG** (`tc=15, bc=15, mxW=5.0, brk=3`) — matches no tested config
2. **Trail10@60 trailing stop** — IS-validated (+44% AvgR improvement) but not independently OOS-validated
3. **ORB confidence threshold 55** — research was at 75
4. **ORB gap filter disabled** — research showed improvement but not deployed
5. **Retest cooldown, merge distance, SL offset** — never sensitivity-tested
6. **V11 tick density** — ORB velocity threshold calibrated on V6 feed, not V11

---

## 4. Trail10@60 — Current Deployment Status

**In backtest scripts:** YES
- `v11/backtest/analyze_trailing_sl.py` — full trailing stop simulation with multiple variants
- `v11/backtest/oos_validation.py` — Trail10@60 used in OOS validation runs
- Both scripts use `simulate_with_trailing()` local function

**In live/backtest infrastructure (`simulator.py`):** NO
- `v11/backtest/simulator.py` `simulate_trade()` has NO trailing stop logic
- Only fixed SL at box boundary + time stop + target

**In live trade management (`trade_manager.py`):** NO
- `v11/execution/trade_manager.py` `check_exit()` has NO trailing stop logic
- Only checks SL hit, TP hit, time stop

**Conclusion:** Trail10@60 exists only in standalone research scripts, not in the production pipeline (neither backtest framework nor live engine). To deploy, it must be:
1. Added to `simulator.py` (behind feature flag)
2. Re-validated with the full pipeline (SMA + CONFIRMING + trail)
3. Ported to `trade_manager.py` (CENTER module — requires approval)

---

## 5. Unresolved Questions

1. **Why is `mxW=5.0` in live?** No journal entry or git commit explains this. It's wider than any tested variant. Was it a typo? An intentional loosening that wasn't documented?

2. **Why is `gap_filter_enabled=False`?** Research showed improvement. Was it disabled due to a bug? Insufficient data? A deliberate choice?

3. **Why is `orb_confidence_threshold=55`?** The research validated at 75. The comment says "lower because mechanical edge exists" but this contradicts the research finding.

4. **V11 vs V6 tick density:** The ORB velocity threshold (168 ticks/min) was the P50 from V6's dedicated tick feed. V11 uses ib_insync's integrated feed. On 2026-04-16, a quiet XAUUSD day saw velocity at 44-52 ticks/min (26-31% of threshold). Is this a calibration issue or genuinely quiet?

5. **ATR implementation mismatch:** `darvas_detector.py` and `level_retest_engine.py` compute ATR differently (bar 1 handling, seeding method). Numerical impact unknown.

6. **Trail10@60 not OOS-validated independently:** It was tested IS and showed +44% AvgR. The OOS validation script uses it, but the OOS result (+0.176 AvgR) was with Trail10@60 + CONFIRMING + SMA. We don't know the OOS result WITHOUT trail for comparison.

---

## 6. Scope Boundaries (from plan)

- **IN:** backtests, replays, statistical analysis, written findings, config change RECOMMENDATIONS
- **OUT:** modifying live configs, modifying CENTER modules, deploying changes
- **CENTER modules (require explicit approval):** `darvas_detector.py`, `trade_manager.py`, `retest_detector.py`, `level_detector.py`, `risk_manager.py`
- **Do NOT modify live configs until research report is reviewed and approved**

---

*This report does NOT modify any live code. Awaiting human review before proceeding to Task 1.*
