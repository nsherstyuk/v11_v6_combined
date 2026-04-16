"""CachedFilter — Record/replay LLM filter responses.

Wraps any LLMFilter implementation with a JSON cache layer.
On cache miss: calls inner filter (if provided), stores response.
On cache hit: returns stored response (instant, free, deterministic).

Cache key: SHA-256 of SignalContext JSON (stable, deterministic).
Cache storage: JSON file mapping hash -> FilterDecision fields.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from ..core.types import FilterDecision
from ..llm.models import SignalContext

log = logging.getLogger(__name__)


class CachedFilter:
    """LLM filter with transparent caching.

    Satisfies the LLMFilter protocol.
    """

    def __init__(
        self,
        inner_filter: Optional[object] = None,
        cache_path: str = "replay_llm_cache.json",
    ):
        self._inner = inner_filter
        self._cache_path = Path(cache_path)
        self._cache: dict[str, dict] = {}
        self._hits = 0
        self._misses = 0
        self._load_cache()

    def _load_cache(self) -> None:
        if self._cache_path.exists():
            try:
                self._cache = json.loads(self._cache_path.read_text())
                log.info(f"CachedFilter: loaded {len(self._cache)} entries from {self._cache_path}")
            except (json.JSONDecodeError, IOError) as e:
                log.warning(f"CachedFilter: failed to load cache: {e}")
                self._cache = {}

    def save(self) -> None:
        """Persist cache to disk."""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(self._cache, indent=2))
        log.info(f"CachedFilter: saved {len(self._cache)} entries "
                 f"(hits={self._hits}, misses={self._misses})")

    @staticmethod
    def _cache_key(context: SignalContext) -> str:
        raw = context.model_dump_json(indent=None)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def evaluate_signal(self, context: SignalContext) -> FilterDecision:
        key = self._cache_key(context)

        if key in self._cache:
            self._hits += 1
            entry = self._cache[key]
            log.debug(f"CachedFilter: HIT {key[:8]} -> approved={entry['approved']}")
            return FilterDecision(
                approved=entry["approved"],
                confidence=entry["confidence"],
                entry_price=entry["entry_price"],
                stop_price=entry["stop_price"],
                target_price=entry["target_price"],
                reasoning=entry["reasoning"],
                risk_flags=entry.get("risk_flags", []),
            )

        self._misses += 1

        if self._inner is not None:
            decision = await self._inner.evaluate_signal(context)
            self._cache[key] = {
                "approved": decision.approved,
                "confidence": decision.confidence,
                "entry_price": decision.entry_price,
                "stop_price": decision.stop_price,
                "target_price": decision.target_price,
                "reasoning": decision.reasoning,
                "risk_flags": list(decision.risk_flags),
            }
            log.debug(f"CachedFilter: MISS {key[:8]} -> called inner, approved={decision.approved}")
            return decision

        # No inner filter and no cache hit: passthrough
        log.debug(f"CachedFilter: MISS {key[:8]} -> no inner filter, passthrough")
        return FilterDecision(
            approved=True,
            confidence=0,
            entry_price=context.breakout_price,
            stop_price=context.box_bottom,
            target_price=0.0,
            reasoning="Cache miss — no inner filter, passthrough approval",
            risk_flags=["cache_miss"],
        )

    async def evaluate_orb_signal(self, context) -> FilterDecision:
        """ORB not supported in replay — passthrough."""
        return FilterDecision(
            approved=True, confidence=0,
            entry_price=0.0, stop_price=0.0, target_price=0.0,
            reasoning="ORB not supported in replay",
            risk_flags=[],
        )

    @property
    def stats(self) -> dict:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}

    # ── LLMFilter protocol: outcome recording (delegates to inner) ────────

    def record_darvas_outcome(self, **kwargs) -> None:
        """Delegate to inner filter if it supports the method."""
        if self._inner is not None and hasattr(self._inner, "record_darvas_outcome"):
            self._inner.record_darvas_outcome(**kwargs)

    def record_orb_outcome(self, **kwargs) -> None:
        """Delegate to inner filter if it supports the method."""
        if self._inner is not None and hasattr(self._inner, "record_orb_outcome"):
            self._inner.record_orb_outcome(**kwargs)

    def refresh_feedback(self) -> None:
        """Delegate to inner filter if it supports the method."""
        if self._inner is not None and hasattr(self._inner, "refresh_feedback"):
            self._inner.refresh_feedback()
