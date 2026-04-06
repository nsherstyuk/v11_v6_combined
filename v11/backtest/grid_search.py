"""
Grid Search — Stage 1 Darvas parameter optimization (no LLM).

Runs DarvasDetector + ImbalanceClassifier over historical bars across
many parameter combinations. No LLM calls — fast, deterministic, repeatable.

For each parameter combo:
    1. Run backtest (detect boxes, generate signals, simulate trades)
    2. Compute metrics (win rate, PnL, Sharpe, drawdown, etc.)
    3. Rank results

Interface:
    build_param_grid() -> List[StrategyConfig]
    run_grid_search(bars, instrument, grid, rr_ratio) -> List[MetricsReport]
    run_grid_search_parallel(bars, instrument, grid, rr_ratio, workers) -> List[MetricsReport]
"""
from __future__ import annotations

import itertools
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from ..config.strategy_config import StrategyConfig, XAUUSD_CONFIG, EURUSD_CONFIG, USDJPY_CONFIG
from ..core.types import Bar
from .simulator import run_backtest, BacktestResult
from .metrics import compute_metrics, MetricsReport, format_report, reports_to_dataframe


# ── Default parameter ranges ────────────────────────────────────────────────

DEFAULT_PARAM_GRID = {
    "top_confirm_bars":     [10, 15, 20, 25],
    "bottom_confirm_bars":  [10, 15, 20, 25],
    "min_box_width_atr":    [0.2, 0.3, 0.5],
    "max_box_width_atr":    [3.0, 5.0, 7.0],
    "min_box_duration":     [15, 20, 30],
    "breakout_confirm_bars": [2, 3, 4],
}

# Smaller grid for quick testing
QUICK_PARAM_GRID = {
    "top_confirm_bars":     [10, 15, 20],
    "bottom_confirm_bars":  [10, 15, 20],
    "min_box_width_atr":    [0.3, 0.5],
    "max_box_width_atr":    [3.0, 5.0],
    "min_box_duration":     [15, 20],
    "breakout_confirm_bars": [2, 3],
}

# Base configs per instrument (non-grid params: spread, tick_size, etc.)
BASE_CONFIGS = {
    "XAUUSD": XAUUSD_CONFIG,
    "EURUSD": EURUSD_CONFIG,
    "USDJPY": USDJPY_CONFIG,
}


def build_param_grid(instrument: str,
                     param_ranges: Optional[Dict] = None) -> List[StrategyConfig]:
    """Build all parameter combinations as StrategyConfig objects.

    Args:
        instrument: Instrument name (used to pick base config for spread/tick_size).
        param_ranges: Dict of param_name -> list of values. Defaults to DEFAULT_PARAM_GRID.

    Returns:
        List of StrategyConfig instances, one per combination.
    """
    if param_ranges is None:
        param_ranges = DEFAULT_PARAM_GRID

    base = BASE_CONFIGS.get(instrument.upper())
    if base is None:
        base = StrategyConfig(instrument=instrument.upper())

    # Generate all combinations
    keys = list(param_ranges.keys())
    values = list(param_ranges.values())
    configs = []

    for combo in itertools.product(*values):
        overrides = dict(zip(keys, combo))
        config = replace(base, **overrides)
        configs.append(config)

    return configs


