"""GBPUSD ORB — LLM regime gate.

For each ORB trade day, ask Haiku 4.5: given trailing market stats and the
date, is the regime currently supportive of a London-open ORB trade?

Inputs to LLM (computed BEFORE trade entry, no lookahead):
  - date (year-month-day)
  - trailing 30d median follow-through pips
  - trailing 30d clean-hold rate
  - prior 5 trade outcomes (TP/SL/TIME string)
  - today's Asian range in pips
  - day of week

Output: TAKE / SKIP + confidence + reason.

Decision: trade only days the LLM approves.

Compare:
  - Baseline (no filter)
  - LLM-approved subset
  - LLM-rejected subset
year-by-year and full-sample.
"""
from __future__ import annotations

import sys
sys.path.insert(0, r"C:\ibkr_grok-_wing_agent")

import asyncio
import hashlib
import json
import logging
import os
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

from v11.backtest.data_loader import load_instrument_bars
from v11.backtest.investigate_orb_xauusd import (
    _metrics, _precompute_gap_metrics, _run_config, _split_by_year,
)
from v11.backtest.orb_fx_grid import _fx_template
from v11.core.types import Bar


CACHE_DIR = Path("v11/backtest/results/llm_regime_gate_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv():
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()


# ── Daily feature computation (no lookahead) ────────────────────────────────

@dataclass
class DayStats:
    date: str
    weekday: str
    asian_rng_pips: float
    london_breakout: bool
    follow_thru_pips: float        # 0 if no breakout
    clean_hold: bool               # london close on breakout side


def compute_daily_stats(bars: List[Bar]) -> Dict[str, DayStats]:
    by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)

    out: Dict[str, DayStats] = {}
    for date, day_bars in by_date.items():
        asian = [b for b in day_bars if b.timestamp.hour < 6]
        london = [b for b in day_bars if 8 <= b.timestamp.hour < 16]
        if not asian or not london:
            continue
        a_high = max(b.high for b in asian); a_low = min(b.low for b in asian)
        l_high = max(b.high for b in london); l_low = min(b.low for b in london)
        l_close = london[-1].close
        up = l_high > a_high; dn = l_low < a_low
        breakout = up or dn
        if up:
            ft = (l_high - a_high) / 0.0001
            clean = l_close > a_high
        elif dn:
            ft = (a_low - l_low) / 0.0001
            clean = l_close < a_low
        else:
            ft = 0.0; clean = False
        weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][day_bars[0].timestamp.weekday()]
        out[date] = DayStats(
            date=date, weekday=weekday,
            asian_rng_pips=(a_high - a_low) / 0.0001,
            london_breakout=breakout,
            follow_thru_pips=ft,
            clean_hold=clean,
        )
    return out


def trailing_features(daily: Dict[str, DayStats], up_to_date: str, window: int = 30):
    """Compute trailing 30d follow-through median and clean-hold rate using
    only days strictly before up_to_date."""
    sorted_dates = sorted(daily.keys())
    if up_to_date not in sorted_dates:
        # find rank
        idx = sum(1 for d in sorted_dates if d < up_to_date)
    else:
        idx = sorted_dates.index(up_to_date)
    prior = sorted_dates[max(0, idx - window): idx]
    if not prior:
        return None, None
    ft = [daily[d].follow_thru_pips for d in prior if daily[d].london_breakout]
    ch = [daily[d].clean_hold for d in prior if daily[d].london_breakout]
    med_ft = statistics.median(ft) if ft else 0.0
    rate_ch = sum(ch) / len(ch) * 100 if ch else 0.0
    return med_ft, rate_ch


# ── LLM call ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are evaluating whether to take a London-open ORB
(Opening Range Breakout) trade on GBPUSD.

Strategy structure: build the price range during Asian session (00:00-06:00
UTC). Trade in London/NY (08:00-16:00 UTC) — break above Asian high goes long,
break below Asian low goes short. Stop = opposite side of Asian range. Target
= 2.5x stop distance (RR=2.5).

Strategy works when:
  - London/NY produces strong directional move after breaking the Asian range
  - Follow-through past the Asian edge is large (>35 pips historically)
  - Breakouts hold (London closes on the breakout side >75% of breakout days)

Strategy fails when:
  - GBPUSD is in vol-compression regime (BoE rate-cut cycle, post-easing)
  - Breakouts get faded intraday
  - Macro environment lacks directional flow (e.g. data-light week)

You have:
  - the date (gives you macro/policy context for that period)
  - trailing 30-day follow-through median pips
  - trailing 30-day clean-hold rate
  - today's Asian range size

Decide: TAKE or SKIP. Be selective only when you have a real reason —
default toward TAKE if trailing stats are healthy and there's no specific
regime concern.

