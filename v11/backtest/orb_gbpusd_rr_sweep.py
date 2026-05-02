"""GBPUSD ORB — RR sweep year-by-year.

Sweeps RR ratio from 1.0 to 3.0. Asks: does a lower target salvage 2024-2026
while preserving 2018-2023?

Same pre-registered template (Asian range, no Wed-skip, velocity off).
"""
from __future__ import annotations

import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
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
    log = logging.getLogger("rr_sweep")
    log.setLevel(logging.WARNING)

    base_cfg = _fx_template("GBPUSD")
    bars = load_instrument_bars("GBPUSD")
    print(f"GBPUSD: {len(bars):,} bars  "
          f"{bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}\n")

    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), base_cfg)

    rrs = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
    all_results = {}

    for rr in rrs:
        cfg = replace(base_cfg, rr_ratio=rr)
        trades = asyncio.run(_run_config(cfg, bars, gap_filter=False,
                                         gap_lookup=gap_lookup, log=log))
        all_results[rr] = trades
        print(f"  RR={rr}: {len(trades)} trades", flush=True)

    # ── Aggregate table ──────────────────────────────────────────────────
    print()
    print("=" * 100)
    print("  RR SWEEP — full sample 2018-2026")
    print("=" * 100)
    print(f"  {'RR':>4} | {'N':>5} {'WR%':>6} {'AvgR':>8} {'PF':>6} | "
          f"{'AvgR_1pip':>10} {'AvgR_2pip':>10}")
    print("  " + "-" * 70)
    for rr in rrs:
        trades = all_results[rr]
        m = _metrics(trades)
        m_1 = _metrics(trades, slippage_pts=0.00005)
        m_2 = _metrics(trades, slippage_pts=0.00010)
        pf = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {rr:>4.2f} | {m['N']:>5} {m['WR']:>6.1f} {m['AvgR']:>+8.3f} {pf:>6} | "
              f"{m_1['AvgR']:>+10.3f} {m_2['AvgR']:>+10.3f}")

    # ── Year-by-year for each RR ─────────────────────────────────────────
    print()
    print("=" * 100)
    print("  YEAR-BY-YEAR AvgR (no slippage) — 2024-2026 = recent regime")
    print("=" * 100)
    years = sorted({datetime.fromisoformat(t["timestamp"]).year
                    for tr in all_results.values() for t in tr})
    hdr = f"  {'RR':>5} |" + "".join(f"{yr:>9}" for yr in years)
    print(hdr)
    print("  " + "-" * (8 + 9 * len(years)))
    for rr in rrs:
        by_year = _split_by_year(all_results[rr])
        cells = []
        for yr in years:
            ts = by_year.get(yr, [])
            if not ts:
                cells.append(f"{'-':>9}")
            else:
                m = _metrics(ts)
                cells.append(f"{m['AvgR']:>+9.3f}")
        print(f"  {rr:>5.2f} |" + "".join(cells))

    # ── OOS (2018-2023) vs IS (2024-2026) for each RR ────────────────────
    print()
    print("=" * 100)
    print("  OOS (2018-2023) vs IS (2024-2026) per RR")
    print("=" * 100)
    print(f"  {'RR':>4} | {'OOS_N':>6} {'OOS_WR':>7} {'OOS_AvgR':>9} {'OOS_PF':>7} | "
          f"{'IS_N':>5} {'IS_WR':>6} {'IS_AvgR':>9} {'IS_PF':>6}")
    print("  " + "-" * 90)
    for rr in rrs:
        trades = all_results[rr]
        oos = [t for t in trades if datetime.fromisoformat(t["timestamp"]).year <= 2023]
        is_ = [t for t in trades if datetime.fromisoformat(t["timestamp"]).year >= 2024]
        om = _metrics(oos); im = _metrics(is_)
        opf = f"{om['PF']:.2f}" if om['PF'] != float("inf") else "  inf"
        ipf = f"{im['PF']:.2f}" if im['PF'] != float("inf") else "  inf"
        print(f"  {rr:>4.2f} | {om['N']:>6} {om['WR']:>7.1f} {om['AvgR']:>+9.3f} {opf:>7} | "
              f"{im['N']:>5} {im['WR']:>6.1f} {im['AvgR']:>+9.3f} {ipf:>6}")

    print("\nDone.")


if __name__ == "__main__":
    main()
