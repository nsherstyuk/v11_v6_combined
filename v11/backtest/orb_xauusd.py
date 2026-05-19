"""XAUUSD ORB backtest — uses the CURRENT live config.

Loads the freshly-downloaded XAUUSD 1-min MIDPOINT data from
tick_vault_data/xauusd/XAUUSD_MIDPOINT.csv and runs the v11 live
XAUUSD_ORB_CONFIG against it. This answers the practical question:
"does the live strategy, as it is configured right now, have
positive expectancy over the 16 years of data we have?"

NO PARAMETER TUNING. NO IS/OOS SEARCHING. The config is
v11.live.run_live.XAUUSD_ORB_CONFIG; this script only loads data
and runs that config once.

IS / OOS split (locked here as the standard for this codebase):
    IS:  2024+ (recent regime)
    OOS: 2018–2023 (longer holdout)
    Pre-2018 (early): 2010-06 → 2017-12 as a third holdout

Run:
    python -m v11.backtest.orb_xauusd
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
from v11.live.run_live import XAUUSD_ORB_CONFIG


ROOT = Path(__file__).resolve().parents[2]
MID_CSV = ROOT / "tick_vault_data" / "xauusd" / "XAUUSD_MIDPOINT.csv"

CONFIG = XAUUSD_ORB_CONFIG

# Slippage in price units, per side. _metrics doubles it for RT.
# XAUUSD: ~$0.30 RT realistic (matches existing orb_fx_grid comment),
# i.e. $0.15 per side.
SLIPPAGE_PER_SIDE = 0.15
SLIPPAGE_LABEL    = "slip+spread $0.30 RT (~3 pips)"

# IS / OOS split (matching investigate_orb_xauusd.py convention)
OOS_START   = datetime(2018, 1, 1)
OOS_END     = datetime(2023, 12, 31, 23, 59, 59)
IS_START    = datetime(2024, 1, 1)
EARLY_START = datetime(2010, 6, 1)
EARLY_END   = datetime(2017, 12, 31, 23, 59, 59)


GATE_OOS_AVGR_MIN = 0.05
GATE_NPY_MIN      = 20


# ── Data load ───────────────────────────────────────────────────────────────

def load_xauusd_bars(csv_path: Path) -> List[Bar]:
    """Load XAUUSD 1-min MIDPOINT bars.

    IBKR CSV columns: date, open, high, low, close (no volume).
    Bar dataclass requires tick_count + buy_volume + sell_volume — we
    default to 1, 0, 0. The current live config has
    velocity_filter_enabled=False so tick_count doesn't matter; the
    backtest is faithful to live in that regard.

    NOTE: this is a known difference from the original
    investigate_orb_xauusd.py run, which used tick-rich nautilus0
    Dukascopy bars and ran with velocity_filter_enabled=True. The
    LIVE strategy as of 2026-04-16 disabled velocity; so this run
    represents the actual deployed configuration, not the historical
    "what we tested before deploying" baseline.
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


# ── Period split ────────────────────────────────────────────────────────────

def _ts(t: dict) -> datetime:
    return datetime.fromisoformat(t["timestamp"])


def split_periods(trades: List[dict]):
    early = [t for t in trades if EARLY_START <= _ts(t) <= EARLY_END]
    oos   = [t for t in trades if OOS_START   <= _ts(t) <= OOS_END]
    is_   = [t for t in trades if IS_START    <= _ts(t)]
    return early, oos, is_


# ── Reporting ───────────────────────────────────────────────────────────────

def _fmt_m(m: dict) -> str:
    return (f"N={m['N']:>5,}  WR={m['WR']:>5.1f}%  "
            f"AvgR={m['AvgR']:+.3f}  PF={m['PF']:.2f}  "
            f"MaxDD={m['MaxDD']:.2f}")


