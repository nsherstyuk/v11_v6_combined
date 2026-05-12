# Agent-to-agent comms protocol — DEPRECATED 2026-05-10

> **This protocol is no longer in use.** After the 2026-05-10
> retrospective, the project moved from a two-agent inbox protocol to
> a single-agent workflow with structured self-review. See
> `docs/workflow.md` for the current process and `CLAUDE.md` for the
> session-bootstrap entry point.
>
> The inbox files (`inbox_cowork.md`, `inbox_claude_code.md`) and
> flag files in this directory are kept as historical record. New
> coordination does not happen here. The `inbox_check.sh` script and
> any launchd timers depending on it can be retired.
>
> The original protocol description follows for reference.

---

# Agent-to-agent comms protocol (historical)

Light, file-based correspondence channel between AI agents working on this
repo. Designed so that two sessions (e.g. Cowork on Anthropic's web side and
Claude Code in a terminal) can exchange substantive questions, second
opinions, and findings without Nick having to manually shuttle every message.

This is NOT a replacement for chat. It is for: substantive questions, status
reports across sessions, second opinions, and institutional memory that
survives context resets on either side.

## Files

| File | Purpose |
|---|---|
| `inbox_cowork.md` | Messages **TO** Cowork. Claude Code (or any other agent) writes here. |
| `inbox_claude_code.md` | Messages **TO** Claude Code. Cowork (or any other agent) writes here. |
| `inbox_cowork.flag` | One line, two ISO 8601 UTC timestamps separated by a space: `<write_ts> <read_ts>`. Bumped by the writer/reader of `inbox_cowork.md`. Used for cheap unread checks. |
| `inbox_claude_code.flag` | Same shape, for `inbox_claude_code.md`. |
| `scripts/inbox_check.sh` | Helper script: returns exit 1 if the named inbox has unread content, exit 0 otherwise. Logs unread events to `~/.inbox_polls.log`. |
| `shared/` | Status documents both agents update. Currently `docs/ops/MIGRATION_STATUS.md` plays this role for the migration; future status docs (e.g. `EQUITY_ORB_STATUS.md`) belong here. |

## Flag-file protocol (cheap polling)

The two `.flag` files are the cheap signal that lets either agent decide
whether to bother reading the full inbox. Each contains a single line:

```
<last_write_iso_utc> <last_read_iso_utc>
```

Example: `2026-05-09T08:42:15Z 2026-05-09T08:30:00Z`

**Important: clock skew.** Different agent environments may not have synchronized
clocks. The Cowork sandbox in particular runs ahead of real UTC by tens of
minutes. To stay robust against skew on either side, both bumps use a
`max()` rule:

```
# After WRITING to inbox_<other>.md
#   write_ts = max(my_now, existing_read_ts + 1 sec)
#   so we always strictly exceed read_ts even if my clock is behind theirs
read W R < docs/agents/inbox_<other>.flag
NOW="$(date -u +%FT%TZ)"
NEW_W=$([[ "$NOW" > "$R" ]] && echo "$NOW" || date -u -j -v+1S -f "%FT%TZ" "$R" +%FT%TZ)
printf "%s %s\n" "$NEW_W" "$R" > docs/agents/inbox_<other>.flag

# After READING inbox_<self>.md
#   read_ts = max(my_now, existing_write_ts)
#   so we always reach write_ts even if my clock is behind theirs
read W R < docs/agents/inbox_<self>.flag
NOW="$(date -u +%FT%TZ)"
NEW_R=$([[ "$NOW" > "$W" ]] && echo "$NOW" || echo "$W")
printf "%s %s\n" "$W" "$NEW_R" > docs/agents/inbox_<self>.flag
```

The simpler bash version when you trust both clocks (skip max if both
clocks are real):

```
# Trusted-clocks shorthand — only use if both agents use real wall-clock UTC.
read W R < docs/agents/inbox_<other>.flag
printf "%s %s\n" "$(date -u +%FT%TZ)" "$R" > docs/agents/inbox_<other>.flag
```

Polling logic — single string compare on ISO timestamps:

```
read W R < docs/agents/inbox_<self>.flag
[[ "$W" > "$R" ]] && echo UNREAD || echo CLEAN
```

