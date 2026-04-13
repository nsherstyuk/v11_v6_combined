"""Replay metrics — compute summary statistics from trade records."""
from __future__ import annotations

import math
from typing import List


def compute_metrics(
    trades: List[dict],
    starting_equity: float = 100_000.0,
) -> dict:
    """Compute summary metrics from a list of trade records.

    Each trade record must have at least a 'pnl' key (float).

    Returns dict with: total_trades, net_pnl, win_rate, profit_factor,
    max_drawdown, max_drawdown_pct, sharpe, avg_pnl, avg_winner, avg_loser.
    """
    if not trades:
        return {
            "total_trades": 0, "net_pnl": 0.0, "win_rate": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0, "sharpe": 0.0,
            "avg_pnl": 0.0, "avg_winner": 0.0, "avg_loser": 0.0,
        }

    pnls = [t["pnl"] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]

    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))

    # Equity curve for drawdown
    equity = [starting_equity]
    for p in pnls:
        equity.append(equity[-1] + p)

    peak = equity[0]
    max_dd = 0.0
    for e in equity[1:]:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualized, assuming ~252 trading days)
    mean_pnl = sum(pnls) / len(pnls)
    if len(pnls) > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(variance)
        sharpe = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "total_trades": len(pnls),
        "net_pnl": sum(pnls),
        "win_rate": len(winners) / len(pnls) if pnls else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown": max_dd,
        "max_drawdown_pct": (max_dd / starting_equity) * 100 if starting_equity > 0 else 0.0,
        "sharpe": round(sharpe, 2),
        "avg_pnl": mean_pnl,
        "avg_winner": sum(winners) / len(winners) if winners else 0.0,
        "avg_loser": sum(losers) / len(losers) if losers else 0.0,
    }