def report(trades: List[dict]) -> None:
    early, oos, is_ = split_periods(trades)
    n_yrs_oos = (OOS_END.year - OOS_START.year + 1)
    n_yrs_is  = max(1, (datetime.now().year - IS_START.year + 1))
    n_yrs_early = (EARLY_END.year - EARLY_START.year + 1)

    print()
    print("=" * 88)
    print("  XAUUSD ORB — current LIVE config (XAUUSD_ORB_CONFIG)")
    print("=" * 88)
    print(f"  range {CONFIG.range_start_hour}-{CONFIG.range_end_hour} UTC, "
          f"trade {CONFIG.trade_start_hour}-{CONFIG.trade_end_hour} UTC, "
          f"RR={CONFIG.rr_ratio}")
    print(f"  min_range_pct={CONFIG.min_range_pct}, "
          f"max_range_pct={CONFIG.max_range_pct}, "
          f"min_range_size={CONFIG.min_range_size}, "
          f"max_range_size={CONFIG.max_range_size}")
    print(f"  skip_weekdays={CONFIG.skip_weekdays}, "
          f"velocity_filter={CONFIG.velocity_filter_enabled}, "
          f"gap_filter={CONFIG.gap_filter_enabled}")
    print(f"  Cost:  {SLIPPAGE_LABEL} (per-side ${SLIPPAGE_PER_SIDE})")
    print()
    print(f"  Total trades       : {len(trades):,}")
    print(f"  EARLY (2010-2017)  : {len(early):,} trades / {n_yrs_early} yrs "
          f"(~{len(early)//n_yrs_early}/yr)")
    print(f"  OOS   (2018-2023)  : {len(oos):,} trades / {n_yrs_oos} yrs "
          f"(~{len(oos)//n_yrs_oos}/yr)")
    print(f"  IS    (2024-now)   : {len(is_):,} trades / {n_yrs_is} yrs "
          f"(~{len(is_)//max(n_yrs_is,1)}/yr)")
    print()

    print("  ── Headline (with slippage) ─────────────────────────────────────")
    for label, sub in [("EARLY (2010-2017, holdout)", early),
                       ("OOS   (2018-2023, gate)   ", oos),
                       ("IS    (2024-now)          ", is_),
                       ("ALL                       ", trades)]:
        m = _metrics(sub, slippage_pts=SLIPPAGE_PER_SIDE)
        print(f"    {label}:  {_fmt_m(m)}")
    print()

    print("  ── No-slippage reference ────────────────────────────────────────")
    for label, sub in [("OOS   (2018-2023)", oos),
                       ("IS    (2024-now) ", is_),
                       ("ALL              ", trades)]:
        m = _metrics(sub, slippage_pts=0.0)
        print(f"    {label}:  {_fmt_m(m)}")
    print()

    # Year-by-year
    print("  ── Year-by-year (with slippage) ─────────────────────────────────")
    by_year = _split_by_year(trades)
    print(f"    {'year':<6} {'N':>5}  {'WR%':>5}  {'AvgR':>8}  "
          f"{'PF':>5}  {'MaxDD':>7}  {'segment':<8}")
    for yr in sorted(by_year):
        m = _metrics(by_year[yr], slippage_pts=SLIPPAGE_PER_SIDE)
        if EARLY_START.year <= yr <= EARLY_END.year:
            seg = "early"
        elif OOS_START.year <= yr <= OOS_END.year:
            seg = "OOS"
        else:
            seg = "IS"
        print(f"    {yr:<6} {m['N']:>5,}  {m['WR']:>5.1f}  "
              f"{m['AvgR']:>+8.3f}  {m['PF']:>5.2f}  "
              f"{m['MaxDD']:>7.2f}  {seg}")
    print()

    # May-2025 slice (Nick's same-season question)
    may2025 = [t for t in trades if "2025-05" in t["timestamp"][:7]]
    m = _metrics(may2025, slippage_pts=SLIPPAGE_PER_SIDE) if may2025 else None
    print("  ── May 2025 slice (same-season as today) ────────────────────────")
    if m and m["N"] > 0:
        print(f"    {_fmt_m(m)}")
    else:
        print(f"    No trades in May 2025.")
    print()

    # Gate
    oos_m = _metrics(oos, slippage_pts=SLIPPAGE_PER_SIDE)
    npy_oos = oos_m["N"] / n_yrs_oos if n_yrs_oos else 0
    avg_r_oos = oos_m["AvgR"]
    print("  ── Decision gate ────────────────────────────────────────────────")
    print(f"    Requires: OOS AvgR_slip ≥ +{GATE_OOS_AVGR_MIN} "
          f"AND N/yr ≥ {GATE_NPY_MIN}")
    print(f"    OOS AvgR_slip = {avg_r_oos:+.4f}  -> "
          f"{'PASS' if avg_r_oos >= GATE_OOS_AVGR_MIN else 'FAIL'}")
    print(f"    OOS N/yr      = {npy_oos:.1f}  -> "
          f"{'PASS' if npy_oos >= GATE_NPY_MIN else 'FAIL'}")
    overall = (avg_r_oos >= GATE_OOS_AVGR_MIN) and (npy_oos >= GATE_NPY_MIN)
    print(f"    OVERALL: {'PASS — strategy validates' if overall else 'FAIL — strategy does NOT validate at this config'}")
    print("=" * 88)


# ── Main ────────────────────────────────────────────────────────────────────

async def main_async():
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("orb_xauusd")

    print(f"Loading {MID_CSV.relative_to(ROOT)} ...")
    bars = load_xauusd_bars(MID_CSV)
    print(f"  {len(bars):,} 1-min bars  "
          f"{bars[0].timestamp.date()} → {bars[-1].timestamp.date()}\n")

    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), CONFIG)

    print("Running XAUUSD ORB...")
    trades = await _run_config(CONFIG, bars,
                                gap_filter=CONFIG.gap_filter_enabled,
                                gap_lookup=gap_lookup, log=log)
    print(f"  -> {len(trades):,} trades produced\n")

    out_dir = ROOT / "v11" / "backtest" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trades).to_csv(out_dir / "xauusd_orb_trades.csv",
                                 index=False)
    print(f"  raw trades saved to "
          f"{(out_dir / 'xauusd_orb_trades.csv').relative_to(ROOT)}\n")

    report(trades)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
