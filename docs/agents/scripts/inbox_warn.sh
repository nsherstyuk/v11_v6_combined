#!/usr/bin/env bash
# inbox_warn.sh — emit an "INBOX UNREAD" warning if there's mail for Claude Code.
#
# Used as the command for a Claude Code UserPromptSubmit hook. Fires before
# every user prompt; output (if any) is prepended to the agent's context as
# additional context. Cost when CLEAN: zero output, single bash exec.
#
# Behavior:
#   - exit 0 always (this script must not block user prompts)
#   - stdout: nothing if CLEAN; one warning line if UNREAD
#
# Usage (in .claude/settings.json):
#   "UserPromptSubmit": [{
#     "matcher": "*",
#     "hooks": [{
#       "type": "command",
#       "command": "/abs/path/to/docs/agents/scripts/inbox_warn.sh"
#     }]
#   }]
set -e

REPO="$HOME/code/ibkr_grok_wing_agent"

# Reuse the existing inbox_check.sh — it returns exit 1 if UNREAD.
if "$REPO/docs/agents/scripts/inbox_check.sh" claude_code; then
    : # CLEAN — emit nothing
else
    # UNREAD — surface a one-line directive into the agent's context.
    # Brief: agent reads docs/agents/inbox_claude_code.md to learn what's there.
    echo "[INBOX] UNREAD mail in docs/agents/inbox_claude_code.md — read and address OPEN entries before continuing the user's task. After reading, bump read_ts on docs/agents/inbox_claude_code.flag (the README has the one-liner)."
fi

exit 0
