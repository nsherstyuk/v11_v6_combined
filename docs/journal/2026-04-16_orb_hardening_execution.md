# ORB Hardening — Plan Execution (2026-04-16)

**Author:** Claude Sonnet 4.6 (AI agent, executing `docs/superpowers/plans/2026-04-16-orb-hardening.md`)
**Duration:** Single session, ~2 hours
**Outcome:** All 5 plan tasks complete. Paper-trade-ready system. 7 commits, 417 tests passing.

---

## Summary

Executed the ORB hardening plan end-to-end. Closed every known live/backtest divergence, extended the backtest with stress-test variants that revealed an important finding about the velocity filter, disabled Darvas/4H strategies (no reproducible OOS edge), and prepared the system for paper trading.

After execution, the user started the system in live mode and spotted a subsequent issue — EURUSD was still being subscribed even though no strategy traded it. Fixed immediately.

---

## Commits (in execution order)

| Commit | What |
|---|---|
| `376c3c1` | fix: live ORB bars use real IBKR tick_count (not snapshot counter) |
| `5631113` | fix: enforce skip_weekdays in live ORB adapter |
| `6e374e9` | feat: extend ORB backtest — velocity OFF, slippage, Wednesday, direction |
| `68fa0c6` | feat: disable Darvas strategy (no reproducible OOS edge) |
| `f5b1bcb` | docs: update project status and review document with hardening results |
| `7b588f7` | fix: remove EURUSD from default instruments, fix startup banner |

---

## Task 1 — Live tick_count Fix

**Root cause (additional finding beyond the plan):** The prior velocity fix (commit `1a9a9fb`) correctly overrode `get_velocity()` to compute from `bar.tick_count`, but bars entering `_bar_buffer` came from `BarAggregator.on_price()` which increments `tick_count` once per IBKR snapshot (~60/min constant). So `tick_count` was still effectively the snapshot count — just routed through a different path. The velocity threshold of 168 was still unreachable.

**Fix:** `ORBAdapter.on_bar()` now calls `_enrich_bar_tick_count(bar)` before buffering. This method calls `self._ib.reqHistoricalDataAsync(..., durationStr='60 S', whatToShow='MIDPOINT')` to fetch the just-completed bar from IBKR, then uses `dataclasses.replace()` to substitute `tick_count` with the real `volume` field (which represents market tick activity for CMDTY instruments on IBKR).

**Fallback:** If the IBKR request fails (timeout, exception, empty result, `volume=0`), the original bar is used unchanged. This keeps live trading resilient to transient IBKR issues.

**Verification:** 9 new tests covering enrichment success, empty-response fallback, zero-volume fallback, exception fallback, and end-to-end `on_bar` flow. The snapshot-count vs real-tick-count threshold boundaries (60 below threshold, 200 above) are documented with explicit tests.

**Open risk:** Cannot verify empirically without live IBKR data. If IBKR's MIDPOINT `volume` field for XAUUSD is actually 0 (undocumented), the fallback path is taken and velocity remains broken. Add monitoring: log warning if tick_count is always ~60 after fix.

---

## Task 2 — skip_weekdays Enforcement

V6 frozen code never checked `skip_weekdays`. The backtest outer loop handled it, but the live adapter did not. Wednesday trades would be taken in live but not in backtest.

**Fix:** Added weekday check in `ORBAdapter.on_price()` **after** daily reset fires (so lingering orders still get cancelled on skip days) but **before** throttle/state machine processing.

**Verification:** 5 new tests covering weekday gating on Wed/Tue/Thu and the daily-reset-still-fires behavior.

---

## Task 3 — Extended Backtest

Added 3 variants to `v11/backtest/investigate_orb_xauusd.py`:
- `velocity=OFF, gap=OFF`
- `velocity=OFF, gap=ON`
- `velocity=ON, gap=ON, Wednesday=included`

Plus slippage stress test and direction breakdown tables.

### IS/OOS Results (2018–2023 OOS, 2024+ IS)

