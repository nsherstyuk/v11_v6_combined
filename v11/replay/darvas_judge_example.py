"""End-to-end usable example: Darvas breakout + LLM-style judge + outcome walker.

Runs without API keys (HeuristicJudge codifies the prompt's checklist in pure
Python). To swap in the real GrokFilter, set XAI_API_KEY and pass --judge=grok.

Pipeline per instrument:
  1. Load 1m CSV bars from C:\\nautilus0\\data\\1m_csv\\.
  2. Resample to chosen timeframe (default 60m).
  3. Stream through DarvasDetector. For every confirmed BreakoutSignal:
       a. Build a v11 SignalContext.
       b. Ask the judge: approve / reject + confidence + flags.
       c. Walk the next N bars forward to determine actual outcome:
          TP (1.5R hit first), SL (box edge hit first), or TIME (neither).
  4. Print a summary table: hit-rate by judge decision and confidence bucket.
     This is the calibration plot the LLM-as-filter literature lives on.

Usage:
  python -m v11.replay.darvas_judge_example --instrument eurusd --tf 60
  python -m v11.replay.darvas_judge_example --instrument xauusd --tf 240
  python -m v11.replay.darvas_judge_example --instrument eurusd --judge grok
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Protocol

import numpy as np
import pandas as pd

from v11.config.strategy_config import (
    EURUSD_CONFIG, USDJPY_CONFIG, XAUUSD_CONFIG, StrategyConfig,
)
from v11.core.darvas_detector import DarvasDetector
from v11.core.types import Bar, BreakoutSignal, Direction
from v11.llm.models import BarData, SignalContext

CSV_DIRS = [
    Path(r"C:\nautilus0\data\1m_csv"),
    Path(r"C:\nautilus0\data\1m_csv_fresh"),
]

CONFIGS = {"eurusd": EURUSD_CONFIG, "xauusd": XAUUSD_CONFIG, "usdjpy": USDJPY_CONFIG}


# ── Bar source ──────────────────────────────────────────────────────────────

def find_csv(instrument: str) -> Path:
    for d in CSV_DIRS:
        p = d / f"{instrument.lower()}_1m_tick.csv"
        if p.exists():
            return p
    raise FileNotFoundError(f"No CSV for {instrument} in {CSV_DIRS}")


def load_resampled_bars(instrument: str, tf_minutes: int,
                        max_bars: Optional[int] = None) -> list[Bar]:
    """Load 1m CSV and resample to tf_minutes bars as v11 Bar objects."""
    csv = find_csv(instrument)
    cols = ["timestamp", "open", "high", "low", "close", "tick_count",
            "buy_volume", "sell_volume"]
    df = pd.read_csv(csv, usecols=cols)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.dropna().sort_values("ts").drop_duplicates("ts").set_index("ts")
    rule = f"{tf_minutes}min"
    agg = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "tick_count": "sum", "buy_volume": "sum", "sell_volume": "sum",
    }).dropna()
    if max_bars:
        agg = agg.tail(max_bars)
    bars = [
        Bar(
            timestamp=ts.to_pydatetime(),
            open=float(r["open"]), high=float(r["high"]),
            low=float(r["low"]), close=float(r["close"]),
            tick_count=int(r["tick_count"]),
            buy_volume=float(r["buy_volume"]),
            sell_volume=float(r["sell_volume"]),
        )
        for ts, r in agg.iterrows()
    ]
    return bars


# ── SignalContext builder ───────────────────────────────────────────────────

def session_for(ts: datetime) -> str:
    h = ts.hour
    if 0 <= h < 7:   return "ASIAN"
    if 7 <= h < 12:  return "LONDON"
    if 12 <= h < 16: return "LONDON_NY_OVERLAP"
    if 16 <= h < 21: return "NY"
    return "ASIAN_OPEN"


def buy_ratio_trend(bars: list[Bar]) -> str:
    if len(bars) < 6:
        return "flat"
    half = len(bars) // 2
    early = np.mean([b.buy_ratio for b in bars[:half]])
    late = np.mean([b.buy_ratio for b in bars[half:]])
    if late > early + 0.04:  return "increasing"
    if late < early - 0.04:  return "decreasing"
    return "flat"


def build_signal_context(
    instrument: str, signal: BreakoutSignal, history: list[Bar],
    atr_avg_period: int = 240,
) -> SignalContext:
    """Map a v11 BreakoutSignal + recent bar history into a SignalContext."""
    recent = history[-30:]
    win = history[-min(len(history), 5):]
    buy_ratio_at = float(np.mean([b.buy_ratio for b in win])) if win else 0.5
    trend = buy_ratio_trend(history[-12:])
    avg_ticks = float(np.mean([b.tick_count for b in recent]))
    if avg_ticks < 5:
        tq = "INSUFFICIENT"
    elif avg_ticks < 30:
        tq = "LOW"
    else:
        tq = "HIGH"
    if trend == "increasing" and buy_ratio_at >= 0.55 and signal.direction == Direction.LONG:
        vc = "CONFIRMING"
    elif trend == "decreasing" and buy_ratio_at <= 0.45 and signal.direction == Direction.SHORT:
        vc = "CONFIRMING"
    elif (signal.direction == Direction.LONG and buy_ratio_at < 0.45) \
            or (signal.direction == Direction.SHORT and buy_ratio_at > 0.55):
        vc = "DIVERGENT"
    else:
        vc = "INDETERMINATE"

    # ATR-vs-average: compare current ATR to mean of bar ranges over a longer window
    if len(history) >= atr_avg_period:
        ranges = np.array([b.high - b.low for b in history[-atr_avg_period:]])
        avg_range = float(ranges.mean())
        atr_vs_avg = signal.atr / avg_range if avg_range > 0 else 1.0
    else:
        atr_vs_avg = 1.0

    bar_data = [
        BarData(t=b.timestamp.isoformat(), o=b.open, h=b.high, l=b.low,
                c=b.close, bv=b.buy_volume, sv=b.sell_volume, tc=b.tick_count)
        for b in recent
    ]
    return SignalContext(
        signal_type="DARVAS_BREAKOUT",
        direction=signal.direction.value,
        instrument=instrument,
        box_top=signal.box.top,
        box_bottom=signal.box.bottom,
        box_duration_bars=signal.box.duration_bars,
        box_width_atr=signal.box.width_atr,
        breakout_price=signal.breakout_price,
        atr=signal.atr,
        atr_vs_avg=round(atr_vs_avg, 3),
        buy_ratio_at_breakout=round(buy_ratio_at, 4),
        buy_ratio_trend=trend,
        tick_quality=tq,
        volume_classification=vc,
        recent_bars=bar_data,
        current_time_utc=signal.timestamp.isoformat(),
        session=session_for(signal.timestamp),
    )


# ── Judge protocol + implementations ────────────────────────────────────────

@dataclass(frozen=True)
class Verdict:
    approved: bool
    confidence: int          # 0-100
    primary_concern: str     # categorical
    reasoning: str


class Judge(Protocol):
    name: str
    def evaluate(self, ctx: SignalContext) -> Verdict: ...


class HeuristicJudge:
    """Pure-Python codification of the prompt-template checklist.

    Provides a sane baseline that runs without an API key and gives the
    replay harness a concrete decision to record. Each rule maps to a flag
    the LLM prompt also looks for, so the calibration framework is
    apples-to-apples when you swap in GrokJudgeAdapter.
    """
    name = "heuristic"

    # Tunables (deliberately conservative defaults)
    MIN_BOX_WIDTH_ATR = 0.6
    MAX_BOX_WIDTH_ATR = 4.0
    MIN_BOX_DURATION = 25
    HIGH_ATR_REGIME = 1.6
    LOW_ATR_REGIME = 0.5

    def evaluate(self, ctx: SignalContext) -> Verdict:
        score = 50
        flags: list[str] = []

        # Box geometry
        if ctx.box_width_atr < self.MIN_BOX_WIDTH_ATR:
            score -= 15; flags.append("box_too_narrow")
        elif ctx.box_width_atr > self.MAX_BOX_WIDTH_ATR:
            score -= 10; flags.append("box_too_wide")
        else:
            score += 10  # clean width

        if ctx.box_duration_bars < self.MIN_BOX_DURATION:
            score -= 10; flags.append("box_too_short")
        elif ctx.box_duration_bars >= 40:
            score += 5

        # Volatility regime
        if ctx.atr_vs_avg > self.HIGH_ATR_REGIME:
            score -= 15; flags.append("elevated_volatility")
        elif ctx.atr_vs_avg < self.LOW_ATR_REGIME:
            score -= 5; flags.append("compressed_volatility")

        # Volume confirmation
        if ctx.volume_classification == "CONFIRMING":
            score += 15
        elif ctx.volume_classification == "DIVERGENT":
            score -= 20; flags.append("volume_divergence")

        if ctx.tick_quality == "INSUFFICIENT":
            score -= 15; flags.append("thin_volume")
        elif ctx.tick_quality == "LOW":
            score -= 5

        # Session (Asian breakouts are noisier on FX)
        if ctx.session == "ASIAN":
            score -= 10; flags.append("asian_session")
        elif ctx.session in ("LONDON", "LONDON_NY_OVERLAP"):
            score += 5

        score = max(0, min(100, score))
        # Bias toward approval (matches prompt calibration guidance), but
        # require >= 55 to take. Rationale: we only want the better-scoring
        # subset of breakouts; lower threshold just trades everything.
        approved = score >= 55 and "volume_divergence" not in flags
        primary = flags[0] if flags else "none"
        reasoning = (f"box_w_atr={ctx.box_width_atr:.2f} "
                     f"dur={ctx.box_duration_bars} "
                     f"atr_vs_avg={ctx.atr_vs_avg:.2f} "
                     f"vol={ctx.volume_classification} "
                     f"session={ctx.session} score={score}")
        return Verdict(approved=approved, confidence=score,
                       primary_concern=primary, reasoning=reasoning)


class GrokJudgeAdapter:
    """Thin wrapper around the existing GrokFilter for parity with HeuristicJudge."""
    name = "grok"

    def __init__(self, api_key: str):
        from v11.llm.grok_filter import GrokFilter
        self._filter = GrokFilter(api_key=api_key, log_dir="grok_logs")

    def evaluate(self, ctx: SignalContext) -> Verdict:
        import asyncio
        decision = asyncio.run(self._filter.evaluate_signal(ctx))
        primary = decision.risk_flags[0] if decision.risk_flags else "none"
        return Verdict(
            approved=decision.approved, confidence=int(decision.confidence),
            primary_concern=primary, reasoning=decision.reasoning,
        )


# ── Outcome walker ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Outcome:
    result: str        # "TP" | "SL" | "TIME"
    pnl_R: float       # PnL in units of initial risk (R)
    bars_held: int


def walk_outcome(signal: BreakoutSignal, fwd_bars: list[Bar],
                 rr: float = 1.5, max_bars: int = 60) -> Outcome:
    """Determine which of TP/SL/TIME stop fires first.

    Stop = the far edge of the box (long: box_bottom; short: box_top).
    Target = entry +/- rr * (entry - stop).
    Bar-by-bar: SL checked first within a bar (conservative).
    """
    entry = signal.breakout_price
    if signal.direction == Direction.LONG:
        stop = signal.box.bottom
        risk = entry - stop
        target = entry + rr * risk
    else:
        stop = signal.box.top
        risk = stop - entry
        target = entry - rr * risk
    if risk <= 0:
        return Outcome("TIME", 0.0, 0)

    for i, bar in enumerate(fwd_bars[:max_bars], start=1):
        if signal.direction == Direction.LONG:
            if bar.low <= stop:
                return Outcome("SL", -1.0, i)
            if bar.high >= target:
                return Outcome("TP", rr, i)
        else:
            if bar.high >= stop:
                return Outcome("SL", -1.0, i)
            if bar.low <= target:
                return Outcome("TP", rr, i)
    # Time stop — mark to last close
    if not fwd_bars:
        return Outcome("TIME", 0.0, 0)
    last = fwd_bars[min(max_bars, len(fwd_bars)) - 1]
    if signal.direction == Direction.LONG:
        pnl = (last.close - entry) / risk
    else:
        pnl = (entry - last.close) / risk
    return Outcome("TIME", round(pnl, 3), min(max_bars, len(fwd_bars)))


# ── Replay loop ─────────────────────────────────────────────────────────────

@dataclass
class Trial:
    ts: datetime
    direction: str
    box_top: float
    box_bottom: float
    box_width_atr: float
    box_duration: int
    atr_vs_avg: float
    session: str
    verdict: Verdict
    outcome: Outcome


def run_replay(instrument: str, tf_min: int, judge: Judge,
               max_bars: Optional[int] = None,
               rr: float = 1.5, max_hold: int = 60) -> list[Trial]:
    cfg = CONFIGS[instrument.lower()]
    bars = load_resampled_bars(instrument, tf_min, max_bars=max_bars)
    print(f"  loaded {len(bars)} {tf_min}m bars  "
          f"{bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}")

    detector = DarvasDetector(cfg)
    history: list[Bar] = []
    trials: list[Trial] = []

    for i, bar in enumerate(bars):
        history.append(bar)
        signal = detector.add_bar(bar)
        if signal is None:
            continue
        ctx = build_signal_context(instrument.upper(), signal, history)
        verdict = judge.evaluate(ctx)
        fwd = bars[i + 1: i + 1 + max_hold]
        outcome = walk_outcome(signal, fwd, rr=rr, max_bars=max_hold)
        trials.append(Trial(
            ts=signal.timestamp, direction=signal.direction.value,
            box_top=signal.box.top, box_bottom=signal.box.bottom,
            box_width_atr=signal.box.width_atr,
            box_duration=signal.box.duration_bars,
            atr_vs_avg=ctx.atr_vs_avg, session=ctx.session,
            verdict=verdict, outcome=outcome,
        ))
    return trials


# ── Reporting ───────────────────────────────────────────────────────────────

def summarise(trials: list[Trial], rr: float) -> None:
    if not trials:
        print("  no breakouts in sample")
        return

    n = len(trials)
    n_tp = sum(1 for t in trials if t.outcome.result == "TP")
    n_sl = sum(1 for t in trials if t.outcome.result == "SL")
    n_tm = sum(1 for t in trials if t.outcome.result == "TIME")
    expectancy = float(np.mean([t.outcome.pnl_R for t in trials]))

    print(f"\n  ALL signals (judge ignored):  n={n}  "
          f"TP={n_tp} ({n_tp/n*100:.0f}%)  SL={n_sl}  TIME={n_tm}  "
          f"E[R]={expectancy:+.3f}")

    # By judge decision
    appr = [t for t in trials if t.verdict.approved]
    rej  = [t for t in trials if not t.verdict.approved]
    print(f"\n  By judge decision:")
    for label, group in [("APPROVE", appr), ("REJECT ", rej)]:
        if not group:
            print(f"    {label}: 0 trades"); continue
        tp = sum(1 for t in group if t.outcome.result == "TP")
        e = float(np.mean([t.outcome.pnl_R for t in group]))
        print(f"    {label}: n={len(group):3d}  hit_rate={tp/len(group)*100:5.1f}%  "
              f"E[R]={e:+.3f}")

    if appr and rej:
        appr_hr = sum(1 for t in appr if t.outcome.result == "TP") / len(appr)
        rej_hr = sum(1 for t in rej if t.outcome.result == "TP") / len(rej)
        print(f"\n  Lift in hit rate (APPROVE - REJECT): "
              f"{(appr_hr - rej_hr) * 100:+.1f} pp")

    # Confidence buckets
    print(f"\n  Hit rate by confidence bucket:")
    print(f"    {'bucket':>10s}  {'n':>4s}  {'hit%':>6s}  {'E[R]':>7s}")
    edges = [(0, 40), (40, 55), (55, 70), (70, 85), (85, 101)]
    for lo, hi in edges:
        g = [t for t in trials if lo <= t.verdict.confidence < hi]
        if not g:
            print(f"    {lo:3d}-{hi-1:<3d}    0     n/a       n/a"); continue
        tp = sum(1 for t in g if t.outcome.result == "TP")
        e = float(np.mean([t.outcome.pnl_R for t in g]))
        print(f"    {lo:3d}-{hi-1:<3d}  {len(g):4d}  "
              f"{tp/len(g)*100:5.1f}%  {e:+7.3f}")

    # Concern-coded reject reasons (does the model reject well?)
    print(f"\n  Reject reasons (primary_concern):")
    from collections import Counter
    counts = Counter(t.verdict.primary_concern for t in rej)
    for concern, c in counts.most_common():
        g = [t for t in rej if t.verdict.primary_concern == concern]
        tp = sum(1 for t in g if t.outcome.result == "TP")
        print(f"    {concern:22s}  n={c:3d}  hit_rate_if_taken={tp/c*100:5.1f}%")


# ── Main ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--instrument", default="eurusd",
                   choices=list(CONFIGS.keys()))
    p.add_argument("--tf", type=int, default=60,
                   help="bar timeframe in minutes (60 = 1H, 240 = 4H)")
    p.add_argument("--judge", default="heuristic",
                   choices=["heuristic", "grok"])
    p.add_argument("--rr", type=float, default=1.5)
    p.add_argument("--max-hold", type=int, default=60,
                   help="max bars to hold a trade before time stop")
    p.add_argument("--max-bars", type=int, default=None,
                   help="optional cap on bars to load (tail)")
    args = p.parse_args(argv)

    print(f"=== Darvas + LLM-judge replay  ({args.instrument} @ {args.tf}m) ===")
    print(f"  judge={args.judge}  rr={args.rr}  max_hold={args.max_hold} bars")

    if args.judge == "grok":
        api_key = os.environ.get("XAI_API_KEY")
        if not api_key:
            print("  XAI_API_KEY not set; falling back to heuristic")
            judge: Judge = HeuristicJudge()
        else:
            judge = GrokJudgeAdapter(api_key)
    else:
        judge = HeuristicJudge()

    trials = run_replay(args.instrument, args.tf, judge,
                        max_bars=args.max_bars, rr=args.rr,
                        max_hold=args.max_hold)
    summarise(trials, args.rr)


if __name__ == "__main__":
    main(sys.argv[1:])
