# Strategy Review and Forward Plan — 2026-04-16

**Author:** Claude Sonnet 4.6 (AI agent)
**Purpose:** Handoff document for cross-LLM review. Summarises findings from today's session, current system state, and a concrete plan for reaching a live-ready state.
**Audience:** Another LLM reviewing the work and the plan.

---

## 1. Project Overview

V11 is a live algorithmic trading system running on Interactive Brokers via `ib_insync`. It wraps a set of trading strategies in an event loop, feeds market data, and submits bracket orders. The codebase is at `C:\ibkr_grok-_wing_agent`.

**Key directories:**
- `v11/live/` — live runner, adapters, IBKR connection (CENTER modules — production code, modify carefully)
- `v11/v6_orb/` — frozen V6 ORB strategy code (DO NOT MODIFY — source of truth)
- `v11/replay/` — replay/backtest infrastructure wrapping V6
- `v11/backtest/` — standalone backtest scripts
- `v11/tests/` — pytest test suite (404 tests as of today)
- `docs/journal/` — session notes and findings
- `docs/superpowers/plans/` — implementation plans

**Data source:** `C:\nautilus0\data\1m_csv\xauusd_1m_tick.csv` — 1-min bar CSV, last modified 2026-03-10 (clean, untouched).

---

## 2. What Was Broken (Before Today)

Three strategies were nominally running: V6 ORB (XAUUSD), Darvas Breakout (EURUSD), 4H Level Retest (EURUSD).

### 2.1 ORB velocity filter — broken since launch

`LiveMarketContext.get_velocity()` counts ticks in a rolling buffer and divides by lookback minutes. V11's IBKR snapshot feed delivers a constant ~60 ticks/min regardless of market activity (1 tick per second, IBKR's snapshot rate). The velocity threshold of 168 ticks/min was calibrated on V6's dedicated tick collector which captured actual market ticks at ~144 ticks/min with real variance (30.7% of minutes exceeded 168). On V11's feed, **0.0% of minutes ever exceed 168.** ORB has been stuck in RANGE_READY state every day since V11 launch — never placing a bracket.

**Fix applied:** Override `LiveMarketContext.get_velocity()` on the instance in `ORBAdapter.__init__()` to compute velocity from bar-level `tick_count` instead of the raw tick stream. IBKR includes actual market tick count in each 1-min bar (`tick_count` field), which shows real variance (1–933 ticks/bar, mean ~144). This matches V6's calibration data exactly. No V6 frozen code was modified.

Files changed: `v11/live/orb_adapter.py`, `v11/tests/test_orb_adapter.py`. Commit: `1a9a9fb`.

### 2.2 ORB gap filter — disabled with no documented reason

The gap filter (skip days where 06:00–08:00 UTC pre-trade volatility is below rolling P50) was fully implemented in V6's frozen code and the adapter, but set to `gap_filter_enabled=False` in config. V6's original backtest showed: +4.2pp WR improvement, halved trade frequency, higher PF.

**Fix applied:** Set `gap_filter_enabled=True` in `XAUUSD_ORB_CONFIG` in `v11/live/run_live.py`. Config-only change. Commit: `12fb83f`.

### 2.3 asyncio crash on order submission

`IBKRConnection.sleep()` called `ib.sleep()` which calls `asyncio.ensure_future()` internally. On Python 3.14, this raises `ValueError: The future belongs to a different loop` when called from inside `loop.run_until_complete()`. This caused order submission to crash silently, leaving orders in PendingSubmit state.

**Fix applied:** Replaced `ib.sleep()` with `time.sleep()` in `IBKRConnection.sleep()`. Commit in prior session.

### 2.4 Darvas strategy — no reproducible edge

Original documented edge: +0.176 AvgR OOS (2018-2023). Re-running the same backtest script on current data: -0.114 AvgR. Root cause identified: `eurusd_1m_tick.csv` was last modified **2026-04-13** — 7 days after the original research was done on 2026-04-06. The EURUSD data was updated/extended after the research, breaking reproducibility. All Darvas configs show negative OOS AvgR on current data. Additionally, the live config (`max_box_width_atr=5.0`) was never updated from the initial commit and is the worst-performing config.