| Config | OOS_N | /yr | OOS_WR | OOS_AvgR |
|---|---|---|---|---|
| velocity=ON,  gap=OFF | 530 | 88.3 | 44.3% | +0.055 |
| velocity=ON,  gap=ON  | 296 | 49.3 | 48.0% | +0.126 |
| velocity=OFF, gap=OFF | 583 | 97.2 | 43.9% | +0.091 |
| **velocity=OFF, gap=ON**  | **315** | **52.5** | **49.5%** | **+0.183** |
| velocity=ON, gap=ON, Wed=include | 380 | 63.3 | 46.1% | +0.083 |

### Slippage Stress Test (velocity=ON, gap=ON, OOS)

| Slippage/side | AvgR | PF |
|---|---|---|
| 0.0 pts | +0.126 | 1.33 |
| 0.1 pts | +0.099 | 1.25 |
| 0.2 pts | +0.072 | 1.17 |
| 0.3 pts | +0.045 | 1.11 |
| 0.5 pts | -0.009 | 0.98 |

### Direction (velocity=ON, gap=ON, OOS)

| Direction | N | WR% | AvgR |
|---|---|---|---|
| LONG | 159 | 50.3% | +0.122 |
| SHORT | 137 | 45.3% | +0.130 |

### Findings

1. **Velocity filter is hurting.** velocity=OFF gap=ON beats velocity=ON gap=ON by +0.057 AvgR OOS. 19 more trades, 1.5pp higher WR, significantly better PF.
2. **Wednesday skip confirmed correct.** Including Wednesday drops AvgR from +0.126 to +0.083. 2018 and 2019 go negative when Wednesday is included.
3. **Slippage survives 0.3pt.** Edge breaks even at ~0.5pt. Realistic XAUUSD bracket slippage (0.1–0.3pt) leaves meaningful edge.
4. **No systematic long bias.** LONG and SHORT have comparable AvgR (0.122 vs 0.130).

---

## Task 4 — Darvas Disabled

Added `darvas_enabled: bool = False` (default) to `LiveConfig`. Wrapped the Darvas + 4H Level Retest wiring in `run_live.py` with `if self.live_cfg.darvas_enabled:`.

Both EURUSD strategies skip loading until the EURUSD data integrity issue is resolved.

**Test impact:** Updated `live_cfg_both` and `live_cfg_eurusd_only` fixtures to set `darvas_enabled=True` to preserve existing test coverage. Added `test_darvas_disabled_by_default` to verify the new default behavior.

---

## Task 5 — Documentation

- Updated `docs/PROJECT_STATUS.md` — V11 status line, build roadmap (phases 21-22), open questions (velocity filter reconsidered, EURUSD data issue added).
- Added Section 10 to `docs/journal/2026-04-16_strategy_review_and_plan.md` — bugs found, extended backtest results, decision gate outcomes, system state.

---

## Post-Execution Fix (User-Triggered)

User attempted `start_v11.bat --live` and observed:
```
Instruments: ['EURUSD', 'XAUUSD']
Strategies:  Darvas+SMA, 4H Retest (EURUSD) + ORB (XAUUSD)
...
Contract qualified: EURUSD
Price stream started: EURUSD
```

EURUSD was being contract-qualified and subscribed even though no strategy traded it. Two root causes:

1. **CLI default:** `--instruments` default was `["EURUSD", "XAUUSD"]`. Changed to `["XAUUSD"]`.
2. **Hardcoded banner:** "Strategies: Darvas+SMA, 4H Retest (EURUSD) + ORB (XAUUSD)" was a static string. Now dynamic based on `darvas_enabled`.
3. **LiveConfig default:** `instruments` field default was all three pairs. Changed to `[XAUUSD_INSTRUMENT]`.

Commit `7b588f7`.

---

## Open Decisions

### Disable velocity filter before paper trading?

**For:** Backtest clearly shows velocity=OFF outperforms. Simpler system, more trades, better OOS metrics.

**Against:** Live tick_count fix hasn't been validated against real IBKR data yet. Changing both velocity and the tick_count source at the same time makes attribution harder if something goes wrong. Keeping velocity=ON means the fix matters and we learn whether it works.

**Recommendation:** Leave velocity=ON for the first paper cycle. Measure whether brackets actually get placed (i.e., whether the tick_count fix works end-to-end). Once confirmed, revisit disabling.

### Should paper trade run with or without LLM?

**Without LLM.** Plan and strategy review both explicitly say so. Paper trade measures mechanical edge. Adding LLM obscures attribution.

