#!/usr/bin/env bash
# Port-listening guard snippet for IBC's gatewaystart.sh on macOS.
# ----------------------------------------------------------------------------
# Insert these lines into ~/ibc/gatewaystart.sh DIRECTLY AFTER THE SHEBANG.
# Mirrors the Windows StartGateway.bat netstat guard. Without this guard,
# manually launching the Gateway while it is already up produces a
# double-login lockout at IBKR (the "multiple users connected" symptom).
#
# Reference:
#   docs/ops/macbook_migration.md (Phase 4 — Launcher)
#   docs/journal/2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md
#
# Apply:
#   1. Open ~/ibc/gatewaystart.sh in any editor.
#   2. Find the shebang line (e.g. `#!/bin/bash`).
#   3. Paste the block below immediately after it.
#   4. Save. The file should already be executable — if not:
#         chmod +x ~/ibc/gatewaystart.sh
# ----------------------------------------------------------------------------

if lsof -nP -iTCP:4001 -sTCP:LISTEN > /dev/null 2>&1; then
    echo "ABORT: port 4001 (live API) already listening — Gateway already up"
    exit 1
fi
if lsof -nP -iTCP:4002 -sTCP:LISTEN > /dev/null 2>&1; then
    echo "ABORT: port 4002 (paper API) already listening — Gateway already up"
    exit 1
fi