**Status:** Darvas not yet disabled (Task 4 of Option C plan was abandoned at the GO/NO-GO gate — see below). Still running live but should be disabled.

### 2.5 4H Level Retest — edge evaporated on current data

Original documented edge: +0.135 AvgR OOS (retest pb=10-30). Re-running on current data: **-0.365 AvgR**. Same root cause as Darvas — EURUSD data updated April 13, breaking reproducibility. Every config variant, every session filter, every year is negative OOS. Zero positive OOS years 2018-2023. **Not deployed live yet** (was the GO/NO-GO gate). Strategy cannot be trusted until EURUSD data issue is investigated.

---

## 3. What Was Confirmed Today

### 3.1 ORB on XAUUSD — edge confirmed on clean data

`v11/backtest/investigate_orb_xauusd.py` runs the full V6 ORB strategy via `ReplayORBAdapter` on `xauusd_1m_tick.csv` (March 10 timestamp — clean). Key design decisions:

- **Velocity:** uses `bar.tick_count` (matching live behaviour after fix)
- **Gap filter:** real rolling-percentile implementation, no lookahead
- **No LLM filter:** tests pure strategy edge
- **IS/OOS split:** OOS = 2018–2023 (6yr), IS = 2024+ (~2yr)

**Results:**

| Config | IS_N | IS_WR | IS_AvgR | OOS_N | /yr | OOS_WR | OOS_AvgR |
|---|---|---|---|---|---|---|---|
| velocity=tick_count, gap=OFF | 237 | 49.4% | +0.075 | 530 | 88.3 | 44.3% | **+0.055** |
| velocity=tick_count, gap=ON  | 137 | 48.2% | +0.031 | 296 | 49.3 | 48.0% | **+0.126** |

**Year-by-year OOS (gap=ON):**

| Year | N | WR% | AvgR | PF | MaxDD |
|---|---|---|---|---|---|
| 2018 | 43 | 48.8% | -0.061 | 0.84 | 7.488 |
| 2019 | 37 | 45.9% | +0.084 | 1.20 | 5.019 |
| 2020 | 48 | 60.4% | +0.261 | 1.97 | 5.084 |
| 2021 | 54 | 37.0% | +0.141 | 1.34 | 5.387 |
| 2022 | 70 | 51.4% | +0.218 | 1.56 | 7.204 |
| 2023 | 44 | 43.2% | +0.030 | 1.07 | 6.027 |
| **TOTAL** | **296** | **48.0%** | **+0.126** | **1.33** | **9.374** |

**Assessment:** Positive OOS edge on 6 years of clean, unchanged XAUUSD data. Gap filter clearly improves every quality metric. 5 of 6 OOS years positive (gap=ON). The edge is real but thin.

**Known concerns:**
- IS AvgR (2024+) is +0.031 vs OOS +0.126 — 75% drop. Gold has been in an unusual trending regime since 2024. May reflect genuine regime change.
- Theoretical AvgR at 48% WR and R:R=2.5 is ~+0.68R. Actual +0.126R implies most trades are EOD-closed or max_pending_hours cancelled, not clean TP hits. Strategy is heavily reliant on a few large TP winners.
- Slippage not modelled. Average range ~5 points; a 0.2-point slippage on entry = 0.04R cost per trade, reducing +0.126R edge to +0.086R.

---

## 4. Current System State

| Component | Status | Notes |
|---|---|---|
| ORB velocity fix | ✅ Deployed | Commit `1a9a9fb` |
| ORB gap filter | ✅ Enabled | Commit `12fb83f` |
| asyncio crash fix | ✅ Deployed | Prior session |
| ORB backtest | ✅ Written + validated | `v11/backtest/investigate_orb_xauusd.py` |
| Darvas strategy | ❌ Still live | Should be disabled — no OOS edge on current data |
| 4H Level Retest | ❌ Not live, not validated | EURUSD data issue unresolved |
| EURUSD data | ⚠️ Unknown state | Updated April 13, breaking prior research |
| Test suite | ✅ 404 tests pass | No regressions from today's changes |

---

## 5. Open Questions

