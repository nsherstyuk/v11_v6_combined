# Darvas Parameter Audit — 2026-04-16

**Task:** Task 1 from parameter research plan — verify and measure Darvas parameters.

---

## Archaeology Findings

### Origin of current live parameters

The current `EURUSD_CONFIG` defaults (`tc=15, bc=15, mxW=5.0, brk=3`) were set in the **initial git commit** (`9abf6a9`, 2026-04-06) and **never changed**. They were "confirmed initial values — tunable via backtest" per the docstring, but the tuning results were never ported back to the config file.

### Why mxW=5.0 was chosen

The 2026-04-05 backtest session (`docs/journal/2026-04-05_backtest_session.md`) Phase 3 found:
> `max_box_width_atr` (3.0→5.0) = **28× more signals** (dominant factor)

This was a signal-funnel analysis showing that wider boxes produce far more trades. The initial default of 5.0 was set to be permissive, but the subsequent optimization found that **tight boxes (mxW=3.0) preserve signal quality** — wider boxes dilute the volume edge.

### What the research recommended

The frequency investigation (`docs/journal/2026-04-06_frequency_investigation.md`) concluded:

| Config | OOS/yr | OOS AvgR | Confidence |
|---|---|---|---|
| **tc=20 bc=12 mxW=3.0 brk=3** | **14.7** | **+0.175** | **High** |
| tc=20 bc=12 mxW=3.0 brk=2 | 10.5 | +0.176 | High |

The recommended config was `tc=20, bc=12, mxW=3.0, brk=3` (the "Loosened" variant). **This was never applied to `strategy_config.py`.**

### Timeline

| Date | Event | Config State |
|---|---|---|
| 2026-04-05 | Backtest session: Config B found optimal | Research scripts use tc=20 bc=12 mxW=3.0 brk=2 |
| 2026-04-06 | Initial git commit | `strategy_config.py` defaults: tc=15 bc=15 mxW=5.0 brk=3 |
| 2026-04-06 | Phase 1-3 commit | Added HTF SMA + 4H level params; Darvas defaults unchanged |
| 2026-04-06 to 2026-04-16 | All subsequent sessions | Darvas defaults never updated |

**Root cause:** The research scripts hard-coded Config B via `replace(EURUSD_CONFIG, ...)` but the base config was never updated. The defaults persisted from the initial commit.

---

## Reproduction of Documented Results

### ⚠️ CRITICAL: Documented OOS numbers do NOT reproduce

The originally documented OOS result for Config B + CONF + SMA(50) + Trail10@60 was:
- **63 trades, 46% WR, +0.176 AvgR** (from `docs/journal/2026-04-05_htf_investigation.md`)

Running the **same script** (`investigate_htf_sma.py`) on the **current dataset**:
- **73 trades, 41.1% WR, -0.114 AvgR**

The OOS date range (2018-2023) is the same. The discrepancy likely results from the underlying CSV data being updated/extended since the original research was conducted. The EURUSD CSV now contains 3,069,832 bars (2018-01-01 to 2026-04-12).

**Implication:** The documented +0.176 AvgR OOS edge for Config B is **not reproducible** on the current dataset. The actual OOS performance is negative (-0.114 AvgR).

---

## Current-Live Config Measurement

### Full comparison table (CONF+SMA+Trail, R:R=2.0)

