"""
V11 Core Types — Single source of truth for all data types.

All value objects are frozen dataclasses (immutable).
Carried forward from v8 Bar/Fill/TradeRecord with new Darvas-specific types.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


# ── Enums ───────────────────────────────────────────────────────────────────

class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class ImbalanceClassification(str, Enum):
    CONFIRMING = "CONFIRMING"
    DIVERGENT = "DIVERGENT"
    INDETERMINATE = "INDETERMINATE"


class ExitReason(str, Enum):
    SL = "SL"
    TIME_STOP = "TIME_STOP"
    SAFETY_LIMIT = "SAFETY_LIMIT"
    SHUTDOWN = "SHUTDOWN"
    MANUAL = "MANUAL"


class TickQuality(str, Enum):
    HIGH = "HIGH"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


# ── Bar (from v8, unchanged) ───────────────────────────────────────────────

@dataclass(frozen=True)
class Bar:
    """A 1-minute OHLCV bar with tick volume breakdown."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    tick_count: int
    buy_volume: float
    sell_volume: float

    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume

    @property
    def buy_ratio(self) -> float:
        total = self.total_volume
        if total == 0:
            return 0.5
        return self.buy_volume / total

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2


# ── Darvas Box ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DarvasBox:
    """A confirmed Darvas box — a consolidation zone with defined top and bottom.

    Immutable once formed. The box represents a completed consolidation period
    that price has been contained within.
    """
    top: float
    bottom: float
    top_confirmed_at: int       # bar index when top was confirmed
    bottom_confirmed_at: int    # bar index when bottom was confirmed
    formation_start: int        # bar index of the initial high that started box formation
    duration_bars: int          # total bars from formation start to box completion
    atr_at_formation: float     # ATR when box was confirmed (for width validation)

    @property
    def width(self) -> float:
        """Price range of the box."""
        return self.top - self.bottom

    @property
    def width_atr(self) -> float:
        """Box width as a multiple of ATR."""
        if self.atr_at_formation <= 0:
            return 0.0
        return self.width / self.atr_at_formation

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


# ── Breakout Signal ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BreakoutSignal:
    """Emitted by DarvasDetector when a confirmed breakout is detected.

    Contains everything needed for the LLM filter to evaluate the signal.
    All detection complexity is hidden behind this simple object.
    """
    timestamp: datetime
    direction: Direction
    box: DarvasBox
    breakout_price: float           # price at confirmation bar
    breakout_bar_index: int         # bar index of the breakout
    atr: float                      # current ATR at breakout


# ── Volume Analysis (enrichment for LLM) ───────────────────────────────────

@dataclass(frozen=True)
class VolumeAnalysis:
    """Volume imbalance data at the time of a breakout signal."""
    buy_ratio_at_breakout: float
    buy_ratio_trend: str            # "increasing", "decreasing", "flat"
    tick_quality: TickQuality
    classification: ImbalanceClassification


# ── LLM Filter Decision ────────────────────────────────────────────────────

@dataclass(frozen=True)
class FilterDecision:
    """Response from the LLM filter after evaluating a breakout signal.

    CENTER element: this is the contract between LLM output and execution.
    Changes require explicit approval.
    """
    approved: bool
    confidence: int                 # 0-100
    entry_price: float
    stop_price: float
    target_price: float
    reasoning: str
    risk_flags: List[str] = field(default_factory=list)


# ── Fill (from v8, extended) ───────────────────────────────────────────────

@dataclass(frozen=True)
class Fill:
    """An execution event reported by the broker."""
    timestamp: datetime
    price: float
    direction: Direction
    reason: str                     # "ENTRY", "SL", "TIME_STOP", etc.
    pnl: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0


# ── Trade Record ────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Complete record of a round-trip trade for reporting."""
    entry_time: datetime
    exit_time: Optional[datetime]
    direction: Direction
    instrument: str
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    box_top: float
    box_bottom: float
    exit_reason: str
    pnl: float
    hold_bars: int
    buy_ratio_at_entry: float
    llm_confidence: int
    llm_reasoning: str = ""
    fill_entry_price: float = 0.0
    fill_exit_price: float = 0.0
    entry_commission: float = 0.0
    exit_commission: float = 0.0
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
