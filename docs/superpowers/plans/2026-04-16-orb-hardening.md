# ORB Hardening Plan — Close all live/backtest divergences

> **For agentic workers:** Execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read the full task before starting each one.

**Goal:** Make the live ORB system match the backtest exactly, close every known gap, extend the backtest with stress-test variants, disable Darvas. End state: a system where paper trading results can be directly compared against backtest expectations.

**Context you need:**
- `docs/journal/2026-04-16_strategy_review_and_plan.md` — full findings + rationale
- `v11/backtest/investigate_orb_xauusd.py` — the working backtest script
- `v11/live/orb_adapter.py` — live ORB adapter (velocity fix is here)
- `v11/execution/bar_aggregator.py` — the live bar source (this is where the bug is)
- `v11/replay/replay_orb.py` — replay ORB infrastructure

**Architecture rules:**
- `v11/v6_orb/` — frozen V6 code, DO NOT MODIFY
- CENTER modules (`v11/execution/`, `v11/core/`) — production code, modify carefully, test thoroughly
- EDGE modules (`v11/live/orb_adapter.py`, `v11/backtest/`) — safe to change
- All tests must pass after each task: `pytest v11/tests/ -x -q`

---

### Task 1: Fix live `tick_count` — BarAggregator counts snapshot ticks, not market ticks

**Why this is critical:** The velocity fix (`_compute_bar_velocity`) reads `bar.tick_count` from bars in `_bar_buffer`. In the backtest, these bars come from CSV data where `tick_count` reflects real market tick activity (1–933, mean ~144). In live, bars come from `BarAggregator` which simply increments a counter on each `on_price()` call (line 71: `self.bar_tick_count += 1`). Since IBKR delivers ~60 snapshot ticks/min, live bars have `tick_count ≈ 60` — the velocity threshold of 168 is still unreachable. **The backtest shows a working strategy but the live system won't match it.**

**Files:**
- Investigate: `v11/execution/bar_aggregator.py` (current tick_count logic)
- Investigate: `v11/live/orb_adapter.py` lines 278-283 (where bars enter `_bar_buffer`)
- Investigate: `v11/live/run_live.py` lines 262-286 (seed_historical uses `volume` field from IBKR — check what this contains)
- Modify: `v11/live/orb_adapter.py` or `v11/execution/bar_aggregator.py` depending on approach
- Test: `v11/tests/test_orb_adapter.py`

**Approach options (investigate, then pick one):**

**Option A: Use IBKR's `reqHistoricalData` for rolling 1-min bars.**
The ORB adapter could periodically request the last few minutes of historical 1-min bars from IBKR, which include real volume/tick counts. This is what `seed_historical` already does at startup (`tc = int(vol)`). The adapter could refresh this on a timer (e.g., every minute after each bar completes).

**Option B: Subscribe to IBKR's `reqRealTimeBars` (5-second bars).**
These include real volume. Aggregate 12 × 5-second bars into a 1-min bar with real tick count. More complex but avoids repeated historical requests.

**Option C: Use the ORB adapter's own IBKR connection to request bar data.**
The adapter already has access to `self._ib` (the shared ib_insync instance). After each minute boundary, request the last bar's real data from IBKR. Simpler than Option B but adds one API call per minute.

