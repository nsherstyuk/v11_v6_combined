# Daily-restart architecture — moving away from the autorestart-token approach

**Date:** 2026-05-10
**Status:** Implemented + dry-run validated. First scheduled fire: 2026-05-11 01:15 EDT.
**Files:** `~/Library/LaunchAgents/com.nick.daily-restart.plist` (new),
`docs/agents/scripts/daily_restart.sh` (new), `~/ibc/config.ini` (edited),
`docs/ops/ibc_config.mac.template.ini` (edited),
`docs/ops/MIGRATION_STATUS.md` Phase 4 (edited),
`docs/ops/IBC_TEST_PLAN.md` (Test F added).

---

## 1. Why we changed approach

The 2026-05-08 silent overnight outage and the follow-up work over
2026-05-09 / 2026-05-10 surfaced multiple ways the IBKR autorestart-
token mechanism is fragile on this Mac:

- **IBC 3.23.0 / Gateway 10.46 widget mismatch.** IBC's "Setting Auto
  restart time" GUI automation logs success but the value doesn't
  actually persist into Gateway. Discovered when `AutoRestartTime=22:00`
  in `~/ibc/config.ini` had been set for ~13h before 2026-05-09 22:00
  EDT and didn't fire.
- **Same-day-set behavior.** A value entered into Gateway's UI directly
  doesn't fire on the same calendar day it was set. Workaround used on
  2026-05-09: re-enter the value mid-evening to get a same-day fire.
  Brittle if the window of opportunity is missed.
- **Mid-day disconnects from unrelated causes.** On 2026-05-10 ~09:32
  EDT a credential rejection cascade brought Gateway down, requiring
  fresh 2FA on respawn. The autorestart-token mechanism only protects
  the scheduled-restart path, not arbitrary disruptions.
- **Vendor-dependent semantics.** The token mechanism is internal to
  IBKR's binary; we have no source-level visibility into when it
  writes / consumes / invalidates the token. Hard to debug.

## 2. The new architecture

**A single OS-level cron at 01:15 ET is the only restart scheduler.**
Everything else stays in place as best-effort hardening:

```
                           ┌─────────────────────────┐
   01:15 ET daily   ──────►│  com.nick.daily-restart │ (cron)
                           └────────────┬────────────┘
                                        │
                                        ▼
                       ┌────────────────────────────────┐
                       │   docs/agents/scripts/         │
                       │   daily_restart.sh             │
                       │                                │
                       │   1. pkill v11.live.run_live   │
                       │   2. unload com.ibc.gateway    │
                       │   3. pkill ibcalpha            │
                       │   4. verify clean              │
                       │   5. reload com.ibc.gateway    │
                       │      → respawns                │
                       │      gatewaystartmacos.sh      │
                       │   6. poll port 4002 ≤5min      │
                       │   7. start v11 via             │
                       │      caffeinate -i             │
                       │   8. log SUCCESS               │
                       └────────────────────────────────┘

   Mid-day safety net (unchanged):
       com.ibc.gateway.plist with KeepAlive=true,
       ThrottleInterval=300 — catches any unexpected
       Gateway exit, respawns gatewaystartmacos.sh,
       2FA may be required if no token (acceptable
       since this is exception-path only).
```

### Why 01:15 ET specifically

- After IBKR's broker-side daily auto-logoff (configurable up to 01:00
  ET — Nick has set Gateway-UI auto-logoff to 01:00 ET).
- 15 min buffer for the broker's cleanup to settle before our restart.
- ~3 hours before v11 ORB trade window (08:00 UTC = 04:00 EDT). Any
  startup pathology has time to be noticed and recovered before
  trading begins.

### Why kill IBC instead of relying on Gateway's auto-restart

- Eliminates the silent-failure surface (widget mismatch, same-day-set,
  token consumed/lost edge cases).
- launchd reload is mechanically simple and observable: process tree
  goes down, port stops listening, port starts listening, new PID.
  Each transition is verifiable in seconds.
- If something is wrong (network, IBKR side), the failure is clean and
  visible — we get a NOTE in `inbox_cowork.md` rather than silent
  drift.

## 3. What stays in place

- **`com.ibc.gateway.plist` (KeepAlive supervisor).** Still needed for
  mid-day disruptions (Mac sleep, IBKR session rejection, Gateway
  crash). The 2026-05-10 09:33 EDT recovery was caught by exactly this
  mechanism.
