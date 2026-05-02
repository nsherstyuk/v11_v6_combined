"""LLM-as-filter experiment runner.

Holds the Darvas params constant at the keeper config from the param sweep
and compares judges on the same trial set:

  Heuristic               (offline baseline)
  Sonnet identified       (LLM with full context)
  Sonnet anonymized       (LLM with prices/dates stripped — hindsight test)
  Optionally: Haiku       (cheap second model for cross-check)

Reports per judge: hit rate, expectancy, lift vs no-filter, agreement matrix
between judges.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import numpy as np

# Load .env if available (project convention)
def _load_dotenv() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

from v11.config.strategy_config import EURUSD_CONFIG, XAUUSD_CONFIG, StrategyConfig
from v11.core.darvas_detector import DarvasDetector
from v11.replay.darvas_judge_example import (
    HeuristicJudge, Verdict, build_signal_context, load_resampled_bars,
    walk_outcome,
)
from v11.replay.openrouter_judge import OpenRouterJudge


# Keeper config from param sweep (EURUSD 1H, n=34, TP=41.2%, E[R]=+0.155)
KEEPER_CFG = replace(
    EURUSD_CONFIG,
    top_confirm_bars=10, bottom_confirm_bars=10,
    min_box_duration=15, breakout_confirm_bars=1,
    min_box_width_atr=0.6, max_box_width_atr=3.0,
)


@dataclass
class Trial:
    idx: int
    ts: str
    direction: str
    box_width_atr: float
    duration: int
    atr_vs_avg: float
    session: str
    outcome_R: float
    is_TP: bool
    is_SL: bool


def collect_trials(instrument: str, tf: int, cfg: StrategyConfig,
                   rr: float, max_hold: int):
    bars = load_resampled_bars(instrument, tf)
    print(f"  {len(bars)} {tf}m bars  {bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}")
    det = DarvasDetector(cfg)
    trials, ctxs = [], []
    for i, bar in enumerate(bars):
        sig = det.add_bar(bar)
        if sig is None:
            continue
        history = bars[: i + 1]
        ctx = build_signal_context(instrument.upper(), sig, history)
        fwd = bars[i + 1: i + 1 + max_hold]
        out = walk_outcome(sig, fwd, rr=rr, max_bars=max_hold)
        trials.append(Trial(
            idx=len(trials), ts=sig.timestamp.isoformat(),
            direction=sig.direction.value,
            box_width_atr=sig.box.width_atr,
            duration=sig.box.duration_bars,
            atr_vs_avg=ctx.atr_vs_avg, session=ctx.session,
            outcome_R=out.pnl_R, is_TP=(out.result == "TP"),
            is_SL=(out.result == "SL"),
        ))
        ctxs.append(ctx)
    return trials, ctxs


def evaluate_judge(name, judge, ctxs, trials):
    verdicts = []
    for i, ctx in enumerate(ctxs):
        v = judge.evaluate(ctx)
        verdicts.append(v)
        print(f"    [{i+1:3d}/{len(ctxs)}] {trials[i].ts[:16]} {trials[i].direction:5s}  "
              f"{'TAKE' if v.approved else 'SKIP':4s} conf={v.confidence:3d}  "
              f"{v.primary_concern}  -> {'TP' if trials[i].is_TP else ('SL' if trials[i].is_SL else 'TIME')}")
    return verdicts


def report(name, verdicts, trials, rr):
    n = len(trials)
    base_tp = sum(t.is_TP for t in trials) / n * 100
    base_e = float(np.mean([t.outcome_R for t in trials]))
    appr = [t for t, v in zip(trials, verdicts) if v.approved]
    rej = [t for t, v in zip(trials, verdicts) if not v.approved]
    if appr:
        a_tp = sum(t.is_TP for t in appr) / len(appr) * 100
        a_e = float(np.mean([t.outcome_R for t in appr]))
    else:
        a_tp = a_e = float("nan")
    if rej:
        r_tp = sum(t.is_TP for t in rej) / len(rej) * 100
    else:
        r_tp = float("nan")
    print(f"\n  --- {name} ---")
    print(f"  baseline:  n={n}  TP%={base_tp:5.1f}  E[R]={base_e:+.3f}")
    print(f"  APPROVE:   n={len(appr):3d}  TP%={a_tp:5.1f}  E[R]={a_e:+.3f}")
    print(f"  REJECT:    n={len(rej):3d}  TP%={r_tp:5.1f}")
    if appr and rej:
        print(f"  lift in TP%: {a_tp - r_tp:+.1f} pp   "
              f"E[R] uplift over baseline: {a_e - base_e:+.3f}")


def agreement(name1, v1, name2, v2):
    both_take = sum(1 for a, b in zip(v1, v2) if a.approved and b.approved)
    both_skip = sum(1 for a, b in zip(v1, v2) if not a.approved and not b.approved)
    disagree = len(v1) - both_take - both_skip
    print(f"  {name1} vs {name2}: both_TAKE={both_take}  both_SKIP={both_skip}  "
          f"disagree={disagree}  agreement_rate={(both_take+both_skip)/len(v1)*100:.0f}%")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--instrument", default="eurusd")
    p.add_argument("--tf", type=int, default=60)
    p.add_argument("--rr", type=float, default=1.5)
    p.add_argument("--max-hold", type=int, default=60)
    p.add_argument("--model", default="anthropic/claude-sonnet-4.5",
                   help="OpenRouter model id")
    p.add_argument("--also-haiku", action="store_true")
    p.add_argument("--also-anonymized", action="store_true",
                   help="Also run identified-mode model in anonymized mode")
    args = p.parse_args()

    print(f"=== LLM-filter experiment ({args.instrument} {args.tf}m, keeper Darvas cfg) ===")
    print(f"  cfg: tcb={KEEPER_CFG.top_confirm_bars} mbd={KEEPER_CFG.min_box_duration} "
          f"bcb={KEEPER_CFG.breakout_confirm_bars} "
          f"w=[{KEEPER_CFG.min_box_width_atr},{KEEPER_CFG.max_box_width_atr}]")

    trials, ctxs = collect_trials(args.instrument, args.tf, KEEPER_CFG,
                                   args.rr, args.max_hold)
    if not trials:
        print("  no trials"); return
    print(f"\n  collected {len(trials)} trials.  "
          f"baseline TP%={sum(t.is_TP for t in trials)/len(trials)*100:.1f}")

    print(f"\n  Heuristic judge ...")
    h_verdicts = [HeuristicJudge().evaluate(c) for c in ctxs]
    report("Heuristic", h_verdicts, trials, args.rr)

    print(f"\n  {args.model} (identified) ...")
    j_id = OpenRouterJudge(model=args.model, mode="identified")
    s_id_verdicts = evaluate_judge(j_id.name, j_id, ctxs, trials)
    print(f"  api stats: calls={j_id.stats.calls} cached={j_id.stats.cached}  "
          f"tokens_in={j_id.stats.tokens_in} tokens_out={j_id.stats.tokens_out}  "
          f"~${j_id.cost_estimate_usd():.3f}")
    report(f"{args.model} (identified)", s_id_verdicts, trials, args.rr)

    if args.also_anonymized:
        print(f"\n  {args.model} (anonymized) ...")
        j_anon = OpenRouterJudge(model=args.model, mode="anonymized")
        s_an_verdicts = evaluate_judge(j_anon.name, j_anon, ctxs, trials)
        print(f"  api stats: calls={j_anon.stats.calls} cached={j_anon.stats.cached}")
        report(f"{args.model} (anonymized)", s_an_verdicts, trials, args.rr)

    if args.also_haiku:
        haiku_model = "anthropic/claude-haiku-4.5"
        print(f"\n  {haiku_model} (identified) ...")
        j_h = OpenRouterJudge(model=haiku_model, mode="identified")
        h_verdicts2 = evaluate_judge(j_h.name, j_h, ctxs, trials)
        print(f"  api stats: calls={j_h.stats.calls} cached={j_h.stats.cached}")
        report(f"{haiku_model} (identified)", h_verdicts2, trials, args.rr)

    # Agreement
    print(f"\n  --- Agreement ---")
    agreement("Heuristic", h_verdicts, args.model, s_id_verdicts)
    if args.also_anonymized:
        agreement(f"{args.model}_id", s_id_verdicts,
                  f"{args.model}_anon", s_an_verdicts)


if __name__ == "__main__":
    main()
