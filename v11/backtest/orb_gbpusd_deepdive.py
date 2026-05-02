"""GBPUSD ORB deep-dive: year-by-year, slippage stress, direction split.

Decides whether GBPUSD's strong 2018-2023 result is:
  (a) genuine, persistent across years -> go to Phase 2 (LLM gate)
  (b) regime-concentrated (Brexit/COVID) -> drop
  (c) sample noise -> drop

Same pre-registered template as orb_fx_grid (no per-year tuning).
"""
from __future__ import annotations

import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.investigate_orb_xauusd import (
    _metrics, _precompute_gap_metrics, _run_config, _split_by_year,
)
from v11.backtest.orb_fx_grid import _fx_template
from v11.core.types import Bar


def main():
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("gbpusd_deepdive")
    log.setLevel(logging.WARNING)

    cfg = _fx_template("GBPUSD")
    bars = load_instrument_bars("GBPUSD")
    print(f"GBPUSD: {len(bars):,} bars  "
          f"{bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}\n")

    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), cfg)

    trades = asyncio.run(_run_config(cfg, bars, gap_filter=False,
                                     gap_lookup=gap_lookup, log=log))
    print(f"  total trades: {len(trades)}\n")

    # ── Year-by-year ──────────────────────────────────────────────────────
    print("=" * 80)
    print("  YEAR-BY-YEAR (no slippage)")
    print("=" * 80)
    print(f"  {'Year':<6} {'N':>5} {'WR%':>6} {'AvgR':>8} {'PF':>6} {'MaxDD':>8} {'CumR':>8}")
    print("  " + "-" * 60)

    by_year = _split_by_year(trades)
    cum = 0.0
    for yr in sorted(by_year.keys()):
        m = _metrics(by_year[yr])
        cum += m["AvgR"] * m["N"]
        pf_str = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        flag = "  <- IS" if yr >= 2024 else ""
        print(f"  {yr:<6} {m['N']:>5} {m['WR']:>6.1f} {m['AvgR']:>+8.3f} "
              f"{pf_str:>6} {m['MaxDD']:>8.3f} {cum:>+8.2f}{flag}")

    # ── Cost sensitivity (full sample) ─────────────────────────────────────
    print("\n" + "=" * 80)
    print("  SLIPPAGE SENSITIVITY (full sample 2018-2026)")
    print("=" * 80)
    print(f"  {'pip RT':>8}  {'slip/side':>10}  {'AvgR':>8}  {'WR%':>6}  {'PF':>6}")
    print("  " + "-" * 50)
    # GBPUSD pip = 0.0001
    for pips_rt in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]:
        slip = (pips_rt / 2) * 0.0001
        m = _metrics(trades, slippage_pts=slip)
        pf_str = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {pips_rt:>8.1f}  {slip:>10.5f}  {m['AvgR']:>+8.3f} "
              f"{m['WR']:>6.1f}  {pf_str:>6}")

    # ── Cost sensitivity (OOS only 2018-2023) ──────────────────────────────
    oos_trades = [t for t in trades
                  if 2018 <= datetime.fromisoformat(t["timestamp"]).year <= 2023]
    print(f"\n  --- OOS subset (2018-2023, n={len(oos_trades)}) ---")
    print(f"  {'pip RT':>8}  {'AvgR':>8}  {'WR%':>6}  {'PF':>6}")
    for pips_rt in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]:
        slip = (pips_rt / 2) * 0.0001
        m = _metrics(oos_trades, slippage_pts=slip)
        pf_str = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {pips_rt:>8.1f}  {m['AvgR']:>+8.3f}  {m['WR']:>6.1f}  {pf_str:>6}")

    # ── Direction split (OOS only) ─────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  DIRECTION SPLIT (OOS 2018-2023, no slippage)")
    print("=" * 80)
    for direction in ("LONG", "SHORT"):
        d = [t for t in oos_trades if t.get("direction") == direction]
        m = _metrics(d)
        pf_str = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {direction:<6}  N={m['N']:>4}  WR={m['WR']:>5.1f}%  "
              f"AvgR={m['AvgR']:>+.3f}  PF={pf_str}")

    # ── Day-of-week split (OOS only) ──────────────────────────────────────
    print("\n" + "=" * 80)
    print("  DAY-OF-WEEK SPLIT (OOS 2018-2023, no slippage)")
    print("=" * 80)
    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    by_dow = defaultdict(list)
    for t in oos_trades:
        ts = datetime.fromisoformat(t["timestamp"])
        if ts.weekday() < 5:
            by_dow[ts.weekday()].append(t)
    for dow in range(5):
        m = _metrics(by_dow[dow])
        pf_str = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {days[dow]:<4}  N={m['N']:>4}  WR={m['WR']:>5.1f}%  "
              f"AvgR={m['AvgR']:>+.3f}  PF={pf_str}")

    # ── Verdict ───────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  PERSISTENCE CHECK")
    print("=" * 80)
    pos_years = sum(1 for yr in by_year
                    if _metrics(by_year[yr])["AvgR"] > 0 and yr <= 2023)
    total_oos_years = sum(1 for yr in by_year if yr <= 2023)
    print(f"  OOS years with positive AvgR: {pos_years}/{total_oos_years}")
    is_years = [yr for yr in by_year if yr >= 2024]
    if is_years:
        for yr in sorted(is_years):
            m = _metrics(by_year[yr])
            print(f"  IS year {yr}: AvgR={m['AvgR']:+.3f}  N={m['N']}  WR={m['WR']:.1f}%")

    print("\nDone.")


if __name__ == "__main__":
    main()
