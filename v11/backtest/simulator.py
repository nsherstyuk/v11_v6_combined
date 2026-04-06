"""
Trade Simulator — Simulates trade lifecycle for backtesting (no broker, no LLM).

Given a BreakoutSignal and subsequent bars, simulates:
    - Entry at breakout_price (+ spread cost)
    - Stop loss at box boundary
    - Time stop after max_hold_bars
    - Target at configurable R:R multiple

Tracks PnL per trade in SimulatedTrade records.

Interface:
    simulate_trade(signal, bars_after, config) -> SimulatedTrade
    run_backtest(bars, config) -> BacktestResult
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from ..core.types import Bar, BreakoutSignal, Direction, DarvasBox
from ..core.darvas_detector import DarvasDetector
from ..core.imbalance_classifier import ImbalanceClassifier
from ..config.strategy_config import StrategyConfig


@dataclass
class SimulatedTrade:
    """Result of a single simulated trade."""
    entry_time: datetime
    exit_time: datetime
    direction: Direction
    entry_price: float
    exit_price: float
    stop_price: float
    box_top: float
    box_bottom: float
    pnl: float                  # net PnL after spread
    pnl_r: float                # PnL in R-multiples
    hold_bars: int
    exit_reason: str            # "SL", "TIME_STOP", "TARGET"
    breakout_bar_index: int
    atr_at_entry: float
    volume_classification: str  # from ImbalanceClassifier


@dataclass
class BacktestResult:
    """Aggregate results from a full backtest run."""
    instrument: str
    config: StrategyConfig
    trades: List[SimulatedTrade] = field(default_factory=list)
    total_bars: int = 0
    total_sessions: int = 0
    signals_generated: int = 0


def _compute_sl_price(signal: BreakoutSignal, config: StrategyConfig) -> float:
    """Compute stop loss price from box boundary."""
    if signal.direction == Direction.LONG:
        return signal.box.bottom
    else:
        return signal.box.top


def _compute_target_price(signal: BreakoutSignal,
                          sl_price: float,
                          rr_ratio: float = 2.0) -> float:
    """Compute target price from entry and SL at given R:R ratio."""
    risk = abs(signal.breakout_price - sl_price)
    if signal.direction == Direction.LONG:
        return signal.breakout_price + risk * rr_ratio
    else:
        return signal.breakout_price - risk * rr_ratio


def simulate_trade(signal: BreakoutSignal,
                   bars_after: List[Bar],
                   config: StrategyConfig,
                   rr_ratio: float = 2.0,
                   volume_class: str = "N/A") -> Optional[SimulatedTrade]:
    """Simulate a single trade from a breakout signal through subsequent bars.

    Args:
        signal: The breakout signal that triggered entry.
        bars_after: Bars following the breakout (for trade management).
        config: Strategy parameters (max_hold_bars, spread_cost).
        rr_ratio: Risk-reward ratio for target computation.
        volume_class: Volume classification string for the record.

    Returns:
        SimulatedTrade if trade was entered and exited, None if no bars to simulate.
    """
    if not bars_after:
        return None

    sl_price = _compute_sl_price(signal, config)
    entry_price = signal.breakout_price
    target_price = _compute_target_price(signal, sl_price, rr_ratio)
    risk = abs(entry_price - sl_price)

    if risk <= 0:
        return None

    is_long = signal.direction == Direction.LONG
    half_spread = config.spread_cost / 2

    # Adjust entry for spread
    effective_entry = entry_price + half_spread if is_long else entry_price - half_spread

    exit_price = 0.0
    exit_reason = "TIME_STOP"
    exit_time = bars_after[-1].timestamp
    hold_bars = len(bars_after)

    for i, bar in enumerate(bars_after):
        if i >= config.max_hold_bars:
            # Time stop — exit at current close
            exit_price = bar.close
            exit_reason = "TIME_STOP"
            exit_time = bar.timestamp
            hold_bars = i + 1
            break

        if is_long:
            # Check SL first (conservative: assume worst case intra-bar)
            if bar.low <= sl_price:
                exit_price = sl_price
                exit_reason = "SL"
                exit_time = bar.timestamp
                hold_bars = i + 1
                break
            # Check target
            if bar.high >= target_price:
                exit_price = target_price
                exit_reason = "TARGET"
                exit_time = bar.timestamp
                hold_bars = i + 1
                break
        else:
            # Short: check SL (price goes up)
            if bar.high >= sl_price:
                exit_price = sl_price
                exit_reason = "SL"
                exit_time = bar.timestamp
                hold_bars = i + 1
                break
            # Check target (price goes down)
            if bar.low <= target_price:
                exit_price = target_price
                exit_reason = "TARGET"
                exit_time = bar.timestamp
                hold_bars = i + 1
                break
    else:
        # Loop completed without break — time stop at last bar
        if exit_price == 0.0:
            exit_price = bars_after[-1].close

    # Adjust exit for spread
    effective_exit = exit_price - half_spread if is_long else exit_price + half_spread

    # PnL computation
    if is_long:
        pnl = effective_exit - effective_entry
    else:
        pnl = effective_entry - effective_exit

    pnl_r = pnl / risk if risk > 0 else 0.0

    return SimulatedTrade(
        entry_time=signal.timestamp,
        exit_time=exit_time,
        direction=signal.direction,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_price=sl_price,
        box_top=signal.box.top,
        box_bottom=signal.box.bottom,
        pnl=pnl,
        pnl_r=pnl_r,
        hold_bars=hold_bars,
        exit_reason=exit_reason,
        breakout_bar_index=signal.breakout_bar_index,
        atr_at_entry=signal.atr,
        volume_classification=volume_class,
    )


def run_backtest(bars: List[Bar],
                 config: StrategyConfig,
                 rr_ratio: float = 2.0,
                 session_gap_minutes: int = 30) -> BacktestResult:
    """Run a full backtest: detect Darvas boxes, generate signals, simulate trades.

    Resets the detector between sessions (gaps > session_gap_minutes).
    No LLM calls — pure signal + trade simulation.

    Args:
        bars: All 1-minute bars for the instrument, sorted by time.
        config: Strategy configuration to use.
        rr_ratio: Risk-reward ratio for target computation.
        session_gap_minutes: Gap threshold to split sessions.

    Returns:
        BacktestResult with all simulated trades and stats.
    """
    from .data_loader import split_by_sessions

    result = BacktestResult(
        instrument=config.instrument,
        config=config,
        total_bars=len(bars),
    )

    sessions = split_by_sessions(bars, gap_minutes=session_gap_minutes)
    result.total_sessions = len(sessions)

    # Process each session independently
    for session_bars in sessions:
        detector = DarvasDetector(config)
        classifier = ImbalanceClassifier(
            max_lookback=max(20, config.imbalance_window * 2),
            min_bar_ticks=config.min_bar_ticks,
        )

        for i, bar in enumerate(session_bars):
            signal = detector.add_bar(bar)
            classifier.add_bar(bar)

            if signal is not None:
                result.signals_generated += 1

                # Get volume classification
                vol_class = classifier.classify(
                    signal.direction,
                    config.imbalance_window,
                    config.divergence_threshold,
                ).value

                # Simulate trade with remaining bars in this session
                bars_after = session_bars[i + 1:]
                trade = simulate_trade(
                    signal, bars_after, config,
                    rr_ratio=rr_ratio,
                    volume_class=vol_class,
                )
                if trade is not None:
                    result.trades.append(trade)

    return result