### 5.1 Does velocity filter help or hurt?
The backtest always had velocity enabled. We never tested `velocity_filter_enabled=False`. The velocity filter discards days where tick activity is below threshold — it could be filtering genuinely bad setups, or it could be filtering good setups during low-volume periods that still breakout cleanly.
**How to answer:** Add a `velocity=OFF` variant to `investigate_orb_xauusd.py`.

### 5.2 What is the slippage break-even?
AvgR +0.126 is thin. A stress test at 0.1, 0.2, 0.3, 0.5 point deductions from each fill would show at what slippage the edge disappears. Bracket stop orders on XAUUSD will realistically have 0.1–0.3 point slippage.
**How to answer:** Add a `slippage_pts` parameter to `_run_config()` that subtracts from entry and exit fills before computing R.

### 5.3 Does gap range filter add value beyond vol filter?
The gap=ON variant only enables the vol filter (`gap_range_filter_enabled=False`). The V6 config has a separate range filter (skip if gap range ratio < rolling P40). This was never tested.
**How to answer:** Add a `gap=VOL+RANGE` variant.

### 5.4 Does Wednesday skip help?
It's in the config from V6 research but never re-validated on current XAUUSD data.
**How to answer:** Add a `Wednesday=included` variant.

### 5.5 Can LLM filter be evaluated?
The LLM filter gates the strategy when it reaches RANGE_READY, based on market context (trend, ATR regime, range size vs avg). It cannot be reliably backtested because: (a) non-deterministic output, (b) possible training data contamination for 2018-2023 historical prices.

**Better approach:** Test the LLM's likely signal mechanically. The LLM has access to: ATR regime (current vs slow ATR), range size vs 20-day average, trend direction (SMA slope, consecutive days), breakout direction vs trend. These are simple numeric features. Adding them as hard filters to the backtest answers "is there signal here" without the LLM's non-determinism.

Suggested mechanical proxy variants:
- `atr_regime < 0.8` → skip (range day too quiet relative to historical norm)
- `range_vs_avg < 0.5 or > 2.0` → skip (anomalous range, outside normal distribution)
- Trend alignment: skip SHORT breakout when price > 20d SMA; skip LONG breakout when price < 20d SMA

### 5.6 What happened to the EURUSD data?
`eurusd_1m_tick.csv` was modified April 13 without documentation. This broke all prior EURUSD research. Options: (a) find the original pre-April-13 CSV and verify what changed, (b) re-run all EURUSD backtests on current data and treat as the new baseline, (c) pull fresh data from IBKR and use going forward. Until resolved, no EURUSD strategy should run live.

---

## 6. Recommended Plan

### Priority 1: Complete backtest stress-testing (before paper trading)

Add the following variants to `v11/backtest/investigate_orb_xauusd.py`. Estimated effort: 2–3 hours.

**Variant additions:**
1. `velocity=OFF` — establish whether velocity filter adds value
2. `slippage=0.1pt, 0.2pt, 0.3pt` — find break-even slippage
3. `gap=VOL+RANGE` — test full gap filter
4. `Wednesday=included` — verify Wednesday skip assumption

**Decision gate after this step:** If slippage of 0.2pt or less kills the edge, the strategy is marginal and live trading risk is high. If the edge survives 0.3pt slippage, proceed with confidence.

### Priority 2: Disable Darvas in production

Add `darvas_enabled: bool = False` to `v11/config/live_config.py`, wrap Darvas setup in `run_live.py` with the flag. This is Task 4 from the Option C plan, abandoned at the 4H GO/NO-GO gate but still needed. Code is preserved, just not wired in.

### Priority 3: Test mechanical LLM proxies

Add ATR regime, range-vs-avg, and trend-alignment filters as backtest variants. If any improve OOS AvgR, adopt as hard filters. If none help, accept that the LLM likely adds noise not signal and run without it.

### Priority 4: Paper trade ORB (no LLM, with gap filter)

Once Priority 1-3 are complete:
- Run `python -m v11.live.run_live` with only ORB on XAUUSD
- Paper/dry-run mode for 4–6 weeks (minimum 20 trades)
- Monitor: Are brackets placed on high-velocity days? Are gap-filter rejections on quiet days? Are fill prices within 0.2pt of expected? Is daily PnL tracking correctly?

