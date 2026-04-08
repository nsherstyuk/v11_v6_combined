"""
CENTER MODULE: LLM Request/Response Models.

These Pydantic models define the contract between the LLM output and
the execution layer. Changes here affect both the prompt and the
trade execution logic.

CHANGES TO THIS FILE REQUIRE EXPLICIT APPROVAL (center element).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class BarData(BaseModel):
    """Compact bar representation for LLM context."""
    t: str          # timestamp ISO
    o: float        # open
    h: float        # high
    l: float        # low
    c: float        # close
    bv: float       # buy_volume
    sv: float       # sell_volume
    tc: int         # tick_count


class SignalContext(BaseModel):
    """Everything the LLM receives when a Darvas breakout fires.

    This is the complete input package for the LLM filter.
    """
    # Signal info
    signal_type: str = "DARVAS_BREAKOUT"
    direction: str                      # "long" or "short"
    instrument: str                     # "XAUUSD", "EURUSD", "USDJPY"

    # Box info
    box_top: float
    box_bottom: float
    box_duration_bars: int
    box_width_atr: float
    breakout_price: float
    atr: float

    # Volume analysis
    buy_ratio_at_breakout: float
    buy_ratio_trend: str                # "increasing", "decreasing", "flat"
    tick_quality: str                   # "HIGH", "LOW", "INSUFFICIENT"
    volume_classification: str          # "CONFIRMING", "DIVERGENT", "INDETERMINATE"

    # Bar context
    recent_bars: List[BarData]          # last N 1-min bars
    daily_bars: Optional[List[BarData]] = None  # last M daily bars (if available)

    # Timing
    current_time_utc: str
    session: str                        # "ASIAN", "LONDON", "NY", "LONDON_NY_OVERLAP", etc.


class LLMResponse(BaseModel):
    """Validated response from the LLM.

    CENTER element: this schema is the contract between LLM and execution.
    The LLM must return JSON matching this schema exactly.
    """
    approved: bool
    confidence: int = Field(ge=0, le=100)
    entry: float
    stop: float
    target: float
    reasoning: str
    risk_flags: List[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError(f"confidence must be 0-100, got {v}")
        return v

    @field_validator("stop")
    @classmethod
    def stop_must_be_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"stop must be >= 0, got {v}")
        return v


class DailyBarData(BaseModel):
    """Compact daily bar for ORB LLM context."""
    date: str       # YYYY-MM-DD
    o: float        # open
    h: float        # high
    l: float        # low
    c: float        # close


class ORBSignalContext(BaseModel):
    """Everything the LLM receives when ORB is in RANGE_READY.

    This is the complete input for the ORB LLM gate.
    Grok decides whether to place brackets based on this context.
    """
    signal_type: str = "ORB_RANGE_READY"
    instrument: str

    # Range stats
    range_high: float
    range_low: float
    range_size: float               # absolute (e.g. $48.16)
    range_size_pct: float           # as % of midpoint (e.g. 1.05)
    range_vs_avg: float             # ratio vs 10-day average range

    # Current price
    current_price: float
    distance_from_high: float       # current_price - range_high (negative if below)
    distance_from_low: float        # current_price - range_low (negative if below)

    # Timing
    session: str                    # ASIAN_CLOSE, LONDON, LONDON_NY_OVERLAP, NY
    day_of_week: str                # Monday, Tuesday, etc.
    current_time_utc: str           # ISO format

    # Bar context
    recent_bars: List[BarData]      # last 360 1-min bars (6 hours)
    daily_bars: List[DailyBarData]  # last 10 daily bars
