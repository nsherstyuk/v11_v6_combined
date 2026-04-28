"""
ORB Stale-Breakout Variant Backtest — XAUUSD IS/OOS

Question: Today's V11 live session (2026-04-28) skipped the trading day
because, at 08:09 UTC (right at trade-window open), spot was $1.82 below
the Asian range low. The V6 stale-breakout guard treats both directions
as dead in this case and goes DONE_TODAY. But only the SELL side was
actually stale — the BUY stop $79 above was perfectly placeable.

This script compares three behaviors over OOS 2018-2023:

  skip      — V6 baseline. If mid is outside range when about to place
              brackets, kill the day (DONE_TODAY).
  wait      — Hold at RANGE_READY. Don't go DONE_TODAY. Each subsequent
              tick re-evaluates: when mid re-enters the range, place
              both brackets. If window closes still outside, no trade.
  one_sided — Place only the side that's NOT stale. mid < low ->
              place BUY only (skip SELL). mid > high -> place SELL only.

Each variant uses identical V6 plumbing (gap filter, velocity filter,
config) — only the stale-breakout decision differs.

Usage:
    python -m v11.backtest.investigate_orb_stale_breakout
"""
from __future__ import annotations

import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import asyncio
import logging
import statistics
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.investigate_orb_xauusd import (
    OOS_START, OOS_END, IS_START, OOS_YEARS,
    _INSTRUMENT_CONFIGS, _precompute_gap_metrics, _metrics, _split_by_year,
)
from v11.core.types import Bar
from v11.replay.replay_orb import (
    ReplayORBAdapter,
    ReplayORBExecutionEngine,
    ReplayORBMarketContext,
)
from v11.v6_orb.config import StrategyConfig as V6StrategyConfig
from v11.v6_orb.market_event import GapMetrics, RangeInfo, Tick
from v11.v6_orb.orb_strategy import ORBStrategy, StrategyState
from v11.v6_orb.interfaces import MarketContext, ExecutionEngine


# ── Variant strategies ────────────────────────────────────────────────────────


class WaitStaleStrategy(ORBStrategy):
    """If mid is outside range at the would-be placement tick, hold at
    RANGE_READY (don't transition to DONE_TODAY). Each subsequent tick
    re-checks. When mid re-enters the range, normal flow places brackets."""

    def _handle_range_and_orders(self, tick, context, execution):
        cfg = self.config

        # Window closed?  (preserve original semantics for post-window kill)
        if not context.time_is_in_trade_window(
                tick.timestamp, cfg.trade_start_hour, cfg.trade_end_hour):
            if self.state == StrategyState.ORDERS_PLACED:
                execution.cancel_orb_brackets()
            self.state = StrategyState.DONE_TODAY
            return

        # Async placement failure
        if (self.state == StrategyState.ORDERS_PLACED
                and hasattr(execution, "entry_placement_failed")
                and execution.entry_placement_failed()):
            try:
                execution.cancel_orb_brackets()
            except Exception:
                pass
            if hasattr(execution, "clear_entry_failure"):
                execution.clear_entry_failure()
            self.orders_placed_time = None
            self.state = StrategyState.RANGE_READY
            return

        # Max pending hours (window-anchored, like the recent fix)
        if (self.state == StrategyState.ORDERS_PLACED
                and cfg.max_pending_hours > 0
                and self.orders_placed_time):
            window_open = tick.timestamp.replace(
                hour=cfg.trade_start_hour, minute=0, second=0, microsecond=0)
            effective_start = max(self.orders_placed_time, window_open)
            elapsed_h = (tick.timestamp - effective_start).total_seconds() / 3600
            if elapsed_h >= cfg.max_pending_hours:
                execution.cancel_orb_brackets()
                self.state = StrategyState.DONE_TODAY
                return

        # Velocity
        if cfg.velocity_filter_enabled:
            vel = context.get_velocity(cfg.velocity_lookback_minutes,
                                       tick.timestamp)
            velocity_ok = vel >= cfg.velocity_threshold
        else:
            vel = 0.0
            velocity_ok = True

        if velocity_ok and self.state == StrategyState.RANGE_READY:
            price = context.get_current_price(tick.timestamp)
            if price is not None and self.range:
                if price > self.range.high or price < self.range.low:
                    # Variant: hold, don't kill the day
                    return
            placed = execution.set_orb_brackets(self.range, cfg.rr_ratio)
            if placed:
                self.orders_placed_time = tick.timestamp
                self.state = StrategyState.ORDERS_PLACED


