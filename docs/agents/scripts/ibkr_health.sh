#!/usr/bin/env bash
# ibkr_health.sh — passive 30-min liveness probe (launchd-driven).
#
# Self-gates on local hour: silent skip during 23:00–06:59 to avoid
# noise during the 01:00 ET Gateway auto-logoff and 01:15 ET cron-driven
# daily restart. Otherwise activates the repo venv and runs ibkr_health.py.
#
# Always propagates the helper's exit code — no `|| true`, no swallowing.
#
# Plan: docs/superpowers/plans/2026-05-10-ibkr-health-check.md
# Companion: docs/agents/scripts/ibkr_health.py
#            ~/Library/LaunchAgents/com.nick.ibkr-health.plist
set -u

REPO="$HOME/code/ibkr_grok_wing_agent"
LOG="$HOME/.ibkr_health.log"

HOUR=$(date '+%H')   # 00..23, local time
# Active window: 07–22 inclusive. Skip 23, 00, 01, 02, 03, 04, 05, 06.
case "$HOUR" in
    23|00|01|02|03|04|05|06)
        echo "[$(date -u +%FT%TZ)] IBKR SKIP (overnight window, hour=$HOUR local)" >> "$LOG"
        exit 0
        ;;
esac

# Activate venv. If venv is missing, log FAIL ourselves so the absence
# surfaces in the health log rather than as a silent launchd error.
if [ ! -f "$REPO/.venv/bin/activate" ]; then
    echo "[$(date -u +%FT%TZ)] IBKR FAIL (reason=venv missing at $REPO/.venv)" >> "$LOG"
    exit 1
fi
# shellcheck disable=SC1091
. "$REPO/.venv/bin/activate"

python "$REPO/docs/agents/scripts/ibkr_health.py"
exit $?
