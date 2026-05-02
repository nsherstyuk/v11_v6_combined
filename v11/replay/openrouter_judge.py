"""OpenRouter LLM judge for Darvas breakouts.

Implements the Verdict-returning Judge protocol from darvas_judge_example.py
using OpenRouter (https://openrouter.ai). One key, many models.

Two prompt modes:
  - identified: real instrument name, real timestamp
  - anonymized: 'Instrument', relative bar indices, prices normalized so
                box top = 1.0000. Removes the LLM's ability to recognize
                specific historical setups (hindsight contamination test).

Caches responses by (model, mode, signal_hash) to avoid re-paying for
the same trial across runs.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from v11.llm.models import SignalContext

# Reuse the Verdict dataclass from the example file
from v11.replay.darvas_judge_example import Verdict


CACHE_DIR = Path("v11/replay/results/llm_judge_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _signal_hash(ctx: SignalContext, mode: str) -> str:
    payload = {
        "mode": mode,
        "ts": ctx.current_time_utc,
        "instrument": ctx.instrument,
        "direction": ctx.direction,
        "box_top": round(ctx.box_top, 6),
        "box_bottom": round(ctx.box_bottom, 6),
        "breakout": round(ctx.breakout_price, 6),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _anonymize_context(ctx: SignalContext) -> dict:
    """Return a dict version of the context with prices normalized and
    instrument/timestamps stripped, to remove hindsight contamination cues."""
    norm = ctx.box_top
    if norm <= 0:
        norm = 1.0
    def n(p: float) -> float:
        return round(p / norm, 6)
    bars = []
    for i, b in enumerate(ctx.recent_bars):
        bars.append({
            "t_minus": len(ctx.recent_bars) - i,
            "o": n(b.o), "h": n(b.h), "l": n(b.l), "c": n(b.c),
            "tc": b.tc, "buy_ratio": round(b.bv / max(b.bv + b.sv, 1e-9), 4),
        })
    return {
        "instrument": "Instrument",
        "direction": ctx.direction,
        "box_top": n(ctx.box_top),       # = 1.0
        "box_bottom": n(ctx.box_bottom),
        "box_width_pct": round((ctx.box_top - ctx.box_bottom) / norm * 100, 4),
        "box_width_atr": round(ctx.box_width_atr, 3),
        "box_duration_bars": ctx.box_duration_bars,
        "breakout_price": n(ctx.breakout_price),
        "atr_pct_of_top": round(ctx.atr / norm * 100, 4),
        "atr_vs_avg": ctx.atr_vs_avg,
        "buy_ratio_at_breakout": ctx.buy_ratio_at_breakout,
        "buy_ratio_trend": ctx.buy_ratio_trend,
        "tick_quality": ctx.tick_quality,
        "volume_classification": ctx.volume_classification,
        "session": ctx.session,
        "recent_bars_normalized": bars,
    }


def _identified_context(ctx: SignalContext) -> dict:
    return ctx.model_dump()


SYSTEM_PROMPT = """You evaluate Darvas-style breakout signals as a filter layer.

A Darvas box is a price consolidation: top, bottom, duration. A breakout
occurs when price closes outside the box. Most breakouts fail (base rate
of hitting 1.5R target before stopping out is roughly 20-40%, depending
on regime). Your job is to filter out the worst setups, not gatekeep
every trade.

For each candidate, decide TAKE or SKIP. Default toward SKIP only when
multiple concerns align. A clean box with confirming volume in a normal
session and normal volatility regime should usually be TAKE with moderate
confidence (60-75).

Concerns to consider (use these as risk_flags):
- box_too_narrow: box_width_atr < 0.5 (noise breakout, no room for follow-through)
- box_too_wide: box_width_atr > 4.0 (consolidation is too sloppy)
- box_too_short: duration < 20 bars (insufficient consolidation)
- elevated_volatility: atr_vs_avg > 1.5 (regime, breakouts often fade)
- compressed_volatility: atr_vs_avg < 0.5 (low energy, may not follow through)
- volume_divergence: buy/sell flow opposes breakout direction
- thin_volume: tick_quality LOW or INSUFFICIENT
- counter_session: ASIAN session FX breakouts are noisy
- no_concern: setup looks clean

