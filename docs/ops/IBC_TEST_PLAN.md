# IBC + IB Gateway test runbook

**Last updated:** 2026-05-10 by Claude Code
**Owner:** ops (Nick) + agents

How to verify the IBC + IB Gateway supervision setup is healthy. Five
named tests A–E. Each test has a clear pass criterion, observation
commands, and a current status. When a test result changes, append the
new run's outcome below the test's "Status" line — don't overwrite
history.

---

## Background — the linchpin you need to understand

Three independent restart mechanisms can affect the IBC/Gateway chain.
Knowing their ordering and timing is required to interpret any test.

1. **IBC's own scheduled `AutoRestartTime`** (configured in
   `~/ibc/config.ini`). IBC tries to push this value into Gateway's
   "Lock and Exit" UI at startup via GUI automation. As of IBC 3.23.0
   + Gateway 10.46 on this Mac, **the push silently fails** — IBC logs
   "Setting Auto restart time" but Gateway doesn't actually persist
   the value. The IBC config value is consequently NOT load-bearing.

2. **Gateway-UI scheduled auto-restart** (set manually in Gateway's
   "Configure → Settings → Lock and Exit" panel). When the scheduled
   time arrives, Gateway does a soft restart: writes an autorestart
   token to disk (passed back to IBC via `-Drestart=<token>` Java flag
   on the next launch), exits cleanly, IBC sees the token and reuses
   the session (no 2FA required). **This is the load-bearing
   mechanism for unattended overnight uptime.**

3. **IBKR server-side daily auto-logoff** at ~23:45 ET. Forced by the
   broker side, not IBC's choice. Without an autorestart token on
   disk, IBC has no way to come back unattended → exits cleanly →
   needs 2FA on next IBC launch.

The intended flow: Gateway-UI auto-restart at 22:00–23:00 ET writes
the token, the 23:45 ET server logoff hits a freshly-restarted session
that's already token-secured, no observable disruption.

When mechanism (2) fails to fire, mechanism (3) hits an unprotected
session and triggers the failure mode that took down Gateway on
2026-05-08 23:45 EDT (see `docs/journal/2026-05-09_ibc_launchd_supervisor.md`).

---

## Test A — Cold start

**Scenario:** Launch the chain from fully stopped state (no IBC, no
Gateway, no port 4002 listener).

**Pass criterion:** port 4002 listening within 60s of script invocation.

**Run command:**
```bash
~/ibc/gatewaystartmacos.sh &
sleep 60
lsof -nP -iTCP:4002 -sTCP:LISTEN
```

**Status:** ✅ PASSED 2026-05-08 (initial Mac migration phase 6 launch).

---

## Test B — launchd respawn of IBC (kill-and-respawn)

**Scenario:** IBC's Java process is killed externally (SIGTERM).
launchd should respawn `gatewaystartmacos.sh`, IBC should re-launch
Gateway, port 4002 should come back.

**Pass criterion:**
- New IBC PID different from the killed one
- Port 4002 listening again within 5 min (ThrottleInterval=300)
- 2FA prompt arrives (no autorestart token because the kill is
  external, not a scheduled auto-restart)
- Reconnect proves Nick's phone-tap path works

**Run command:**
```bash
PRE=$(pgrep -f ibcalpha | head -1)
echo "pre: $PRE"
kill $PRE
# Wait, then verify
sleep 90   # may need up to 5 min — depends on ThrottleInterval timing
pgrep -fl ibcalpha     # should show NEW PID
lsof -nP -iTCP:4002 -sTCP:LISTEN
```

**Status:** ✅ PASSED 2026-05-09 10:52–10:56 EDT.
- Pre-kill PID: 48286. Post-respawn PID: 50742 (different — proves
  fresh process).
- Port 4002 listening on the new PID.
- 2FA prompted, Nick approved (~30s).
- Download script saw `Not connected` → reconnected on attempt 6/6
  (cumulative ~210s of sleeps + ~6 timeouts) — barely under the
  reconnect budget. Subsequently bumped `RECONNECT_MAX_ATTEMPTS` 6→10
  for ~9.5min budget.

---

## Test C — IBC respawn of Gateway (Gateway-only kill)

**Scenario:** Kill Gateway's Java process WITHOUT killing IBC. IBC
should detect Gateway exit and re-launch Gateway, ideally reusing the
authenticated session (no 2FA prompt).

**Pass criterion:** Gateway's Java PID changes, IBC PID stays the
same, port 4002 returns, no 2FA prompt.

**Run command:** *(only meaningful if IBC and Gateway are separate
processes — on this Mac they're a single Java process running
`ibcalpha.ibc.IbcGateway`, so this test isn't structurally applicable.
Documented for completeness.)*

**Status:** ⚠️ NOT APPLICABLE on this Mac's IBC version. Single Java
process for both IBC and Gateway. Defer; revisit if IBC packaging
changes.

---

## Test D — Scheduled auto-restart at 22:00–22:30 ET

**Scenario:** Gateway-UI auto-restart fires at the configured time,
writes the autorestart token, exits, IBC re-launches via the token,
no 2FA needed.

**Pass criterion:**
- IBC log shows "Shutdown progress" / "exit status" entries at the
  scheduled time
- Java command line of the new instance contains
  `-Drestart=<token-string>` (proof the token mechanism was used)
- Download script sees a brief `connection lost`, reconnects within
  ~30s
- No 2FA prompt to Nick's phone
- New PID different from pre-restart PID

