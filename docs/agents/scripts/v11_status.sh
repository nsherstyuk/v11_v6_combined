#!/usr/bin/env bash
#
# v11_status.sh — one-command live status snapshot of v11.
#
# Run anytime from any terminal. Self-contained, no dependencies on
# Claude / external services. Shows enough to know whether v11 is
# healthy, what state the strategy is in, what today's trade activity
# looks like, and whether anything errored.
#
# Examples:
#   $ ~/code/ibkr_grok_wing_agent/docs/agents/scripts/v11_status.sh
#   $ alias v11s='~/code/ibkr_grok_wing_agent/docs/agents/scripts/v11_status.sh'
#   $ watch -n 30 ~/code/ibkr_grok_wing_agent/docs/agents/scripts/v11_status.sh
#
# Created 2026-05-20 so Nick can check v11 directly without asking
# Claude every time.

set -u

PID_FILE="$HOME/.v11_paper.pid"
LOG="$HOME/.v11_paper.log"
CRON_LOG="$HOME/.daily_restart.log"
GW_HOST=127.0.0.1
GW_PORT=4002

TODAY=$(date "+%Y-%m-%d")

bar() { printf -- "%.s─" $(seq 1 70); echo; }

bar
printf "  v11 status  —  %s\n" "$(date '+%a %d %b %Y  %H:%M:%S %Z')"
bar

# ── v11 process ──
if [[ ! -f "$PID_FILE" ]]; then
    echo "  PROCESS:  ✗  PID file missing ($PID_FILE)"
    exit 1
fi
pid=$(cat "$PID_FILE")
if ! ps -p "$pid" >/dev/null 2>&1; then
    echo "  PROCESS:  ✗  PID $pid not running"
    echo "  Last cron entry:"
    tail -3 "$CRON_LOG" 2>/dev/null | sed 's/^/    /'
    exit 1
fi
etime=$(ps -p "$pid" -o etime= | tr -d ' ')
echo "  PROCESS:  ✓  PID $pid  uptime $etime"

# ── Gateway ──
if nc -z -w 1 "$GW_HOST" "$GW_PORT" >/dev/null 2>&1; then
    echo "  GATEWAY:  ✓  port $GW_PORT open"
else
    echo "  GATEWAY:  ✗  port $GW_PORT closed"
fi

# ── Last status + state ──
echo
last_status=$(grep "\[STATUS\]" "$LOG" 2>/dev/null | tail -1)
if [[ -n "$last_status" ]]; then
    # Extract just the time + the data after [STATUS]
    ts=$(echo "$last_status" | awk '{print $2}')
    after=$(echo "$last_status" | sed 's/.*\[STATUS\] *//')
    echo "  STATUS:   ($ts)"
    echo "    $after"
fi
echo

# ── Today's events ──
events=$(awk -v d="$TODAY" '$1==d' "$LOG" 2>/dev/null | \
    grep -E "Range from IBKR|ORB state:|Entry stops placed|orderStatusEvent.*Filled|SL/TP placed|ENTRY:|EXIT:|MARKET:|FLATTEN|Trade window closed|stale breakout" | \
    tail -8)
if [[ -n "$events" ]]; then
    echo "  EVENTS TODAY:"
    echo "$events" | awk '{$1=""; print "    " $0}'
else
    echo "  EVENTS TODAY:  (none)"
fi
echo

# ── Errors today (last hour) ──
errs=$(awk -v d="$TODAY" '$1==d && /ERROR|CRITICAL/' "$LOG" 2>/dev/null | tail -3)
if [[ -n "$errs" ]]; then
    echo "  ⚠  ERRORS TODAY (last 3):"
    echo "$errs" | awk '{$1=""; print "    " $0}'
else
    echo "  ERRORS:  none today"
fi

# ── Last cron fire ──
echo
last_cron=$(tail -10 "$CRON_LOG" 2>/dev/null | grep -E "SUCCESS|FAIL" | tail -1)
if [[ -n "$last_cron" ]]; then
    echo "  LAST CRON:  $(echo "$last_cron" | sed 's/^/    /' | head -c 200)"
fi
bar
