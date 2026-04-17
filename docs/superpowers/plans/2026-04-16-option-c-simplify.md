# Option C: Fix ORB + Validate 4H + Kill Darvas

> **For agentic workers:** Execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read the full task before starting each one.

**Goal:** Get V11 into a state where every running strategy has a validated edge, and nothing is silently broken.

**Architecture:** Strip Darvas (no reproducible edge), fix ORB velocity filter (proven V6 edge, currently disabled), re-validate 4H Level Retest on current dataset. End state: 2 strategies (ORB on XAUUSD, 4H Retest on EURUSD), both with confirmed edges.

**Tech Stack:** Python 3.14, ib_insync, pytest, existing backtest infrastructure.

**Context you need:**
- `docs/journal/2026-04-16_full_development_handover.md` — full project state
- `docs/journal/2026-04-16_orb_velocity_recalibration.md` — Finding 2 (velocity broken)
- `docs/journal/2026-04-16_darvas_param_audit.md` — Finding 1 (Darvas has no edge)
- `v11/ARCHITECTURE.md` — CENTER vs EDGE module map (know what needs careful treatment)

**Decision gate after Task 3:** If 4H Level Retest no longer validates, fall back to Option D (rethink algorithm selection entirely). Do NOT proceed to Task 4 with only ORB — one strategy is not a portfolio.

---

### Task 1: Fix ORB velocity filter (bar-level tick_count proxy)

