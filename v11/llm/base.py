"""
LLM Filter Protocol — Abstract interface for signal evaluation.

Design decision hidden: Which LLM provider is used.
Why it might change: A/B testing different models (Grok, GPT-4o, Claude).
Interface is narrower than implementation: consumers call evaluate_signal()
and get a FilterDecision back. They never touch HTTP clients, prompt
formatting, JSON parsing, or retry logic.

All implementations must:
    1. Accept a SignalContext
    2. Return a FilterDecision
    3. Never raise on LLM failure (return a rejection instead)
    4. Log every request/response pair to grok_logs/
    5. Provide outcome-recording hooks that are no-ops for stateless filters
       and actually record to a feedback ledger for LLM-backed filters
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import SignalContext, ORBSignalContext
from ..core.types import FilterDecision


@runtime_checkable
class LLMFilter(Protocol):
    """Protocol for LLM-based signal filtering.

    Any class implementing this protocol can be used as the filter layer.
    Swap models by changing the implementation class in config.

    The outcome-recording methods (record_darvas_outcome, record_orb_outcome,
    refresh_feedback) let callers report closed-trade results back to the
    filter WITHOUT knowing whether a ledger exists. Stateless filters
    implement them as no-ops; LLM-backed filters record to their ledger.
    This keeps consumers decoupled from filter internals (no more
    ``hasattr(f, '_ledger')`` reach-ins).
    """

    async def evaluate_signal(self, context: SignalContext) -> FilterDecision:
        """Evaluate a Darvas/Retest breakout signal.

        Must never raise — on failure, return FilterDecision(approved=False, ...).
        Must log every request/response pair.
        """
        ...

    async def evaluate_orb_signal(self, context: ORBSignalContext) -> FilterDecision:
        """Evaluate an ORB setup signal.

        Must never raise — on failure, return FilterDecision(approved=True, ...)
        (ORB falls back to mechanical approval).
        """
        ...

    def record_darvas_outcome(
        self,
        *,
        instrument: str,
        decision_timestamp: str,
        approved: bool,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        breakout_price: float,
    ) -> None:
        """Record a closed Darvas or 4H Retest trade for the feedback loop.

        Implementations without a ledger should make this a no-op.
        Must never raise.
        """
        ...

    def record_orb_outcome(
        self,
        *,
        instrument: str,
        decision_date: str,
        approved: bool,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
        pnl: float,
        range_high: float,
        range_low: float,
    ) -> None:
        """Record a closed ORB trade for the feedback loop.

        Implementations without a ledger should make this a no-op.
        Must never raise.
        """
        ...

    def refresh_feedback(self) -> None:
        """Rebuild the feedback table from the ledger (if any).

        Called after outcomes are recorded so future evaluate_* calls see
        updated context. No-op for stateless filters. Must never raise.
        """
        ...
