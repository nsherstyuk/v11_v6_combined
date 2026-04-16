"""
Passthrough LLM Filter — Auto-approves all signals with mechanical SL/TP.

Used when LLM filtering is disabled (--no-llm flag). Computes entry, stop,
and target prices from the signal's structural levels (box boundaries for
Darvas, level price for 4H Retest) using the same logic as the backtester.

Satisfies the LLMFilter protocol (v11/llm/base.py).
"""
from __future__ import annotations

import logging

from .models import SignalContext
from ..core.types import FilterDecision

log = logging.getLogger("v11_live")


class PassthroughFilter:
    """Auto-approve all signals with mechanically computed SL/TP.

    SL placement:
        - LONG: box_bottom (opposite boundary)
        - SHORT: box_top (opposite boundary)

    TP placement:
        - entry + risk × R:R ratio (default 2.0)

    For 4H Level Retest signals, the LevelRetestEngine overrides these
    prices with its own structural SL/TP anyway, so the values here
    are only used by the Darvas engine.
    """

    def __init__(self, rr_ratio: float = 2.0):
        self._rr_ratio = rr_ratio

    async def evaluate_signal(self, context: SignalContext) -> FilterDecision:
        """Auto-approve with mechanical SL/TP from box boundaries."""
        entry = context.breakout_price
        direction = context.direction

        if direction == "long":
            sl = context.box_bottom
            risk = entry - sl
            tp = entry + risk * self._rr_ratio
        else:
            sl = context.box_top
            risk = sl - entry
            tp = entry - risk * self._rr_ratio

        if risk <= 0:
            log.warning(
                f"Passthrough: zero/negative risk for {context.instrument} "
                f"{direction} entry={entry} sl={sl} — rejecting")
            return FilterDecision(
                approved=False,
                confidence=0,
                entry_price=entry,
                stop_price=sl,
                target_price=entry,
                reasoning="Mechanical filter: zero or negative risk distance",
            )

        log.info(
            f"Passthrough: AUTO-APPROVE {context.instrument} {direction} "
            f"entry={entry} sl={sl} tp={tp:.5f} R:R={self._rr_ratio}")

        return FilterDecision(
            approved=True,
            confidence=100,
            entry_price=entry,
            stop_price=sl,
            target_price=tp,
            reasoning="Mechanical approval — LLM filter disabled",
        )

    async def evaluate_orb_signal(self, context) -> FilterDecision:
        """Auto-approve ORB signals -- LLM filter disabled."""
        log.info(
            f"Passthrough: AUTO-APPROVE ORB {context.instrument} "
            f"range={context.range_low:.2f}-{context.range_high:.2f}")
        return FilterDecision(
            approved=True,
            confidence=100,
            entry_price=0.0,
            stop_price=0.0,
            target_price=0.0,
            reasoning="Mechanical approval -- LLM filter disabled",
        )

    # ── LLMFilter protocol: outcome recording (no-ops) ─────────────────────

    def record_darvas_outcome(self, **kwargs) -> None:
        """No-op: passthrough filter has no ledger."""

    def record_orb_outcome(self, **kwargs) -> None:
        """No-op: passthrough filter has no ledger."""

    def refresh_feedback(self) -> None:
        """No-op: passthrough filter has no feedback table."""