Or just call `docs/agents/scripts/inbox_check.sh <self>` — exit code 1
means unread.

## Bootstrap protocol — every session

When you (an AI agent) start a session in this repo and the session involves
shared work:

1. Read THIS file (`docs/agents/README.md`).
2. Run `docs/agents/scripts/inbox_check.sh <your_name>`. Exit 0 = nothing
   new; skip to your task. Exit 1 = unread; proceed.
3. Read your own inbox (the one ADDRESSED TO you). Find OPEN entries.
4. Address them in order. When you respond:
   - Append your reply to the OTHER agent's inbox.
   - Reference the original by its timestamp.
   - The reply itself stays OPEN until the other agent acknowledges.
5. After reading your inbox, bump the read timestamp in your `.flag` file
   (preserve the write timestamp). After writing to the other agent's
   inbox, bump THEIR flag's write timestamp (preserve their read
   timestamp). See "Flag-file protocol" above for the exact one-liners.
6. When you have a new question or finding for the other agent, append it
   to their inbox.
7. **Append-only.** Do not edit past entries. Append-only is the only safe
   pattern when two writers share files.
8. When a thread is fully resolved, append a final CLOSED note (in either
   direction) referencing the thread anchor.

## Message format

```
## YYYY-MM-DD HH:MM TZ — [TOPIC] — [OPEN | ANSWERED | CLOSED]
**From:** <agent name>
**Re:** <thread anchor if responding to something specific, else omit>

[Body. 1–3 paragraphs max. Be specific. ASK, REPORT, or NOTE.
 Link to files rather than inline-pasting code unless tiny.]

---
```

Always end an entry with `---` so the next appender can find the boundary
cleanly.

## Tone and discipline

- **ASK, REPORT, or NOTE.** Three message types is enough.
  - **ASK** = you want a response.
  - **REPORT** = informational, no response required.
  - **NOTE** = institutional memory, fact you want preserved.
- **Be specific.** "What do you think of the download script?" is bad.
  "Should the download script's MAX_CONSEC_DISCONNECTS be raised from 5
  given today's IBC outage was 90 minutes? See line 105." is good.
- **Brief.** If you're writing more than 3 paragraphs, you're probably
  drifting from comms into documentation — write a journal entry instead
  and link to it.
- **No chit-chat.** No "hi", "thanks", "let me know if I can help".
  Both ends are agents — pleasantries are noise.

## Long-running tasks — heartbeat reports

For any task or autonomous process expected to take longer than 4 hours
of wall-clock time (data downloads, paper trading sessions, multi-day
backtests), the owning agent must append periodic progress NOTEs to the
other agent's inbox at the cadences below. This gives the other agent
(and the human) visibility without needing to reach out manually.

| Expected total duration | Heartbeat cadence |
|---|---|
| 4–12 hours | one mid-task NOTE at ~50% progress |
| 12–48 hours | NOTE every 4–6 hours |
| > 48 hours | NOTE every 8 hours |

Each heartbeat NOTE should include: process name + PID, elapsed time,
current step / progress fraction, anything anomalous since the last
heartbeat, and ETA to next milestone. Keep it under 5 lines.

Mark these as `NOTE — OPEN` if a question is embedded, otherwise
`REPORT — CLOSED` (no response required, just visibility).

For short tasks (single multi-step pieces of work completed within ~1
hour), a single completion REPORT at the end is sufficient — no
heartbeats needed.

## Asynchrony — known limitation

Neither agent gets pinged when the other writes. The flow is:
"I write → next time the other agent has a session, they read it." For
high-frequency back-and-forth, paste-mediated chat is still faster. The
file inbox shines for substantive questions and findings.

## Maintenance

- Inbox files are append-only and will grow. If they exceed ~300KB or
  get unwieldy, archive entries marked CLOSED into a dated archive file
  (e.g. `inbox_cowork.archive_2026Q2.md`) and start the live file fresh.
  Don't archive OPEN or ANSWERED entries.
- Both inbox files are tracked in git. Commit messages should be terse,
  e.g. "agents: cowork → claude_code re: launchd plist".
