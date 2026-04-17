"""
ORB XAUUSD — IS/OOS Backtest.

Loads clean XAUUSD 1-min data, runs V6 ORBStrategy via ReplayORBAdapter with:
  - velocity computed from tick_count (matching live, not bar-count proxy)
  - real gap filter from pre-computed rolling percentiles (no lookahead)
  - no LLM filter

Produces IS (2024+) / OOS (2018-2023) metrics + year-by-year breakdown.
"""
import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import asyncio
import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from v11.backtest.data_loader import load_instrument_bars
from v11.core.types import Bar
from v11.replay.replay_orb import ReplayORBAdapter
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import GapMetrics


# ── Date splits ─────────────────────────────────────────────────────────────

OOS_START = datetime(2018, 1, 1)
OOS_END   = datetime(2023, 12, 31, 23, 59, 59)
IS_START  = datetime(2024, 1, 1)
IS_END    = datetime(2029, 12, 31, 23, 59, 59)
OOS_YEARS = 6   # 2018-2023 inclusive


# ── V6 config for XAUUSD ────────────────────────────────────────────────────

BASE_CFG = V6StrategyConfig(
    instrument="XAUUSD",
    range_start_hour=0,
    range_end_hour=6,
    trade_start_hour=8,
    trade_end_hour=16,
    skip_weekdays=(2,),           # skip Wednesday (0=Mon … 6=Sun)
    velocity_filter_enabled=True,
    velocity_lookback_minutes=3,
    velocity_threshold=168.0,     # ticks/min threshold (live value)
    rr_ratio=2.5,
    min_range_size=1.0,
    max_range_size=50.0,
    min_range_pct=0.05,
    max_range_pct=2.0,
    be_hours=999,
    max_pending_hours=4,
    time_exit_minutes=0,
    gap_filter_enabled=False,     # toggled per run
    gap_vol_percentile=50.0,
    gap_range_filter_enabled=False,
    gap_range_percentile=40.0,
    gap_rolling_days=60,
    gap_start_hour=6,
    gap_end_hour=8,
    price_decimals=2,
)


# ── Gap filter pre-computation ───────────────────────────────────────────────

def _precompute_gap_metrics(
    bars_by_date: Dict[str, List[Bar]],
    cfg: V6StrategyConfig,
) -> Dict[str, GapMetrics]:
    """Pre-compute gap metrics for every date with no lookahead.

    For each date (chronological order):
      - compute gap_vol from 1-min bars in [gap_start_hour, gap_end_hour)
      - compute gap_range_ratio = (gap_high - gap_low) / overnight_range
      - check against rolling percentiles of PREVIOUS days
      - THEN append to history
    """
    import numpy as np

    lookup: Dict[str, GapMetrics] = {}
    vol_history: List[float] = []
    range_history: List[float] = []

    for date_str in sorted(bars_by_date.keys()):
        day_bars = bars_by_date[date_str]

        # Gap bars: gap_start_hour <= hour < gap_end_hour
        gap_bars = [b for b in day_bars
                    if cfg.gap_start_hour <= b.timestamp.hour < cfg.gap_end_hour]

        # Overnight bars: 00:00 - gap_start_hour (for range denominator)
        overnight_bars = [b for b in day_bars
                          if b.timestamp.hour < cfg.gap_start_hour]

        # ── Gap volatility ──────────────────────────────────────
        gap_vol = 0.0
        if len(gap_bars) >= 2:
            closes = [b.close for b in gap_bars]
            log_returns = [math.log(closes[i] / closes[i - 1])
                           for i in range(1, len(closes))
                           if closes[i - 1] > 0]
            if len(log_returns) >= 1:
                gap_vol = statistics.stdev(log_returns) if len(log_returns) >= 2 else 0.0

        # ── Gap range ratio ─────────────────────────────────────
        gap_range_ratio = 0.0
        if gap_bars:
            g_high = max(b.high for b in gap_bars)
            g_low  = min(b.low  for b in gap_bars)
            gap_range = g_high - g_low

            if overnight_bars:
                o_high = max(b.high for b in overnight_bars)
                o_low  = min(b.low  for b in overnight_bars)
                overnight_range = o_high - o_low
                if overnight_range > 0:
                    gap_range_ratio = gap_range / overnight_range
                else:
                    gap_range_ratio = 1.0
            else:
                gap_range_ratio = 1.0

        # ── Check against rolling percentiles (BEFORE appending) ──
        MIN_HISTORY = 10
        WINDOW = cfg.gap_rolling_days

        if len(vol_history) < MIN_HISTORY:
            vol_passes   = True
            range_passes = True
        else:
            recent_vol   = vol_history[-WINDOW:]
            recent_range = range_history[-WINDOW:]
            vol_threshold   = float(np.percentile(recent_vol,   cfg.gap_vol_percentile))
            range_threshold = float(np.percentile(recent_range, cfg.gap_range_percentile))
            vol_passes   = gap_vol >= vol_threshold
            range_passes = gap_range_ratio >= range_threshold

        lookup[date_str] = GapMetrics(
            gap_volatility=gap_vol,
            gap_range=gap_range_ratio,
            vol_passes=vol_passes,
            range_passes=range_passes,
        )

        # Append AFTER check (no lookahead)
        vol_history.append(gap_vol)
        range_history.append(gap_range_ratio)

    return lookup