**Option D: Change BarAggregator to accept external tick_count.**
If another source provides the real tick count (e.g., from IBKR's streaming bars), inject it into the bar before routing to strategies. This keeps the fix centralised.

- [ ] **Step 1: Investigate what IBKR provides**

Read the ib_insync docs or test empirically:
- What does `reqHistoricalData` return in the `volume` field for XAUUSD 1-min bars? Is it real tick count or something else?
- Does `reqRealTimeBars` include tick count for FX/CFDs?
- Does `ib.reqMktData` or `ib.reqTickByTickData` provide bar-level tick counts?

Check `v11/live/run_live.py` lines 275-285 — the historical seeding already uses `int(vol)` as tick_count. Verify this produces realistic values (100-300 range, not ~60).

- [ ] **Step 2: Choose approach and implement**

Pick the simplest approach that provides real tick counts to `_bar_buffer`. Modify only EDGE code (the adapter) if possible. If BarAggregator must change, that's CENTER code — be careful and add tests.

The key constraint: `_compute_bar_velocity()` must see bars with realistic `tick_count` values (matching the CSV data distribution: mean ~144, variance 1–933) so the 168 threshold works as designed.

- [ ] **Step 3: Add a test that verifies live bars have realistic tick_count**

In `v11/tests/test_orb_adapter.py`, add a test that:
- Creates bars with `tick_count=60` (simulating current BarAggregator output)
- Verifies velocity from these bars would be ~60 (below 168 — broken)
- Creates bars with `tick_count=200` (simulating real IBKR data)
- Verifies velocity from these bars would be ~200 (above 168 — working)

This test documents the requirement even if the underlying IBKR integration can't be tested without a live connection.

- [ ] **Step 4: Run full test suite**

Run: `pytest v11/tests/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add v11/live/orb_adapter.py v11/execution/bar_aggregator.py v11/tests/test_orb_adapter.py
git commit -m "fix: live ORB bars use real IBKR tick_count, not snapshot counter

BarAggregator counted on_price() calls (~60/min from IBKR snapshots),
making live tick_count identical to the raw tick stream the velocity
fix was supposed to replace. Now uses IBKR historical/realtime bar
data for real market tick counts (mean ~144, matching backtest CSV)."
```

---

### Task 2: Enforce `skip_weekdays` in live

**Why:** The backtest skips Wednesdays (`skip_weekdays=(2,)` in config). The live system never checks this — I searched every file in `v11/v6_orb/` and `v11/live/`. The V6 frozen strategy doesn't reference `skip_weekdays`. The adapter doesn't either. In live, Wednesday trades would be taken, creating a live/backtest divergence.

**Files:**
- Modify: `v11/live/orb_adapter.py` — add skip_weekdays check in `on_price()`
- Test: `v11/tests/test_orb_adapter.py`

- [ ] **Step 1: Add weekday check to `on_price()`**

In `ORBAdapter.on_price()`, before any processing, add:

```python
# Skip configured weekdays (e.g., Wednesday=2)
if self._v6_config.skip_weekdays and now.weekday() in self._v6_config.skip_weekdays:
    return
```

Place this right after the daily reset check (after line 178), before any state machine processing. This ensures the strategy stays in IDLE on skip days.

Also check: what happens if the strategy is in ORDERS_PLACED or IN_TRADE when a new skip-day starts? The daily reset at line 176-178 should handle this (it cancels orders and closes positions). Verify this is the case.

- [ ] **Step 2: Add test**

Test that:
- On a Wednesday (weekday=2), `on_price()` returns immediately
- On Tuesday/Thursday, `on_price()` processes normally
- If a position is open when Wednesday starts, daily reset closes it

- [ ] **Step 3: Run full test suite**

Run: `pytest v11/tests/ -x -q`

- [ ] **Step 4: Commit**

```bash
git add v11/live/orb_adapter.py v11/tests/test_orb_adapter.py
git commit -m "fix: enforce skip_weekdays in live ORB adapter

V6 frozen code doesn't check skip_weekdays. The backtest outer loop
handles it, but the live adapter never did — Wednesday trades would
be taken in live but not in backtest. Now checked in on_price()."
```

---

### Task 3: Extend backtest with stress-test variants

**Why:** The current backtest runs 2 variants (gap ON/OFF). We need velocity OFF, slippage, Wednesday, and fill-bias variants to understand the true edge before paper trading.

**Files:**
- Modify: `v11/backtest/investigate_orb_xauusd.py`

- [ ] **Step 1: Add velocity=OFF variant**

Add a config variant with `velocity_filter_enabled=False`. This shows whether velocity filtering helps or hurts. In `_run_config`, when velocity is disabled, the override still applies but the strategy won't check it (controlled by `cfg.velocity_filter_enabled`).

Add these runs:
```python
("velocity=OFF, gap=OFF", replace(BASE_CFG, velocity_filter_enabled=False), False),
("velocity=OFF, gap=ON",  replace(cfg_gap_on, velocity_filter_enabled=False), True),
```

- [ ] **Step 2: Add slippage stress test**

Add a `slippage_pts` parameter to `_run_config()`. After collecting trade records, apply slippage:
- For each trade, subtract `slippage_pts` from pnl (entry slippage + exit slippage)
- Actually: for entry stop orders, slippage makes entry worse. For SL/TP, slippage also makes exit worse. So total round-trip slippage per trade ≈ 2 × slippage_pts in price terms.
- R-adjustment: `adjusted_pnl = pnl - 2 * slippage_pts`, then compute R as before.

Run at: 0.0, 0.1, 0.2, 0.3, 0.5 points of slippage per side.

Print a separate table:
```
SLIPPAGE STRESS TEST (gap=ON)
  Slippage/side    N    WR%    AvgR     PF
  0.0 pts        296   48.0   +0.126   1.33
  0.1 pts        296   47.x   +0.xxx   x.xx
  0.2 pts        296   ...
  0.3 pts        296   ...
  0.5 pts        296   ...
```

- [ ] **Step 3: Add Wednesday=included variant**

Add a config variant with `skip_weekdays=()` (empty tuple — trade all days including Wednesday):

```python
("velocity=ON, gap=ON, Wed=included",
 replace(cfg_gap_on, skip_weekdays=()), True),
```

This shows whether skipping Wednesday actually helps.

- [ ] **Step 4: Add short-first fill priority variant**

The current `check_bar_fills()` checks long entry before short entry, creating a systematic long bias on wide bars. To quantify this:

In `_run_config`, add an optional `short_first` parameter. When True, override the execution engine's `check_bar_fills` to check short before long. Compare results.

Alternatively, since modifying the execution engine is complex, a simpler approach: after collecting trades, report the long/short breakdown separately:
```
DIRECTION BREAKDOWN (gap=ON)
  Direction    N    WR%    AvgR
  LONG        xxx   xx.x   +x.xxx
  SHORT       xxx   xx.x   +x.xxx
```

This doesn't fix the bias but shows whether there's a significant direction asymmetry.

- [ ] **Step 5: Run the extended backtest**

Run: `python -m v11.backtest.investigate_orb_xauusd`

Capture full output. This will take longer with more variants — expect 3-5 minutes.

- [ ] **Step 6: Commit**

```bash
git add v11/backtest/investigate_orb_xauusd.py
git commit -m "feat: extend ORB backtest — velocity OFF, slippage, Wednesday, direction split"
```

---

### Task 4: Disable Darvas in production

**Why:** No reproducible OOS edge on current EURUSD data. All configs negative. Still wired into the live runner.

**Files:**
- Modify: `v11/config/live_config.py` — add `darvas_enabled` flag
- Modify: `v11/live/run_live.py` — wrap Darvas setup with flag
- Test: `v11/tests/`

- [ ] **Step 1: Read how Darvas is wired in**

Read `v11/live/run_live.py` and find where the Darvas strategy is added (likely via `runner.add_strategy()` for EURUSD). Also read `v11/config/live_config.py` to see existing config structure.

- [ ] **Step 2: Add darvas_enabled flag**

In `v11/config/live_config.py`, add:
```python
darvas_enabled: bool = False  # Disabled: no reproducible OOS edge (2026-04-16 audit)
```

In `v11/live/run_live.py`, wrap the Darvas strategy addition:
```python
if live_cfg.darvas_enabled:
    # ... existing Darvas setup code ...
```

- [ ] **Step 3: Update tests**

Any test that assumes Darvas is always loaded should handle the `darvas_enabled=False` case.

- [ ] **Step 4: Run full test suite**

Run: `pytest v11/tests/ -x -q`

- [ ] **Step 5: Commit**

```bash
git add v11/config/live_config.py v11/live/run_live.py v11/tests/
git commit -m "feat: disable Darvas strategy (no reproducible OOS edge)

Darvas param audit (2026-04-16) found all configs have negative AvgR
OOS on current EURUSD dataset. Code preserved; disabled via config flag."
```

---

### Task 5: Update documentation

- [ ] **Step 1: Update PROJECT_STATUS.md**

Update `docs/PROJECT_STATUS.md` to reflect:
- Darvas disabled (with reason + date)
- ORB velocity fix deployed + live tick_count fix
- Gap filter enabled
- skip_weekdays now enforced in live
- Current strategy count: 1 (ORB on XAUUSD)
- 4H Level Retest: suspended pending EURUSD data investigation
- Link to `docs/journal/2026-04-16_strategy_review_and_plan.md` for full context

- [ ] **Step 2: Update the strategy review document**

Add an addendum to `docs/journal/2026-04-16_strategy_review_and_plan.md`:
- Section 10: "Opus Review Findings" — the tick_count bug, skip_weekdays gap, fill bias
- Update Priority 0 in the plan to reflect the fix
- Note the extended backtest results (slippage break-even, velocity OFF findings)

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: update project status and review document with hardening results"
```

---

## Decision gates

**After Task 1:** If IBKR doesn't provide real tick counts through any available API for XAUUSD (unlikely but possible), the velocity fix approach needs rethinking. Options: recalibrate the threshold for snapshot ticks (would need a separate backtest), or use a different velocity proxy entirely. **STOP and escalate if no API provides real tick counts.**

**After Task 3:** Review the extended backtest results:
- If slippage of 0.2pt kills the edge (AvgR goes negative) → the strategy is marginal for live trading. Proceed to paper trading but with low expectations.
- If velocity=OFF outperforms velocity=ON → the velocity filter is hurting. Consider disabling it (simpler system, fewer moving parts).
- If Wednesday=included is better → remove the skip. Simpler is better.

**After all tasks:** The system is ready for paper trading. Run `python -m v11.live.run_live --dry-run` and monitor for 2-3 minutes to verify:
- Only ORB strategy loaded (no Darvas)
- Velocity shows realistic values (not stuck at ~60)
- Gap filter correctly skips quiet days
- No Wednesdays traded

---

## What success looks like

After all 5 tasks:
- Live ORB system matches backtest: same velocity calculation, same day skipping, same gap filter
- Backtest extended with slippage, velocity OFF, Wednesday, and direction breakdown
- Darvas disabled
- Every known live/backtest divergence is closed
- Clear understanding of slippage break-even
- Ready for paper trading with known parameters