**Why:** ORB has been completely disabled since V11 launch. The velocity threshold (168 ticks/min) is never reached because V11's IBKR feed delivers ~60 ticks/min (snapshot rate). V6's data had ~144 ticks/min from a dedicated tick collector. The fix: compute velocity from bar-level `tick_count` (which IBKR includes in 1-min bars and shows real variance 1-933, matching V6's distribution).

**Files:**
- Modify: `v11/live/orb_adapter.py` (EDGE module — safe to change)
- Test: `v11/tests/test_orb_adapter.py`
- Do NOT modify anything in `v11/v6_orb/` (frozen V6 code)

**Approach:** The ORB adapter already wraps V6's strategy. Add a method that computes velocity from the bar aggregator's recent bars' `tick_count` field, and inject this into the V6 context before each tick evaluation.

- [ ] **Step 1: Read current velocity flow**

Read these files to understand how velocity currently works:
- `v11/live/orb_adapter.py` — the adapter, focus on `on_tick()` and any velocity-related code
- `v11/v6_orb/live_context.py` — `get_velocity()` method
- `v11/v6_orb/strategy.py` — where velocity is checked in the state machine

Understand: where does `get_velocity()` get called, what does it return, and how does the threshold gate work?

- [ ] **Step 2: Write failing test for bar-level velocity**

In `v11/tests/test_orb_adapter.py`, add a test that:
- Creates an adapter with a bar aggregator that has recent bars with known `tick_count` values
- Calls the new velocity method
- Asserts the velocity is computed from bar tick_counts, not raw tick stream
- E.g., 3 bars with tick_counts [200, 150, 180] over 3 minutes → velocity = 176.7

- [ ] **Step 3: Run test, verify it fails**

Run: `pytest v11/tests/test_orb_adapter.py -k "bar_velocity" -v`
Expected: FAIL (method doesn't exist yet)

- [ ] **Step 4: Implement bar-level velocity in adapter**

Add a method to `ORBAdapter` that:
1. Gets the last N minutes of completed bars from the bar aggregator's buffer
2. Sums their `tick_count` values
3. Divides by the lookback period (default 3 minutes, matching V6's `velocity_lookback_minutes`)
4. Returns the velocity

Then override/inject this velocity into the V6 context before `on_tick()` calls. Options:
- Set a property on `LiveMarketContext` that the adapter can override
- Or monkey-patch `get_velocity` per-tick (less clean but contained in EDGE code)

The key constraint: do NOT modify `v11/v6_orb/` files.

- [ ] **Step 5: Run test, verify it passes**

Run: `pytest v11/tests/test_orb_adapter.py -k "bar_velocity" -v`
Expected: PASS

- [ ] **Step 6: Add edge-case tests**

Test these cases:
- No bars yet (startup) → velocity = 0
- Fewer bars than lookback → use available bars
- Bars with tick_count = 0 → still works (counts as zero)

- [ ] **Step 7: Run full test suite**

Run: `pytest v11/tests/ -x -q`
Expected: All tests pass, no regressions

- [ ] **Step 8: Commit**

```bash
git add v11/live/orb_adapter.py v11/tests/test_orb_adapter.py
git commit -m "fix: ORB velocity filter uses bar-level tick_count proxy

V11's IBKR snapshot feed delivers ~60 ticks/min regardless of market
activity. The V6 velocity threshold (168) was calibrated on bar-level
tick_counts which show real variance (1-933, mean 144). This change
computes velocity from bar tick_counts instead of raw tick stream,
making the threshold work as originally designed."
```

---

### Task 2: Enable ORB gap filter

**Why:** V6 research showed gap filter improved WR +4.2pp and Avg PnL +$0.82/trade. The filter is fully implemented in V6 code but disabled in V11's config. With velocity fixed, gap filter should be enabled.

**Files:**
- Modify: `v11/live/run_live.py` — `XAUUSD_ORB_CONFIG` (line ~125-154)
- Possibly: `v11/live/orb_adapter.py` if gap filter needs adapter-level wiring

- [ ] **Step 1: Read the gap filter config and code path**

Read:
- `v11/live/run_live.py` lines 125-154 — find `gap_filter_enabled` setting
- `v11/v6_orb/live_context.py` — find gap filter logic (should check if day's pre-market volatility exceeds P50)
- `v11/v6_orb/strategy.py` — find where gap filter gates entry

Understand: is it just a config flag, or does the adapter need to wire something up?

- [ ] **Step 2: Enable gap filter in config**

In `XAUUSD_ORB_CONFIG` in `run_live.py`, set `gap_filter_enabled=True`.

If the gap filter needs rolling daily history to compute percentiles, check that the adapter provides this (it should — `docs/journal/2026-04-16_orb_gap_filter_audit.md` says it's "fully implemented, just needs config flag").

- [ ] **Step 3: Verify with existing tests**

Run: `pytest v11/tests/test_orb_adapter.py -v`
Expected: All pass. If any test assumes `gap_filter_enabled=False`, update it.

- [ ] **Step 4: Run full test suite**

Run: `pytest v11/tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add v11/live/run_live.py v11/tests/test_orb_adapter.py
git commit -m "feat: enable ORB gap filter (V6-validated +4.2pp WR improvement)"
```

---

### Task 3: Re-validate 4H Level Retest on current dataset

**Why:** The Darvas OOS results were non-reproducible on the current dataset. We need to check whether the 4H Level Retest (+0.135 AvgR OOS, 22 trades/yr) still holds before relying on it.

**CRITICAL: This is a GO/NO-GO gate.** If the 4H edge has also evaporated, STOP and escalate to the user. Do not proceed to Task 4.

**Files:**
- Run: `v11/backtest/investigate_4h_levels_deep.py` (existing script)
- Read: `docs/journal/2026-04-06_4h_deep_dive.md` (documented results to compare against)
- May need: `v11/backtest/investigate_htf_levels.py` for broader context

- [ ] **Step 1: Read the documented 4H results**

From `docs/journal/2026-04-06_4h_deep_dive.md`, note the key numbers to reproduce:
- Retest pb=10-30: 22.3 trades/yr, 39.6% WR, +0.135 AvgR OOS
- Year-by-year: which years positive, which negative

- [ ] **Step 2: Run the 4H backtest script**

Run: `python v11/backtest/investigate_4h_levels_deep.py`

Capture the full output. This will take a few minutes (processes years of 1-min data).

- [ ] **Step 3: Compare results to documented numbers**

Create a comparison table:

| Metric | Documented (2026-04-06) | Current Run | Delta |
|---|---|---|---|
| OOS trades/yr | 22.3 | ? | |
| OOS WR% | 39.6% | ? | |
| OOS AvgR | +0.135 | ? | |
| IS AvgR | +0.230 | ? | |

**If OOS AvgR is still positive (even if smaller):** The edge exists. Proceed to Task 4.

**If OOS AvgR is negative or near zero:** The edge has evaporated, same as Darvas. STOP. Write a brief report and escalate to the user. We're in Option D territory.

- [ ] **Step 4: Document findings**

Write results to `docs/journal/2026-04-16_4h_revalidation.md` with:
- Exact numbers from current run
- Comparison to original
- GO/NO-GO decision and reasoning

- [ ] **Step 5: Commit**

```bash
git add docs/journal/2026-04-16_4h_revalidation.md
git commit -m "docs: 4H level retest re-validation on current dataset"
```

---

### Task 4: Disable Darvas strategy

**Why:** No reproducible OOS edge. All configs show negative AvgR OOS. 12-17 trades/year is not statistically meaningful. The SMA filter is doing all the work, and without an underlying edge, Darvas adds complexity and risk for no return.

**Files:**
- Modify: `v11/live/run_live.py` — remove Darvas from the strategy lineup
- Modify: `v11/live/multi_strategy_runner.py` — only if needed
- Keep: all Darvas modules (`darvas_detector.py`, `live_engine.py` etc.) — don't delete, just don't wire in

**Approach:** The cleanest way is to remove the Darvas strategy from `MultiStrategyRunner.add_darvas_strategy()` call in `run_live.py`, or add a config flag to disable it. Keep the code — it might be useful for future research. Just stop running it live.

- [ ] **Step 1: Read how strategies are wired in run_live.py**

Read `v11/live/run_live.py` — find where `add_darvas_strategy()` or equivalent is called. Understand what adding/removing a strategy entails.

- [ ] **Step 2: Add a config flag to disable Darvas**

In `v11/config/live_config.py`, add:
```python
darvas_enabled: bool = False  # Disabled: no reproducible OOS edge (2026-04-16 audit)
```

In `run_live.py`, wrap the Darvas setup in `if live_cfg.darvas_enabled:`.

This way Darvas can be re-enabled for testing without code changes.

- [ ] **Step 3: Update tests**

Any test that assumes Darvas is always loaded should handle the `darvas_enabled=False` case.

- [ ] **Step 4: Run full test suite**

Run: `pytest v11/tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add v11/live/run_live.py v11/config/live_config.py v11/tests/
git commit -m "feat: disable Darvas strategy (no reproducible OOS edge)

Darvas param audit (2026-04-16) found all configs have negative AvgR
OOS. The documented +0.176 edge does not reproduce on current dataset.
Code preserved for future research; disabled via darvas_enabled=False."
```

---

### Task 5: Verify end-to-end with dry run

**Why:** After all changes, verify the system starts correctly with only ORB + 4H Retest, and that ORB can actually evaluate velocity from bar tick_counts.

- [ ] **Step 1: Run dry mode**

```bash
python -m v11.live.run_live --dry-run
```

Watch the first 2-3 minutes of output. Check:
- Only 2 strategies loaded (4H_Level_Retest on EURUSD, V6_ORB on XAUUSD)
- No Darvas_Breakout in status lines
- ORB velocity display shows realistic numbers (not stuck at 60)
- No errors or tracebacks

- [ ] **Step 2: Check ORB velocity in status log**

After a few minutes, the STATUS line for ORB should show velocity computed from bar tick_counts. It should vary (not be constant 60). If it says `velocity=0`, the bar buffer may not have filled yet — wait for 3+ minutes of bars.

- [ ] **Step 3: Stop and review**

Ctrl+C to stop. Review the log output. Write a brief summary of what you observed.

- [ ] **Step 4: Update PROJECT_STATUS.md**

Update `docs/PROJECT_STATUS.md` to reflect:
- Darvas disabled (with reason)
- ORB velocity fixed (bar-level proxy)
- Gap filter enabled
- 4H re-validation result
- Current strategy count: 2 (ORB + 4H Retest)

- [ ] **Step 5: Commit**

```bash
git add docs/PROJECT_STATUS.md
git commit -m "docs: update status — Option C applied (ORB fixed, Darvas disabled)"
```

---

## Decision tree

```
Task 1 (fix velocity) ──→ Task 2 (enable gap filter) ──→ Task 3 (validate 4H)
                                                              │
                                                    ┌────────┴────────┐
                                                    │                 │
                                              Edge exists        Edge gone
                                                    │                 │
                                              Task 4 (kill       STOP → Option D
                                              Darvas)            (rethink algos)
                                                    │
                                              Task 5 (verify
                                              end-to-end)
```

## What success looks like

After completing all 5 tasks:
- V11 runs with 2 strategies: ORB (XAUUSD) + 4H Level Retest (EURUSD)
- ORB velocity filter works correctly using bar-level tick_counts
- ORB gap filter is enabled
- 4H Level Retest edge is confirmed on current dataset
- Darvas is disabled but code is preserved
- All tests pass
- System is ready for paper trading observation

## What to watch for during paper trading

After deploying, monitor for 1-2 weeks:
1. Does ORB actually place bracket orders now? (It should, on days when velocity > 168)
2. Does the gap filter correctly skip quiet days?
3. Does 4H Level Retest generate signals? (Expect ~1-2 per week)
4. Are fill prices reasonable? (Slippage within expected range)
5. Is the daily P&L tracking correctly?

If after 2 weeks ORB is still not placing orders, the velocity threshold needs further recalibration — escalate.