# ── Metrics ──────────────────────────────────────────────────────────────────

def _max_drawdown(rs: List[float]) -> float:
    """Max peak-to-trough in cumulative R equity curve."""
    if not rs:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _metrics(trades: List[dict], slippage_pts: float = 0.0) -> dict:
    """Compute trade metrics. slippage_pts deducts 2× per trade (round-trip)."""
    if not trades:
        return {"N": 0, "WR": 0.0, "AvgR": 0.0, "PF": 0.0, "MaxDD": 0.0}

    rs = []
    for t in trades:
        rng = t["range_high"] - t["range_low"]
        if rng <= 0:
            continue  # skip degenerate
        adjusted_pnl = t["pnl"] - 2 * slippage_pts
        rs.append(adjusted_pnl / rng)

    if not rs:
        return {"N": 0, "WR": 0.0, "AvgR": 0.0, "PF": 0.0, "MaxDD": 0.0}

    wins   = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    wr     = len(wins) / len(rs) * 100
    avg_r  = statistics.mean(rs)
    pf     = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    mdd    = _max_drawdown(rs)

    return {
        "N":     len(rs),
        "WR":    wr,
        "AvgR":  avg_r,
        "PF":    pf,
        "MaxDD": mdd,
    }


def _split_by_year(trades: List[dict]) -> Dict[int, List[dict]]:
    by_year: Dict[int, List[dict]] = defaultdict(list)
    for t in trades:
        ts = datetime.fromisoformat(t["timestamp"])
        by_year[ts.year].append(t)
    return dict(by_year)


# ── Single config run ─────────────────────────────────────────────────────────