- **launchd supervision-handle limitation.** Known: `gatewaystartmacos.sh`
  exec-forks into Java and exits, so `launchctl list` shows PID `-`
  for `com.ibc.gateway`. Functionally still works because KeepAlive
  fires on "no managed process running", just with up to 5min of
  detection lag. Documented in the prior journal entry; not blocking.
- **Downloader's reconnect logic.** With `RECONNECT_MAX_ATTEMPTS=10`
  (bumped 2026-05-10), the script can survive the daily 01:15 ET
  cycle without abandoning a ticker. Worst-case 9.5 min reconnect
  budget vs ~1-3 min outage during the restart window.

## 4. What was tested

**Dry-run 2026-05-10 09:59 EDT — PASSED.**

```
[09:59:29] daily_restart.sh START
[09:59:29] [1] no v11 running — skip
[09:59:29] [2] unloading launchd com.ibc.gateway plist
[09:59:31] [3] killing IBC + Gateway
[10:00:01] [4] verifying clean state — clean
[10:00:01] [5] re-loading launchd com.ibc.gateway plist
[10:00:16] [6] port 4002 listening after 30s
[10:00:16] [7] starting v11.live.run_live
[10:00:46]     v11 confirmed alive
[10:00:46] [8] SUCCESS — Gateway PID=66877 v11 PID=66898
```

Total cycle: 77 seconds. **Notably, no 2FA was needed** — IBKR's
broker-side session was still warm when IBC re-launched, so the
respawn was unattended. This means the cron should run cleanly at
01:15 ET every night without Nick at his phone.

## 5. Failure modes + responses

| Symptom | Likely cause | Auto-response |
|---|---|---|
| `[FAIL] ibcalpha still alive after 30s sleep` | pkill didn't reach the process (e.g., process is unresponsive Java) | Script aborts before reload, exit 1, NOTE not appended (state ambiguous, manual triage needed) |
| `[FAIL] port 4002 still not listening after 5 min` | 2FA needed and Nick wasn't at phone, or IBKR-side problem | Script appends a `daily-restart-failure-YYYY-MM-DD` NOTE to `inbox_cowork.md`, exit 1. Cowork sees on next inbox poll, triages with Nick. |
| `[FAIL] v11 not alive 30s after launch` | Python startup error (uncommon — would surface in logs) | Script exits 1. Manually inspect `~/.v11_paper.log`. |

## 6. How to monitor

- **Live log during a fire:** `tail -f ~/.daily_restart.log`
- **Last-fire summary:** `tail -20 ~/.daily_restart.log` (each cycle is
  ~10 lines)
- **launchd's own stderr:** `~/.daily_restart.err.log` (should be empty
  on success)
- **v11 paper run:** `~/.v11_paper.log`, PID at `~/.v11_paper.pid`
- **Inbox failure NOTE:** `~/code/ibkr_grok_wing_agent/docs/agents/inbox_cowork.md`
  (auto-appended only on failure)

## 7. How to disable

```
# Disable the cron entirely (e.g., for an unattended weekend):
launchctl unload -w ~/Library/LaunchAgents/com.nick.daily-restart.plist

# Re-enable:
launchctl load -w ~/Library/LaunchAgents/com.nick.daily-restart.plist

# Manual fire (for testing):
bash ~/code/ibkr_grok_wing_agent/docs/agents/scripts/daily_restart.sh
```

## 8. Open follow-ups

- **Health-check script** `docs/agents/scripts/ibkr_health.sh` + 30-min
  launchd timer — separate DIRECTIVE from Cowork (`ibc-test-plan`,
  not yet built). Adjacent to this work, will land soon.
- **Q2 self-pinging heartbeats** — separate plan from Cowork's retro
  ANSWER. Marker-file pattern at `~/.long_running_processes/`. Not
  blocking the daily-restart deployment.
- **Tomorrow's 01:15 ET first-real-fire.** First production validation.
  If anything goes wrong: failure NOTE auto-appears in inbox_cowork.md,
  triage Monday morning.

## See also

- `docs/journal/2026-05-09_ibc_launchd_supervisor.md` — the immediate
  predecessor incident + first-pass fix
- `docs/journal/2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md`
  — original incident that started the IBC supervision rabbit hole
- `docs/agents/inbox_claude_code.md` — Cowork's `daily-restart-architecture`
  DIRECTIVE 2026-05-10
- `docs/ops/IBC_TEST_PLAN.md` Test F — the test case for this script