class OneSidedStaleStrategy(ORBStrategy):
    """When mid is outside range, place only the non-stale side.
    Requires the execution engine to support set_orb_brackets_one_sided.
    """

    def _handle_range_and_orders(self, tick, context, execution):
        cfg = self.config

        if not context.time_is_in_trade_window(
                tick.timestamp, cfg.trade_start_hour, cfg.trade_end_hour):
            if self.state == StrategyState.ORDERS_PLACED:
                execution.cancel_orb_brackets()
            self.state = StrategyState.DONE_TODAY
            return

        if (self.state == StrategyState.ORDERS_PLACED
                and hasattr(execution, "entry_placement_failed")
                and execution.entry_placement_failed()):
            try:
                execution.cancel_orb_brackets()
            except Exception:
                pass
            if hasattr(execution, "clear_entry_failure"):
                execution.clear_entry_failure()
            self.orders_placed_time = None
            self.state = StrategyState.RANGE_READY
            return

        if (self.state == StrategyState.ORDERS_PLACED
                and cfg.max_pending_hours > 0
                and self.orders_placed_time):
            window_open = tick.timestamp.replace(
                hour=cfg.trade_start_hour, minute=0, second=0, microsecond=0)
            effective_start = max(self.orders_placed_time, window_open)
            elapsed_h = (tick.timestamp - effective_start).total_seconds() / 3600
            if elapsed_h >= cfg.max_pending_hours:
                execution.cancel_orb_brackets()
                self.state = StrategyState.DONE_TODAY
                return

        if cfg.velocity_filter_enabled:
            vel = context.get_velocity(cfg.velocity_lookback_minutes,
                                       tick.timestamp)
            velocity_ok = vel >= cfg.velocity_threshold
        else:
            velocity_ok = True

        if velocity_ok and self.state == StrategyState.RANGE_READY:
            price = context.get_current_price(tick.timestamp)
            side = "BOTH"
            if price is not None and self.range:
                if price > self.range.high:
                    side = "SHORT"   # BUY stop stale; only SELL viable
                elif price < self.range.low:
                    side = "LONG"    # SELL stop stale; only BUY viable
            placed = execution.set_orb_brackets_one_sided(
                self.range, cfg.rr_ratio, side)
            if placed:
                self.orders_placed_time = tick.timestamp
                self.state = StrategyState.ORDERS_PLACED


# ── Execution engine extension for one-sided placement ───────────────────────


def _install_one_sided(execution: ReplayORBExecutionEngine):
    """Monkey-patch the replay execution engine to support one-sided
    placement. Inactive side gets entry levels set far away so check_bar_fills
    never triggers it."""

    original = execution.set_orb_brackets

    def set_orb_brackets_one_sided(range_info: RangeInfo, rr_ratio: float,
                                    side: str) -> bool:
        # First call original to set both, then disable one side
        ok = original(range_info, rr_ratio)
        if not ok:
            return ok
        if side == "LONG":
            # Disable SHORT entry: push trigger out of reach
            execution._short_entry = -1.0e18
            execution._short_sl = -1.0e18
            execution._short_tp = -1.0e18
        elif side == "SHORT":
            # Disable LONG entry: push trigger out of reach
            execution._long_entry = 1.0e18
            execution._long_sl = 1.0e18
            execution._long_tp = 1.0e18
        # "BOTH": leave as-is
        return ok

    execution.set_orb_brackets_one_sided = set_orb_brackets_one_sided


# ── Variant runner ────────────────────────────────────────────────────────────


async def _run_variant(
    variant: str,
    cfg: V6StrategyConfig,
    all_bars: List[Bar],
    gap_lookup: Dict[str, GapMetrics],
    log: logging.Logger,
) -> Tuple[List[dict], Dict[str, int]]:
    """Run one variant. Returns (trades, counters)."""
    cfg = replace(cfg, gap_filter_enabled=True)  # match live (gap=ON)

    adapter = ReplayORBAdapter(v6_config=cfg, llm_filter=None, log=log)

    # ── Replace strategy with variant ──
    if variant == "skip":
        adapter._strategy = ORBStrategy(cfg, logger=log)
    elif variant == "wait":
        adapter._strategy = WaitStaleStrategy(cfg, logger=log)
    elif variant == "one_sided":
        adapter._strategy = OneSidedStaleStrategy(cfg, logger=log)
        _install_one_sided(adapter._execution)
    else:
        raise ValueError(f"unknown variant: {variant}")

    # ── Velocity from tick_count (matches live) ──
    _ctx = adapter._context

    def _tick_velocity(lookback_minutes, current_time):
        cutoff = current_time - timedelta(minutes=lookback_minutes)
        recent = [b for b in _ctx._bars if b.timestamp >= cutoff]
        if not recent:
            return 0.0
        return sum(b.tick_count for b in recent) / max(lookback_minutes, 1)

    _ctx.get_velocity = _tick_velocity

    # ── Real gap filter ──
    _default_gap = GapMetrics(0.0, 0.0, True, True)
    _ctx.get_gap_metrics = lambda now, gs, ge, vp, rp, rd: gap_lookup.get(
        now.strftime("%Y-%m-%d"), _default_gap)

    # ── Counters ──
    counters = {
        "stale_at_eval_days": 0,    # days where mid was outside range at eval
        "rescued_to_trade": 0,      # days where variant produced a trade
                                    # that 'skip' would have killed
    }

    # Group by date and iterate
    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in all_bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)

    skip_weekdays = set(cfg.skip_weekdays)

    for date_str in sorted(bars_by_date.keys()):
        day_bars = bars_by_date[date_str]
        if not day_bars:
            continue
        if day_bars[0].timestamp.weekday() in skip_weekdays:
            continue
        for bar in day_bars:
            await adapter.on_bar(bar)

    return adapter._trade_records, counters


