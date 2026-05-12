"""ibkr_health.py — passive IBKR liveness probe.

Opens a fresh ib_insync connection on clientId=99, calls reqCurrentTime,
appends a one-line OK / FAIL record to ~/.ibkr_health.log, exits 0 / 1.

Strict rules (per docs/superpowers/plans/2026-05-10-ibkr-health-check.md):
  - REPORT only. No orders, no kill, no restart, no recovery action.
  - Hard 10s overall budget via signal.alarm. If anything hangs past 10s,
    SIGALRM fires, the handler logs a FAIL line and os._exit(1)'s
    immediately — no cleanup that could swallow the failure signal.
  - Disconnects cleanly on the success path. On timeout / exception path,
    we prefer a guaranteed log-then-exit over polite cleanup.
  - clientId=99 is intentional (download uses 2, v11 default 1,
    check_ibkr_permissions.py uses 999). If you change this, document
    why in the script header.

Reads IB_HOST / IB_PORT from .env (no hardcoded values). Repo root is
inferred from this file's location (docs/agents/scripts/ibkr_health.py).
"""
from __future__ import annotations

import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
LOG_PATH = Path.home() / ".ibkr_health.log"
HARD_TIMEOUT_S = 10
CLIENT_ID = 99


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(line: str) -> None:
    """Append a single line to the health log. Never raises — logging
    must never be the failure path that hides the actual failure."""
    try:
        with LOG_PATH.open("a") as f:
            f.write(line.rstrip("\n") + "\n")
    except Exception:
        # Last-ditch: write to stderr so launchd captures it. Don't raise.
        try:
            sys.stderr.write(line.rstrip("\n") + "\n")
        except Exception:
            pass


def _alarm_handler(signum, frame):
    """SIGALRM fired — we hung. Log FAIL and hard-exit before any
    cleanup code can run and accidentally suppress the exit code."""
    _log(f"[{_ts()}] IBKR FAIL (reason=hard-timeout {HARD_TIMEOUT_S}s — "
         f"connect or reqCurrentTime hung)")
    os._exit(1)


def main() -> int:
    # Arm the hard timeout BEFORE doing anything that could hang.
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(HARD_TIMEOUT_S)

    # Load .env without depending on dotenv being importable in stripped
    # environments — but we're inside the repo's venv, so dotenv IS
    # available. Use it for consistency with other scripts.
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(REPO / ".env")
    except Exception as e:
        _log(f"[{_ts()}] IBKR FAIL (reason=env-load {type(e).__name__}: {e})")
        return 1

    host = os.getenv("IB_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("IB_PORT", "4002"))
    except ValueError as e:
        _log(f"[{_ts()}] IBKR FAIL (reason=bad-port {e})")
        return 1

    try:
        from ib_insync import IB  # type: ignore
    except Exception as e:
        _log(f"[{_ts()}] IBKR FAIL (reason=import {type(e).__name__}: {e})")
        return 1

    ib = IB()
    try:
        # ib_insync's own connect timeout is in seconds; we set it shorter
        # than our SIGALRM budget so a clean failure beats the hard kill.
        ib.connect(host, port, clientId=CLIENT_ID, timeout=6)
        if not ib.isConnected():
            _log(f"[{_ts()}] IBKR FAIL (reason=connect-returned-disconnected)")
            return 1
        server_time = ib.reqCurrentTime()
        _log(f"[{_ts()}] IBKR OK (server_time={server_time.isoformat()} "
             f"host={host} port={port} clientId={CLIENT_ID})")
        return 0
    except Exception as e:
        _log(f"[{_ts()}] IBKR FAIL (reason={type(e).__name__}: {e})")
        return 1
    finally:
        signal.alarm(0)  # disarm — success path or clean exception
        try:
            ib.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
