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

from openai import OpenAI
from pydantic import ValidationError

from .base import LLMFilter
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
        timeout: float = 10.0,
        log_dir: Optional[str] = None,
    ):
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
        self._model = model
        self._timeout = timeout
        self._log_dir = Path(log_dir) if log_dir else None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    async def evaluate_signal(self, context: SignalContext) -> FilterDecision:
        """Evaluate a breakout signal via Grok.

        Never raises — returns a rejection FilterDecision on any failure.
        Logs every request/response pair to grok_logs/.
        """
        context_json = context.model_dump_json(indent=2)
        prompt = build_signal_prompt(context_json)

        start_time = time.monotonic()
        raw_response = None
        error_msg = None

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=self._timeout,
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
        except (TimeoutError, asyncio.TimeoutError) as e:
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
        prompt = build_orb_signal_prompt(context_json)
        start_time = time.monotonic()
        raw_response = None

        for attempt in range(2):  # original + 1 retry
            timeout = self._timeout if attempt == 0 else 5.0
            start_time = time.monotonic()
            raw_response = None

            try:
                response = self._client.chat.completions.create(
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

                self._log_conversation(
                    context=context,
                    raw_response=raw_response,
                    decision=decision,
                    latency=latency,
                    tokens_in=response.usage.prompt_tokens if response.usage else 0,
                    tokens_out=response.usage.completion_tokens if response.usage else 0,
                )
                return decision

            except (TimeoutError, asyncio.TimeoutError):
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
