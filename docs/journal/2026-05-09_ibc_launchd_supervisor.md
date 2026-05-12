# IBC + Gateway silent overnight outage — diagnosis and two-pronged fix

**Date:** 2026-05-09
**Status:** Both fixes landed; respawn test pending Nick's go-ahead.
**Files touched:** `~/Library/LaunchAgents/com.ibc.gateway.plist` (new),
Gateway's GUI Lock-and-Exit settings (manual edit by Nick),
`docs/ops/MIGRATION_STATUS.md` Phase 4 line.

---

## 1. Symptom

This morning Nick found IBC AND IB Gateway dead, port 4002 silent, the
overnight US-equities download script crashed having marked 14 of 15
tickers "crashed" with `ConnectionError: Not connected`. Gateway had
been gone for ~7 hours (23:45 EDT shutdown → 07:04 EDT manual relaunch).

The download script's own reconnect-on-disconnect logic was already in
place from earlier this morning (commit pending) — it correctly
attempted reconnects and gave up only after IBKR Gateway went fully
unreachable. The actual gap was one layer up: nothing supervised IBC.

## 2. Diagnostic chain

Read order: Friday IBC log → IBC config → Mac sleep history → kernel /
oom logs → Gateway persistent settings.

### What killed IBC

`~/ibc/Logs/ibc-3.23.0_GATEWAY-10.46_Friday.txt` tail:

```
2026-05-08 23:40:30  IBC: detected frame "DU1558484 Exit Session Setting (Simulated Trading)"; event=Opened
2026-05-08 23:45:00  IBC: detected dialog "Shutdown progress"; event=Opened
2026-05-08 23:45:00  IBC: CommandServer closing / shutdown
IBC returned exit status 0
Finding autorestart file
autorestart file not found: full authentication will be required
Gateway finished
```

Verdict: **scheduled IBKR server-side auto-logoff at 23:45 EDT**, IBC
handled it cleanly (exit 0), no autorestart token was written to disk
beforehand, IBC chose not to attempt re-auth (would need 2FA), parent
script tree exited, no supervisor caught it. Not a crash, not OOM, not
a 2FA timeout in the conventional sense.

### Ruled out

- **Mac sleep:** the Mac DID enter Deep Idle later that night (woke
  07:00:49 EDT from `NUB.SPMI0Sw3IRQ` — power button / lid). Sleep was
  a CONSEQUENCE not the cause: IBC died at 23:45, sleep happened later.
- **Reboot:** `last reboot` shows last boot Tue 2026-05-05 21:11; uptime
  was 3d 10h continuous through the incident → no reboot.
- **OOM / jetsam:** `log show --predicate 'eventMessage CONTAINS "killed"
  OR ... "lowmem" OR "jetsam"'` for 23:40–23:50 → empty.
- **2FA timeout:** never attempted. IBC chose not to attempt re-auth
  without a token, so no prompt was generated to time out.

### Why the configured `AutoRestartTime=22:00` didn't help

`~/ibc/config.ini` has `AutoRestartTime=22:00` and `ColdRestartTime=14:00`.
IBC pushed those values to Gateway's UI at startup (Friday log line
20:31:48: `IBC: Setting Auto restart time`). **But the 22:00 ET
auto-restart never fired** — there is no 22:00 event in the Friday log
between launch (20:31) and the 23:45 logoff, and no `autorestart` token
was found on disk in `~/ibc`, `~/Jts`, or `~/Applications/IB*`.

**Hypothesis (Cowork concurred):** IBC 3.23.0's "Setting Auto restart
time" routine works via GUI automation against Gateway's Lock-and-Exit
dialog. IBC's check is "did I issue the click", not "did the value
persist". Gateway 10.46 likely renamed/moved the target widget; IBC
clicks a phantom and logs success. Known IBC failure pattern, typical
lag is 1–4 weeks after a Gateway minor bump before IBC publishes a
patch.

We did not deterministically confirm via `~/Jts/jts.ini` because Nick's
GUI check + direct fix made the question moot. (`ibg.xml` is encrypted
with the `IBGZENC` magic prefix; `jts.ini` would have been the right
file.)

## 3. Fixes — two layers of defense

### Layer 1 — Gateway UI auto-restart, set directly (Nick, 2026-05-09)

Bypasses IBC's GUI-automation path entirely. Nick opened Gateway's UI
manually and set the Auto-Restart time to 22:00 in the Lock-and-Exit
panel. With this, the autorestart token is written by Gateway itself
on the way down, so the next morning's startup picks up the session
without 2FA — exactly what the IBC config was supposed to deliver.

**New single point of failure to be aware of:** Gateway's UI value can
be reset by future updates, and there is no guarantee IBC's
`AutoRestartTime=22:00` will start working again silently in a later
release. **If we ever see another silent overnight outage, FIRST check
is whether Gateway's UI Auto-Restart value is still set.**

### Layer 2 — launchd supervisor for IBC

`~/Library/LaunchAgents/com.ibc.gateway.plist` — new. Properties:

