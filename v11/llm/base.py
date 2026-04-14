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