Return JSON only, this exact schema:
{
  "decision": "take" | "skip",
  "confidence": <int 0-100>,
  "primary_concern": "<one of the flags above>",
  "reasoning": "<<=2 short sentences>"
}
"""


def _build_user_prompt(ctx_dict: dict) -> str:
    return ("Evaluate this Darvas breakout candidate. Return JSON only.\n\n"
            + json.dumps(ctx_dict, indent=2, default=str))


@dataclass
class CallStats:
    calls: int = 0
    cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class OpenRouterJudge:
    """LLM judge via OpenRouter. Single class works for any OpenRouter model."""

    def __init__(self, model: str, mode: str = "identified",
                 api_key: Optional[str] = None, timeout: float = 60.0):
        assert mode in ("identified", "anonymized")
        self._model = model
        self._mode = mode
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        self._key = key
        self._timeout = timeout
        self.name = f"openrouter:{model.split('/')[-1]}:{mode}"
        self.stats = CallStats()

    def _cache_path(self, ctx: SignalContext) -> Path:
        h = _signal_hash(ctx, self._mode)
        safe_model = self._model.replace("/", "_").replace(":", "_")
        return CACHE_DIR / f"{safe_model}_{self._mode}_{h}.json"

    def evaluate(self, ctx: SignalContext) -> Verdict:
        cp = self._cache_path(ctx)
        if cp.exists():
            self.stats.cached += 1
            data = json.loads(cp.read_text())
            return Verdict(approved=data["approved"], confidence=data["confidence"],
                           primary_concern=data["primary_concern"],
                           reasoning=data["reasoning"])

        ctx_dict = (_anonymize_context(ctx) if self._mode == "anonymized"
                    else _identified_context(ctx))
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(ctx_dict)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": 800,
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/local/v11",
            "X-Title": "v11 Darvas judge replay",
        }
        try:
            r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                              json=body, headers=headers, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return Verdict(approved=False, confidence=0,
                           primary_concern="api_error", reasoning=f"API failure: {e}")

        try:
            raw = data["choices"][0]["message"]["content"]
            finish = data["choices"][0].get("finish_reason", "?")
            usage = data.get("usage", {})
            self.stats.calls += 1
            self.stats.tokens_in += int(usage.get("prompt_tokens", 0) or 0)
            self.stats.tokens_out += int(usage.get("completion_tokens", 0) or 0)
            # Strip code fences if model wrapped JSON in markdown
            stripped = raw.strip()
            if stripped.startswith("```"):
                stripped = stripped.split("```", 2)[1]
                if stripped.startswith("json"):
                    stripped = stripped[4:]
                stripped = stripped.strip().rstrip("`").strip()
            # Find first { and last }
            if "{" in stripped and "}" in stripped:
                stripped = stripped[stripped.index("{"): stripped.rindex("}") + 1]
            parsed = json.loads(stripped)
            decision = parsed.get("decision", "skip")
            verdict = Verdict(
                approved=(decision == "take"),
                confidence=int(parsed.get("confidence", 0)),
                primary_concern=str(parsed.get("primary_concern", "unknown")),
                reasoning=str(parsed.get("reasoning", ""))[:300],
            )
        except Exception as e:
            # Log raw for debugging
            try:
                err_path = CACHE_DIR / f"_error_{_signal_hash(ctx, self._mode)}.txt"
                err_path.write_text(f"finish={finish}\nerror={e}\n\nRAW:\n{raw}")
            except Exception:
                pass
            return Verdict(approved=False, confidence=0,
                           primary_concern="parse_error",
                           reasoning=f"Parse failure ({finish}): {str(e)[:120]}")

        try:
            cp.write_text(json.dumps({
                "approved": verdict.approved, "confidence": verdict.confidence,
                "primary_concern": verdict.primary_concern,
                "reasoning": verdict.reasoning,
            }))
        except Exception:
            pass
        return verdict

    def cost_estimate_usd(self) -> float:
        """Rough cost estimate. Update if pricing changes."""
        # Conservative blend: assume ~$3/M in, $15/M out (Sonnet-ish)
        return self.stats.tokens_in / 1e6 * 3 + self.stats.tokens_out / 1e6 * 15