- `Label = com.ibc.gateway`
- `ProgramArguments = [/Users/mykolasherstyuk/ibc/gatewaystartmacos.sh]`
- `RunAtLoad = true`, `KeepAlive = <true/>` (unconditional —
  IBC's clean exit IS the failure mode we're guarding against;
  `{SuccessfulExit: false}` would NOT have caught last night's exit 0)
- `ThrottleInterval = 300` (5 min — Cowork's recommendation. Realistic
  worst case is a 2FA prompt loop when ColdRestartTime fires while Nick
  isn't at his phone. At 60s that's ~60 prompts/hour, IBKR may flag the
  account; at 300s it caps at ~12/hour.)
- `EnvironmentVariables.PATH = /opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`
  (TWS_MAJOR_VRSN and IBC_PATH are set by the script itself, not duplicated
  in the plist)
- `WorkingDirectory = /Users/mykolasherstyuk/ibc`
- `StandardOutPath = /Users/mykolasherstyuk/ibc/Logs/launchd-stdout.log`
- `StandardErrorPath = /Users/mykolasherstyuk/ibc/Logs/launchd-stderr.log`
- `AbandonProcessGroup = <true/>` (so `launchctl unload` doesn't rip
  Gateway down mid-flight)

Manual control (also in the plist's comment header):

```
launchctl unload -w ~/Library/LaunchAgents/com.ibc.gateway.plist  # disable
launchctl load   -w ~/Library/LaunchAgents/com.ibc.gateway.plist  # enable
launchctl list | grep com.ibc.gateway                              # status
```

**Important framing — launchd is the safety net, NOT the root cause
fix.** Without Layer 1, launchd would respawn IBC nightly at 23:45 ET
into a 2FA prompt that times out, and Nick would get prompted every
midnight. The two layers are both required: Layer 1 prevents the
nightly outage, Layer 2 catches anything else (crashes, OOM, hangs,
Gateway UI value being reset by future updates).

## 4. Verification (so far)

- Plist installed, `plutil -lint` clean, `launchctl list` shows
  `com.ibc.gateway` registered.
- RunAtLoad fired immediately on install. With Gateway already running,
  the script's port-guard (`lsof -nP -iTCP:4002 -sTCP:LISTEN`) detected
  the existing listener and exited 1 with the expected message
  "ABORT: port 4002 (paper API) already listening". Logged to
  `~/ibc/Logs/launchd-stdout.log`. ThrottleInterval will pace retries
  every 5 min until the existing Gateway eventually exits.
- Gateway port 4002 still listening (java PID 48286, untouched by the
  plist install).

**Pending: kill-and-respawn test.** Nick's original Step 3 plan was:
```
kill $(pgrep -f ibcalpha)
sleep 90
pgrep -fl ibcalpha   # should show a NEW PID
```
This will trigger a fresh login → 2FA prompt. Deferred until Nick has
phone in hand. Once executed and the new PID confirms launchd respawned
the chain, Layer 2 is fully proven.

## 5. Process notes

- Today was the first substantive use of the agent-comms inbox channel
  (`docs/agents/inbox_*.md`). Cowork answered both my open questions
  (the IBC widget hypothesis + the KeepAlive choice) and relayed Nick's
  approval of the plist with the ThrottleInterval=300 amendment, which
  let me do this work without Nick paste-shuttling each round-trip.
- Clock-skew between sides surfaced: Cowork's flag write_ts ran ~50min
  ahead of real UTC, so the polling protocol's strict-greater-than
  comparison reported UNREAD even after I'd actually read. Worked
  around locally with `read_ts = max(now, write_ts)`; flagged to
  Cowork to fix at source.

## 6. Memory / takeaways

- IBC's "Setting X" log line means "I issued the click", not "the value
  persisted". For any IBC-driven Gateway setting, the source-of-truth
  is Gateway's GUI (or `~/Jts/jts.ini` for plaintext settings), not
  IBC's launch log.
- IBC version skew vs Gateway minor bumps is a recurring failure mode.
  Watch for it after every Gateway update. Symptom: silent overnight
  outage with `autorestart file not found` in the IBC log.
- `KeepAlive = <true/>` (unconditional) is the right choice for any
  process whose CLEAN exit is the failure mode you care about.
  `{SuccessfulExit: false}` is wrong here even though it's the more
  common idiom.
- Per-direction flag-file polling needs skew tolerance. Adopt
  `read_ts = max(now, observed_write_ts)` as the bump rule on both
  sides if Cowork's clock skew persists.

## See also

- `docs/journal/2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md`
  — earlier IBC incident; same `AutoRestartTime` config landed on
  Windows there. The Mac side needs the launchd supervisor to match
  what Windows had via the `StartGateway.bat` parent process.
- `docs/agents/inbox_cowork.md` and `inbox_claude_code.md` — full
  Q&A thread for today's diagnosis.
- `~/Library/LaunchAgents/com.ibc.gateway.plist` — the artifact.
- `docs/ops/MIGRATION_STATUS.md` — Phase 4 line updated to point here.
