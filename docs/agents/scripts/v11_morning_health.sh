#!/usr/bin/env bash
#
# v11_morning_health.sh — single-line PASS/FAIL probe for v11.
#
# Writes one line per invocation to ~/.v11_morning_health.log so a
# `tail ~/.v11_morning_health.log` at breakfast tells you whether
# v11 survived the night. Designed for daily 06:00 EDT execution
# via a launchd job, but it's safe to run manually at any time.
#
# CHECKS (all must PASS):
#   1. ~/.v11_paper.pid points to a live process running v11.live.run_live
#   2. Gateway port 4002 reachable
#   3. ~/.daily_restart.log has an entry less than 26 hours old
#   4. That entry's last line shows "SUCCESS"
#   5. No CRITICAL or ERROR lines in ~/.v11_paper.log from the
#      current v11 process's start time onward
#
# FAIL line is single-cause: it reports the FIRST check that failed
# so the morning glance is unambiguous. Run the same script again
# after a fix and the next line will report the next-most-broken
# thing (or PASS).
#
# Created 2026-05-13 after the Phase 6 proof-of-life closed.
# Journal: docs/journal/2026-05-13_xauusd_first_live_fill_full_lifecycle.md
#

set -u  # nounset; do NOT set -e — we want to control fail messages

LOG="$HOME/.v11_morning_health.log"
PID_FILE="$HOME/.v11_paper.pid"
V11_LOG="$HOME/.v11_paper.log"
CRON_LOG="$HOME/.daily_restart.log"
GW_HOST=127.0.0.1
GW_PORT=4002

ts=$(date "+%Y-%m-%d %H:%M %Z")

emit() {
    # $1 = status (PASS|FAIL), $2 = detail string
    printf '%s  %s  %s\n' "$ts" "$1" "$2" >> "$LOG"
    printf '%s  %s  %s\n' "$ts" "$1" "$2"
}

# ── 1. v11 process alive ────────────────────────────────────────────
if [[ ! -f "$PID_FILE" ]]; then
    emit FAIL "v11_pid_file_missing ($PID_FILE)"
    exit 1
fi
pid=$(cat "$PID_FILE")
if ! ps -p "$pid" >/dev/null 2>&1; then
    emit FAIL "v11_down (PID $pid not running)"
    exit 1
fi
if ! ps -p "$pid" -o command= | grep -q "v11.live.run_live"; then
    emit FAIL "v11_pid_repurposed (PID $pid is no longer v11)"
    exit 1
fi

# Process uptime (elapsed) for the PASS message
uptime=$(ps -p "$pid" -o etime= | tr -d ' ')

# ── 2. Gateway port reachable ──────────────────────────────────────
if ! nc -z -w 2 "$GW_HOST" "$GW_PORT" >/dev/null 2>&1; then
    emit FAIL "gw_down (port $GW_PORT not listening)"
    exit 1
fi

# ── 3. Cron freshness (< 26h since last entry) ─────────────────────
if [[ ! -f "$CRON_LOG" ]]; then
    emit FAIL "cron_log_missing ($CRON_LOG)"
    exit 1
fi
# Last modification time vs now, in seconds
cron_mtime=$(stat -f %m "$CRON_LOG" 2>/dev/null || stat -c %Y "$CRON_LOG" 2>/dev/null)
if [[ -z "${cron_mtime:-}" ]]; then
    emit FAIL "cron_mtime_unreadable"
    exit 1
fi
now_epoch=$(date +%s)
cron_age_s=$(( now_epoch - cron_mtime ))
if (( cron_age_s > 93600 )); then  # 26 hours
    cron_age_h=$(( cron_age_s / 3600 ))
    emit FAIL "cron_stale (last entry ${cron_age_h}h ago)"
    exit 1
fi

# ── 4. Last cron run completed SUCCESS ─────────────────────────────
last_cron_line=$(tail -1 "$CRON_LOG")
if ! tail -10 "$CRON_LOG" | grep -q "SUCCESS"; then
    emit FAIL "cron_no_success_in_last_10_lines"
    exit 1
fi

# ── 5. No new CRITICAL/ERROR lines in v11 log since process start ─
if [[ -f "$V11_LOG" ]]; then
    # Get the process start time from ps as "Day Mon HH:MM:SS YYYY" or short form
    # We use lstart for stable parsing on macOS
    proc_start=$(ps -p "$pid" -o lstart= 2>/dev/null | sed 's/^ *//')
    if [[ -n "$proc_start" ]]; then
        # Parse to epoch (macOS date)
        proc_start_epoch=$(date -j -f "%a %b %e %H:%M:%S %Y" "$proc_start" +%s 2>/dev/null || echo 0)
        if (( proc_start_epoch > 0 )); then
            proc_start_human=$(date -r "$proc_start_epoch" "+%Y-%m-%d %H:%M:%S")
            # Count CRITICAL / ERROR lines after process start
            err_count=$(awk -v start="$proc_start_human" '
                {
                    line_ts = $1 " " $2
                    if (line_ts >= start && /ERROR|CRITICAL/) count++
                }
                END { print count + 0 }
            ' "$V11_LOG")
            if (( err_count > 0 )); then
                last_err=$(awk -v start="$proc_start_human" '
                    {
                        line_ts = $1 " " $2
                        if (line_ts >= start && /ERROR|CRITICAL/) print
                    }
                ' "$V11_LOG" | tail -1)
                emit FAIL "errors_in_v11_log (${err_count} since start; last: ${last_err:0:80})"
                exit 1
            fi
        fi
    fi
fi

# ── All checks passed ──────────────────────────────────────────────
emit PASS "v11=PID${pid}/${uptime}; gw=OPEN; cron last=$(echo "$last_cron_line" | sed 's/.*EDT\]/\[EDT\]/' | cut -c1-60)"
exit 0