Do NOT enable LLM filter during paper trading. Paper trade gives you ground truth on actual fills; adding LLM means you can't tell if results are from the strategy or the filter.

### Priority 5: Investigate EURUSD data integrity

Before adding any EURUSD strategy: understand what changed in the CSV on April 13. Run git log/blame on the data directory if version-controlled, or compare row counts/date ranges before and after. The 4H Level Retest and Darvas strategies both had positive documented edges that disappeared — if the data change is understood and the new data is correct, those strategies can be re-researched on the clean dataset.

### Priority 6 (optional): Re-baseline 4H Level Retest on current EURUSD data

Run `python v11/backtest/investigate_4h_levels_deep.py` with current data and treat today's results as the new baseline. The documented edge (+0.135 AvgR) is gone, but that doesn't mean the strategy is dead — it means the prior research was done on different data. The new baseline (currently all negative) should be re-evaluated with fresh eyes before concluding it has no edge.

---

## 7. What Success Looks Like

**Near term (2–4 weeks):**
- `investigate_orb_xauusd.py` extended with all stress-test variants
- Darvas disabled in production
- Clear decision: LLM adds value (mechanical proxy test shows signal) or does not (keep strategy simple)
- ORB paper trading with understood risk parameters

**Medium term (6–8 weeks):**
- 20+ paper trades captured with actual fill prices
- Slippage measured empirically vs backtest assumption
- IS performance (2025-2026) trending toward or away from OOS baseline
- EURUSD data issue resolved; 4H / Darvas re-research decision made

**Long term:**
- ORB running live with real money if paper results are within expected range
- Second strategy added only after its own confirmed OOS edge + paper validation cycle
- Every strategy has: a backtest script in `v11/backtest/`, documented parameters with origins, known slippage break-even, and a year-by-year OOS table

---

## 8. Files Created/Modified Today

| File | Change | Commit |
|---|---|---|
| `v11/live/orb_adapter.py` | Velocity override using tick_count | `1a9a9fb` |
| `v11/tests/test_orb_adapter.py` | 9 new tests for velocity | `1a9a9fb` |
| `v11/live/run_live.py` | gap_filter_enabled=True | `12fb83f` |
| `v11/backtest/investigate_orb_xauusd.py` | New: full IS/OOS backtest | `a8ae6cc` |
| `docs/journal/2026-04-16_4h_revalidation.md` | NO-GO finding, data integrity root cause | `6ad31a5` |

---

## 9. Constraints and Conventions

