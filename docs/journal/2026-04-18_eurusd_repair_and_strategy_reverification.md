# EURUSD Data Repair + Strategy Re-verification (2026-04-18)

**Session goal:** Diagnose and fix the EURUSD CSV corruption; re-run Darvas and 4H Level Retest backtests on clean data to confirm/refute the "edge evaporated" verdict.

**Result:**
- ✅ Data repaired — 277,941 rows (2018-01-01 → 2018-09-27) had OHLC/avg_spread/max_spread multiplied by 100× due to wrong Dukascopy pipet constant. Fixed in place.
- ❌ Strategies still have no OOS edge on clean data. The data bug was a red herring for the strategy evaporation.

---

## 1. Corruption root cause

The April-12 re-download by GLM 5.1 used a script derived from `build_1m_from_bi5.py` (which is hardcoded for XAUUSD with `PIPET = 0.001`). EURUSD requires `PIPET = 1e-5` (Dukascopy stores EURUSD ticks as 5-decimal-place pipets).

Result: every price column (open, high, low, close, avg_spread, max_spread) in rows from 2018-01-01 22:00 UTC to 2018-09-27 22:27 UTC was exactly 100× the correct value. Tick counts, volumes, and ratios were correct throughout.

Why only Jan-Sept 2018? The re-download was partial. After Sept 27 2018, the script either switched to a different code path or was running against pre-existing data that didn't need re-generation.

## 2. Repair method

`v11/backtest/repair_eurusd_prices.py` — detects rows with `close > 5` (EURUSD has never exceeded ~2 historically), divides price columns by 100. Writes to `eurusd_1m_tick_repaired.csv` without touching the original.

Validation: 100% exact match against the 85,133-row fresh sample from the proven `download_fx_universal.py` pipeline, across all 6 price columns.

File swap (manual):
```
mv eurusd_1m_tick.csv eurusd_1m_tick.corrupted.bak
mv eurusd_1m_tick_repaired.csv eurusd_1m_tick.csv
```

## 3. Strategy re-verification

### Darvas Config B (tc=20 bc=12 mxW=3.0 brk=2)

| Period | Stack | N | WR% | AvgR |
|---|---|---|---|---|
| OOS 2018-2023 | CONF+SMA+Trail | 74 | 41.9% | **−0.109** |
| OOS 2018-2023 | CONF+SMA | 74 | 35.1% | −0.228 |
| IS 2024-2026 | CONF+SMA+Trail | 30 | 53.3% | **+0.243** |

Original research claim: `+0.175 AvgR OOS` → actual on repaired data: `−0.109 AvgR OOS`.

Year-by-year OOS Config B CONF+SMA+Trail: 4 of 6 years negative (2018, 2019, 2020, 2023).

### 4H Level Retest

Every variant OOS-negative on repaired data:

| Config | OOS_N | OOS_WR | OOS_AvgR |
|---|---|---|---|
| Direct SL=0.3 RR=2.0 CONF | 853 | 37.0% | −0.199 |
| Retest pb=10-30 (the claimed "breakthrough") | 178 | 34.8% | **−0.393** |
| No Asian session filter | 604 | 36.9% | −0.189 |
| Direct SL=1.0 ATR (loosest) | 853 | 38.3% | −0.088 |

Original claim: `Retest pb=10-30: +0.135 AvgR OOS, 22.3 trades/yr` → actual: `−0.393 AvgR, 29.7 trades/yr`.

All 6 OOS years negative.

## 4. Hypotheses for the divergence from original research

None of these is proven; listed for future investigation if relevant:

1. **Dukascopy retroactive data corrections.** Dukascopy occasionally backfills corrections when upstream tick errors are discovered. The original research may have run on slightly different tick streams than what's served today.
2. **Script methodology drift.** The audit scripts (`research_darvas_param_audit.py`, `investigate_4h_levels_deep.py`) may handle gaps, sessions, ATR, or trailing stops in subtly different ways than the original investigation code. Small differences × small edges = verdict flip.
3. **Original edge was statistically fragile.** 22 trades/year × 6 years = 132 OOS trades. With AvgR of +0.135 and high per-trade variance, the confidence interval is wide enough that noise in inputs could flip the sign.

Running down these hypotheses is NOT worth doing right now — the EURUSD strategies are disabled for other reasons (paper trade focus, correlation with ORB) and re-enabling them would require a full fresh walk-forward anyway.

## 5. Practical decisions

- **Darvas stays disabled** (`darvas_enabled=False`). The v11 config flag prevents any accidental re-enable.
- **4H Level Retest stays disabled** (same flag gates both EURUSD strategies).
- **Do not chase the original research's claimed edge.** The "original" dataset may no longer be reproducible via Dukascopy, and the edge wasn't robust enough to survive a data refresh.
- **Keep `eurusd_1m_tick.corrupted.bak`** for 1-2 weeks as insurance, then delete.
- **Paper trade ORB.** Still the primary roadmap item. Not yet started. Everything else is a distraction.

## 6. Files

- `v11/backtest/compare_fresh_vs_corrupted_eurusd.py` — diagnostic script, surfaced the 100× smoking gun
- `v11/backtest/repair_eurusd_prices.py` — the repair itself
- `v11/backtest/darvas_repaired_output.txt` — gitignored, but generated locally
- `v11/backtest/4h_retest_repaired_output.txt` — same
- `C:\nautilus0\data\1m_csv\eurusd_1m_tick.csv` — now the repaired file
- `C:\nautilus0\data\1m_csv\eurusd_1m_tick.corrupted.bak` — original, for safety