def run_grid_search(bars: List[Bar],
                    instrument: str,
                    param_ranges: Optional[Dict] = None,
                    rr_ratio: float = 2.0,
                    session_gap_minutes: int = 30,
                    min_trades: int = 10,
                    verbose: bool = True) -> List[MetricsReport]:
    """Run grid search sequentially (single process).

    Args:
        bars: All historical bars for the instrument.
        instrument: Instrument name.
        param_ranges: Parameter grid (defaults to DEFAULT_PARAM_GRID).
        rr_ratio: Risk-reward ratio for target computation.
        session_gap_minutes: Gap threshold for session splitting.
        min_trades: Minimum trades for a result to be included.
        verbose: Print progress updates.

    Returns:
        List of MetricsReport, sorted by Sharpe ratio descending.
    """
    grid = build_param_grid(instrument, param_ranges)

    if verbose:
        total = len(grid)
        print(f"\n[GRID SEARCH] {instrument}: {total} parameter combinations")
        print(f"[GRID SEARCH] Data: {len(bars):,} bars, R:R={rr_ratio}")

    reports: List[MetricsReport] = []
    t0 = time.time()

    for i, config in enumerate(grid):
        result = run_backtest(bars, config, rr_ratio=rr_ratio,
                              session_gap_minutes=session_gap_minutes)
        report = compute_metrics(result)

        if report.trades_taken >= min_trades:
            reports.append(report)

        if verbose and (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (total - i - 1) / rate if rate > 0 else 0
            print(f"  [{i+1}/{total}] {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining "
                  f"| {len(reports)} valid results so far")

    elapsed = time.time() - t0

    # Sort by Sharpe descending
    reports.sort(key=lambda r: r.sharpe_ratio, reverse=True)

    if verbose:
        print(f"\n[GRID SEARCH] Complete in {elapsed:.1f}s")
        print(f"[GRID SEARCH] {len(reports)} results with >= {min_trades} trades")
        if reports:
            print(f"\n{'=' * 60}")
            print(f"  TOP 5 RESULTS (by Sharpe)")
            print(f"{'=' * 60}")
            for j, r in enumerate(reports[:5]):
                print(f"\n  #{j+1}: Sharpe={r.sharpe_ratio:.2f} | "
                      f"WR={r.win_rate:.1f}% | "
                      f"PF={r.profit_factor:.2f} | "
                      f"Trades={r.trades_taken} | "
                      f"MaxDD={r.max_drawdown:.4f}")
                print(f"       Params: tc={r.config_params.get('top_confirm_bars'):.0f} "
                      f"bc={r.config_params.get('bottom_confirm_bars'):.0f} "
                      f"minW={r.config_params.get('min_box_width_atr')} "
                      f"maxW={r.config_params.get('max_box_width_atr')} "
                      f"dur={r.config_params.get('min_box_duration'):.0f} "
                      f"brk={r.config_params.get('breakout_confirm_bars'):.0f}")

    return reports


# ── Single-combo worker for parallel execution ──────────────────────────────

def _run_single_combo(args: Tuple) -> Optional[MetricsReport]:
    """Worker function for parallel grid search.

    Args is a tuple of (bars, config, rr_ratio, session_gap_minutes, min_trades).
    Must be a top-level function for pickling.
    """
    bars, config, rr_ratio, session_gap_minutes, min_trades = args
    result = run_backtest(bars, config, rr_ratio=rr_ratio,
                          session_gap_minutes=session_gap_minutes)
    report = compute_metrics(result)
    if report.trades_taken >= min_trades:
        return report
    return None


def run_grid_search_parallel(bars: List[Bar],
                             instrument: str,
                             param_ranges: Optional[Dict] = None,
                             rr_ratio: float = 2.0,
                             session_gap_minutes: int = 30,
                             min_trades: int = 10,
                             workers: int = 4,
                             verbose: bool = True) -> List[MetricsReport]:
    """Run grid search in parallel using ProcessPoolExecutor.

    Args:
        bars: All historical bars.
        instrument: Instrument name.
        param_ranges: Parameter grid.
        rr_ratio: Risk-reward ratio.
        session_gap_minutes: Gap for session splitting.
        min_trades: Minimum trades filter.
        workers: Number of parallel workers.
        verbose: Print progress.

    Returns:
        List of MetricsReport sorted by Sharpe descending.
    """
    grid = build_param_grid(instrument, param_ranges)
    total = len(grid)

    if verbose:
        print(f"\n[GRID SEARCH PARALLEL] {instrument}: {total} combos, {workers} workers")
        print(f"[GRID SEARCH PARALLEL] Data: {len(bars):,} bars, R:R={rr_ratio}")

    t0 = time.time()
    reports: List[MetricsReport] = []

    # Build args for each combo
    work_items = [(bars, config, rr_ratio, session_gap_minutes, min_trades)
                  for config in grid]

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_single_combo, item): i
                   for i, item in enumerate(work_items)}

        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            report = future.result()
            if report is not None:
                reports.append(report)

            if verbose and done_count % 50 == 0:
                elapsed = time.time() - t0
                rate = done_count / elapsed
                eta = (total - done_count) / rate if rate > 0 else 0
                print(f"  [{done_count}/{total}] {elapsed:.0f}s, ~{eta:.0f}s left "
                      f"| {len(reports)} valid")

    elapsed = time.time() - t0
    reports.sort(key=lambda r: r.sharpe_ratio, reverse=True)

    if verbose:
        print(f"\n[GRID SEARCH PARALLEL] Complete in {elapsed:.1f}s")
        print(f"[GRID SEARCH PARALLEL] {len(reports)} results with >= {min_trades} trades")
        if reports:
            print(f"\n{'=' * 60}")
            print(f"  TOP 5 RESULTS (by Sharpe)")
            print(f"{'=' * 60}")
            for j, r in enumerate(reports[:5]):
                print(f"\n  #{j+1}: Sharpe={r.sharpe_ratio:.2f} | "
                      f"WR={r.win_rate:.1f}% | "
                      f"PF={r.profit_factor:.2f} | "
                      f"Trades={r.trades_taken} | "
                      f"MaxDD={r.max_drawdown:.4f}")
                print(f"       Params: tc={r.config_params.get('top_confirm_bars'):.0f} "
                      f"bc={r.config_params.get('bottom_confirm_bars'):.0f} "
                      f"minW={r.config_params.get('min_box_width_atr')} "
                      f"maxW={r.config_params.get('max_box_width_atr')} "
                      f"dur={r.config_params.get('min_box_duration'):.0f} "
                      f"brk={r.config_params.get('breakout_confirm_bars'):.0f}")

    return reports