async def _run_config(
    cfg: V6StrategyConfig,
    all_bars: List[Bar],
    gap_filter: bool,
    gap_lookup: Dict[str, GapMetrics],
    log: logging.Logger,
) -> List[dict]:
    """Run one config variant. Returns list of trade records."""

    # Rebuild config with gap_filter toggle
    from dataclasses import replace
    cfg = replace(cfg, gap_filter_enabled=gap_filter)

    adapter = ReplayORBAdapter(v6_config=cfg, llm_filter=None, log=log)

    # ── Fix 1: velocity from tick_count (matching live) ──────────────────
    _ctx = adapter._context

    def _tick_count_velocity(lookback_minutes: int, current_time: datetime) -> float:
        cutoff = current_time - timedelta(minutes=lookback_minutes)
        recent = [b for b in _ctx._bars if b.timestamp >= cutoff]
        if not recent:
            return 0.0
        return sum(b.tick_count for b in recent) / max(lookback_minutes, 1)

    _ctx.get_velocity = _tick_count_velocity

    # ── Fix 2: real gap filter via pre-computed lookup ────────────────────
    _default_gap = GapMetrics(
        gap_volatility=0.0,
        gap_range=0.0,
        vol_passes=True,
        range_passes=True,
    )

    def _gap_from_lookup(now, gs, ge, vp, rp, rd):
        return gap_lookup.get(now.strftime("%Y-%m-%d"), _default_gap)

    _ctx.get_gap_metrics = _gap_from_lookup

    # ── Group bars by date ────────────────────────────────────────────────
    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in all_bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)

    # ── Iterate dates in order ────────────────────────────────────────────
    skip_weekdays = set(cfg.skip_weekdays)

    for date_str in sorted(bars_by_date.keys()):
        day_bars = bars_by_date[date_str]
        if not day_bars:
            continue

        # Skip configured weekdays (e.g. Wednesday=2)
        weekday = day_bars[0].timestamp.weekday()
        if weekday in skip_weekdays:
            continue

        for bar in day_bars:
            await adapter.on_bar(bar)

    return adapter._trade_records


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Silence chatty replay logs
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("orb_bt")
    log.setLevel(logging.WARNING)

    print("Loading XAUUSD bars…", flush=True)
    all_bars = load_instrument_bars("XAUUSD")
    print(f"  Loaded {len(all_bars):,} bars "
          f"({all_bars[0].timestamp.date()} – {all_bars[-1].timestamp.date()})")

    # Pre-compute gap metrics once (shared across both runs)
    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in all_bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)

    print("Pre-computing gap metrics…", flush=True)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), BASE_CFG)
    print(f"  Gap lookup: {len(gap_lookup)} dates, "
          f"{sum(1 for g in gap_lookup.values() if g.vol_passes)} vol_pass days")

    # ── Config variants ───────────────────────────────────────────────────
    from dataclasses import replace
    cfg_vel_off  = replace(BASE_CFG, velocity_filter_enabled=False)
    cfg_wed_all  = replace(BASE_CFG, skip_weekdays=())  # include Wednesday

    variants = [
        ("velocity=ON,  gap=OFF",              BASE_CFG,    False),
        ("velocity=ON,  gap=ON",               BASE_CFG,    True),
        ("velocity=OFF, gap=OFF",              cfg_vel_off, False),
        ("velocity=OFF, gap=ON",               cfg_vel_off, True),
        ("velocity=ON,  gap=ON,  Wed=include", cfg_wed_all, True),
    ]

    results = {}
    for label, cfg, gap_filter in variants:
        print(f"Running: {label}…", flush=True)
        trades = asyncio.run(
            _run_config(cfg, all_bars, gap_filter, gap_lookup, log)
        )
        print(f"  {len(trades)} trades total")
        results[label] = trades

    # ── IS/OOS split ──────────────────────────────────────────────────────
    def _split(trades):
        oos = [t for t in trades
               if OOS_START <= datetime.fromisoformat(t["timestamp"]) <= OOS_END]
        is_ = [t for t in trades
               if datetime.fromisoformat(t["timestamp"]) >= IS_START]
        return is_, oos

    # ── Print IS/OOS summary table ────────────────────────────────────────
    W = 106
    print()
    print("=" * W)
    print("  ORB XAUUSD — IS/OOS RESULTS")
    print("=" * W)
    hdr = (f"  {'Config':<38} | {'IS_N':>5} {'IS_WR':>6} {'IS_AvgR':>8} | "
           f"{'OOS_N':>6} {'/yr':>5} {'OOS_WR':>7} {'OOS_AvgR':>9}")
    print(hdr)
    sep = "  " + "-" * 38 + "+-" + "-" * 23 + "+-" + "-" * 31
    print(sep)

    for label, trades in results.items():
        is_trades, oos_trades = _split(trades)
        im = _metrics(is_trades)
        om = _metrics(oos_trades)
        oos_per_yr = om["N"] / OOS_YEARS if OOS_YEARS else 0

        print(
            f"  {label:<38} | "
            f"{im['N']:>5} {im['WR']:>6.1f} {im['AvgR']:>8.3f} | "
            f"{om['N']:>6} {oos_per_yr:>5.1f} {om['WR']:>7.1f} {om['AvgR']:>9.3f}"
        )

    # ── Year-by-year OOS ──────────────────────────────────────────────────
    for label, trades in results.items():
        _, oos_trades = _split(trades)
        print()
        print("=" * W)
        print(f"  YEAR-BY-YEAR OOS ({label})")
        print("=" * W)
        print(f"  {'Year':<12} {'N':>5} {'WR%':>7} {'AvgR':>8} {'PF':>6} {'MaxDD':>7}")
        print("  " + "-" * 50)

        by_year = _split_by_year(oos_trades)
        for yr in sorted(by_year.keys()):
            m = _metrics(by_year[yr])
            pf_str = f"{m['PF']:.2f}" if m["PF"] != float("inf") else "  inf"
            print(
                f"  {yr:<12} {m['N']:>5} {m['WR']:>7.1f} {m['AvgR']:>8.3f} "
                f"{pf_str:>6} {m['MaxDD']:>7.3f}"
            )

        m_all = _metrics(oos_trades)
        pf_str = f"{m_all['PF']:.2f}" if m_all["PF"] != float("inf") else "  inf"
        print("  " + "-" * 50)
        print(
            f"  {'TOTAL':<12} {m_all['N']:>5} {m_all['WR']:>7.1f} {m_all['AvgR']:>8.3f} "
            f"{pf_str:>6} {m_all['MaxDD']:>7.3f}"
        )

    # ── Slippage stress test (gap=ON OOS only) ────────────────────────────
    gap_on_label = "velocity=ON,  gap=ON"
    gap_on_trades = results.get(gap_on_label, [])
    _, gap_on_oos = _split(gap_on_trades)

    if gap_on_oos:
        print()
        print("=" * W)
        print("  SLIPPAGE STRESS TEST (velocity=ON, gap=ON, OOS)")
        print("=" * W)
        print(f"  {'Slippage/side':>14}  {'N':>5}  {'WR%':>6}  {'AvgR':>8}  {'PF':>6}")
        print("  " + "-" * 46)
        for slip in [0.0, 0.1, 0.2, 0.3, 0.5]:
            m = _metrics(gap_on_oos, slippage_pts=slip)
            pf_str = f"{m['PF']:.2f}" if m["PF"] != float("inf") else "   inf"
            print(
                f"  {slip:>13.1f}  {m['N']:>5}  {m['WR']:>6.1f}  "
                f"{m['AvgR']:>8.3f}  {pf_str:>6}"
            )

    # ── Direction breakdown (gap=ON OOS) ─────────────────────────────────
    if gap_on_oos:
        print()
        print("=" * W)
        print("  DIRECTION BREAKDOWN (velocity=ON, gap=ON, OOS)")
        print("=" * W)
        print(f"  {'Direction':<12}  {'N':>5}  {'WR%':>6}  {'AvgR':>8}  {'PF':>6}")
        print("  " + "-" * 42)
        for direction in ("LONG", "SHORT"):
            dir_trades = [t for t in gap_on_oos if t.get("direction") == direction]
            m = _metrics(dir_trades)
            pf_str = f"{m['PF']:.2f}" if m["PF"] != float("inf") else "   inf"
            print(
                f"  {direction:<12}  {m['N']:>5}  {m['WR']:>6.1f}  "
                f"{m['AvgR']:>8.3f}  {pf_str:>6}"
            )

    print()
    print("Done.")


if __name__ == "__main__":
    main()
