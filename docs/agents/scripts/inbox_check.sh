#!/usr/bin/env bash
# inbox_check.sh — cheap unread-check for the agent comms inbox.
# Returns exit code 1 if there's unread content, 0 otherwise. Logs to
# ~/.inbox_polls.log only when something is unread (so the log doesn't
# fill with no-op entries).
#
# Usage:
#   inbox_check.sh cowork        # check if Cowork has unread mail
#   inbox_check.sh claude_code   # check if Claude Code has unread mail
#
# Cron example (5-min polling for Claude Code):
#   */5 * * * *  $HOME/code/ibkr_grok_wing_agent/docs/agents/scripts/inbox_check.sh claude_code
set -e

WHOM="${1:-}"
if [[ -z "$WHOM" ]]; then
    echo "usage: $0 <cowork|claude_code>" >&2
    exit 2
fi

REPO="$HOME/code/ibkr_grok_wing_agent"
FLAG="$REPO/docs/agents/inbox_${WHOM}.flag"
LOG="$HOME/.inbox_polls.log"

if [[ ! -f "$FLAG" ]]; then
    echo "[$(date -u +%FT%TZ)] inbox_${WHOM}: flag file missing ($FLAG)" >> "$LOG"
    exit 2
fi

read W R < "$FLAG"
if [[ -z "$W" || -z "$R" ]]; then
    echo "[$(date -u +%FT%TZ)] inbox_${WHOM}: malformed flag (W='$W' R='$R')" >> "$LOG"
    exit 2
fi

# String compare on ISO 8601 UTC timestamps is lexicographically correct.
if [[ "$W" > "$R" ]]; then
    echo "[$(date -u +%FT%TZ)] inbox_${WHOM}: UNREAD (write=$W read=$R)" >> "$LOG"
    exit 1
fi

exit 0
