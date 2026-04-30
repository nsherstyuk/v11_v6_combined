# AUD/NZD — RETRACTION: Phase 1.5 and Phase 2 results were a bug

**Date:** 2026-04-30
**Status:** RETRACTION. Two prior journal entries published in good faith reported a strong mean-reversion edge. Both were artifacts of a time-pairing bug. The corrected analysis shows **no tradable edge exists**.
**Affected commits:** `294be36` (Phase 1.5 partial), `f2cb6a2` (Phase 1.5 full-data), `c575ca9` (Phase 2 baseline).
**Fix commit:** to follow this journal.

---

## 1. The bug

Both `investigate_audnzd_reversion.py` and `backtest_audnzd_phase2.py` anchored bars to a "night" date with:

```python
sub["night"] = (sub["timestamp"] - pd.Timedelta(hours=2)).dt.date
```

For first_half bars (22:00 D – 01:59 D+1) this groups them under date D. Correct.

For second_half bars (02:00 D+1 – 05:59 D+1), `(02:00 D+1) − 2h = 00:00 D+1`, which has date D+1 — **not D**. So:

- first_half night=D = bars 22:00 D through 01:59 D+1 (**correct**)
- second_half night=D = bars 02:00 D through 05:59 D (the morning **before** the overnight, not the morning after)

The "pair" the script joined together was therefore:
- Entry timestamp: 01:59 of date D+1 (correct end of overnight).
- Exit timestamp: 05:59 of date D — **20 hours earlier than entry, not 4 hours later**.

Phase 2 trades were tagged with `entry_ts > exit_ts`. Visible in the committed `phase2_trades.csv`:

```
entry_ts: 2018-01-05 01:59:00+00:00
exit_ts:  2018-01-04 05:59:00+00:00
```

The bug was caught when Phase 3 MAE/MFE traversal returned `bars_in_trade = 0` for every trade — there are no bars between entry and an earlier exit.

## 2. Why the bug produced *favorable*-looking numbers

Mean-reverting stationary processes are time-symmetric in this regard: if at time T price is far from the long-run mean, then on average at T−k the price was closer to the mean (because the deviation built up over time). So:

```
corr(dev_T, price_{T−k} − price_T) < 0   (looking BACKWARD)
corr(dev_T, price_{T+k} − price_T) < 0   (looking FORWARD)
```

The script computed the first quantity but interpreted it as the second. That gave:

- Strong negative correlation (−0.55 full-sample) — present in *both* directions of time, doesn't prove forward tradability.
- Beautiful 70% win rates and PF > 3 in Phase 2 — but the "trades" exited 20h before they entered, which is not a thing you can do.

## 3. The fix

Anchor offset must be ≥ 6h to keep second-half bars on the correct night key:

```python
first_half["night"] = (first_half["timestamp"] - pd.Timedelta(hours=6)).dt.date
second_half["night"] = (second_half["timestamp"] - pd.Timedelta(hours=6)).dt.date
```

Now:
- 22:00 D − 6h = 16:00 D → date D ✓
- 01:59 D+1 − 6h = 19:59 D → date D ✓
- 02:00 D+1 − 6h = 20:00 D → date D ✓
- 05:59 D+1 − 6h = 23:59 D → date D ✓

Both halves of one continuous overnight session share the same night key, and entry < exit.

(Note: Phase 1 EDA `investigate_audnzd_eda.py` already used `pd.Timedelta(hours=end_h)` with `end_h=6` — it was correct all along. Its FAIL verdict on activity was correct. Phase 1.5 introduced the `2h` offset to "split the night" and broke it.)

## 4. Corrected Phase 1.5 results (2105 nights, 2018-01 → 2026-04)

| Metric | Buggy result | **Corrected** | Threshold | Status |
|---|---:|---:|---|:---:|
| Pearson corr(dev_02, move_06) | −0.547 | **+0.010** | < −0.10 | **FAIL** |
| Hit rate moving toward midpoint | 66.1% | **49.0%** | > 55% | **FAIL** |
| Mean net after 4-pip cost | +7.99 pips | **−4.09 pips** | > 0 | **FAIL** |

Year-by-year corr ranges −0.08 to +0.16 — random. No year has hit_rate > 55% except 2021 (55.4%, n=260). Stratification by stretch is flat: every magnitude bin has hit_rate 47–53%. There is no edge.

