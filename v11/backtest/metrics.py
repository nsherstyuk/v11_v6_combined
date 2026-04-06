"""
Backtest Metrics — Compute performance statistics from simulated trades.

Metrics computed:
    - Signal count, trade count
    - Win rate (%)
    - Average PnL (price units and R-multiples)
    - Profit factor
    - Max drawdown (price units and %)
    - Sharpe ratio (annualized from per-trade returns)
    - Calmar ratio (annualized return / max drawdown)
    - Average hold time (bars)
    - Exit reason breakdown

Interface:
    compute_metrics(trades, config) -> MetricsReport
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from .simulator import SimulatedTrade, BacktestResult


@dataclass
class MetricsReport:
    """Complete metrics summary for a backtest run."""
    instrument: str
    total_bars: int
    total_sessions: int
    signals_generated: int
    trades_taken: int

    # Win/loss
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0

    # PnL
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_pnl_r: float = 0.0
    total_pnl_r: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0
    profit_factor: float = 0.0

    # Drawdown
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Hold time
    avg_hold_bars: float = 0.0
    max_hold_bars: int = 0

    # Exit reasons
    exit_reasons: Dict[str, int] = field(default_factory=dict)

    # Direction breakdown
    long_trades: int = 0
    short_trades: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0

    # Volume classification breakdown
    volume_classes: Dict[str, int] = field(default_factory=dict)

    # Config params used (for grid search output)
    config_params: Dict[str, float] = field(default_factory=dict)


def compute_metrics(result: BacktestResult) -> MetricsReport:
    """Compute all metrics from a BacktestResult.

    Args:
        result: BacktestResult containing trades and metadata.

    Returns:
        MetricsReport with all computed statistics.
    """
    trades = result.trades
    config = result.config

    report = MetricsReport(
        instrument=result.instrument,
        total_bars=result.total_bars,
        total_sessions=result.total_sessions,
        signals_generated=result.signals_generated,
        trades_taken=len(trades),
        config_params={
            "top_confirm_bars": config.top_confirm_bars,
            "bottom_confirm_bars": config.bottom_confirm_bars,
            "min_box_width_atr": config.min_box_width_atr,
            "max_box_width_atr": config.max_box_width_atr,
            "min_box_duration": config.min_box_duration,
            "breakout_confirm_bars": config.breakout_confirm_bars,
            "imbalance_window": config.imbalance_window,
            "max_hold_bars": config.max_hold_bars,
        },
    )

    if not trades:
        return report

    # Win/loss counts
    pnls = [t.pnl for t in trades]
    pnl_rs = [t.pnl_r for t in trades]

    report.wins = sum(1 for p in pnls if p > 0)
    report.losses = sum(1 for p in pnls if p < 0)
    report.breakeven = sum(1 for p in pnls if p == 0)
    report.win_rate = report.wins / len(trades) * 100

    # PnL
    report.total_pnl = sum(pnls)
    report.avg_pnl = report.total_pnl / len(trades)
    report.total_pnl_r = sum(pnl_rs)
    report.avg_pnl_r = report.total_pnl_r / len(trades)
    report.best_trade_pnl = max(pnls)
    report.worst_trade_pnl = min(pnls)

    # Profit factor
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    report.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Drawdown (equity curve from cumulative PnL)
    equity_curve = []
    cumulative = 0.0
    for p in pnls:
        cumulative += p
        equity_curve.append(cumulative)

    peak = 0.0
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    report.max_drawdown = max_dd
    if peak > 0:
        report.max_drawdown_pct = (max_dd / peak) * 100

    # Sharpe ratio (annualized)
    # Using per-trade PnL in R-multiples
    if len(pnl_rs) > 1:
        mean_r = report.avg_pnl_r
        std_r = _std(pnl_rs)
        if std_r > 0:
            # Annualize: assume ~250 trading days, ~6.5 hours = 390 bars/day
            # Approximate trades per year from trade frequency
            trades_per_year = _estimate_trades_per_year(trades, result.total_bars)
            report.sharpe_ratio = (mean_r / std_r) * math.sqrt(max(trades_per_year, 1))

    # Calmar ratio (annualized return / max drawdown)
    if report.max_drawdown > 0 and result.total_bars > 0:
        bars_per_year = 252 * 390  # ~98,280 1-min bars per year
        annualized_pnl = report.total_pnl * (bars_per_year / result.total_bars)
        report.calmar_ratio = annualized_pnl / report.max_drawdown

    # Hold time
    hold_bars_list = [t.hold_bars for t in trades]
    report.avg_hold_bars = sum(hold_bars_list) / len(hold_bars_list)
    report.max_hold_bars = max(hold_bars_list)

    # Exit reason breakdown
    for t in trades:
        report.exit_reasons[t.exit_reason] = report.exit_reasons.get(t.exit_reason, 0) + 1

    # Direction breakdown
    long_trades = [t for t in trades if t.direction.value == "long"]
    short_trades = [t for t in trades if t.direction.value == "short"]
    report.long_trades = len(long_trades)
    report.short_trades = len(short_trades)
    if long_trades:
        report.long_win_rate = sum(1 for t in long_trades if t.pnl > 0) / len(long_trades) * 100
    if short_trades:
        report.short_win_rate = sum(1 for t in short_trades if t.pnl > 0) / len(short_trades) * 100

    # Volume classification breakdown
    for t in trades:
        vc = t.volume_classification
        report.volume_classes[vc] = report.volume_classes.get(vc, 0) + 1

    return report


def _std(values: List[float]) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _estimate_trades_per_year(trades: List[SimulatedTrade], total_bars: int) -> float:
    """Estimate annualized trade count from observed frequency."""
    if total_bars <= 0 or not trades:
        return 0.0
    bars_per_year = 252 * 390
    trade_freq = len(trades) / total_bars
    return trade_freq * bars_per_year


def format_report(report: MetricsReport) -> str:
    """Format a MetricsReport as a readable string."""
    lines = [
        f"{'=' * 60}",
        f"  BACKTEST REPORT: {report.instrument}",
        f"{'=' * 60}",
        f"  Bars: {report.total_bars:,}  |  Sessions: {report.total_sessions:,}",
        f"  Signals: {report.signals_generated}  |  Trades: {report.trades_taken}",
        f"{'─' * 60}",
        f"  Win Rate:      {report.win_rate:.1f}%  ({report.wins}W / {report.losses}L / {report.breakeven}BE)",
        f"  Total PnL:     {report.total_pnl:+.4f}",
        f"  Avg PnL:       {report.avg_pnl:+.4f}",
        f"  Avg PnL (R):   {report.avg_pnl_r:+.2f}R",
        f"  Best Trade:    {report.best_trade_pnl:+.4f}",
        f"  Worst Trade:   {report.worst_trade_pnl:+.4f}",
        f"  Profit Factor: {report.profit_factor:.2f}",
        f"{'─' * 60}",
        f"  Max Drawdown:  {report.max_drawdown:.4f}  ({report.max_drawdown_pct:.1f}%)",
        f"  Sharpe Ratio:  {report.sharpe_ratio:.2f}",
        f"  Calmar Ratio:  {report.calmar_ratio:.2f}",
        f"{'─' * 60}",
        f"  Avg Hold:      {report.avg_hold_bars:.1f} bars",
        f"  Max Hold:      {report.max_hold_bars} bars",
        f"  Long/Short:    {report.long_trades}L / {report.short_trades}S",
        f"  Long WR:       {report.long_win_rate:.1f}%  |  Short WR: {report.short_win_rate:.1f}%",
        f"{'─' * 60}",
        f"  Exit Reasons:  {report.exit_reasons}",
        f"  Vol Classes:   {report.volume_classes}",
        f"{'─' * 60}",
        f"  Config: {report.config_params}",
        f"{'=' * 60}",
    ]
    return "\n".join(lines)


def reports_to_dataframe(reports: List[MetricsReport]):
    """Convert a list of MetricsReports into a pandas DataFrame for grid search analysis."""
    import pandas as pd

    rows = []
    for r in reports:
        row = {
            "instrument": r.instrument,
            "trades": r.trades_taken,
            "signals": r.signals_generated,
            "win_rate": r.win_rate,
            "total_pnl": r.total_pnl,
            "avg_pnl": r.avg_pnl,
            "avg_pnl_r": r.avg_pnl_r,
            "profit_factor": r.profit_factor,
            "max_drawdown": r.max_drawdown,
            "sharpe_ratio": r.sharpe_ratio,
            "calmar_ratio": r.calmar_ratio,
            "avg_hold_bars": r.avg_hold_bars,
            "long_trades": r.long_trades,
            "short_trades": r.short_trades,
            "long_wr": r.long_win_rate,
            "short_wr": r.short_win_rate,
        }
        row.update(r.config_params)
        rows.append(row)

    return pd.DataFrame(rows)