Return JSON only:
{
  "decision": "take" | "skip",
  "confidence": <int 0-100>,
  "primary_concern": "<short flag>",
  "reasoning": "<<=2 short sentences>"
}
"""


def _hash_input(payload: dict, model: str) -> str:
    s = json.dumps({"m": model, **payload}, sort_keys=True)
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def query_llm(payload: dict, model: str, api_key: str, timeout: float = 60.0):
    h = _hash_input(payload, model)
    cp = CACHE_DIR / f"{model.replace('/','_')}_{h}.json"
    if cp.exists():
        return json.loads(cp.read_text()), True
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ("Evaluate this GBPUSD ORB day. "
                                          "Return JSON only.\n\n"
                                          + json.dumps(payload, indent=2))},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 400,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/local/v11",
        "X-Title": "v11 GBPUSD ORB regime gate",
    }
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                          json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"decision": "skip", "confidence": 0,
                "primary_concern": "api_error",
                "reasoning": f"API err: {e}",
                "_usage": {"in": 0, "out": 0}}, False

    raw = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip().rstrip("`").strip()
    if "{" in stripped and "}" in stripped:
        stripped = stripped[stripped.index("{"): stripped.rindex("}") + 1]
    try:
        parsed = json.loads(stripped)
    except Exception as e:
        parsed = {"decision": "skip", "confidence": 0,
                  "primary_concern": "parse_error",
                  "reasoning": f"parse fail: {e}"}
    parsed["_usage"] = {"in": int(usage.get("prompt_tokens", 0) or 0),
                        "out": int(usage.get("completion_tokens", 0) or 0)}
    try:
        cp.write_text(json.dumps(parsed))
    except Exception:
        pass
    return parsed, False


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    model = "anthropic/claude-haiku-4.5"

    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("orb_llm_gate")
    log.setLevel(logging.WARNING)

    cfg = replace(_fx_template("GBPUSD"), rr_ratio=2.5)
    bars = load_instrument_bars("GBPUSD")
    print(f"GBPUSD: {len(bars):,} bars\n", flush=True)

    print("Computing daily stats ...", flush=True)
    daily = compute_daily_stats(bars)
    print(f"  {len(daily)} days with both Asian and London sessions\n", flush=True)

    bars_by_date: Dict[str, List[Bar]] = defaultdict(list)
    for b in bars:
        bars_by_date[b.timestamp.strftime("%Y-%m-%d")].append(b)
    gap_lookup = _precompute_gap_metrics(dict(bars_by_date), cfg)

    print("Running ORB to collect trades ...", flush=True)
    trades = asyncio.run(_run_config(cfg, bars, gap_filter=False,
                                     gap_lookup=gap_lookup, log=log))
    print(f"  {len(trades)} trades\n", flush=True)

    # Sort trades chronologically; build prior-5-outcome rolling state
    trades.sort(key=lambda t: t["timestamp"])
    prior_outcomes: deque = deque(maxlen=5)

    # ── Run LLM gate per trade ─────────────────────────────────────────────
    print(f"Querying LLM ({model}) per trade ...", flush=True)
    verdicts = []
    n_cached = n_called = 0
    tokens_in = tokens_out = 0
    for i, t in enumerate(trades):
        date = t["timestamp"][:10]
        med_ft, rate_ch = trailing_features(daily, date, window=30)
        ds = daily.get(date)
        payload = {
            "instrument": "GBPUSD",
            "date": date,
            "weekday": ds.weekday if ds else "?",
            "asian_range_pips": round(ds.asian_rng_pips, 1) if ds else None,
            "trailing_30d_follow_through_pips_median": round(med_ft or 0, 1),
            "trailing_30d_clean_hold_rate_pct": round(rate_ch or 0, 1),
            "prior_5_trade_outcomes": list(prior_outcomes),
            "rr_target": 2.5,
        }
        v, was_cached = query_llm(payload, model, api_key)
        verdicts.append(v)
        if was_cached:
            n_cached += 1
        else:
            n_called += 1
            tokens_in += v.get("_usage", {}).get("in", 0)
            tokens_out += v.get("_usage", {}).get("out", 0)

        # Update prior outcomes
        out_label = "TP" if t["pnl"] > 0 else ("SL" if t["pnl"] < 0 else "TIME")
        prior_outcomes.append(out_label)

        if (i + 1) % 50 == 0 or i == len(trades) - 1:
            print(f"  [{i+1:4d}/{len(trades)}]  cached={n_cached} called={n_called} "
                  f"tok_in={tokens_in} tok_out={tokens_out}",
                  flush=True)

    # Cost estimate (Haiku ~$1/M in, $5/M out)
    cost = tokens_in / 1e6 * 1.0 + tokens_out / 1e6 * 5.0
    print(f"\n  Estimated cost: ~${cost:.3f}\n", flush=True)

    # ── Decisions ──────────────────────────────────────────────────────────
    approved_trades = [t for t, v in zip(trades, verdicts) if v.get("decision") == "take"]
    rejected_trades = [t for t, v in zip(trades, verdicts) if v.get("decision") != "take"]

    print("=" * 90)
    print("  LLM REGIME GATE RESULTS — RR=2.5")
    print("=" * 90)
    print(f"  Total trades: {len(trades)}")
    print(f"  TAKE: {len(approved_trades)}  SKIP: {len(rejected_trades)}\n")

    # Full sample
    print("  --- Full sample (2018-2026) ---")
    print(f"  {'Slice':<14} {'N':>5} {'WR%':>6} {'AvgR':>8} {'PF':>6}")
    for label, ts in [("baseline", trades), ("LLM_approve", approved_trades),
                      ("LLM_reject", rejected_trades)]:
        m = _metrics(ts)
        pf = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {label:<14} {m['N']:>5} {m['WR']:>6.1f} {m['AvgR']:>+8.3f} {pf:>6}")

    # OOS / IS split
    print("\n  --- OOS (2018-2023) ---")
    print(f"  {'Slice':<14} {'N':>5} {'WR%':>6} {'AvgR':>8} {'PF':>6}")
    for label, ts in [("baseline", trades), ("LLM_approve", approved_trades),
                      ("LLM_reject", rejected_trades)]:
        oos = [t for t in ts if datetime.fromisoformat(t["timestamp"]).year <= 2023]
        m = _metrics(oos)
        pf = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {label:<14} {m['N']:>5} {m['WR']:>6.1f} {m['AvgR']:>+8.3f} {pf:>6}")

    print("\n  --- IS (2024-2026) ---")
    print(f"  {'Slice':<14} {'N':>5} {'WR%':>6} {'AvgR':>8} {'PF':>6}")
    for label, ts in [("baseline", trades), ("LLM_approve", approved_trades),
                      ("LLM_reject", rejected_trades)]:
        is_ = [t for t in ts if datetime.fromisoformat(t["timestamp"]).year >= 2024]
        m = _metrics(is_)
        pf = f"{m['PF']:.2f}" if m['PF'] != float("inf") else "  inf"
        print(f"  {label:<14} {m['N']:>5} {m['WR']:>6.1f} {m['AvgR']:>+8.3f} {pf:>6}")

    # Year-by-year
    print("\n" + "=" * 90)
    print("  YEAR-BY-YEAR — APPROVE vs REJECT")
    print("=" * 90)
    print(f"  {'Year':<6} | {'BaseN':>6} {'BaseR':>7} | {'AppN':>5} {'AppR':>7} | "
          f"{'RejN':>5} {'RejR':>7} | {'Skip%':>6}")
    print("  " + "-" * 70)
    by_year_base = _split_by_year(trades)
    by_year_app = _split_by_year(approved_trades)
    by_year_rej = _split_by_year(rejected_trades)
    for yr in sorted(by_year_base.keys()):
        bm = _metrics(by_year_base[yr])
        am = _metrics(by_year_app.get(yr, []))
        rm = _metrics(by_year_rej.get(yr, []))
        skip_pct = (rm["N"] / bm["N"] * 100) if bm["N"] else 0
        print(f"  {yr:<6} | {bm['N']:>6} {bm['AvgR']:>+7.3f} | "
              f"{am['N']:>5} {am['AvgR']:>+7.3f} | {rm['N']:>5} {rm['AvgR']:>+7.3f} | "
              f"{skip_pct:>5.1f}%")

    # ── Save artifacts ──────────────────────────────────────────────────────
    out = {
        "model": model, "n_calls": n_called, "n_cached": n_cached,
        "tokens_in": tokens_in, "tokens_out": tokens_out, "est_cost": cost,
        "trades": [{"ts": t["timestamp"], "pnl": t["pnl"],
                    "range": t["range_high"] - t["range_low"],
                    "verdict": v.get("decision"),
                    "confidence": v.get("confidence"),
                    "concern": v.get("primary_concern"),
                    "reason": v.get("reasoning"),
                   } for t, v in zip(trades, verdicts)],
    }
    Path("v11/backtest/results").mkdir(parents=True, exist_ok=True)
    Path("v11/backtest/results/orb_gbpusd_llm_gate.json").write_text(
        json.dumps(out, indent=2, default=str))
    print(f"\n  saved -> v11/backtest/results/orb_gbpusd_llm_gate.json")

    print("\nDone.")


if __name__ == "__main__":
    main()
