"""
RiskManager — Combined risk management across all strategies and instruments.

Design (V11_DESIGN.md §11):
    - Per-strategy daily trade count tracking
    - Combined portfolio daily loss limit (across all strategies)
    - Max 1 position per instrument (first signal gets the slot)
    - All strategies pause when combined loss limit hit

The RiskManager does NOT own TradeManagers or submit orders. It is a
read-only gate that strategies query before entering trades.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class StrategyStats:
    """Mutable daily stats for a single strategy."""
    name: str
    daily_trades: int = 0
    daily_pnl: float = 0.0


class RiskManager:
    """Portfolio-level risk management across all strategies.

    Interface (narrow):
        can_trade(instrument, strategy_name) -> (bool, reason)
        record_trade_entry(instrument, strategy_name)
        record_trade_exit(instrument, strategy_name, pnl)
        is_instrument_in_trade(instrument) -> bool
        reset_daily()

    Thread-safety: NOT thread-safe. Designed for single-threaded async loop
    where strategies are processed sequentially per bar.
    """

    def __init__(
        self,
        max_daily_loss: float,
        max_daily_trades_per_strategy: int,
        max_concurrent_positions: int,
        log: logging.Logger,
    ):
        """
        Args:
            max_daily_loss: Combined daily loss limit in USD across all strategies.
                            When total PnL drops below -max_daily_loss, all trading pauses.
            max_daily_trades_per_strategy: Max trades per strategy per day.
            max_concurrent_positions: Max total open positions across all instruments.
            log: Logger instance.
        """
        self._max_daily_loss = max_daily_loss
        self._max_daily_trades = max_daily_trades_per_strategy
        self._max_positions = max_concurrent_positions
        self._log = log

        # Per-strategy stats
        self._strategies: Dict[str, StrategyStats] = {}

        # Active positions: instrument -> strategy_name
        self._positions: Dict[str, str] = {}

        # Combined daily PnL
        self._combined_pnl: float = 0.0
        self._combined_trades: int = 0

    @property
    def combined_pnl(self) -> float:
        """Total PnL across all strategies today."""
        return self._combined_pnl

    @property
    def combined_trades(self) -> int:
        """Total trades across all strategies today."""
        return self._combined_trades

    @property
    def open_position_count(self) -> int:
        """Number of currently open positions."""
        return len(self._positions)

    def can_trade(self, instrument: str, strategy_name: str) -> tuple[bool, str]:
        """Check if a strategy is allowed to enter a trade.

        Returns:
            (allowed, reason) — reason is empty string if allowed,
            descriptive message if blocked.
        """
        # 1. Combined daily loss limit
        if self._max_daily_loss > 0:
            if self._combined_pnl <= -self._max_daily_loss:
                return (False,
                        f"Combined daily loss limit: ${self._combined_pnl:.2f} "
                        f"<= -${self._max_daily_loss:.2f}")

        # 2. Max concurrent positions
        if self._max_positions > 0:
            if len(self._positions) >= self._max_positions:
                return (False,
                        f"Max concurrent positions: "
                        f"{len(self._positions)}/{self._max_positions}")

        # 3. Instrument already has open position (from any strategy)
        if instrument in self._positions:
            holder = self._positions[instrument]
            return (False,
                    f"{instrument} already has position from {holder}")

        # 4. Per-strategy daily trade limit
        stats = self._strategies.get(strategy_name)
        if stats and self._max_daily_trades > 0:
            if stats.daily_trades >= self._max_daily_trades:
                return (False,
                        f"{strategy_name} daily trade limit: "
                        f"{stats.daily_trades}/{self._max_daily_trades}")

        return (True, "")

    def record_trade_entry(self, instrument: str, strategy_name: str) -> None:
        """Record that a strategy entered a trade on an instrument."""
        self._positions[instrument] = strategy_name

        if strategy_name not in self._strategies:
            self._strategies[strategy_name] = StrategyStats(name=strategy_name)
        self._strategies[strategy_name].daily_trades += 1
        self._combined_trades += 1

        self._log.info(
            f"RISK: {strategy_name} entered {instrument} "
            f"(positions: {len(self._positions)}/{self._max_positions}, "
            f"trades today: {self._combined_trades})")

    def record_trade_exit(self, instrument: str, strategy_name: str,
                          pnl: float) -> None:
        """Record that a trade exited with given PnL."""
        self._positions.pop(instrument, None)

        if strategy_name not in self._strategies:
            self._strategies[strategy_name] = StrategyStats(name=strategy_name)
        self._strategies[strategy_name].daily_pnl += pnl
        self._combined_pnl += pnl

        self._log.info(
            f"RISK: {strategy_name} exited {instrument} PnL=${pnl:+.2f} "
            f"(combined PnL: ${self._combined_pnl:+.2f}, "
            f"positions: {len(self._positions)})")

    def is_instrument_in_trade(self, instrument: str) -> bool:
        """Check if an instrument has an open position from any strategy."""
        return instrument in self._positions

    def get_open_instruments(self) -> set[str]:
        """Return set of instruments that currently have open positions."""
        return set(self._positions.keys())

    def get_position_strategy(self, instrument: str) -> str:
        """Return the strategy name holding a position on instrument, or 'UNKNOWN'."""
        return self._positions.get(instrument, "UNKNOWN")

    def get_strategy_stats(self, strategy_name: str) -> Optional[StrategyStats]:
        """Get daily stats for a strategy. None if strategy not seen."""
        return self._strategies.get(strategy_name)

    def reset_daily(self) -> None:
        """Reset all daily counters. Call at market open."""
        for stats in self._strategies.values():
            stats.daily_trades = 0
            stats.daily_pnl = 0.0
        self._combined_pnl = 0.0
        self._combined_trades = 0
        self._log.info("RISK: Daily counters reset")

    def get_status(self) -> dict:
        """Diagnostic status snapshot."""
        return {
            'combined_pnl': self._combined_pnl,
            'combined_trades': self._combined_trades,
            'open_positions': dict(self._positions),
            'strategies': {
                name: {'trades': s.daily_trades, 'pnl': s.daily_pnl}
                for name, s in self._strategies.items()
            },
        }
