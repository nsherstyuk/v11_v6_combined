"""GBPUSD regime / data-quality sanity check.

Two questions:
  (a) Is 2025+ data quality different (spreads, gaps, timestamp completeness)?
  (b) Did GBPUSD's Asian-range and London follow-through regime change in 2025?

If (a) shows anomaly -> the 2025 collapse may be data, not regime.
If (b) shows regime shift (smaller ranges, weaker follow-through) -> regime story.
If neither -> sample noise or pure crowding.
"""
from __future__ import annotations

import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import statistics
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from v11.backtest.data_loader import load_instrument_bars
from v11.core.types import Bar


def main():
    print("Loading GBPUSD ...", flush=True)
    bars = load_instrument_bars("GBPUSD")
    print(f"  {len(bars):,} bars  {bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}\n")

    by_year_day = defaultdict(lambda: defaultdict(list))
    for b in bars:
        by_year_day[b.timestamp.year][b.timestamp.date()].append(b)

    # ── (a) Data-quality per year ────────────────────────────────────────
    print("=" * 90)
    print("  DATA QUALITY PER YEAR")
    print("=" * 90)
    print(f"  {'Year':<6} {'Days':>5} {'Bars':>10} {'Bars/day':>10} "
          f"{'AvgSpr':>9} {'MaxSpr_p99':>11} {'Tick_avg':>10}")
    print("  " + "-" * 75)

    for yr in sorted(by_year_day.keys()):
        days = by_year_day[yr]
        n_days = len(days)
        bars_yr = [b for d in days.values() for b in d]
        n_bars = len(bars_yr)
        bpd = n_bars / max(n_days, 1)
        # avg_spread / max_spread might be 0 in some files
        spr = [b.avg_spread for b in bars_yr if getattr(b, "avg_spread", 0) > 0]
        max_spr = [b.max_spread for b in bars_yr if getattr(b, "max_spread", 0) > 0]
        ticks = [b.tick_count for b in bars_yr if getattr(b, "tick_count", 0) > 0]
        avg_spr = statistics.mean(spr) if spr else 0.0
        p99_max_spr = sorted(max_spr)[int(len(max_spr) * 0.99)] if max_spr else 0.0
        avg_tick = statistics.mean(ticks) if ticks else 0.0
        print(f"  {yr:<6} {n_days:>5} {n_bars:>10,} {bpd:>10.0f} "
              f"{avg_spr:>9.5f} {p99_max_spr:>11.5f} {avg_tick:>10.1f}")

    # ── (b) Asian range + London follow-through per year ─────────────────
    print()
    print("=" * 90)
    print("  ASIAN RANGE (00-06 UTC) AND LONDON FOLLOW-THROUGH PER YEAR")
    print("=" * 90)
    print(f"  {'Year':<6} {'Days':>5} {'AsianRng_pips':>14} "
          f"{'LonRng_pips':>13} {'BrkRate%':>10} {'FollowThru_pips':>17}")
    print("  " + "-" * 80)

    for yr in sorted(by_year_day.keys()):
        days = by_year_day[yr]
        asian_ranges_pips: List[float] = []
        lon_ranges_pips: List[float] = []
        breakout_count = 0
        followthru_pips: List[float] = []
        n_valid_days = 0

        for date, day_bars in days.items():
            asian = [b for b in day_bars if b.timestamp.hour < 6]
            london = [b for b in day_bars if 8 <= b.timestamp.hour < 16]
            if not asian or not london:
                continue
            n_valid_days += 1
            a_high = max(b.high for b in asian)
            a_low  = min(b.low  for b in asian)
            a_rng_pips = (a_high - a_low) / 0.0001
            asian_ranges_pips.append(a_rng_pips)

            l_high = max(b.high for b in london)
            l_low  = min(b.low  for b in london)
            lon_ranges_pips.append((l_high - l_low) / 0.0001)

            # Breakout: did London exceed Asian high or low?
            up_break = l_high > a_high
            dn_break = l_low  < a_low
            if up_break or dn_break:
                breakout_count += 1
                # Follow-through magnitude: max excursion past the broken edge
                if up_break:
                    ft = (l_high - a_high) / 0.0001
                else:
                    ft = (a_low - l_low) / 0.0001
                followthru_pips.append(ft)

        if n_valid_days == 0:
            continue
        med_a = statistics.median(asian_ranges_pips)
        med_l = statistics.median(lon_ranges_pips)
        brk_rate = breakout_count / n_valid_days * 100
        med_ft = statistics.median(followthru_pips) if followthru_pips else 0.0
        print(f"  {yr:<6} {n_valid_days:>5} {med_a:>14.1f} "
              f"{med_l:>13.1f} {brk_rate:>10.1f} {med_ft:>17.1f}")

    # ── (c) "Clean breakout" rate (London close holds outside Asian range) ──
    print()
    print("=" * 90)
    print("  CLEAN BREAKOUT RATE (London close holds outside Asian range)")
    print("=" * 90)
    print(f"  {'Year':<6} {'Brkdays':>7} {'CleanHold%':>11} "
          f"{'AvgClose_pips':>14}")
    print("  " + "-" * 50)

    for yr in sorted(by_year_day.keys()):
        days = by_year_day[yr]
        brk_days = 0
        clean_hold = 0
        clean_close_pips: List[float] = []
        for date, day_bars in days.items():
            asian = [b for b in day_bars if b.timestamp.hour < 6]
            london = [b for b in day_bars if 8 <= b.timestamp.hour < 16]
            if not asian or not london:
                continue
            a_high = max(b.high for b in asian)
            a_low  = min(b.low  for b in asian)
            l_high = max(b.high for b in london)
            l_low  = min(b.low  for b in london)
            l_close = london[-1].close
            up_break = l_high > a_high
            dn_break = l_low  < a_low
            if not (up_break or dn_break):
                continue
            brk_days += 1
            # "Clean": London close on the breakout side
            if up_break and l_close > a_high:
                clean_hold += 1
                clean_close_pips.append((l_close - a_high) / 0.0001)
            elif dn_break and l_close < a_low:
                clean_hold += 1
                clean_close_pips.append((a_low - l_close) / 0.0001)
        if brk_days == 0:
            continue
        rate = clean_hold / brk_days * 100
        avg_cc = statistics.mean(clean_close_pips) if clean_close_pips else 0.0
        print(f"  {yr:<6} {brk_days:>7} {rate:>11.1f} {avg_cc:>14.1f}")

    print("\nDone.")


if __name__ == "__main__":
    main()
