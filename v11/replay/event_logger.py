"""EventLogger — Structured event logging for replay runs.

Emits JSON lines to a file and optionally to console.
Collects trade records for metrics computation.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


class EventLogger:
    """Structured event logger for replay runs.

    Events are written as JSONL (one JSON object per line).
    Trade exit events are also collected for metrics.
    """

    def __init__(
        self,
        output_path: str,
        verbosity: str = "normal",
    ):
        self._output_path = Path(output_path)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._output_path, "w")
        self._verbosity = verbosity
        self._counts: Counter = Counter()
        self._trade_records: list[dict] = []

        # Events shown at each verbosity level
        self._console_events = {
            "quiet": {"TRADE_ENTERED", "TRADE_EXITED", "RISK_LIMIT_HIT"},
            "normal": {
                "TRADE_ENTERED", "TRADE_EXITED", "SIGNAL_APPROVED",
                "SIGNAL_REJECTED", "LLM_RESPONSE", "RISK_LIMIT_HIT",
                "DAILY_RESET", "SESSION_START", "SESSION_GAP",
            },
            "verbose": None,  # None = show all
        }

    def emit(
        self,
        event: str,
        strategy: str,
        instrument: str,
        timestamp: str,
        data: dict,
    ) -> None:
        """Emit a structured event."""
        record = {
            "ts": timestamp,
            "event": event,
            "strategy": strategy,
            "instrument": instrument,
            "data": data,
        }
        self._file.write(json.dumps(record, default=str) + "\n")
        self._counts[event] += 1

        # Collect trade exit records for metrics
        if event == "TRADE_EXITED":
            self._trade_records.append(data)

        # Console output based on verbosity
        allowed = self._console_events.get(self._verbosity)
        if allowed is None or event in allowed:
            self._print_event(record)

    def _print_event(self, record: dict) -> None:
        event = record["event"]
        ts = record["ts"]
        inst = record["instrument"]
        strategy = record["strategy"]
        data = record["data"]

        if event == "TRADE_ENTERED":
            direction = data.get("direction", "?")
            entry = data.get("entry_price", 0)
            sl = data.get("stop_price", 0)
            tp = data.get("target_price", 0)
            print(f"  [{ts}] {inst} {strategy} ENTER {direction} @ {entry} SL={sl} TP={tp}")
        elif event == "TRADE_EXITED":
            pnl = data.get("pnl", 0)
            reason = data.get("exit_reason", "?")
            hold = data.get("hold_bars", 0)
            print(f"  [{ts}] {inst} {strategy} EXIT {reason} PnL=${pnl:+.2f} hold={hold}bars")
        elif event == "SIGNAL_APPROVED":
            conf = data.get("confidence", 0)
            direction = data.get("direction", "?")
            print(f"  [{ts}] {inst} {strategy} APPROVED {direction} conf={conf}")
        elif event == "SIGNAL_REJECTED":
            reason = data.get("reason", "?")
            print(f"  [{ts}] {inst} {strategy} REJECTED: {reason[:80]}")
        elif event == "DAILY_RESET":
            print(f"  [{ts}] --- DAILY RESET ---")
        elif event == "SESSION_START":
            bars = data.get("bars", 0)
            print(f"  [{ts}] {inst} SESSION START ({bars} bars)")
        elif event == "SESSION_GAP":
            gap = data.get("gap_minutes", 0)
            print(f"  [{ts}] SESSION GAP ({gap:.0f} min)")
        else:
            print(f"  [{ts}] {inst} {strategy} {event}")

    @property
    def trade_records(self) -> list[dict]:
        return self._trade_records

    def get_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def close(self) -> None:
        self._file.close()
