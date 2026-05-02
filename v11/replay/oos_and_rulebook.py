"""OOS validation + empirical rulebook + three-way filter comparison.

Pipeline:
  1. Run param sweep on 2018-01..2022-12 (IS).
  2. Pick best config by E[R] with n>=15.
  3. Apply best config to 2023-01..2026-04 (OOS). Report.
  4. Mine IS trial outcomes for conditions correlated with TP vs SL.
     Output: rules with empirical support stats.
  5. Apply empirical rules as a filter on OOS. Report.
  6. (Separate) hand off OOS contexts + rulebook text to LLM judge.

We deliberately DO NOT use OOS data when picking config or building rules.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime
from itertools import product
from pathlib import Path

import numpy as np

from v11.config.strategy_config import EURUSD_CONFIG, StrategyConfig
from v11.core.darvas_detector import DarvasDetector
from v11.replay.darvas_judge_example import (
    build_signal_context, load_resampled_bars, walk_outcome,
)
from v11.llm.models import SignalContext


SPLIT_YEAR = 2023  # IS: <2023, OOS: >=2023


def collect_for_cfg(cfg: StrategyConfig, bars, rr: float, max_hold: int):
    """Return (trials_with_outcome, contexts) parallel lists."""
    det = DarvasDetector(cfg)
    trials = []
    ctxs = []
    for i, bar in enumerate(bars):
        sig = det.add_bar(bar)
        if sig is None:
            continue
        ctx = build_signal_context("EURUSD", sig, bars[: i + 1])
        out = walk_outcome(sig, bars[i + 1: i + 1 + max_hold], rr=rr, max_bars=max_hold)
        trials.append({
            "ts": sig.timestamp.isoformat(),
            "year": sig.timestamp.year,
            "direction": sig.direction.value,
            "session": ctx.session,
            "box_width_atr": float(sig.box.width_atr),
            "duration": int(sig.box.duration_bars),
            "atr_vs_avg": float(ctx.atr_vs_avg),
            "buy_ratio": float(ctx.buy_ratio_at_breakout),
            "vol_class": ctx.volume_classification,
            "tick_quality": ctx.tick_quality,
            "outcome": "TP" if out.result == "TP" else ("SL" if out.result == "SL" else "TIME"),
            "pnl_R": float(out.pnl_R),
            "is_TP": out.result == "TP",
        })
        ctxs.append(ctx)
    return trials, ctxs


def split_is_oos(trials, ctxs):
    is_t, is_c, oos_t, oos_c = [], [], [], []
    for t, c in zip(trials, ctxs):
        if t["year"] < SPLIT_YEAR:
            is_t.append(t); is_c.append(c)
        else:
            oos_t.append(t); oos_c.append(c)
    return is_t, is_c, oos_t, oos_c


def cfg_metric(trials):
    n = len(trials)
    if n == 0:
        return {"n": 0, "tp": 0, "tp_pct": 0.0, "exp_R": 0.0}
    tp = sum(t["is_TP"] for t in trials)
    return {"n": n, "tp": tp, "tp_pct": tp / n * 100,
            "exp_R": float(np.mean([t["pnl_R"] for t in trials]))}


# ── Param sweep IS only ──────────────────────────────────────────────────────

def is_param_sweep(bars_is, rr: float, max_hold: int) -> StrategyConfig:
    grid = list(product(
        [10, 15, 20], [15, 25, 40], [1, 2, 3],
        [(0.3, 5.0), (0.6, 3.0), (1.0, 2.5)],
    ))
    print(f"\n=== IS param sweep on {len(bars_is)} bars (years <{SPLIT_YEAR}) ===")
    print(f"  {'tcb':>3s}  {'mbd':>3s}  {'bcb':>3s}  {'wmin':>4s}  {'wmax':>4s}  "
          f"{'n':>3s}  {'TP%':>5s}  {'E[R]':>7s}")
    best = None
    for tcb, mbd, bcb, (wmin, wmax) in grid:
        cfg = replace(EURUSD_CONFIG, top_confirm_bars=tcb, bottom_confirm_bars=tcb,
                      min_box_duration=mbd, breakout_confirm_bars=bcb,
                      min_box_width_atr=wmin, max_box_width_atr=wmax)
        trials, _ = collect_for_cfg(cfg, bars_is, rr, max_hold)
        m = cfg_metric(trials)
        if m["n"] < 15:
            continue
        flag = ""
        if best is None or m["exp_R"] > best[1]["exp_R"]:
            best = (cfg, m); flag = "  <- best so far"
        print(f"  {tcb:3d}  {mbd:3d}  {bcb:3d}  {wmin:4.1f}  {wmax:4.1f}  "
              f"{m['n']:3d}  {m['tp_pct']:5.1f}  {m['exp_R']:+7.3f}{flag}")
    if best is None:
        raise RuntimeError("no IS config met n>=15")
    print(f"\n  IS winner: tcb={best[0].top_confirm_bars} mbd={best[0].min_box_duration} "
          f"bcb={best[0].breakout_confirm_bars} "
          f"w=[{best[0].min_box_width_atr},{best[0].max_box_width_atr}]  "
          f"n={best[1]['n']} TP%={best[1]['tp_pct']:.1f} E[R]={best[1]['exp_R']:+.3f}")
    return best[0]


# ── Empirical rulebook ───────────────────────────────────────────────────────

def mine_rules(is_trials):
    """For each categorical / banded feature, compute TP% per bucket and
    flag buckets with TP% materially different from baseline. Returns
    a list of rule dicts.
    """
    n = len(is_trials)
    base = sum(t["is_TP"] for t in is_trials) / n
    rules = []
    print(f"\n=== Mining IS rules (n={n}, baseline TP%={base*100:.1f}) ===")

    def by_bucket(get_bucket):
        d = defaultdict(list)
        for t in is_trials:
            d[get_bucket(t)].append(t)
        return d

    # Session
    print("\n  By session:")
    for k, g in sorted(by_bucket(lambda t: t["session"]).items()):
        if len(g) < 5: continue
        tp_pct = sum(t["is_TP"] for t in g) / len(g) * 100
        delta = tp_pct - base * 100
        flag = "TAKE" if delta > 8 else ("AVOID" if delta < -8 else "neutral")
        print(f"    {k:24s}  n={len(g):3d}  TP%={tp_pct:5.1f}  delta={delta:+5.1f}pp  {flag}")
        if abs(delta) >= 8 and len(g) >= 8:
            rules.append({"feature": "session", "value": k, "n": len(g),
                          "tp_pct": round(tp_pct, 1), "delta_pp": round(delta, 1),
                          "verdict": flag})

    # Direction
    print("\n  By direction:")
    for k, g in by_bucket(lambda t: t["direction"]).items():
        if len(g) < 5: continue
        tp_pct = sum(t["is_TP"] for t in g) / len(g) * 100
        delta = tp_pct - base * 100
        flag = "TAKE" if delta > 8 else ("AVOID" if delta < -8 else "neutral")
        print(f"    {k:8s}  n={len(g):3d}  TP%={tp_pct:5.1f}  delta={delta:+5.1f}pp  {flag}")
        if abs(delta) >= 8 and len(g) >= 8:
            rules.append({"feature": "direction", "value": k, "n": len(g),
                          "tp_pct": round(tp_pct, 1), "delta_pp": round(delta, 1),
                          "verdict": flag})

    # Box width ATR bucket
    def width_bucket(t):
        w = t["box_width_atr"]
        if w < 1.0:   return "0.6-1.0"
        if w < 1.5:   return "1.0-1.5"
        if w < 2.0:   return "1.5-2.0"
        if w < 2.5:   return "2.0-2.5"
        return "2.5+"
    print("\n  By box_width_atr:")
    for k, g in sorted(by_bucket(width_bucket).items()):
        if len(g) < 5: continue
        tp_pct = sum(t["is_TP"] for t in g) / len(g) * 100
        delta = tp_pct - base * 100
        flag = "TAKE" if delta > 8 else ("AVOID" if delta < -8 else "neutral")
        print(f"    width_atr {k:8s}  n={len(g):3d}  TP%={tp_pct:5.1f}  delta={delta:+5.1f}pp  {flag}")
        if abs(delta) >= 8 and len(g) >= 8:
            rules.append({"feature": "box_width_atr_bucket", "value": k, "n": len(g),
                          "tp_pct": round(tp_pct, 1), "delta_pp": round(delta, 1),
                          "verdict": flag})

    # ATR regime bucket
    def atr_bucket(t):
        v = t["atr_vs_avg"]
        if v < 0.7:   return "compressed_<0.7"
        if v < 1.2:   return "normal_0.7-1.2"
        if v < 1.6:   return "elevated_1.2-1.6"
        return "extreme_1.6+"
    print("\n  By atr_vs_avg:")
    for k, g in sorted(by_bucket(atr_bucket).items()):
        if len(g) < 5: continue
        tp_pct = sum(t["is_TP"] for t in g) / len(g) * 100
        delta = tp_pct - base * 100
        flag = "TAKE" if delta > 8 else ("AVOID" if delta < -8 else "neutral")
        print(f"    atr_regime {k:18s}  n={len(g):3d}  TP%={tp_pct:5.1f}  delta={delta:+5.1f}pp  {flag}")
        if abs(delta) >= 8 and len(g) >= 8:
            rules.append({"feature": "atr_regime_bucket", "value": k, "n": len(g),
                          "tp_pct": round(tp_pct, 1), "delta_pp": round(delta, 1),
                          "verdict": flag})

    # Volume class
    print("\n  By volume_classification:")
    for k, g in by_bucket(lambda t: t["vol_class"]).items():
        if len(g) < 5: continue
        tp_pct = sum(t["is_TP"] for t in g) / len(g) * 100
        delta = tp_pct - base * 100
        flag = "TAKE" if delta > 8 else ("AVOID" if delta < -8 else "neutral")
        print(f"    vol {k:14s}  n={len(g):3d}  TP%={tp_pct:5.1f}  delta={delta:+5.1f}pp  {flag}")
        if abs(delta) >= 8 and len(g) >= 8:
            rules.append({"feature": "vol_class", "value": k, "n": len(g),
                          "tp_pct": round(tp_pct, 1), "delta_pp": round(delta, 1),
                          "verdict": flag})

    print(f"\n  {len(rules)} rules with |delta|>=8pp and n>=8")
    return rules, base * 100


# ── Apply rules as Python filter ────────────────────────────────────────────

def width_bucket_for(w):
    if w < 1.0:   return "0.6-1.0"
    if w < 1.5:   return "1.0-1.5"
    if w < 2.0:   return "1.5-2.0"
    if w < 2.5:   return "2.0-2.5"
    return "2.5+"


def atr_bucket_for(v):
    if v < 0.7:   return "compressed_<0.7"
    if v < 1.2:   return "normal_0.7-1.2"
    if v < 1.6:   return "elevated_1.2-1.6"
    return "extreme_1.6+"


def trial_features(t):
    return {
        "session": t["session"],
        "direction": t["direction"],
        "box_width_atr_bucket": width_bucket_for(t["box_width_atr"]),
        "atr_regime_bucket": atr_bucket_for(t["atr_vs_avg"]),
        "vol_class": t["vol_class"],
    }


def apply_rules_filter(trial, rules):
    """Vote: count TAKE rules and AVOID rules that match this trial.
    APPROVE iff TAKE_votes > AVOID_votes (strict). Otherwise SKIP.
    """
    feats = trial_features(trial)
    take_votes = avoid_votes = 0
    matched = []
    for r in rules:
        if feats.get(r["feature"]) == r["value"]:
            matched.append(f"{r['feature']}={r['value']}:{r['verdict']}({r['delta_pp']:+.0f}pp)")
            if r["verdict"] == "TAKE":  take_votes += 1
            elif r["verdict"] == "AVOID": avoid_votes += 1
    approved = take_votes > avoid_votes
    return approved, take_votes, avoid_votes, matched


# ── Reporting helpers ───────────────────────────────────────────────────────

def report(label, trials):
    n = len(trials)
    if n == 0:
        print(f"  {label:30s}  n=0"); return
    tp = sum(t["is_TP"] for t in trials)
    e = float(np.mean([t["pnl_R"] for t in trials]))
    print(f"  {label:30s}  n={n:3d}  TP%={tp/n*100:5.1f}  E[R]={e:+.3f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rr", type=float, default=1.5)
    p.add_argument("--max-hold", type=int, default=60)
    p.add_argument("--save", default="v11/replay/results/oos_rulebook_artifacts.json")
    p.add_argument("--fixed-cfg", action="store_true",
                   help="skip param sweep; use wider config tcb=10 bcb=1 w=[0.3,5.0]")
    args = p.parse_args()

    print("Loading EURUSD 60m bars ...")
    bars = load_resampled_bars("eurusd", 60)
    is_bars_end = next((i for i, b in enumerate(bars) if b.timestamp.year >= SPLIT_YEAR), len(bars))
    bars_is = bars[:is_bars_end]
    bars_oos = bars[is_bars_end:]
    print(f"  IS:  {len(bars_is)} bars  -> {bars_is[-1].timestamp.date()}")
    print(f"  OOS: {len(bars_oos)} bars  {bars_oos[0].timestamp.date()} -> {bars_oos[-1].timestamp.date()}")

    cfg_winner = is_param_sweep(bars_is, args.rr, args.max_hold)

    print(f"\n=== Apply IS-winner config to OOS ===")
    full_trials, full_ctxs = collect_for_cfg(cfg_winner, bars, args.rr, args.max_hold)
    is_trials, is_ctxs, oos_trials, oos_ctxs = split_is_oos(full_trials, full_ctxs)
    print()
    report("IS slice (no filter)",  is_trials)
    report("OOS slice (no filter)", oos_trials)

    rules, baseline_tp = mine_rules(is_trials)

    print(f"\n=== Apply empirical rules filter to OOS ===")
    approved, rejected = [], []
    for t in oos_trials:
        ok, tv, av, matched = apply_rules_filter(t, rules)
        t["_rule_take"] = tv; t["_rule_avoid"] = av; t["_rule_matched"] = matched
        (approved if ok else rejected).append(t)
    report("OOS no filter", oos_trials)
    report("OOS rules APPROVE", approved)
    report("OOS rules REJECT",  rejected)
    if approved and rejected:
        a_tp = sum(t["is_TP"] for t in approved) / len(approved) * 100
        r_tp = sum(t["is_TP"] for t in rejected) / len(rejected) * 100
        print(f"  rules lift in TP%: {a_tp - r_tp:+.1f} pp")

    # Save rules + OOS contexts so the LLM script can read them
    Path(args.save).parent.mkdir(parents=True, exist_ok=True)
    Path(args.save).write_text(json.dumps({
        "cfg": {
            "top_confirm_bars": cfg_winner.top_confirm_bars,
            "min_box_duration": cfg_winner.min_box_duration,
            "breakout_confirm_bars": cfg_winner.breakout_confirm_bars,
            "min_box_width_atr": cfg_winner.min_box_width_atr,
            "max_box_width_atr": cfg_winner.max_box_width_atr,
        },
        "is_baseline_tp_pct": round(baseline_tp, 1),
        "is_n": len(is_trials),
        "rules": rules,
        "oos_trials": [{k: v for k, v in t.items() if not k.startswith("_") or k in ("_rule_take", "_rule_avoid", "_rule_matched")}
                       for t in oos_trials],
        # Save oos_contexts as serialized SignalContext
        "oos_contexts": [c.model_dump() for c in oos_ctxs],
    }, indent=2, default=str))
    print(f"\n  saved artifacts -> {args.save}")


if __name__ == "__main__":
    main()
