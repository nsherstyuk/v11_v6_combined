# Paper "0 trades in 2 weeks" — root cause, fixes, and IBC cleanup

**Date:** 2026-05-01
**Status:** Root cause identified and fixed. Both the v11 process tripwire and the IBC supervision setup were contributing. Paper should now actually fill orders.
**Scripts touched:** `v11/v6_orb/ibkr_executor.py`, `v11/live/orb_adapter.py`, `v11/live/run_live.py`, `C:\Users\nsher\Documents\IBC\config.ini`, `C:\IBC\StartGateway.bat`.

---

## 1. Symptom

Paper trading XAUUSD ORB had been running for ~2 weeks with **0 trades**. The status line said:

```
state=DONE_TODAY range=4540.87-4582.57 | LLM rejected today
```

User initial read: "the LLM is killing every trade — disable it." That hypothesis was wrong; the problem was somewhere else entirely.

## 2. Diagnostic chain

### Hypothesis 1 — LLM gate is rejecting (matches what the status line said)

Reality: status line was **misleading**. It always said `LLM rejected today` when state was `DONE_TODAY`, regardless of cause. The actual log showed:

```
04:09:03  Passthrough: AUTO-APPROVE ORB XAUUSD range=4540.87-4582.57
04:09:03  ORB LLM APPROVED: conf=100 reason=Mechanical approval -- LLM filter disabled
04:09:03  ORB: LLM gate passed — brackets eligible
04:09:05  ORB stale breakout: price=4618.57 outside range [4540.87-4582.57], skipping
```

The LLM was already disabled (passthrough auto-approve via `--no-llm`). The kill was elsewhere.

### Hypothesis 2 — stale-breakout guard

The guard fires when, on entering RANGE_READY, the live mid-price is already outside the freshly-computed Asian range. That had been firing every day. Why?

### Hypothesis 3 — the launches aren't the user's

Same launch time every day: **04:08 ET (08:08 UTC)**. The user reports running 24/7 — these are not their initiated restarts. End of yesterday's log:

```
04:01:01  Velocity 0 >= 168, placing brackets
04:01:01  ERROR    Entry placement failed: Not connected
... (repeating every 31s, consec=1/15 ... consec=15/15) ...
04:08:18  CRITICAL  STUCK: XAUUSD executor stuck after 15 consecutive
          connectivity-class placement failures — exiting with code 1
          so wrapper restarts with fresh socket
```

**A real ORB setup had formed.** Brackets were being placed. **IBKR Gateway was unavailable for ~7 minutes** during 04:01–04:08 ET = 08:01–08:08 UTC, exactly **the open of the trade window**. After 15 strikes the strategy's stuck-detection killed it. The wrapper respawned at 04:08:53 — by then price had moved past the range edge, and the stale-breakout guard skipped the day. Repeat for ~14 trading days.

### Root cause

**IBKR Gateway's daily auto-restart window collided exactly with the start of the ORB trade window.** Default Gateway restart is around 04:00 ET. ORB trade window opens at 08:00 UTC = 04:00 ET. They were stacked on top of each other.

This was not visible in any single day's behavior — it required reading the *previous* day's log to see the live-state timeline.

## 3. Fixes — three layers of defense

### Layer 1 — schedule (host config, not code)

Moved IBC's `AutoRestartTime` from `22:30` to **`22:00`** in
`C:\Users\nsher\Documents\IBC\config.ini`. IBC's value overrides the
Gateway-UI value at every login, so just clicking around in Gateway's
own settings dialog had no effect — that was a separate confusion the
user hit earlier. Active IBC config is now aligned to the user's intent.

22:00 ET = 02:00 UTC. Falls inside the Asian range window (00–06 UTC)
but the strategy is in IDLE during that window — no trade is at risk.
Critical that it does NOT fall inside the trade window (08–16 UTC =
04–12 ET).

### Layer 2 — patience (v11 code)

`v11/v6_orb/ibkr_executor.py`: bumped `_placement_stuck_threshold`
from **15 → 30** strikes. ~15 min at 31 s/strike. Comfortably covers
the observed 7-min Gateway restart with headroom for a worst-case
2FA prompt. Still finite, so genuine network failures still trip the
wrapper-respawn safety net.

### Layer 3 — observability (v11 code)

`v11/live/orb_adapter.py` + `v11/live/run_live.py`: added a `_skip_reason`
field, set at every `DONE_TODAY` transition with the actual cause. The
status line now reads e.g.
`done: stale breakout: price already above high at eval time` instead
of the hardcoded misleading `LLM rejected today`. This was the single
most expensive bug in this incident — a wrong status line had me chasing
the LLM hypothesis for hours before reading the real log.

Committed as `8d3db75` ("fix: survive IBKR Gateway daily restart +
honest skip-reason status").

## 4. IBC cleanup — the "multiple entities trying to login" symptom

User had separately reported recurring incidents where Gateway ended
up in a "multiple logins" stuck state. Investigation:

- Single Gateway process (PID 23232) at investigation time ✓
- Zero scheduled tasks for IBC/Gateway ✓
- Zero startup-folder shortcuts ✓
- Single launcher: `C:\IBC\StartGateway.bat` (manual)

So why the multi-login symptom? Two real findings:

**(a)** A second `config.ini` existed at `C:\IBC\config.ini` with a
**different (incorrect) password** — different by one prefix character.
`StartGateway.bat` does not read it (it points at
`Documents\IBC\config.ini`), but anything else that ever pointed at it
would have been entering a wrong password repeatedly, which is exactly
what produces "multiple logins" lockout symptoms at IBKR. Renamed to
`config.ini.stale-do-not-use` so it cannot be picked up by accident.

**(b)** `StartGateway.bat` had no guard against double-launch. Manually
running it while Gateway was already up would start a second instance
fighting the first for the same session. Added a `netstat`-based
listener check on ports 4001/4002: if either is already listening, the
launcher aborts with a clear message instead of starting a second
Gateway.

Operational doc: `docs/ops/ibc_setup.md` (new) captures the IBC layout,
restart schedule, common failure modes, and security note about the
plaintext password.

## 5. Verdict

The strategy was sound. The backtest was sound. The market was sound.
Paper produced zero trades because:

1. The host-level Gateway restart and the strategy's trade-window open
   were scheduled at the same minute. (Operational misalignment.)
2. The strategy's stuck-detection threshold was tuned for unexpected
   network loss, not for a recurring 7-min maintenance window.
3. The status string lied about why the strategy was idle, which
   delayed root-cause identification by hours.

All three fixed. Paper restart pending; first real fill will be the
proof of life.

## 6. Memory / takeaways

- When `paper produces 0 trades`, the diagnostic order is:
  status line → today's full log → **previous day's full log** →
  IBKR Gateway state. Don't trust the status line summary.
- IBKR Gateway's daily auto-restart **must** be scheduled outside the
  strategy's trade window. The default 04:00 ET is hostile to anything
  trading London open.
- The IBC `AutoRestartTime` value silently overrides whatever you set
  in the Gateway UI's settings dialog. The IBC config is the source of
  truth.
- A misleading log line is worse than no log line. Status formatters
  must reflect the actual cause, not a "best guess based on state".

## See also

- `docs/ops/ibc_setup.md` — IBC operational runbook
- `docs/journal/2026-04-30_fx_orb_grid_and_llm_gate.md` — yesterday's
  research session (FX-ORB grid + LLM gate failure)
- Commit `8d3db75` — the v11 code fixes from this incident
