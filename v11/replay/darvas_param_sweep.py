"""Sweep Darvas params to find a stronger baseline before adding LLM filter.

Default params on EURUSD 1H produce 15% TP at 1.5R = -0.6R expectancy. No
filter can rescue that. This sweep varies (top_confirm_bars, min_box_duration,
breakout_confirm_bars, min/max_box_width_atr) and reports per-config:
  n trades, TP%, SL%, TIME%, E[R], net E[R] after typical FX cost (~0.3 bps RT)

Goal: find at least one config with TP% >= 25% AND n >= 30 trades so the
LLM-filter test downstream has signal-to-noise.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from itertools import product

import numpy as np

from v11.config.strategy_config import EURUSD_CONFIG, XAUUSD_CONFIG, StrategyConfig
from v11.core.darvas_detector import DarvasDetector
from v11.core.types import Direction
from v11.replay.darvas_judge_example import (
    load_resampled_bars, walk_outcome,
)


def evaluate(cfg: StrategyConfig, bars, rr: float, max_hold: int) -> dict:
    det = DarvasDetector(cfg)
    outcomes = []
    for i, bar in enumerate(bars):
        sig = det.add_bar(bar)
        if sig is None:
            continue
        fwd = bars[i + 1: i + 1 + max_hold]
        outcomes.append(walk_outcome(sig, fwd, rr=rr, max_bars=max_hold))
    n = len(outcomes)
    if n == 0:
        return {"n": 0}
    tp = sum(1 for o in outcomes if o.result == "TP")
    sl = sum(1 for o in outcomes if o.result == "SL")
    tm = sum(1 for o in outcomes if o.result == "TIME")
    e = float(np.mean([o.pnl_R for o in outcomes]))
    return {"n": n, "tp_pct": tp / n * 100, "sl_pct": sl / n * 100,
            "tm_pct": tm / n * 100, "exp_R": e}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--instrument", default="eurusd")
    p.add_argument("--tf", type=int, default=60)
    p.add_argument("--rr", type=float, default=1.5)
    p.add_argument("--max-hold", type=int, default=60)
    args = p.parse_args()

    base = {"eurusd": EURUSD_CONFIG, "xauusd": XAUUSD_CONFIG}[args.instrument]
    print(f"Loading {args.instrument} {args.tf}m bars ...")
    bars = load_resampled_bars(args.instrument, args.tf)
    print(f"  {len(bars)} bars  {bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}")
    print(f"  rr={args.rr}  max_hold={args.max_hold}\n")

    grid = list(product(
        [10, 15, 20],          # top_confirm_bars (= bottom_confirm_bars)
        [15, 25, 40],          # min_box_duration
        [1, 2, 3],             # breakout_confirm_bars
        [(0.3, 5.0), (0.6, 3.0), (1.0, 2.5)],  # (min, max) box_width_atr
    ))
    print(f"  {'tcb':>3s}  {'mbd':>3s}  {'bcb':>3s}  {'wmin':>4s}  {'wmax':>4s}  "
          f"{'n':>4s}  {'TP%':>5s}  {'SL%':>5s}  {'TM%':>5s}  {'E[R]':>7s}")
    keepers = []
    for tcb, mbd, bcb, (wmin, wmax) in grid:
        cfg = replace(base, top_confirm_bars=tcb, bottom_confirm_bars=tcb,
                      min_box_duration=mbd, breakout_confirm_bars=bcb,
                      min_box_width_atr=wmin, max_box_width_atr=wmax)
        r = evaluate(cfg, bars, args.rr, args.max_hold)
        if r["n"] == 0:
            continue
        flag = ""
        if r["n"] >= 30 and r["tp_pct"] >= 25:
            flag = "  *KEEP*"
            keepers.append((r["exp_R"], r["n"], r["tp_pct"], cfg))
        print(f"  {tcb:3d}  {mbd:3d}  {bcb:3d}  {wmin:4.1f}  {wmax:4.1f}  "
              f"{r['n']:4d}  {r['tp_pct']:5.1f}  {r['sl_pct']:5.1f}  "
              f"{r['tm_pct']:5.1f}  {r['exp_R']:+7.3f}{flag}")

    if keepers:
        keepers.sort(reverse=True)
        print(f"\n  Top 3 by E[R] with n>=30 and TP%>=25:")
        for exp_R, n, tp, cfg in keepers[:3]:
            print(f"    tcb={cfg.top_confirm_bars} mbd={cfg.min_box_duration} "
                  f"bcb={cfg.breakout_confirm_bars} "
                  f"w=[{cfg.min_box_width_atr:.1f},{cfg.max_box_width_atr:.1f}]  "
                  f"n={n} TP%={tp:.1f}  E[R]={exp_R:+.3f}")
    else:
        print(f"\n  No config achieved both n>=30 and TP%>=25.")


if __name__ == "__main__":
    main()