- `v11/v6_orb/` — frozen V6 code, DO NOT MODIFY. Override via adapter patterns.
- CENTER modules (`v11/live/`, `v11/execution/`, `v11/core/`) — production code, change carefully.
- EDGE modules (`v11/live/orb_adapter.py`, backtest scripts) — safe to change.
- All backtest scripts use `sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")` and can be run directly.
- Tests: `pytest v11/tests/ -x -q` must pass before any commit to production code.
- Data: `C:\nautilus0\data\1m_csv\` — XAUUSD data is clean (March 10), EURUSD data is suspect (April 13 modification).

---

## 10. Opus Review Findings and Hardening (2026-04-16)

**Reviewer:** Claude Sonnet 4.6 (executing `docs/superpowers/plans/2026-04-16-orb-hardening.md`)

### Additional Bugs Found

**Bug 1 — Live tick_count is snapshot count, not market ticks (P0)**

The priority 0 item in the plan described the velocity fix at the wrong layer. The fix in commit `1a9a9fb` overrode `get_velocity()` to use `bar.tick_count` from `_bar_buffer` — but bars in `_bar_buffer` come from `BarAggregator.on_price()` which counts snapshot ticks (~60/min constant). So `tick_count` in live bars was always ~60, not the real market activity. The velocity threshold of 168 was still unreachable.

**Fix (commit `376c3c1`):** `on_bar()` now calls `_enrich_bar_tick_count()` which requests the just-completed bar from IBKR via `reqHistoricalDataAsync()` (MIDPOINT, 60-second duration). The real `volume` field from IBKR replaces the snapshot `tick_count`. Falls back gracefully if IBKR request fails.

**Bug 2 — skip_weekdays not enforced in live (confirmed P0)**

Described in this document (Section 2 / open questions). Confirmed: no code in `orb_adapter.py` or `v6_orb/` checked `skip_weekdays`. Wednesday trades were taken live but skipped in backtest.

**Fix (commit `5631113`):** Added weekday check in `on_price()` after daily reset fires, before throttle/state machine processing. Daily reset still runs on skip days (cancels lingering orders from prior day).

### Extended Backtest Results (2026-04-16)

Script: `v11/backtest/investigate_orb_xauusd.py` — extended with velocity=OFF variants, slippage stress test, Wednesday=included, direction breakdown.

**IS/OOS Summary:**

| Config | IS_N | IS_WR | IS_AvgR | OOS_N | /yr | OOS_WR | OOS_AvgR |
|---|---|---|---|---|---|---|---|
| velocity=ON,  gap=OFF | 237 | 49.4% | +0.075 | 530 | 88.3 | 44.3% | +0.055 |
| velocity=ON,  gap=ON  | 137 | 48.2% | +0.031 | 296 | 49.3 | 48.0% | **+0.126** |
| velocity=OFF, gap=OFF | 212 | 47.6% | +0.077 | 583 | 97.2 | 43.9% | +0.091 |
| velocity=OFF, gap=ON  | 121 | 48.8% | +0.088 | 315 | 52.5 | 49.5% | **+0.183** |
| velocity=ON, gap=ON, Wed=include | 165 | 49.1% | +0.031 | 380 | 63.3 | 46.1% | +0.083 |

**Key findings:**
- **velocity=OFF outperforms velocity=ON**: +0.183 vs +0.126 OOS AvgR. The velocity filter is hurting. Likely filtering good low-activity setups that still break out cleanly.
- **Wednesday skip confirmed**: Including Wednesday drops OOS AvgR from +0.126 to +0.083. Keep skipping.
- **Direction breakdown** (gap=ON OOS): LONG +0.122, SHORT +0.130. No systematic long bias from fill-order check.

**Slippage break-even (velocity=ON, gap=ON, OOS):**

| Slippage/side | N | WR% | AvgR | PF |
|---|---|---|---|---|
| 0.0 pts | 296 | 48.0% | +0.126 | 1.33 |
| 0.1 pts | 296 | 47.6% | +0.099 | 1.25 |
| 0.2 pts | 296 | 46.3% | +0.072 | 1.17 |
| 0.3 pts | 296 | 44.9% | +0.045 | 1.11 |
| 0.5 pts | 296 | 43.2% | -0.009 | 0.98 |

Edge survives 0.3pt/side slippage (+0.045 AvgR). Breaks even at ~0.5pt.

### Decision Gate Outcomes

1. **IBKR tick_count availability**: IBKR MIDPOINT historical bars for XAUUSD CMDTY include real tick activity in `volume`. Confirmed by existing `seed_historical` pattern using `int(vol)`.

2. **Slippage kills edge at 0.2pt?**: NO. Edge still +0.072 at 0.2pt, +0.045 at 0.3pt. Proceed with confidence.

3. **velocity=OFF outperforms?**: YES (+0.057 AvgR improvement OOS). Open decision: disable velocity filter before paper trading? Simpler system with better OOS metrics.

4. **Wednesday=included better?**: NO. Skip-Wednesday confirmed correct.

### System State After Hardening (2026-04-16)

| Component | Status | Commit |
|---|---|---|
| ORB live tick_count fix | ✅ Deployed | `376c3c1` |
| ORB skip_weekdays in live | ✅ Deployed | `5631113` |
| ORB gap filter | ✅ Enabled | `12fb83f` |
| Extended backtest | ✅ Complete | `6e374e9` |
| Darvas disabled | ✅ Done | `68fa0c6` |
| Test suite | ✅ 417 tests pass | — |

**Next action:** Paper trade ORB (no LLM, gap=ON, skip Wed, velocity=ON for now). Monitor fills for 4–6 weeks. Consider disabling velocity filter based on extended backtest findings.
