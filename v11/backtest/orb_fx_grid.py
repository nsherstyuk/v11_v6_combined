"""ORB IS/OOS grid across FX majors + XAUUSD anchor.

Pre-registered structure — same for every FX pair, NO per-pair tuning:
  range:      00:00–06:00 UTC (Asian)
  trade:      08:00–16:00 UTC (London + NY)
  RR:         2.5
  range pct:  0.05% – 2.0% of price (absolute filter disabled)
  velocity:   OFF (XAUUSD-tuned threshold doesn't transfer to FX)
  gap:        OFF
  weekdays:   no skip (XAUUSD's Wednesday-skip is gold-specific)

XAUUSD anchor uses its existing tuned config (velocity=ON gold-tuned,
Wednesday-skip, gap=OFF) so it is directly comparable to
investigate_orb_xauusd.py headline.

IS = 2024+, OOS = 2018–2023, matching existing convention.
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
    _metrics, _precompute_gap_metrics, _run_config,
)
from v11.core.types import Bar
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig


OOS_START = datetime(2018, 1, 1)
OOS_END   = datetime(2023, 12, 31, 23, 59, 59)
IS_START  = datetime(2024, 1, 1)
OOS_YEARS = 6


# ── Per-instrument configs ──────────────────────────────────────────────────
# FX majors share one pre-registered template. XAUUSD keeps its tuned config.

def _fx_template(symbol: str) -> V6StrategyConfig:
    return V6StrategyConfig(
        instrument=symbol,
        range_start_hour=0, range_end_hour=6,
        trade_start_hour=8, trade_end_hour=16,
        skip_weekdays=(),                  # no Wednesday-skip for FX
        velocity_filter_enabled=False,     # gold-tuned threshold doesn't transfer
        velocity_lookback_minutes=3,
        velocity_threshold=200.0,
        rr_ratio=2.5,
        min_range_size=0.0,                # disable absolute filter
        max_range_size=1e9,
        min_range_pct=0.05, max_range_pct=2.0,
        be_hours=999, max_pending_hours=4, time_exit_minutes=0,
        gap_filter_enabled=False,
        gap_vol_percentile=50.0,
        gap_range_filter_enabled=False,
        gap_range_percentile=40.0,
        gap_rolling_days=60,
        gap_start_hour=6, gap_end_hour=8,
        price_decimals=5,                  # FX majors quoted to 5 dp
    )


_FX_PAIRS = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF"]

_XAUUSD_CFG = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0, range_end_hour=6,
    trade_start_hour=8, trade_end_hour=16,
    skip_weekdays=(2,),                    # Wednesday-skip (XAUUSD-validated)
    velocity_filter_enabled=True,
    velocity_lookback_minutes=3,
    velocity_threshold=168.0,
    rr_ratio=2.5,
    min_range_size=1.0, max_range_size=50.0,
    min_range_pct=0.05, max_range_pct=2.0,
    be_hours=999, max_pending_hours=4, time_exit_minutes=0,
    gap_filter_enabled=False,
    gap_vol_percentile=50.0,
    gap_range_filter_enabled=False,
    gap_range_percentile=40.0,
    gap_rolling_days=60,
    gap_start_hour=6, gap_end_hour=8,
    price_decimals=2,
)

# Typical 1-pip RT slippage in absolute price units (per side = pip/2)
_PIP_RT_PRICE: Dict[str, float] = {
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001,
    "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
    "XAUUSD": 0.30,   # ~$0.30 RT realistic on retail
}


# ── Per-instrument run ──────────────────────────────────────────────────────

async def run_one(symbol: str, cfg: V6StrategyConfig, log: logging.Logger):
    bars = load_instrument_bars(symbol)
    print(f"  {symbol}: {len(bars):,} bars  "
          f"{bars[0].timestamp.date()} -> {bars[-1].timestamp.date()}", flush=True)

    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), cfg)

    trades = await _run_config(cfg, bars, gap_filter=False,
                               gap_lookup=gap_lookup, log=log)
    return trades


def split(trades):
    oos = [t for t in trades
           if OOS_START <= datetime.fromisoformat(t["timestamp"]) <= OOS_END]
    is_ = [t for t in trades
           if datetime.fromisoformat(t["timestamp"]) >= IS_START]
    return is_, oos


def main():
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("orb_fx_grid")
    log.setLevel(logging.WARNING)

    print("=== ORB FX-grid (pre-registered template, no per-pair tuning) ===\n")

    runs = []  # (symbol, trades, slip_per_side)

    # FX pairs
    for sym in _FX_PAIRS:
        cfg = _fx_template(sym)
        trades = asyncio.run(run_one(sym, cfg, log))
        runs.append((sym, trades, _PIP_RT_PRICE[sym] / 2))
        print(f"    -> {len(trades)} trades total\n", flush=True)

    # XAUUSD anchor
    print("  XAUUSD (existing tuned config — anchor) ...")
    xau_trades = asyncio.run(run_one("XAUUSD", _XAUUSD_CFG, log))
    runs.append(("XAUUSD", xau_trades, _PIP_RT_PRICE["XAUUSD"] / 2))
    print(f"    -> {len(xau_trades)} trades total\n", flush=True)

    # ── Unified summary ───────────────────────────────────────────────────
    W = 120
    print("=" * W)
    print("  IS/OOS RESULTS  (IS=2024+, OOS=2018-2023)")
    print("=" * W)
    hdr = (f"  {'Symbol':<8} | "
           f"{'IS_N':>5} {'IS_WR':>6} {'IS_AvgR':>8} {'IS_PF':>6} | "
           f"{'OOS_N':>6} {'/yr':>5} {'OOS_WR':>7} {'OOS_AvgR':>9} {'OOS_PF':>7} | "
           f"{'AvgR_slip':>10}")
    print(hdr)
    print("  " + "-" * (W - 2))

    summary = []
    for sym, trades, slip in runs:
        is_t, oos_t = split(trades)
        im = _metrics(is_t)
        om = _metrics(oos_t)
        om_slip = _metrics(oos_t, slippage_pts=slip)
        per_yr = om["N"] / OOS_YEARS if OOS_YEARS else 0
        im_pf = f"{im['PF']:.2f}" if im['PF'] != float("inf") else "  inf"
        om_pf = f"{om['PF']:.2f}" if om['PF'] != float("inf") else "   inf"
        print(
            f"  {sym:<8} | "
            f"{im['N']:>5} {im['WR']:>6.1f} {im['AvgR']:>8.3f} {im_pf:>6} | "
            f"{om['N']:>6} {per_yr:>5.1f} {om['WR']:>7.1f} {om['AvgR']:>9.3f} {om_pf:>7} | "
            f"{om_slip['AvgR']:>10.3f}"
        )
        summary.append((sym, om, om_slip, per_yr))

    # ── Decision-gate filter ──────────────────────────────────────────────
    print()
    print("=" * W)
    print("  DECISION GATE: OOS AvgR >= +0.05R after slippage AND N/yr >= 20")
    print("=" * W)
    survivors = [s for s in summary
                 if s[2]["AvgR"] >= 0.05 and s[3] >= 20]
    if not survivors:
        print("  No instrument passes the gate. ORB-Asian-range structure has no "
              "exploitable edge on these FX majors at this template.")
    else:
        for sym, om, om_slip, per_yr in survivors:
            print(f"  {sym}: OOS N={om['N']} ({per_yr:.1f}/yr)  "
                  f"AvgR={om['AvgR']:+.3f}  AvgR_slip={om_slip['AvgR']:+.3f}  "
                  f"WR={om['WR']:.1f}%  -> investigate further")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