def save_results(reports: List[MetricsReport],
                 output_path: str | Path,
                 sort_by: str = "sharpe_ratio") -> None:
    """Save grid search results to CSV.

    Args:
        reports: List of MetricsReport objects.
        output_path: Path for the output CSV file.
        sort_by: Column name to sort by (descending).
    """
    df = reports_to_dataframe(reports)
    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=False)
    df.to_csv(output_path, index=False)
    print(f"[GRID SEARCH] Results saved to {output_path} ({len(df)} rows)")


# ── CLI entry point ─────────────────────────────────────────────────────────

def main():
    """Run grid search from command line."""
    import argparse

    parser = argparse.ArgumentParser(description="V11 Darvas Grid Search (Stage 1)")
    parser.add_argument("--instrument", type=str, default="XAUUSD",
                        help="Instrument to backtest (default: XAUUSD)")
    parser.add_argument("--start", type=str, default=None,
                        help="Start date YYYY-MM-DD (default: all data)")
    parser.add_argument("--end", type=str, default=None,
                        help="End date YYYY-MM-DD (default: all data)")
    parser.add_argument("--rr", type=float, default=2.0,
                        help="Risk-reward ratio (default: 2.0)")
    parser.add_argument("--min-trades", type=int, default=10,
                        help="Min trades to include result (default: 10)")
    parser.add_argument("--quick", action="store_true",
                        help="Use smaller parameter grid for quick testing")
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel workers (default: 1 = sequential)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output CSV path (default: auto-named)")

    args = parser.parse_args()

    from datetime import datetime
    from .data_loader import load_instrument_bars

    # Parse dates
    start = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None

    print(f"[GRID SEARCH] Loading {args.instrument} bars...")
    bars = load_instrument_bars(args.instrument, start=start, end=end)
    print(f"[GRID SEARCH] Loaded {len(bars):,} bars")

    param_ranges = QUICK_PARAM_GRID if args.quick else DEFAULT_PARAM_GRID

    if args.workers > 1:
        reports = run_grid_search_parallel(
            bars, args.instrument, param_ranges,
            rr_ratio=args.rr, min_trades=args.min_trades,
            workers=args.workers,
        )
    else:
        reports = run_grid_search(
            bars, args.instrument, param_ranges,
            rr_ratio=args.rr, min_trades=args.min_trades,
        )

    # Save results
    if args.output:
        output_path = args.output
    else:
        output_path = f"v11_grid_search_{args.instrument}_{datetime.now():%Y%m%d_%H%M%S}.csv"

    if reports:
        save_results(reports, output_path)
        print(f"\n{'=' * 60}")
        print(f"  BEST RESULT:")
        print(f"{'=' * 60}")
        print(format_report(reports[0]))
    else:
        print("[GRID SEARCH] No results met minimum trade threshold.")


if __name__ == "__main__":
    main()
