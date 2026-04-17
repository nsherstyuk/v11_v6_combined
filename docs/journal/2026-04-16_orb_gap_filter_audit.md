# ORB Gap Filter Audit — 2026-04-16

**Task:** Task 4 from parameter research plan — audit why ORB gap filter is disabled.

---

## 4.1 Archaeology

### Original research results (from `docs/PROJECT_STATUS.md`)

V6 backtest on XAUUSD:

| Config | Trades | WR% | Avg PnL | Total PnL |
|---|---|---|---|---|
| No gap filter | 1,613 | 46.4% | $0.70 | $1,137 |
| **Gap filter (vol > P50)** | **780** | **50.6%** | **$1.52** | **$1,187** |

- WR improvement: +4.2pp
- Avg PnL improvement: +$0.82/trade (+117%)
- Total PnL: roughly the same (quality filter, not PnL multiplier)
- Trade count halved (780 vs 1,613) — filters out low-quality days

### Why is it disabled?

**No documented reason.** The gap filter was set to `False` in:
1. `v11/v6_orb/config.py` — V6 frozen code default: `gap_filter_enabled: bool = False`
2. `v11/live/run_live.py` — `XAUUSD_ORB_CONFIG` doesn't override (inherits False)
3. `docs/superpowers/plans/2026-04-07-orb-llm-gate.md` — LLM gate design spec also has it False
4. `v11/replay/replay_runner.py` — replay also has it False

The most likely explanation: **conservative default during initial V11 integration**. When wiring V6 into V11's multi-strategy runner, the integrator disabled optional filters to minimize complexity and risk. The gap filter requires:
- Rolling history persistence (gap_rolling_history.json)
- At least 30 days of history before thresholds are meaningful
- IBKR bar fetching during the gap period (06:00-08:00 UTC)

None of these were set up during initial integration, and no one re-enabled it afterward.

### Implementation status

The gap filter is **fully implemented and functional** in V11:

| Component | Status | Location |
|---|---|---|
| V6 strategy logic | ✅ Complete | `v11/v6_orb/orb_strategy.py:115-135` |
| LiveMarketContext | ✅ Complete | `v11/v6_orb/live_context.py:132-185` |
| IBKR bar fetching | ✅ Complete | `v11/v6_orb/live_context.py:187+` |
| Rolling history persistence | ✅ Complete | `v11/v6_orb/live_context.py:267-290` |
| ORB adapter wiring | ✅ Complete | `v11/live/orb_adapter.py:189-193, 635-647` |
| Replay stub | ✅ Always passes | `v11/replay/replay_orb.py:114-125` |
| Config flag | ❌ Disabled | `gap_filter_enabled: bool = False` |

**Enabling the gap filter requires only a config change** — no code changes needed.

---

## 4.2 Backtest Comparison

### Limitation

The V11 replay simulator stubs the gap filter to always pass (returns `vol_passes=True, range_passes=True`). This means we **cannot** use the replay simulator to measure the gap filter's impact on V11's pipeline. The original V6 backtest results are the only available data.

### What we know

1. **V6 backtest showed clear improvement** — +4.2pp WR, +$0.82 Avg PnL
2. **The gap filter is a quality gate** — it skips days where the 06:00-08:00 UTC pre-trade window has low volatility, indicating a quiet market where breakouts are less reliable
3. **The filter requires rolling history** — needs ~30 days of gap data before thresholds stabilize. On first deployment, the filter will pass all days until enough history accumulates
4. **The filter is independent of the velocity issue** — gap filter uses bar-level data (1-min bars during 06:00-08:00), not tick-stream velocity. It works correctly on V11's feed.

### Interaction with velocity filter

The gap filter and velocity filter operate at different stages:
- **Gap filter:** Applied at trade window open (08:00 UTC) — skips the entire day if gap period was too quiet
- **Velocity filter:** Applied during trade window (08:00-16:00 UTC) — gates bracket placement on momentum

If the velocity filter is fixed (Task 2, using bar-level tick_count), both filters would work together:
1. Gap filter rejects quiet days at 08:00 → DONE_TODAY (no trades)
2. On active days, velocity filter gates bracket timing during 08:00-16:00

This is the intended V6 design. Currently, the velocity filter blocks ALL days (see Task 2), making the gap filter moot.

---

## 4.3 Recommendation

**Enable the gap filter** by setting `gap_filter_enabled=True` in `XAUUSD_ORB_CONFIG`.

### Rationale

1. **Validated improvement** — V6 research showed +4.2pp WR, +$0.82 Avg PnL
2. **Fully implemented** — no code changes needed, just a config flag
3. **Feed-independent** — uses bar-level data, not tick-stream velocity
4. **Safe default behavior** — passes all days until rolling history accumulates (~30 days)
5. **Complements velocity filter** — once velocity is fixed (Task 2), both filters work together as designed

### Config change

In `v11/live/run_live.py`, modify `XAUUSD_ORB_CONFIG`:

```python
XAUUSD_ORB_CONFIG = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0,
    range_end_hour=6,
    trade_start_hour=8,
    trade_end_hour=16,
    skip_weekdays=(2,),
    rr_ratio=2.5,
    min_range_size=1.0,
    max_range_size=15.0,
    velocity_filter_enabled=True,
    velocity_lookback_minutes=3,
    velocity_threshold=168.0,
    gap_filter_enabled=True,          # ENABLE — V6 research: +4.2pp WR, +$0.82 AvgPnL
    gap_vol_percentile=50.0,          # P50 threshold (skip quietest 50% of days)
    qty=1,
    point_value=1.0,
    price_decimals=2,
)
```

### Prerequisites

1. **Fix velocity filter first (Task 2)** — currently the velocity filter blocks all days, making the gap filter irrelevant. If velocity is fixed to use bar-level tick_count, both filters work as designed.
2. **If velocity is NOT fixed**, enabling gap filter alone would still help — it would skip ~50% of days at the start, and the stale breakout check would handle the rest. But ORB would still rarely place orders due to the velocity issue.

### Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Gap filter too aggressive on V11 | Low | Medium | P50 threshold is moderate; can lower to P40 |
| Insufficient rolling history at startup | Certain | Low | Filter passes all days until history accumulates |
| Gap filter skips profitable days | Medium | Medium | Monitor skipped days vs traded days performance |
| Interaction with LLM filter | Low | Low | Gap filter is pre-LLM; LLM only sees gap-passing days |

### Monitoring plan

After enabling:
1. Log each day's gap metrics (vol, range, pass/fail)
2. Track: days skipped by gap filter vs days traded
3. Compare WR and Avg PnL on gap-passing days vs all days
4. After 60 days, review whether P50 is the right threshold

---

*This report does NOT modify any live code. Awaiting human review.*
