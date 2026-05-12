#!/usr/bin/env bash
# daily_restart.sh — deterministic daily reset of v11 + IBC + Gateway.
#
# Single source of truth for restart scheduling, replacing the unreliable
# IBC autorestart-token approach. Invoked by ~/Library/LaunchAgents/
# com.nick.daily-restart.plist at 01:15 ET every day. Can also be run
# manually for dry-run validation:
#
#   bash ~/code/ibkr_grok_wing_agent/docs/agents/scripts/daily_restart.sh
#
# Side effects when run (BE AWARE):
#   - Stops any running v11.live.run_live process.
#   - Briefly tears down IBC + Gateway (port 4002 will be down ~1-3 min).
#   - Triggers a 2FA prompt to Nick's IBKR mobile app on the respawn.
#   - Starts v11 with `caffeinate -i python -m v11.live.run_live --live --no-llm`.
#
# Hard guarantees:
#   - Logs to ~/.daily_restart.log (append).
#   - Exit 0 on success, exit 1 on any failure (with reason logged).
#   - Disconnects launchd's com.ibc.gateway supervisor temporarily so it
#     doesn't fight us during the kill phase, then re-enables it for the
#     respawn.
#
# Reference: docs/journal/2026-05-10_daily_restart_architecture.md
#            docs/agents/inbox_claude_code.md (daily-restart-architecture
#            DIRECTIVE 2026-05-10)

set -u

LOG="$HOME/.daily_restart.log"
INBOX="$HOME/code/ibkr_grok_wing_agent/docs/agents/inbox_cowork.md"
REPO="$HOME/code/ibkr_grok_wing_agent"
PLIST="$HOME/Library/LaunchAgents/com.ibc.gateway.plist"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*" >> "$LOG"; }

log "=================================================================="
log "daily_restart.sh START — invoked by ${0}, parent PID=$PPID, user=$USER"

# ── 1. Stop v11 if running ──────────────────────────────────────────────
log "[1] stopping v11.live.run_live (if any)"
if pgrep -f "v11\.live\.run_live" > /dev/null 2>&1; then
    pkill -f "v11\.live\.run_live" || true
    sleep 5
    if pgrep -f "v11\.live\.run_live" > /dev/null 2>&1; then
        log "    [!] v11 still alive after 5s — escalating to SIGKILL"
        pkill -9 -f "v11\.live\.run_live" || true
        sleep 2
    fi
    log "    v11 stopped"
else
    log "    no v11 running — skip"
fi

# ── 2. Disable launchd supervisor so it doesn't race with our kill ──────
log "[2] unloading launchd com.ibc.gateway plist"
launchctl unload -w "$PLIST" 2>/dev/null || true
sleep 2

# ── 3. Kill IBC + Gateway ────────────────────────────────────────────────
log "[3] killing IBC + Gateway"
pkill -f ibcalpha || true
pkill -f "IB Gateway" || true
sleep 30

# ── 4. Verify nothing left — abort if zombies persist ────────────────────
log "[4] verifying clean state"
if pgrep -fl ibcalpha; then
    log "    [FAIL] ibcalpha still alive after 30s sleep — aborting"
    log "    Re-loading launchd supervisor before exit so we don't leave the system in a worse state"
    launchctl load -w "$PLIST" 2>/dev/null || true
    exit 1
fi
log "    clean — no ibcalpha processes"

# ── 5. Re-enable launchd supervisor (this respawns gatewaystartmacos.sh) ─
log "[5] re-loading launchd com.ibc.gateway plist"
launchctl load -w "$PLIST"
if [ $? -ne 0 ]; then
    log "    [FAIL] launchctl load returned non-zero"
    exit 1
fi

# ── 6. Poll port 4002 for up to 5 min ────────────────────────────────────
log "[6] polling port 4002 (max 5 min)"
PORT_UP=0
for i in $(seq 1 20); do
    if lsof -nP -iTCP:4002 -sTCP:LISTEN > /dev/null 2>&1; then
        PORT_UP=1
        log "    port 4002 listening after ${i} × 15s = $((i*15))s"
        break
    fi
    sleep 15
done

if [ $PORT_UP -eq 0 ]; then
    REASON="port 4002 still not listening after 5 min — Gateway failed to come up (2FA timeout? credentials? Gateway crash?)"
    log "    [FAIL] $REASON"
    # Append a NOTE to inbox_cowork.md so Cowork sees the failure on next poll.
    DATE_STR=$(date '+%Y-%m-%d')
    {
        echo ""
        echo "## $(date '+%Y-%m-%d %H:%M %Z') — daily-restart-failure-$DATE_STR — NOTE — OPEN"
        echo "**From:** daily_restart.sh"
        echo ""
        echo "Daily restart at 01:15 ET failed — port 4002 did not come up within 5 min after launchd reload. Most recent reason: $REASON. Full log: \`~/.daily_restart.log\`. Manual recovery: tap 2FA on phone if prompted, OR run \`bash docs/agents/scripts/daily_restart.sh\` manually after waking the Mac and checking IBKR mobile app."
        echo ""
        echo "---"
    } >> "$INBOX"
    exit 1
fi

# ── 7. Start v11 with caffeinate -i ──────────────────────────────────────
log "[7] starting v11.live.run_live --live --no-llm (caffeinate -i wrapped)"
cd "$REPO"
# shellcheck disable=SC1091
source .venv/bin/activate
nohup caffeinate -i python -m v11.live.run_live --live --no-llm \
    >> "$HOME/.v11_paper.log" 2>&1 &
V11_PID=$!
echo "$V11_PID" > "$HOME/.v11_paper.pid"
log "    v11 launched, PID=$V11_PID, written to ~/.v11_paper.pid"

sleep 30
if ! pgrep -fl "v11\.live\.run_live" > /dev/null 2>&1; then
    log "    [FAIL] v11 not alive 30s after launch — startup failure"
    exit 1
fi
log "    v11 confirmed alive"

# ── 8. Final summary ─────────────────────────────────────────────────────
NEW_GW_PID=$(pgrep -f ibcalpha | head -1)
log "[8] SUCCESS — Gateway PID=${NEW_GW_PID:-unknown} v11 PID=$V11_PID"
log "=================================================================="
exit 0
