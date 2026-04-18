"""
Grok LLM Filter Implementation.

Implements the LLMFilter protocol using xAI's Grok API via the OpenAI-compatible client.
Logs every request/response pair to grok_logs/ as JSON files.

Design decision hidden: HTTP client setup, retry logic, JSON parsing, error handling.
Interface: evaluate_signal(context) -> FilterDecision
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import OpenAI, APITimeoutError
from pydantic import ValidationError

from .base import LLMFilter
from .decision_ledger import DecisionLedger
from .models import SignalContext, LLMResponse, ORBSignalContext
from .prompt_templates import (
    SYSTEM_PROMPT, build_signal_prompt,
    ORB_SYSTEM_PROMPT, build_orb_signal_prompt,
)
from ..core.types import FilterDecision

logger = logging.getLogger(__name__)


class GrokFilter:
    """Grok-based LLM filter for Darvas breakout signals.

    Implements the LLMFilter protocol.
    Uses xAI's OpenAI-compatible API endpoint.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "grok-4-1-fast-reasoning",
        base_url: str = "https://api.x.ai/v1",
        timeout: float = 30.0,
        signal_timeout: Optional[float] = None,
        log_dir: Optional[str] = None,
    ):
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._model = model
        self._timeout = timeout
        self._signal_timeout = signal_timeout or timeout  # Darvas/4H uses longer timeout
        self._log_dir = Path(log_dir) if log_dir else None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)

        # Decision ledger for feedback loop
        self._ledger = DecisionLedger(log_dir) if log_dir else None
        self._feedback_table: str = ""  # fallback unfiltered table
        if self._ledger:
            self._feedback_table = self._ledger.build_feedback_table()
            if self._feedback_table:
                logger.info(
                    f"Loaded decision feedback: {self._ledger.stats}")
            else:
                logger.info("Decision ledger: no assessed decisions yet")

    def refresh_feedback(self) -> None:
        """Rebuild fallback feedback table from ledger (called after new assessments)."""
        if self._ledger:
            self._feedback_table = self._ledger.build_feedback_table()
            stats = self._ledger.stats
            assessed = stats.get("assessed", 0)
            if assessed > 0:
                logger.info(
                    f"Feedback refreshed: {assessed} assessed, "
                    f"accuracy={stats.get('accuracy_pct', 0)}%")

    # ── LLMFilter protocol: outcome recording ─────────────────────────────

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
        """Assess a closed Darvas/Retest trade via the ledger. No-op if no ledger."""
        if self._ledger is None:
            return
        try:
            from ..replay.auto_assessor import assess_darvas_decision
            assess_darvas_decision(
                ledger=self._ledger,
                instrument=instrument,
                decision_timestamp=decision_timestamp,
                approved=approved,
                entry_price=entry_price,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl=pnl,
                breakout_price=breakout_price,
            )
        except Exception as e:  # never raise
            logger.warning(f"record_darvas_outcome failed: {e}")

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
        """Assess a closed ORB trade via the ledger. No-op if no ledger."""
        if self._ledger is None:
            return
        try:
            from ..replay.auto_assessor import assess_orb_decision
            assess_orb_decision(
                ledger=self._ledger,
                instrument=instrument,
                decision_date=decision_date,
                approved=approved,
                entry_price=entry_price,
                exit_price=exit_price,
                exit_reason=exit_reason,
                pnl=pnl,
                range_high=range_high,
                range_low=range_low,
            )
        except Exception as e:  # never raise
            logger.warning(f"record_orb_outcome failed: {e}")

    def _build_orb_feedback(self, context: ORBSignalContext) -> str:
        """Build regime-filtered feedback for ORB signals."""
        if not self._ledger:
            return self._feedback_table
        return self._ledger.build_regime_filtered_table(
            strategy="ORB",
            regime_key="atr_regime",
            regime_value=context.atr_regime,
            regime_tolerance=0.3,
        ) or self._feedback_table

    def _build_darvas_feedback(self, context: SignalContext) -> str:
        """Build regime-filtered feedback for Darvas/Retest signals."""
        if not self._ledger:
            return self._feedback_table
        strategy = "DARVAS" if context.signal_type == "DARVAS_BREAKOUT" else "4H_RETEST"
        return self._ledger.build_regime_filtered_table(
            strategy=strategy,
            regime_key="atr_vs_avg",
            regime_value=context.atr_vs_avg,
            regime_tolerance=0.3,
        ) or self._feedback_table

    async def evaluate_signal(self, context: SignalContext) -> FilterDecision:
        """Evaluate a breakout signal via Grok.

        Never raises — returns a rejection FilterDecision on any failure.
        Logs every request/response pair to grok_logs/.
        """
        context_json = context.model_dump_json(indent=2)
        feedback = self._build_darvas_feedback(context)
        prompt = build_signal_prompt(context_json, feedback=feedback)

        start_time = time.monotonic()
        raw_response = None
        error_msg = None

        try:
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=self._signal_timeout,
            )
            raw_response = response.choices[0].message.content
            latency = time.monotonic() - start_time

            # Parse and validate
            llm_resp = LLMResponse.model_validate_json(raw_response)

            decision = FilterDecision(
                approved=llm_resp.approved,
                confidence=llm_resp.confidence,
                entry_price=llm_resp.entry,
                stop_price=llm_resp.stop,
                target_price=llm_resp.target,
                reasoning=llm_resp.reasoning,
                risk_flags=llm_resp.risk_flags,
            )

            logger.info(
                f"LLM [{self._model}] {context.instrument} {context.direction}: "
                f"approved={decision.approved} conf={decision.confidence} "
                f"latency={latency:.1f}s"
            )

            # Record to decision ledger
            if self._ledger:
                self._ledger.record_decision(
                    strategy="DARVAS" if context.signal_type == "DARVAS_BREAKOUT" else "4H_RETEST",
                    instrument=context.instrument,
                    decision="APPROVE" if decision.approved else "REJECT",
                    confidence=decision.confidence,
                    reasoning=decision.reasoning,
                    risk_flags=list(decision.risk_flags),
                    context={
                        "direction": context.direction,
                        "breakout_price": context.breakout_price,
                        "entry_price": decision.entry_price,
                        "stop_price": decision.stop_price,
                        "target_price": decision.target_price,
                        "box_top": context.box_top,
                        "box_bottom": context.box_bottom,
                        "atr": context.atr,
                        "atr_vs_avg": context.atr_vs_avg,
                        "session": context.session,
                    },
                )

            # Log the conversation
            self._log_conversation(
                context=context,
                raw_response=raw_response,
                decision=decision,
                latency=latency,
                tokens_in=response.usage.prompt_tokens if response.usage else 0,
                tokens_out=response.usage.completion_tokens if response.usage else 0,
            )

            return decision

        except ValidationError as e:
            error_msg = f"LLM response validation failed: {e}"
            logger.error(error_msg)
            logger.error(f"Raw response: {raw_response}")
        except (TimeoutError, asyncio.TimeoutError, APITimeoutError) as e:
            error_msg = (
                f"LLM TIMEOUT after {self._timeout}s — "
                f"signal rejected due to latency, not LLM judgment")
            logger.warning(error_msg)
        except Exception as e:
            error_msg = f"LLM call failed: {e}"
            logger.error(error_msg)

        latency = time.monotonic() - start_time

        # Log failed attempt
        self._log_conversation(
            context=context,
            raw_response=raw_response or "",
            decision=None,
            latency=latency,
            error=error_msg,
        )

        # Return rejection on any failure
        return FilterDecision(
            approved=False,
            confidence=0,
            entry_price=context.breakout_price,
            stop_price=context.box_bottom if context.direction == "long" else context.box_top,
            target_price=0.0,
            reasoning=f"LLM filter failed: {error_msg}",
            risk_flags=["llm_error"],
        )

    def _log_conversation(
        self,
        context: SignalContext,
        raw_response: str,
        decision: Optional[FilterDecision],
        latency: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Log every LLM request/response pair as a JSON file.

        Naming: YYYY-MM-DD_HHMMSS_{instrument}_{direction}.json
        Per grok_logs/README.md specification.
        """
        if self._log_dir is None:
            return

        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d_%H%M%S")
        direction = getattr(context, 'direction', 'ORB')
        filename = f"{ts}_{context.instrument}_{direction}.json"

        log_entry = {
            "timestamp_utc": now.isoformat(),
            "model": self._model,
            "instrument": context.instrument,
            "direction": getattr(context, 'direction', 'ORB'),
            "request": {
                "signal_context": context.model_dump(),
            },
            "response": {
                "raw": raw_response,
                "parsed": {
                    "approved": decision.approved,
                    "confidence": decision.confidence,
                    "entry_price": decision.entry_price,
                    "stop_price": decision.stop_price,
                    "target_price": decision.target_price,
                    "reasoning": decision.reasoning,
                    "risk_flags": list(decision.risk_flags),
                } if decision else None,
            },
            "latency_seconds": round(latency, 3),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "error": error,
        }

        try:
            filepath = self._log_dir / filename
            with open(filepath, "w") as f:
                json.dump(log_entry, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Failed to log LLM conversation: {e}")

    async def evaluate_orb_signal(self, context: ORBSignalContext) -> FilterDecision:
        """Evaluate an ORB setup via Grok. Retry once on timeout, then proceed mechanically.

        Unlike evaluate_signal (which rejects on failure), ORB falls back to
        mechanical approval because the ORB edge exists without the LLM.
        """
        context_json = context.model_dump_json(indent=2)
        feedback = self._build_orb_feedback(context)
        prompt = build_orb_signal_prompt(context_json, feedback=feedback)
        start_time = time.monotonic()
        raw_response = None

        for attempt in range(2):  # original + 1 retry
            timeout = self._timeout if attempt == 0 else 5.0
            start_time = time.monotonic()
            raw_response = None

            try:
                response = await asyncio.to_thread(
                    self._client.chat.completions.create,
                    model=self._model,
                    messages=[
                        {"role": "system", "content": ORB_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    timeout=timeout,
                )
                raw_response = response.choices[0].message.content
                latency = time.monotonic() - start_time

                llm_resp = LLMResponse.model_validate_json(raw_response)

                decision = FilterDecision(
                    approved=llm_resp.approved,
                    confidence=llm_resp.confidence,
                    entry_price=0.0,
                    stop_price=0.0,
                    target_price=0.0,
                    reasoning=llm_resp.reasoning,
                    risk_flags=llm_resp.risk_flags,
                )

                logger.info(
                    f"ORB LLM [{self._model}] {context.instrument}: "
                    f"approved={decision.approved} conf={decision.confidence} "
                    f"latency={latency:.1f}s")

                # Record to decision ledger
                if self._ledger:
                    self._ledger.record_decision(
                        strategy="ORB",
                        instrument=context.instrument,
                        decision="APPROVE" if decision.approved else "REJECT",
                        confidence=decision.confidence,
                        reasoning=decision.reasoning,
                        risk_flags=list(decision.risk_flags),
                        context={
                            "range_high": context.range_high,
                            "range_low": context.range_low,
                            "range_size": context.range_size,
                            "range_vs_avg": context.range_vs_avg,
                            "atr_regime": context.atr_regime,
                            "current_price": context.current_price,
                            "session": context.session,
                            "day_of_week": context.day_of_week,
                        },
                    )

                self._log_conversation(
                    context=context,
                    raw_response=raw_response,
                    decision=decision,
                    latency=latency,
                    tokens_in=response.usage.prompt_tokens if response.usage else 0,
                    tokens_out=response.usage.completion_tokens if response.usage else 0,
                )
                return decision

            except (TimeoutError, asyncio.TimeoutError, APITimeoutError):
                latency = time.monotonic() - start_time
                if attempt == 0:
                    logger.warning(
                        f"ORB LLM timeout ({latency:.1f}s) -- retrying with 5s timeout")
                    continue
                else:
                    logger.warning(
                        f"ORB LLM double timeout -- proceeding mechanically")

            except ValidationError as e:
                logger.error(f"ORB LLM validation failed: {e}")
                logger.error(f"Raw response: {raw_response}")
                break

            except Exception as e:
                logger.error(f"ORB LLM call failed: {e}")
                break

        # Fallback: proceed mechanically (place brackets)
        self._log_conversation(
            context=context,
            raw_response=raw_response or "",
            decision=None,
            latency=time.monotonic() - start_time,
            error="ORB LLM failed/timed out -- mechanical fallback",
        )

        return FilterDecision(
            approved=True,
            confidence=0,
            entry_price=0.0,
            stop_price=0.0,
            target_price=0.0,
            reasoning="ORB LLM unavailable -- mechanical fallback (brackets placed)",
            risk_flags=["llm_fallback"],
        )