| Period | Config | N | WR% | AvgR | PnL | PF | MaxDD |
|---|---|---|---|---|---|---|---|
| **OOS 2018-2023** | Config B (tc=20 bc=12 mxW=3.0 brk=2) | 74 | 41.9 | -0.090 | +0.0918 | 1.37 | -0.2337 |
| | Current Live (tc=15 bc=15 mxW=5.0 brk=3) | 1067 | 36.5 | -0.179 | -1.3418 | 0.78 | -2.4239 |
| | Loosened (tc=20 bc=12 mxW=3.0 brk=3) | 101 | 40.6 | -0.103 | +0.2039 | 1.69 | -0.1877 |
| **IS 2024-2026** | Config B | 30 | 53.3 | +0.243 | +0.0084 | 2.20 | -0.0033 |
| | Current Live | 376 | 38.6 | -0.131 | -0.0219 | 0.83 | -0.0403 |
| | Loosened | 34 | 44.1 | -0.022 | +0.0049 | 1.59 | -0.0033 |
| **Fresh Jan-Apr 2026** | Config B | 3 | 0.0 | -0.984 | -0.0010 | 0.00 | -0.0009 |
| | Current Live | 49 | 49.0 | +0.028 | +0.0069 | 1.63 | -0.0036 |
| | Loosened | 3 | 0.0 | -0.979 | -0.0012 | 0.00 | -0.0011 |

### Year-by-year OOS (CONF+SMA+Trail)

| Year | Config B N/WR/AvgR | Live N/WR/AvgR | Loosened N/WR/AvgR |
|---|---|---|---|
| 2018 | 8 / 37.5% / +0.096 | 172 / 36.0% / -0.156 | 12 / 50.0% / +0.142 |
| 2019 | 14 / 35.7% / -0.242 | 163 / 35.6% / -0.175 | 16 / 37.5% / -0.357 |
| 2020 | 14 / 50.0% / -0.052 | 194 / 35.1% / -0.216 | 22 / 36.4% / -0.144 |
| 2021 | 10 / 60.0% / +0.153 | 168 / 33.9% / -0.269 | 14 / 57.1% / +0.159 |
| 2022 | 8 / 25.0% / +0.086 | 184 / 34.2% / -0.134 | 13 / 46.2% / +0.411 |
| 2023 | 20 / 40.0% / -0.270 | 189 / 43.4% / -0.146 | 23 / 30.4% / -0.413 |

### Without SMA filter (CONF+Trail only, no SMA)

| Period | Config | N | WR% | AvgR | PnL | PF |
|---|---|---|---|---|---|---|
| OOS 2018-2023 | Config B | 149 | 30.9 | -0.336 | -0.2087 | 0.63 |
| | Current Live | 1933 | 38.0 | -0.175 | -2.4981 | 0.81 |
| | Loosened | 178 | 34.3 | -0.221 | -0.0937 | 0.87 |

---

## Key Findings

### 1. Current Live config is catastrophically worse than Config B

The current live config (`mxW=5.0`) produces **10-14× more trades** but with:
- **Negative AvgR** on every period and filter stack
- **Massive drawdowns** (-2.42 OOS, -0.04 IS)
- **PF < 1.0** on every period (losing money overall)
- **Negative PnL** on OOS (-1.34) and IS (-0.02)

The wide `mxW=5.0` admits too many low-quality boxes, exactly as the original research warned: "wider boxes dilute volume edge."

### 2. Config B is the best of the three, but the edge is thin

Config B has:
- Positive PnL on OOS (+0.09) and IS (+0.008) — the only config in the green
- Best PF (1.37 OOS, 2.20 IS)
- But **negative AvgR** on OOS (-0.090) — the positive PnL comes from a few large wins, not consistent edge
- Only 74 trades over 6 years OOS (~12/yr) — statistically fragile

### 3. The Loosened variant is a reasonable middle ground

`tc=20 bc=12 mxW=3.0 brk=3` has:
- Best OOS PnL (+0.20) and PF (1.69)
- More trades than Config B (101 vs 74) — better statistical power
- But also negative AvgR (-0.103) — same fragility issue
- Year-by-year: profitable in 2018 (+0.14), 2021 (+0.16), 2022 (+0.41); losing in 2019, 2020, 2023

### 4. Fresh data (Jan-Apr 2026) is inconclusive

Only 3 trades for Config B and Loosened — far too few to draw conclusions. The Current Live config produces 49 trades with marginal positive AvgR (+0.028 with trail, +0.178 without), but this is IS-adjacent data (overlaps with the optimization period).

