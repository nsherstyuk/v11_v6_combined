"""
V6 ORB — Copied from C:\\nautilus0\\v6_orb_refactor\\ (READ ONLY reference).

These files are frozen copies of the proven V6 Opening Range Breakout strategy.
Imports have been flattened to work within the v11 package. The original V6 code
in C:\\nautilus0\\v6_orb_refactor\\ is UNMODIFIED.

Do NOT modify the logic in these files. Changes to strategy logic must be
validated against the V6 backtest baseline first.
"""
from .orb_strategy import ORBStrategy, StrategyState
from .market_event import Tick, Fill, RangeInfo, GapMetrics
from .config import StrategyConfig as V6StrategyConfig
from .interfaces import MarketContext, ExecutionEngine
from .live_context import LiveMarketContext
from .ibkr_executor import IBKRExecutionEngine

__all__ = [
    'ORBStrategy', 'StrategyState',
    'Tick', 'Fill', 'RangeInfo', 'GapMetrics',
    'V6StrategyConfig',
    'MarketContext', 'ExecutionEngine',
    'LiveMarketContext', 'IBKRExecutionEngine',
]
