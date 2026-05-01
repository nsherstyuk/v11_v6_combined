# IBC / IB Gateway operational setup

Operational runbook for how IBC supervises IB Gateway on this machine.
Lives outside `v11/` because IBC is host-level infrastructure, not v11
code, but this file lives inside the repo so the configuration
decisions survive box rebuilds and onboarding.

## Layout

| Path | Role |
|---|---|
| `C:\IBC\StartGateway.bat` | Launcher. Manual run only. |
| `C:\IBC\Stop.bat` | Clean shutdown. |
| `C:\Users\nsher\Documents\IBC\config.ini` | **Active** IBC configuration. |
| `C:\IBC\config.ini.stale-do-not-use` | Renamed dead config (see history below). |
| `C:\IBC\Logs\` | IBC logs. |
| `C:\Jts\ibgateway\1041\` | Gateway 1041 install. |
| `C:\Jts\` (root) | Gateway settings (`jts.ini`, etc.). |

`StartGateway.bat` line 35 hardcodes `set CONFIG=%USERPROFILE%\Documents\IBC\config.ini`.
That is the only config IBC ever reads.

## Restart schedule

- **Gateway auto-restart: 22:00 ET** (driven by IBC `AutoRestartTime=22:00`
  in active config). IBC overrides whatever is set in the Gateway UI, so
  changing only the UI value will not stick.
- **Cold restart (weekly, full re-login including 2FA): 14:00 ET** Sundays
  (`ColdRestartTime=14:00`). Off-market.
- These two times are **chosen** to avoid the v11 ORB trade window
  (08:00–16:00 UTC = 04:00–12:00 ET). Picking 04:00 ET (the IBKR-default
  window) collides exactly with the trade-window open and was the root
  cause of the 2026-04-30 zero-trades-for-2-weeks incident.

## v11 live tolerance

`v11/v6_orb/ibkr_executor.py` carries a stuck-detection tripwire that
exits the live process after N consecutive connectivity-class placement
failures so the wrapper can respawn with a fresh socket. As of
2026-05-01 the threshold is **30 strikes (~15 min at 31 s/strike)**,
which is comfortably more than IBKR Gateway's daily restart takes
(~7 min observed). Earlier value (15 strikes) was too tight and killed
the process every morning.

## Common failure modes and what they look like

| Symptom | Likely cause | Fix |
|---|---|---|
| Gateway prompts for 2FA repeatedly even after entering | Two IBC supervisors running | Check `tasklist | grep java`, kill duplicates |
| "Multiple users connected" notification | Double-launch via `StartGateway.bat` while already running | The 2026-05-01 port-listening guard in StartGateway.bat now aborts before this happens |
| Gateway logs in then immediately logs back out, repeats | Bad password in IBC config | Check `IbPassword=` in active config |
| v11 placement spam "Not connected" 7+ minutes around 04:00 ET | Gateway's UI auto-restart was at 04:00, not aligned with IBC | Confirm `AutoRestartTime` in active IBC config, not just Gateway UI |
| Two `java.exe` Gateway processes | A previous Gateway didn't terminate before a relaunch | Use Task Manager to kill both, then launch fresh |

## Diagnostic commands

```
tasklist | findstr java               # how many Gateway processes are alive
netstat -ano | findstr ":4002 "       # paper API port (4001 = live)
type "%USERPROFILE%\Documents\IBC\config.ini" | findstr "AutoRestartTime ColdRestartTime ClosedownAt CommandServerPort"
```

## Change history

- **2026-05-01** — IBC restart aligned to 22:00 ET; stale duplicate
  config in `C:\IBC\` renamed to `.stale-do-not-use` (had wrong password,
  likely cause of "multiple entities trying to login" lockouts when
  invoked accidentally); `StartGateway.bat` got a port-listening guard
  to prevent double-launch. See journal
  `2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md`.

## Security note

`IbPassword=` is stored in plaintext in the active IBC config. This is how
IBC works. **Do not back up, sync, or copy `Documents\IBC\config.ini` to
shared storage** (OneDrive, Dropbox, Google Drive, public git remotes,
shared backups, etc.) without first redacting the password line.