## 5. Corrected Phase 2 baseline

The same fix produces 1173 trades after the 5-pip threshold:

| Split | n | WR | AvgR | PF | MaxDD |
|---|---:|---:|---:|---:|---:|
| ALL | 1173 | 35.4% | −4.36 pips | 0.46 | −5,119.5 |
| **OOS** (2018-23) | 852 | 35.9% | −3.97 pips | 0.50 | −3,385.6 |
| **IS** (2024-26) | 321 | 34.0% | −5.40 pips | 0.34 | −1,708.2 |

Every year negative. BUY and SELL sides both unprofitable (PF 0.47 / 0.45). The strategy bleeds at exactly the realized spread cost, which is what you'd expect from a noise-driven trade entered at random.

## 6. What the data actually says about AUD/NZD overnight

Combining the original Phase 1 EDA (correct) with the corrected Phase 1.5/Phase 2:

- The 22:00–06:00 UTC window is **not** quiet (Phase 1: quiet/busy ratio 0.89, median spread 2.14 pips — both failed thresholds).
- Price at 02:00 UTC has **no statistically meaningful tendency** to revert toward the first-half midpoint by 06:00 (Phase 1.5 corrected: corr +0.01).
- A naive overnight-reversion trade **loses** the spread (~4 pips/round-trip) every night on average, with high variance.

The hypothesis "AUD/NZD overnight Asian session shows tradeable mean reversion" is **falsified** by the data we have.

## 7. What I am retracting

- `2026-04-29_audnzd_phase15_reversion_verdict.md` — claim of PASS on partial data. **Retracted.** Numbers reflected a backwards-in-time correlation, not a tradable edge.
- `2026-04-30_audnzd_phase15_full_data_confirms.md` — claim of full-data PASS, "IS stronger than OOS." **Retracted.** Same bug; the apparent strengthening over time is consistent with a random walk being increasingly time-symmetric on average as more data accumulates.
- `2026-04-30_audnzd_phase2_baseline.md` — claim of OOS PF 3.54 baseline. **Retracted.** The trades were impossible (exit before entry).

These journals are kept in the repo (do not delete history), but they are superseded by this entry. Anyone reading them must read this retraction first.

## 8. What I should have caught earlier

- The asymmetric offset between the two halves was a code smell I missed. Phase 1 EDA used `end_h` (6h) as the offset; Phase 1.5 swapped to `2h` to "anchor on the start" — but a single offset can't correctly anchor both halves *if it isn't ≥ end_h*. There was no test verifying `entry_ts < exit_ts`.
- Numbers that look too good are evidence of a bug, not of a strategy. PF 3.5 with no parameter tuning on 8 years of FX data should have triggered immediate skepticism. The Phase 2 journal §4c flagged this as a concern but didn't act on it. The action would have been: print one trade, look at its `entry_ts` and `exit_ts`, sanity-check.
- Phase 3 MAE/MFE was the diagnostic that exposed it — `bars_in_trade = 0` is a sentinel. Building Phase 3 immediately after Phase 2 is what surfaced the bug. That's the procedural lesson: do not rest on a strong-looking backtest without an intra-trade walk-through.

## 9. Decision

- Commit the offset fix in both scripts.
- Commit corrected outputs (`phase15_corrected.txt`, `phase2_corrected.txt`, regenerated `reversion_nights.csv`, regenerated `phase2_trades.csv`).
- Commit this retraction.
- **Stop AUDNZD investigation.** The hypothesis is falsified. The 4 hours spent downloading data was not wasted (we have a clean dataset for any future FX pairs work), but the specific Asian-session reversion thesis is dead.
- Do not start Phase 3 / 4. There is nothing for them to filter.
- Update the original `2026-04-29_audnzd_periodic_reversion_idea.md` plan with a status block at the top noting the falsification.

## 10. Note on autonomous loop discipline

The user enabled an autonomous loop overnight. Within that loop I executed the originally-prescribed Phase 1.5 full-data revalidation and Phase 2 baseline because the design was already settled in journal entries. Both used the same bugged code path that produced the original Phase 1.5 result.

The retraction would have surfaced a day later regardless of the loop — the bug existed in the code committed before the loop began. But the loop did add two more committed journal entries that are now invalidated. Lesson: when a strategy passes a gate, a sanity diagnostic (here, "look at one trade's timestamps") belongs *before* extending the work, not after.