Use: `start_v11.bat --live --no-llm`

---

## Files Changed (Strategic Summary)

| Layer | File | Change |
|---|---|---|
| EDGE (adapter) | `v11/live/orb_adapter.py` | Added tick_count enrichment + skip_weekdays check |
| CONFIG | `v11/config/live_config.py` | Added `darvas_enabled`, trimmed default instruments |
| ENTRY POINT | `v11/live/run_live.py` | Wrapped Darvas loading, fixed banner, trimmed CLI default |
| BACKTEST | `v11/backtest/investigate_orb_xauusd.py` | 3 new variants + 2 new tables |
| TESTS | `v11/tests/test_orb_adapter.py` | 14 new tests |
| TESTS | `v11/tests/test_run_live.py` | 1 new test, 2 fixture updates |
| DOCS | `docs/PROJECT_STATUS.md` | Status + roadmap + open questions |
| DOCS | `docs/journal/2026-04-16_strategy_review_and_plan.md` | Section 10 addendum |

**Infrastructure untouched:** `IBKRConnection`, `TradeManager`, `RiskManager`, `GatewayManager`, `BarAggregator`, `MultiStrategyRunner`, auto-reconnect logic, emergency shutdown, price staleness detection, broker sync.

---

## Assessment of Current State

### What is solid

- V11 now has a single validated strategy (ORB on XAUUSD) with +0.126 OOS AvgR (or +0.183 if velocity filter is disabled).
- All 5 known live/backtest divergences closed: velocity fix, tick_count fix, skip_weekdays, gap filter, Darvas disable.
- Infrastructure is battle-tested: auto-reconnect, IBC auto-login, emergency shutdown, tick logging, Streamlit dashboard, 417 tests passing.
- Data integrity audit is complete (XAUUSD clean, EURUSD suspect — documented).

### What is thin

- **Single strategy, single instrument.** Concentration risk is real.
- **Edge is not fat.** +0.126 OOS AvgR with break-even slippage at ~0.5pt. Realistic trading costs will eat into this. A 0.2pt slippage drops the effective edge to +0.072.
- **Regime uncertainty.** IS (2024+) AvgR is +0.031 vs OOS +0.126 — 75% drop. Gold has been in an unusual trending regime since 2024. May reflect genuine regime change that persists.
- **Live tick_count fix is unverified empirically.** The fallback path keeps the system safe, but if IBKR doesn't provide real volume in MIDPOINT bars for XAUUSD CMDTY, velocity filtering remains broken in live.

### Paper trading expectations

At ~1 trade/week, 20 trades takes 4–5 months. This is slow. If the user wants faster validation, options are:
1. Disable velocity filter (predicted ~1 trade/week → matches current)
2. Run multiple paper accounts? (impractical for IBKR)
3. Accept the timeline and use the months to investigate EURUSD data and research additional instruments.

### What to monitor in paper trading

1. **Are brackets being placed at all?** If velocity filter is blocking every day (tick_count fix not working), state machine will sit in RANGE_READY all day.
2. **Fill quality.** Are actual fills within 0.2pt of expected entry?
3. **Daily PnL tracking.** Does the dashboard match expected strategy behavior?
4. **Gap filter rejections.** Is it skipping ~45% of days (as backtest suggests)?
5. **tick_count distribution.** Log periodically to confirm real values (100–300 range) vs snapshot count (~60).

---

## Next Steps

**Immediate (user):** Run `start_v11.bat --live --no-llm` and verify:
- Banner shows `Strategies: ORB (XAUUSD)` only
- No EURUSD price stream
- ORB state machine reaches RANGE_READY during 08:00–16:00 UTC trade window
- Brackets get placed on some days

**Week 1 (monitoring):** Check logs for tick_count values and state transitions. Confirm the fix is actually producing real tick counts.

**Week 4–6 (data collection):** Aim for 20+ paper trades. Compare fill prices, WR, PnL to backtest expectations.

**Parallel (research):** Investigate what happened to `eurusd_1m_tick.csv` on 2026-04-13. Until resolved, no EURUSD strategy can be trusted.

**If paper cycle shows velocity filter is blocking trades:** Disable it and do a second paper cycle. Extended backtest suggests this will increase trade count without hurting edge.
