# FX-ORB grid + GBPUSD regime sanity + LLM gate — full session

**Date:** 2026-04-30
**Status:** Done. GBPUSD ORB has a real 2018-2024 edge that broke in 2025. Lower RR doesn't salvage it. LLM regime gate anti-selects (second confirmation of price-only LLM filter failure). XAUUSD ORB remains the only validated v11 strategy.
**Scripts:**
- `v11/backtest/orb_fx_grid.py`
- `v11/backtest/orb_gbpusd_deepdive.py`
- `v11/backtest/gbpusd_regime_sanity.py`
- `v11/backtest/orb_gbpusd_rr_sweep.py`
- `v11/backtest/orb_gbpusd_llm_gate.py`

---

## 1. Pre-registered FX-ORB template

Single template, no per-pair tuning, applied to 6 FX majors + XAUUSD anchor:

- Range window: 00:00–06:00 UTC (Asian session)
- Trade window: 08:00–16:00 UTC (London)
- RR: 2.5, velocity OFF, gap filter ON, no Wed-skip
- Range as % of price filter: 0.05–2.0%

| Symbol | n | OOS AvgR (raw) | OOS AvgR (1pip RT) | OOS PF | Verdict |
|---|---:|---:|---:|---:|---|
| **GBPUSD** | **683** | **+0.128** | **+0.092** | **1.26** | **Pass — drill down** |
| EURUSD | — | flat | flat | ~1.0 | drop |
| AUDUSD | — | flat | neg | <1.0 | drop |
| NZDUSD | — | flat | neg | <1.0 | drop |
| USDCAD | — | flat | neg | <1.0 | drop |
| USDCHF | — | flat | neg | <1.0 | drop |
| XAUUSD (anchor) | — | +0.055 | — | — | confirms harness |

Only GBPUSD passed the gate. XAUUSD anchor reproduced the known edge — confirms harness is consistent with `investigate_orb_xauusd.py`.

## 2. GBPUSD year-by-year deep-dive

7 of 9 years positive; 2025 broke; 2026 partial sample disastrous.

| Year | n | AvgR | Note |
|---|---:|---:|---|
| 2018 | ~80 | + | green |
| 2019 | ~80 | + | green |
| 2020 | ~80 | + | green |
| 2021 | ~75 | + | green |
| 2022 | ~80 | + | green |
| 2023 | ~75 | + | green |
| 2024 | ~75 | + | green |
| **2025** | ~80 | **−0.071** | **broke** |
| 2026 (YTD) | ~25 | −0.308 | partial |

LONG/SHORT roughly symmetric. Wed/Fri strongest; Mon dead. No single direction or weekday explains 2025 break.

## 3. Regime sanity check

Two-axis check on whether the 2025 break is data quality or genuine market change.

**Data quality (per-year H1 bar counts, gap distribution, anomaly rate):** clean. No structural anomaly between 2018-2023 and 2024-2026.

**Microstructure (per-year):**

| Era | Median follow-through (pips past Asian range edge) | Clean-hold rate |
|---|---:|---:|
| 2018-2023 avg | ~43 | 77–84% |
| 2024-2026 avg | ~32 | dropped 2025 to **71.7%** |

Follow-through compressed ~25%. Clean-hold dropped 5+ pp in 2025. **Verdict: genuine regime change**, not a data artifact.

## 4. RR sweep — can a lower target salvage 2025?

Sweep over RR ∈ {1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0}, full sample 2018-2026.

- Lower RR is **uniformly worse** across the full sample.
- 2025 is negative at **every** RR (−0.021 to −0.071).
- Higher RR (2.5–3.0) consistently best historically — strategy depends on outlier big-trend days and choking the runners hurts more than the win-rate gain helps.

Verdict: target tuning cannot save 2025. **Drop GBPUSD ORB.**

## 5. LLM regime gate (Haiku 4.5 via OpenRouter)

Per-day regime classifier. Trailing features only (no lookahead): 30d follow-through median pips, 30d clean-hold rate, prior 5 outcomes, today's Asian range, weekday, date. JSON output: take/skip + confidence + concern + reasoning. SHA-256 cache by (model, payload).

n = 946 trades classified. Cost: **$1.17** (567K in / 127K out).

| Bucket | n | AvgR |
|---|---:|---:|
| LLM APPROVE (take) | 245 | **−0.006** |
| LLM REJECT (skip) | 701 | **+0.125** |

**LLM anti-selected in 7 of 9 years.** Following its judgment wipes out the entire +84R baseline expectancy.

The model reaches for textbook risk narratives ("BoE policy inflection", "vol compression", "fading after losing streak", "elevated ATR") and skips the structurally-sound days that contain the high-payoff outliers a breakout strategy depends on.

This is the **second confirmed instance** of LLM anti-selection on price-only filter contexts (first: Darvas/Sonnet 4.6, 2026-04). Saved as `feedback_llm_filter_anti_select.md`.

## 6. Verdict

- **GBPUSD ORB:** real edge 2018-2024, broke in 2025 (regime change). Lower RR doesn't help. LLM gate actively anti-selects. **Drop.**
- **All other FX majors (EUR/AUD/NZD/CAD/CHF):** no edge under the pre-registered template. Drop.
- **XAUUSD ORB:** remains the **only validated v11 strategy**.
- **LLM-as-filter on price-only contexts:** confirmed bad pattern, twice. Reserve LLM for event-driven gating where it has true informational asymmetry (FOMC/BoE/CPI/earnings/known crisis windows).

## 7. Next step

Unchanged from prior weeks: **start paper-trading XAUUSD ORB.** `start_v11.bat --live --no-llm`. Run 4-6 weeks. Collect 20+ fills. The project keeps producing more research instead of starting paper — that is the actual blocker.

---

**See also:**
- `memory/feedback_llm_filter_anti_select.md` — feedback memory, both LLM-filter failure instances
- `memory/project_v11_status.md` — updated strategy verdict table
- `docs/journal/2026-04-16_strategy_review_and_plan.md` — master strategy audit
- `v11/backtest/results/llm_regime_gate_cache/` — Haiku trial cache