**Run command:** passive observation around the scheduled time. Use:
```bash
# A few minutes before:
PRE=$(pgrep -f ibcalpha | head -1); echo "pre-restart PID: $PRE"
# Around the scheduled time, watch the log:
tail -f ~/ibc/Logs/ibc-3.23.0_GATEWAY-10.46_$(date +%A).txt
# After:
NEW=$(pgrep -f ibcalpha | head -1); echo "post-restart PID: $NEW"
# Verify the token flag is in the new process command line:
ps -p $NEW -o command= | grep -o 'Drestart=[a-z]*' | head -1
# And confirm the download script saw a clean reconnect:
grep -E "connection lost|reconnected" tick_vault_data/us_equities/_download.log | tail -5
```

**Status:** 🟡 PASSED-WITH-CAVEAT 2026-05-09 22:25 EDT.

The scheduled value was set in the Gateway UI to 22:00 EDT around
07–09 EDT yesterday morning. **At 22:00 EDT it did NOT fire.** PID
50742 (from Test B) was still serving port 4002 unchanged at 22:16
EDT. Nick re-entered the Gateway UI at ~22:23 and rescheduled to
22:25 — that 22:25 reschedule **did fire**:
- PID 50742 → 57774 (new instance)
- Java command line on the new instance contains
  `-Drestart=clopfafhcdnhhbbaapmnhlgpgggfeghfihjlaipm`
- Download saw `Peer closed → reconnected on attempt 1` (~30s outage)
- Zero 2FA prompts

**Caveat / known limitation:** Gateway's auto-restart value does NOT
fire on the same day it's set. The exact rule is unconfirmed (could
be "applies to next day's session" or "needs to be set N hours before
the target time"). **Workaround:** set the value at least the night
before the target time, ideally by editing it during a previous
session.

**Next test of unattended automatic firing:** tonight 2026-05-10
22:25 EDT. If the value Nick set yesterday fires automatically tonight
without a fresh reschedule, the workaround is durable. If it doesn't,
escalate.

---

## Test E — Token-based re-auth at IBKR's 23:45 ET forced logoff

**Scenario:** IBKR's server-side daily auto-logoff fires. Gateway
exits, but the autorestart token from Test D is on disk, so IBC
re-launches and immediately reuses the session.

**Pass criterion:**
- Gateway re-authenticates without sending a 2FA prompt to Nick's
  phone
- Port 4002 returns within ~30 min
- IBC log does NOT contain `autorestart file not found: full
  authentication will be required` for this cycle

**Status:** 🟡 BYPASSED 2026-05-09 23:45 EDT.

The 22:25 EDT auto-restart (Test D) had already secured the session
with the token, so the 23:45 server-side cycle did not initiate a
Gateway shutdown — Gateway PID 57774 stayed unchanged across 23:45
and is still alive 12+ hours later. What DID fire was a server-side
data-farm rotation: `Error 1100: Connectivity between IBKR and TWS
has been lost.` × 4 → `Error 1102: Connectivity restored - data
maintained`. Session preserved. Download script saw a few stale
ib_insync messages and threw a non-fatal `KeyError: 3` (handled
internally). No Nick intervention.

**Test E was not actually exercised** because Test D pre-empted the
trigger. To genuinely test E in isolation we'd need to skip the
22:25 EDT auto-restart for one night and observe the 23:45 EDT
server logoff against a session WITHOUT a fresh token. Not worth
doing intentionally — current behavior is the desired-state.

---

## Test F — daily_restart.sh dry-run

**Scenario:** Run the new `docs/agents/scripts/daily_restart.sh`
manually in a quiet window (Sunday or pre-market). Validates the
end-to-end cycle that the 01:15 ET cron will fire automatically.

**Pass criteria:**
- Script exits 0
- Each numbered step in `~/.daily_restart.log` shows a non-FAIL outcome
- Gateway respawns with NEW PID, port 4002 listening within ≤5 min
- v11 paper starts (PID written to `~/.v11_paper.pid`)
- No `daily-restart-failure-YYYY-MM-DD` NOTE auto-appended to
  `inbox_cowork.md`

**Run command:**
```bash
bash ~/code/ibkr_grok_wing_agent/docs/agents/scripts/daily_restart.sh
tail -20 ~/.daily_restart.log
```

**Status:** ✅ PASSED 2026-05-10 09:59:29 → 10:00:46 EDT (77 s total).
- Gateway PID 66272 → 66877 (proves restart cycle)
- Port 4002 listening after 30 s of polling
- v11 PID 66898 launched (then manually stopped per pre-Monday gating)
- **Notably no 2FA prompt** — IBKR's broker-side session was still
  warm; the kill+respawn cycle was unattended-clean.

This is the deployment-validation test for the daily-restart
architecture. Production fire is 2026-05-11 01:15 EDT.

---

## Future test additions

- **Test G** — Mac sleep / lid close while download is running.
  Observe whether reconnect logic recovers when the laptop wakes up.
  Status: NOT YET RUN.
- **Test H** — Network bounce (toggle Wi-Fi off / on for 60s while
  Gateway is running). Status: NOT YET RUN.

These are for routine production hardening but not blockers.

---

## See also

- `docs/journal/2026-05-09_ibc_launchd_supervisor.md` — full incident
  postmortem and two-pronged fix
- `docs/ops/MIGRATION_STATUS.md` — Phase 4 line points here
- `docs/agents/scripts/ibkr_health.sh` — per-30-min health check (will
  populate `~/.ibkr_health.log`)
- `~/Library/LaunchAgents/com.ibc.gateway.plist` — launchd safety net