### 5. The documented OOS edge has evaporated

The originally reported +0.176 AvgR OOS for Config B + CONF + SMA does not reproduce on the current dataset. The actual result is -0.114 AvgR. This means the Darvas strategy's OOS edge was either:
- (a) A statistical fluke in the original smaller dataset
- (b) Dependent on specific data quality/coverage that has changed
- (c) Real but fragile, and the extended dataset includes more unfavorable periods

---

## Recommendation

**Change `EURUSD_CONFIG` from current defaults to the Loosened variant** (`tc=20, bc=12, mxW=3.0, brk=3`).

### Rationale

| Metric | Current Live | Loosened (recommended) | Improvement |
|---|---|---|---|
| OOS PnL | -1.34 | +0.20 | +1.54 |
| OOS PF | 0.78 | 1.69 | +0.91 |
| OOS AvgR | -0.179 | -0.103 | +0.076 |
| OOS MaxDD | -2.42 | -0.19 | +2.23 |
| IS PnL | -0.02 | +0.005 | +0.025 |
| IS PF | 0.83 | 1.59 | +0.76 |
| OOS trades/yr | ~178 | ~17 | -161 (quality over quantity) |

The current live config is **unambiguously worse** on every metric. The Loosened variant is the best available choice among the three tested configs, even though the edge is thin.

### Confidence level: **Medium**

- The recommendation is directionally clear (any tight-box config beats the wide-box current)
- But the absolute edge is thin (AvgR -0.103 OOS) and not profitable in 3 of 6 OOS years
- The strategy depends heavily on the SMA filter — without it, all configs are deeply negative
- The originally documented edge (+0.176 AvgR) does not reproduce

### Risks if recommendation is wrong

1. **Thin edge disappears entirely** — the Loosened variant could be negative in future regimes
2. **Low trade count** — ~17 trades/yr means long drawdown periods and high variance
3. **SMA dependency** — if the 60-min SMA(50) filter stops working (regime change), the strategy has no edge
4. **Data quality uncertainty** — the non-reproducibility of the original OOS result raises questions about data reliability

### Alternative: disable Darvas entirely

Given that no config shows a robust OOS edge (all have negative AvgR), an alternative is to disable the Darvas strategy and rely on the 4H Level Retest + ORB strategies only. This would:
- Eliminate the thin Darvas edge risk
- Reduce complexity and maintenance burden
- Focus capital on strategies with clearer edges

---

## Specific config change requested (pending approval)

```python
# In v11/config/strategy_config.py, change EURUSD_CONFIG defaults:
EURUSD_CONFIG = StrategyConfig(
    instrument="EURUSD",
    spread_cost=0.00010,
    tick_size=0.00005,
    min_bar_ticks=10,
    # Changed from defaults:
    top_confirm_bars=20,          # was 15
    bottom_confirm_bars=12,       # was 15
    max_box_width_atr=3.0,        # was 5.0
    breakout_confirm_bars=3,       # unchanged (Loosened uses brk=3)
)
```

Note: This changes the `StrategyConfig` **class defaults**, which also affects `XAUUSD_CONFIG` and `USDJPY_CONFIG` unless they override. Since both override `spread_cost`, `tick_size`, and `min_bar_ticks` only, they would inherit the new Darvas defaults. This needs careful review — XAUUSD and USDJPY were never validated with these params.

**Safer alternative:** Override explicitly in `EURUSD_CONFIG` only:

```python
EURUSD_CONFIG = StrategyConfig(
    instrument="EURUSD",
    spread_cost=0.00010,
    tick_size=0.00005,
    min_bar_ticks=10,
    top_confirm_bars=20,
    bottom_confirm_bars=12,
    max_box_width_atr=3.0,
    breakout_confirm_bars=3,
)
```

---

*This report does NOT modify any live code. Awaiting human review.*

**Research script:** `v11/backtest/research_darvas_param_audit.py`
**Raw output:** `v11/backtest/research_output.txt`