def _print_table(results: Dict[str, List[dict]], width: int = 106) -> None:
    print()
    print("=" * width)
    print("  ORB XAUUSD STALE-BREAKOUT VARIANTS — IS/OOS RESULTS  (gap=ON)")
    print("=" * width)
    hdr = (f"  {'Variant':<12} | {'IS_N':>5} {'IS_WR':>6} {'IS_AvgR':>8} | "
           f"{'OOS_N':>6} {'/yr':>5} {'OOS_WR':>7} {'OOS_AvgR':>9} "
           f"{'OOS_PF':>7} {'OOS_DD':>7}")
    print(hdr)
    print("  " + "-" * (width - 2))

    def _split(trades):
        oos = [t for t in trades
               if OOS_START <= datetime.fromisoformat(t["timestamp"]) <= OOS_END]
        is_ = [t for t in trades
               if datetime.fromisoformat(t["timestamp"]) >= IS_START]
        return is_, oos

    for variant, trades in results.items():
        is_, oos = _split(trades)
        im = _metrics(is_)
        om = _metrics(oos)
        oos_per_yr = om["N"] / OOS_YEARS if OOS_YEARS else 0
        pf_str = (f"{om['PF']:.2f}" if om["PF"] != float("inf") else "inf")
        print(
            f"  {variant:<12} | "
            f"{im['N']:>5} {im['WR']:>6.1f} {im['AvgR']:>8.3f} | "
            f"{om['N']:>6} {oos_per_yr:>5.1f} {om['WR']:>7.1f} "
            f"{om['AvgR']:>9.3f} {pf_str:>7} {om['MaxDD']:>7.3f}")

    print()


def main():
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("orb_stale")
    log.setLevel(logging.WARNING)
    # Silence noisy adapter logs during the multi-pass backtest
    logging.getLogger("v11.replay.replay_orb").setLevel(logging.ERROR)

    base = _INSTRUMENT_CONFIGS["XAUUSD"]
    # Match live: velocity OFF (per 2026-04-16 OOS finding)
    cfg = replace(base, velocity_filter_enabled=False)

    print("Loading XAUUSD bars...", flush=True)
    all_bars = load_instrument_bars("XAUUSD")
    print(f"  {len(all_bars):,} bars "
          f"({all_bars[0].timestamp.date()} - {all_bars[-1].timestamp.date()})")

    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in all_bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    print("Pre-computing gap metrics...", flush=True)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), cfg)

    results: Dict[str, List[dict]] = {}
    for variant in ("skip", "wait", "one_sided"):
        print(f"Running variant: {variant} ...", flush=True)
        trades, _ = asyncio.run(
            _run_variant(variant, cfg, all_bars, gap_lookup, log))
        print(f"  {len(trades)} trades")
        results[variant] = trades

    _print_table(results)

    # Year-by-year for one_sided OOS only (most likely to deviate)
    width = 60
    print("=" * width)
    print("  YEAR-BY-YEAR OOS — one_sided  (vs skip baseline in parens)")
    print("=" * width)
    print(f"  {'Year':<6} {'N':>4} {'WR%':>6} {'AvgR':>8}  ({'skip AvgR':>10})")
    print("  " + "-" * (width - 4))
    by_year_one = _split_by_year(
        [t for t in results["one_sided"]
         if OOS_START <= datetime.fromisoformat(t["timestamp"]) <= OOS_END])
    by_year_skip = _split_by_year(
        [t for t in results["skip"]
         if OOS_START <= datetime.fromisoformat(t["timestamp"]) <= OOS_END])
    for yr in sorted(by_year_one.keys()):
        m = _metrics(by_year_one[yr])
        ms = _metrics(by_year_skip.get(yr, []))
        print(f"  {yr:<6} {m['N']:>4} {m['WR']:>6.1f} {m['AvgR']:>8.3f}  "
              f"({ms['AvgR']:>10.3f})")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
