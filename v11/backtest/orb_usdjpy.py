"""USDJPY ORB backtest — pre-registered single-parameter run.

Hypothesis and parameters locked in:
    docs/superpowers/specs/2026-05-15-usdjpy-orb-preregistration.md

Test mirrors the XAUUSD ORB pattern (Asian-range, London/NY-break) on
USDJPY. NO parameter tuning — run once, report against the gate,
journal the result either way.

Data:
    tick_vault_data/fx/USDJPY_MIDPOINT.csv  (3.76M 1-min bars, 2005-03 → 2026-04)

Decision gate (pre-registered):
    OOS AvgR_slip ≥ +0.05R  AND  N/yr ≥ 20  ⇒ PASS

Run:
    python -m v11.backtest.orb_usdjpy
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from v11.backtest.investigate_orb_xauusd import (
    _metrics, _precompute_gap_metrics, _run_config, _split_by_year,
)
from v11.core.types import Bar
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig


ROOT = Path(__file__).resolve().parents[2]
MID_CSV = ROOT / "tick_vault_data" / "fx" / "USDJPY_MIDPOINT.csv"

# ── Pre-registered parameters (DO NOT TUNE) ─────────────────────────────────

CONFIG = V6StrategyConfig(
    instrument="USDJPY",
    range_start_hour=0, range_end_hour=6,
    trade_start_hour=8, trade_end_hour=16,
    skip_weekdays=(),                  # no Wednesday-skip for FX
    velocity_filter_enabled=False,
    velocity_lookback_minutes=3,
    velocity_threshold=200.0,
    rr_ratio=2.5,
    min_range_size=0.0,                # disable absolute filter; use pct only
    max_range_size=1e9,
    min_range_pct=0.05, max_range_pct=0.50,
    be_hours=999, max_pending_hours=4, time_exit_minutes=0,
    gap_filter_enabled=False,
    gap_vol_percentile=50.0,
    gap_range_filter_enabled=False,
    gap_range_percentile=40.0,
    gap_rolling_days=60,
    gap_start_hour=6, gap_end_hour=8,
    price_decimals=3,                  # USDJPY quoted to 3 decimals on IDEALPRO
)

# USDJPY 1 pip = 0.01 in price units. Pre-registered cost:
#   ~0.5-1 pip spread + 0.5 pip slip per side = ~1.5 pips RT
# _metrics takes slippage per SIDE and doubles it to RT.
# 1.5 pips RT / 2 = 0.0075 per side in price units.
SLIPPAGE_PER_SIDE = 0.0075   # 1.5 pip RT total
SLIPPAGE_LABEL    = "slip+spread 1.5 pips RT"

# Pre-registered IS / OOS split
OOS_START = datetime(2019, 1, 1)
OOS_END   = datetime(2022, 12, 31, 23, 59, 59)
IS_START  = datetime(2023, 1, 1)
IS_END    = datetime(2025, 12, 31, 23, 59, 59)
# Pre-2019 retained as third holdout, not part of the gate decision
EARLY_START = datetime(2005, 1, 1)
EARLY_END   = datetime(2018, 12, 31, 23, 59, 59)

# Pre-registered decision gate
GATE_OOS_AVGR_MIN = 0.05
GATE_NPY_MIN      = 20


# ── Data load ───────────────────────────────────────────────────────────────

def load_usdjpy_bars(csv_path: Path) -> List[Bar]:
    """Load USDJPY 1-min MIDPOINT bars and produce Bar objects.

    Our IBKR CSV has columns: date, open, high, low, close (no volume).
    The Bar dataclass requires tick_count + buy_volume + sell_volume —
    we default them to 1, 0, 0 since:
      - the ORB strategy with velocity_filter_enabled=False does not
        consume tick_count
      - the FX template explicitly disables velocity (gold-tuned values
        don't transfer to FX)
    """
    df = pd.read_csv(csv_path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    bars: List[Bar] = []
    for r in df.itertuples(index=False):
        bars.append(Bar(
            timestamp=r.date,
            open=float(r.open),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            tick_count=1,
            buy_volume=0.0,
            sell_volume=0.0,
        ))
    return bars


# ── IS/OOS split ────────────────────────────────────────────────────────────

def _ts(t: dict) -> datetime:
    return datetime.fromisoformat(t["timestamp"])


def split_periods(trades: List[dict]):
    early = [t for t in trades if EARLY_START <= _ts(t) <= EARLY_END]
    oos   = [t for t in trades if OOS_START   <= _ts(t) <= OOS_END]
    is_   = [t for t in trades if IS_START    <= _ts(t) <= IS_END]
    return early, oos, is_


# ── Reporting ───────────────────────────────────────────────────────────────

def _fmt_m(m: dict) -> str:
    return (f"N={m['N']:>5,}  WR={m['WR']:>5.1f}%  "
            f"AvgR={m['AvgR']:+.3f}  PF={m['PF']:.2f}  "
            f"MaxDD={m['MaxDD']:.2f}")


def report(trades: List[dict]) -> None:
    early, oos, is_ = split_periods(trades)
    n_yrs_oos = (OOS_END.year - OOS_START.year + 1)
    n_yrs_is  = (IS_END.year - IS_START.year + 1)

    print()
    print("=" * 88)
    print("  USDJPY ORB — pre-registered single-param run")
    print("=" * 88)
    print(f"  Config: range 0-6 UTC, trade 8-16 UTC, RR={CONFIG.rr_ratio}")
    print(f"          range_pct {CONFIG.min_range_pct}%–{CONFIG.max_range_pct}%, "
          f"max_pending_hours={CONFIG.max_pending_hours}")
    print(f"  Cost:   {SLIPPAGE_LABEL} (slippage_per_side={SLIPPAGE_PER_SIDE})")
    print()
    print(f"  Total trades                : {len(trades):,}")
    print(f"  Pre-2019 (third holdout)    : {len(early):,} "
          f"trades over 14 years (~{len(early)//14}/yr)")
    print(f"  OOS 2019-2022 (gate window) : {len(oos):,} "
          f"trades over {n_yrs_oos} years (~{len(oos)//n_yrs_oos}/yr)")
    print(f"  IS  2023-2025               : {len(is_):,} "
          f"trades over {n_yrs_is} years (~{len(is_)//n_yrs_is}/yr)")
    print()

    print("  ── Headline (with slippage) ─────────────────────────────────────")
    for label, sub in [("EARLY (2005-2018, holdout)", early),
                       ("OOS   (2019-2022, gate)   ", oos),
                       ("IS    (2023-2025)         ", is_),
                       ("ALL                       ", trades)]:
        m = _metrics(sub, slippage_pts=SLIPPAGE_PER_SIDE)
        print(f"    {label}:  {_fmt_m(m)}")
    print()

    print("  ── No-slippage reference (raw signal quality) ───────────────────")
    for label, sub in [("OOS   (2019-2022)", oos),
                       ("IS    (2023-2025)", is_),
                       ("ALL              ", trades)]:
        m = _metrics(sub, slippage_pts=0.0)
        print(f"    {label}:  {_fmt_m(m)}")
    print()

    print("  ── Year-by-year (with slippage) ─────────────────────────────────")
    by_year = _split_by_year(trades)
    print(f"    {'year':<6} {'N':>5}  {'WR%':>5}  {'AvgR':>8}  "
          f"{'PF':>5}  {'MaxDD':>7}  {'segment':<12}")
    for yr in sorted(by_year):
        m = _metrics(by_year[yr], slippage_pts=SLIPPAGE_PER_SIDE)
        if OOS_START.year <= yr <= OOS_END.year:
            seg = "OOS"
        elif IS_START.year <= yr <= IS_END.year:
            seg = "IS"
        else:
            seg = "early"
        print(f"    {yr:<6} {m['N']:>5,}  {m['WR']:>5.1f}  "
              f"{m['AvgR']:>+8.3f}  {m['PF']:>5.2f}  "
              f"{m['MaxDD']:>7.2f}  {seg}")
    print()

    # ── Pre-registered decision gate ────────────────────────────────────
    oos_m = _metrics(oos, slippage_pts=SLIPPAGE_PER_SIDE)
    npy_oos = oos_m["N"] / n_yrs_oos if n_yrs_oos else 0
    avg_r_oos = oos_m["AvgR"]

    print("  ── Decision gate (PRE-REGISTERED, no tuning allowed) ────────────")
    print(f"    Requires: OOS AvgR_slip ≥ +{GATE_OOS_AVGR_MIN} AND N/yr ≥ {GATE_NPY_MIN}")
    print(f"    OOS AvgR_slip = {avg_r_oos:+.4f}   "
          f"(threshold {GATE_OOS_AVGR_MIN:+.4f})  "
          f"-> {'PASS' if avg_r_oos >= GATE_OOS_AVGR_MIN else 'FAIL'}")
    print(f"    OOS N/yr      = {npy_oos:.1f}   "
          f"(threshold {GATE_NPY_MIN})  "
          f"-> {'PASS' if npy_oos >= GATE_NPY_MIN else 'FAIL'}")
    overall = (avg_r_oos >= GATE_OOS_AVGR_MIN) and (npy_oos >= GATE_NPY_MIN)
    print(f"    DECISION: {'PASS — write deployment plan' if overall else 'FAIL — journal null'}")
    print("=" * 88)


# ── Main ────────────────────────────────────────────────────────────────────

async def main_async():
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("orb_usdjpy")

    print(f"Loading {MID_CSV.relative_to(ROOT)} ...")
    bars = load_usdjpy_bars(MID_CSV)
    print(f"  {len(bars):,} 1-min bars  "
          f"{bars[0].timestamp.date()} → {bars[-1].timestamp.date()}\n")

    # Pre-compute (unused since gap_filter_enabled=False, but the runner wants the lookup)
    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), CONFIG)

    print("Running USDJPY ORB...")
    trades = await _run_config(CONFIG, bars, gap_filter=False,
                                gap_lookup=gap_lookup, log=log)
    print(f"  -> {len(trades):,} trades produced\n")

    # Save raw trades for downstream analysis
    out_dir = ROOT / "v11" / "backtest" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trades).to_csv(out_dir / "usdjpy_orb_trades.csv", index=False)
    print(f"  raw trades saved to "
          f"{(out_dir / 'usdjpy_orb_trades.csv').relative_to(ROOT)}\n")

    report(trades)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
