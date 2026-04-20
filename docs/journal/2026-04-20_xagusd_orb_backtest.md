# XAGUSD ORB Backtest — Verdict: Don't Add Silver

**Date:** 2026-04-20
**Question:** Does ORB have an edge on XAGUSD comparable to XAUUSD, and is silver a viable diversification target?
**Verdict:** NO on both counts. Keep ORB XAUUSD-only.

---

## 1. Data

- Downloaded via `v11/backtest/download_xagusd_sequential.py` (the sequential / rate-limit-safe downloader built today)
- 2,889,620 1-min bars covering 2018-01-01 → 2026-04-17
- 24 hours wall time for the download
- Clean: no NaN, no phantom columns, prices progress coherently
- Sanity year-by-year: silver $15 in 2018 → ~$82 mean by 2026 (consistent with the gold data's own $1200 → $4800 rally)

## 2. Backtest

Ran `python -m v11.backtest.investigate_orb_xauusd --symbol XAGUSD` — same canonical script used for XAUUSD, with a scale-adjusted config (absolute range filters scaled ~100× from gold to match silver's ~100× lower price).

### OOS 2018-2023 summary (XAGUSD)

| Config | N | WR% | AvgR | PF | MaxDD |
|---|---|---|---|---|---|
| velocity=ON, gap=OFF | 226 | 43.4% | **+0.004** | 1.01 | 8.75 |
| velocity=ON, gap=ON | 131 | 38.2% | **−0.076** | 0.82 | 10.80 |
| velocity=OFF, gap=OFF | 558 | 40.7% | **−0.031** | 0.94 | 31.98 |
| **velocity=OFF, gap=ON** | **276** | **42.0%** | **−0.027** | **0.94** | **21.31** |
| velocity=ON, gap=ON, Wed=include | 154 | 39.0% | −0.084 | 0.80 | 13.50 |

**No edge.** Every config is either zero or negative OOS. The nominally "best" variant (velocity=ON, gap=OFF) is statistically indistinguishable from zero (+0.004 across 226 trades).

### Side-by-side vs XAUUSD

The critical comparison (velocity=OFF, gap=ON — the winner for gold):

|  | XAUUSD (canonical) | XAGUSD |
|---|---|---|
| OOS trades | 315 | 276 |
| OOS WR% | **49.5%** | 42.0% |
| OOS AvgR | **+0.183** | **−0.027** |
| OOS PF | 1.48 | 0.94 |
| OOS MaxDD (R) | 6.5 | 21.3 |
| Positive OOS years (of 6) | 6 | 2 |

XAUUSD has a clear edge; XAGUSD does not. It's not a parameter-tuning issue — we tested 5 config variants and none produced a positive OOS AvgR.

### Slippage stress test (XAGUSD, velocity=ON gap=ON, OOS)

Catastrophic:

| Slippage/side | AvgR |
|---|---|
| 0.0 | −0.076 |
| 0.1 | **−0.985** |
| 0.2 | **−1.895** |

Silver's absolute ranges are small (typical $0.30-$1.00 Asian range). A 0.1-point slippage is 10–30% of the R unit — an order of magnitude worse than gold's ~2%. Even if XAGUSD had a mild positive edge at zero slippage, realistic execution costs would vaporize it instantly.

### Direction breakdown (XAGUSD, OOS)

| Direction | N | WR% | AvgR |
|---|---|---|---|
| LONG | 63 | 31.7% | −0.091 |
| SHORT | 68 | 44.1% | −0.061 |

Both sides negative. Not a one-side bias issue.

## 3. Correlation vs XAUUSD

Daily-return correlation (XAUUSD vs XAGUSD) on 2,528 overlapping days:

- **Overall: 0.783** (high)
- Per-year range: 0.66 (2025) to 0.88 (2026)
- Gold/silver price ratio: 76.7 → 59.0 over the period

This is the classic precious-metals correlation. Silver moves with gold (driven by USD strength, inflation expectations, monetary policy, safe-haven flows). There are short periods of divergence but on average silver's daily moves explain ~61% of gold's variance (r² = 0.613).

**Even if XAGUSD had an ORB edge (it doesn't), 0.78 correlation means running both is concentration, not diversification.** True diversification would require a strategy on an asset with near-zero correlation to gold (equities during risk-on regimes, certain FX crosses, volatility products).

## 4. Why silver doesn't work for ORB

Two hypotheses, both likely contributing:

1. **Session structure.** ORB assumes an Asian-range consolidation followed by a London/NY directional break. XAUUSD shows this pattern because gold is heavily traded in all three sessions. Silver is more US-session-dominated and less liquid in Asian hours — the Asian range often has very little real price discovery, so the breakout is against noise rather than against structured consolidation.

2. **Spread-to-range ratio.** Silver's bid-ask spread is a much larger fraction of its daily range than gold's. Market-makers price silver wider relative to realized volatility. This makes the strategy's slippage sensitivity catastrophic (see the stress test above).

## 5. Decision

**Do not wire XAGUSD into the live system.**

- No OOS edge on any config tested
- High correlation with existing XAUUSD position — not diversification
- Slippage destroys any remaining marginal edge
- Silver ORB is not a bug to fix; the strategy-instrument pairing is structurally wrong

The `--symbol XAGUSD` backtest variant stays in the canonical script for future "has this been re-tested on newer data?" lookups. No code changes to `run_live.py` or `LiveConfig`.

## 6. What this closes out

Track 4 of the parallel-tracks plan is done:

| # | Track | Outcome |
|---|---|---|
| 1 | Paper trade validation checklist | ✅ Complete |
| 2 | Replay validation + silent-order-fix regression tests | ✅ Complete |
| 3 | Reconcile ORB backtests (delete Cascade's duplicate) | ✅ Complete |
| 4 | XAGUSD ORB backtest | ✅ Complete — **negative result, correctly parked** |

All parallel-track work is now done. The primary loop — paper trade ORB XAUUSD and observe — is the remaining focus.

## 7. Files

- `v11/backtest/download_xagusd_sequential.py` — the rate-limit-safe sequential downloader (also useful for future Dukascopy downloads of other instruments)
- `v11/backtest/investigate_orb_xauusd.py` — canonical ORB backtest, now accepts `--symbol XAGUSD`
- `v11/backtest/data_loader.py` — XAGUSD registered in INSTRUMENT_FILE_MAP
- `C:\nautilus0\data\1m_csv\xagusd_1m_tick.csv` — the downloaded dataset (2.89M bars)

## 8. What I did NOT do

- **No changes to `XAUUSD_ORB_CONFIG`** — ORB stays as configured
- **No `xagusd_enabled` flag** — not adding the infrastructure for a strategy that doesn't work
- **No adapter for XAGUSD ORB** — same reason
- **No further XAGUSD research** — the verdict is decisive; moving on

Per the scope discipline agreed earlier: negative result is documented and filed. Do not revisit unless something materially changes (e.g. ORB XAUUSD paper trading produces unexpected results that make regime-specific research relevant).
