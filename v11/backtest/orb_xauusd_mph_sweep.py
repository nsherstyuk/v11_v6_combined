"""XAUUSD ORB — max_pending_hours sweep.

Pre-registered: docs/superpowers/specs/2026-05-20-max-pending-hours-sweep.md

Tests max_pending_hours ∈ {4, 6, 8, 12} on the same 16-year data,
all other params identical to the live config. Reports OOS / IS /
EARLY tables plus year-by-year for each cell, then applies the
pre-registered decision rules to pick a winner (or keep current).

Run:
    python -m v11.backtest.orb_xauusd_mph_sweep
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from v11.backtest.investigate_orb_xauusd import (
    _metrics, _precompute_gap_metrics, _run_config, _split_by_year,
)
from v11.backtest.orb_xauusd import (
    load_xauusd_bars, SLIPPAGE_PER_SIDE, OOS_START, OOS_END,
    IS_START, EARLY_START, EARLY_END,
)
from v11.core.types import Bar
from v11.live.run_live import XAUUSD_ORB_CONFIG


ROOT = Path(__file__).resolve().parents[2]
MID_CSV = ROOT / "tick_vault_data" / "xauusd" / "XAUUSD_MIDPOINT.csv"

# Pre-registered values (LOCKED)
MAX_PENDING_HOURS_VALUES = [4, 6, 8, 12]


def _ts(t):
    return datetime.fromisoformat(t["timestamp"])


def split_periods(trades):
    early = [t for t in trades if EARLY_START <= _ts(t) <= EARLY_END]
    oos   = [t for t in trades if OOS_START   <= _ts(t) <= OOS_END]
    is_   = [t for t in trades if IS_START    <= _ts(t)]
    return early, oos, is_


def positive_year_count(trades, start_yr, end_yr):
    """How many of the years in [start, end] had positive AvgR_slip?"""
    by_year = _split_by_year(trades)
    pos = 0
    for yr in range(start_yr, end_yr + 1):
        if yr not in by_year:
            continue
        m = _metrics(by_year[yr], slippage_pts=SLIPPAGE_PER_SIDE)
        if m["AvgR"] > 0:
            pos += 1
    return pos


async def main_async():
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("orb_xauusd_mph_sweep")

    print(f"Loading {MID_CSV.relative_to(ROOT)} ...")
    bars = load_xauusd_bars(MID_CSV)
    print(f"  {len(bars):,} 1-min bars\n")

    # Pre-compute gap_lookup once
    bars_by_date = defaultdict(list)
    for b in bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date),
                                          XAUUSD_ORB_CONFIG)

    # Run each variant
    cells = {}
    for mph in MAX_PENDING_HOURS_VALUES:
        cfg = replace(XAUUSD_ORB_CONFIG, max_pending_hours=mph)
        label = f"MP{mph}"
        print(f"Running {label} (max_pending_hours={mph}) ...")
        trades = await _run_config(
            cfg, bars, gap_filter=cfg.gap_filter_enabled,
            gap_lookup=gap_lookup, log=log)
        cells[label] = trades
        print(f"  {label}: {len(trades):,} trades")

    print()
    print("=" * 96)
    print("  XAUUSD ORB — max_pending_hours sweep")
    print(f"  Pre-reg: docs/superpowers/specs/2026-05-20-max-pending-hours-sweep.md")
    print(f"  Cost:    $0.30 RT slip+spread (per-side {SLIPPAGE_PER_SIDE})")
    print("=" * 96)

    # Headline table — by cell × period
    print()
    print(f"  {'cell':<6} {'period':<22} {'N':>5}  {'WR%':>5}  "
          f"{'AvgR_slip':>10}  {'PF':>5}  {'MaxDD':>7}")
    print("  " + "-" * 73)
    for label in MAX_PENDING_HOURS_VALUES_LABELED:
        trades = cells[label]
        early, oos, is_ = split_periods(trades)
        for period_label, sub in [("EARLY 2010-2017", early),
                                   ("OOS   2018-2023", oos),
                                   ("IS    2024-now ", is_),
                                   ("ALL            ", trades)]:
            m = _metrics(sub, slippage_pts=SLIPPAGE_PER_SIDE)
            print(f"  {label:<6} {period_label:<22} {m['N']:>5,}  "
                  f"{m['WR']:>5.1f}  {m['AvgR']:>+10.4f}  "
                  f"{m['PF']:>5.2f}  {m['MaxDD']:>7.2f}")
        print("  " + "-" * 73)

    # Year-by-year for each cell
    for label in MAX_PENDING_HOURS_VALUES_LABELED:
        print()
        print(f"  ── {label} year-by-year ──")
        trades = cells[label]
        by_year = _split_by_year(trades)
        print(f"    {'year':<6} {'N':>5}  {'WR%':>5}  {'AvgR':>9}  {'PF':>5}")
        for yr in sorted(by_year):
            m = _metrics(by_year[yr], slippage_pts=SLIPPAGE_PER_SIDE)
            print(f"    {yr:<6} {m['N']:>5,}  {m['WR']:>5.1f}  "
                  f"{m['AvgR']:>+9.3f}  {m['PF']:>5.2f}")

    # ── Decision rules ────────────────────────────────────────────────────
    print()
    print("=" * 96)
    print("  Decision rules (pre-registered)")
    print("=" * 96)

    n_yrs_oos = (OOS_END.year - OOS_START.year + 1)
    summary = []
    for label in MAX_PENDING_HOURS_VALUES_LABELED:
        trades = cells[label]
        _, oos, _ = split_periods(trades)
        early, _, _ = split_periods(trades)
        m_oos = _metrics(oos, slippage_pts=SLIPPAGE_PER_SIDE)
        m_early = _metrics(early, slippage_pts=SLIPPAGE_PER_SIDE)
        pos_yrs = positive_year_count(trades, OOS_START.year, OOS_END.year)
        summary.append({
            "label": label,
            "oos_avgr": m_oos["AvgR"],
            "oos_npy": m_oos["N"] / n_yrs_oos,
            "early_avgr": m_early["AvgR"],
            "pos_oos_yrs": pos_yrs,
        })

    print(f"  {'cell':<6} {'OOS_AvgR':>9}  {'OOS_N/yr':>9}  "
          f"{'EARLY_AvgR':>11}  {'OOS_pos_yrs':>11}")
    for s in summary:
        print(f"  {s['label']:<6} {s['oos_avgr']:>+9.4f}  "
              f"{s['oos_npy']:>9.1f}  {s['early_avgr']:>+11.4f}  "
              f"{s['pos_oos_yrs']:>11}/{n_yrs_oos}")
    print()

    # Find MP4 reference
    mp4 = next(s for s in summary if s["label"] == "MP4")
    candidates = [s for s in summary if s["label"] != "MP4"]
    # Winner by OOS_AvgR
    candidates_sorted = sorted(candidates, key=lambda s: -s["oos_avgr"])
    winner = candidates_sorted[0]

    print(f"  Current live (MP4) OOS AvgR_slip: {mp4['oos_avgr']:+.4f}")
    print(f"  Best alternative  ({winner['label']}) OOS AvgR_slip: "
          f"{winner['oos_avgr']:+.4f}")
    delta = winner["oos_avgr"] - mp4["oos_avgr"]
    print(f"  Δ vs MP4: {delta:+.4f}")
    print()

    print("  Conditions for live change:")
    c1 = winner["oos_avgr"] >= 0.05
    print(f"    1. OOS AvgR ≥ +0.05            "
          f"({winner['oos_avgr']:+.4f}) -> "
          f"{'PASS' if c1 else 'FAIL'}")
    c2 = delta >= 0.030
    print(f"    2. Δ vs MP4 ≥ +0.030           "
          f"({delta:+.4f}) -> {'PASS' if c2 else 'FAIL'}")
    c3 = winner["oos_npy"] >= 20
    print(f"    3. OOS N/yr ≥ 20               "
          f"({winner['oos_npy']:.1f}) -> {'PASS' if c3 else 'FAIL'}")
    c4 = winner["pos_oos_yrs"] >= 4
    print(f"    4. OOS positive years ≥ 4/6    "
          f"({winner['pos_oos_yrs']}) -> {'PASS' if c4 else 'FAIL'}")
    c5 = winner["early_avgr"] >= mp4["early_avgr"] - 0.10
    print(f"    5. EARLY AvgR ≥ MP4_EARLY−0.10 "
          f"({winner['early_avgr']:+.4f} vs MP4 "
          f"{mp4['early_avgr']:+.4f}) -> {'PASS' if c5 else 'FAIL'}")
    print()

    if all([c1, c2, c3, c4, c5]):
        print(f"  DECISION: PROPOSE LIVE CHANGE — "
              f"max_pending_hours: 4 -> {winner['label'][2:]}")
    else:
        print(f"  DECISION: KEEP MP4 (current live). At least one condition failed.")
    print("=" * 96)


# Use the same MP labels as cell keys
MAX_PENDING_HOURS_VALUES_LABELED = [f"MP{h}" for h in MAX_PENDING_HOURS_VALUES]


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
