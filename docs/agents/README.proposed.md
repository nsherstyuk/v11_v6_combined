# Agent-to-agent comms protocol (proposed v2) — SUPERSEDED 2026-05-10

> **This proposal was superseded before adoption.** It tried to fix
> the two-agent comms protocol by tightening the review pattern.
> Subsequent discussion concluded the underlying two-agent
> coordination wasn't worth its overhead at all, and the project
> moved to a single-agent workflow instead. See `docs/workflow.md`
> for the current process and `CLAUDE.md` for the session-bootstrap
> entry point.
>
> The structured REVIEW_REQUEST / REVIEW_RESPONSE / pre-mortem
> patterns proposed here were carried forward into `docs/workflow.md`
> as the self-review templates — adapted for single-agent use rather
> than two-agent coordination.
>
> Original proposal follows for reference.

---

# Agent-to-agent comms protocol (proposed v2 — historical)

**Status:** PROPOSED 2026-05-10 by Cowork. Superseded before adoption
by the single-agent workflow in `docs/workflow.md`.

Light, file-based channel between AI agents working on this repo.
Designed for substantive review, second opinions, and institutional
memory that survives context resets — NOT for routine coordination or
rote ops.

This is NOT a chat replacement. Use chat for back-and-forth, ad hoc
questions, and decisions Nick wants to make in real time. Use the
inbox for: requested reviews of plans/artifacts before execution,
findings that should outlive a single session, and progress reports
on long-running work.

## Roles — the foundation

Two roles, mapped to two agents:

- **Claude Code = proposer/executor.** Has hands on the system. Drafts
  plans. Executes work. Owns artifacts. Requests review when about to
  do something with high cost-of-being-wrong.
- **Cowork = reviewer/challenger.** No hands. Reviews on request. The
  job is to find what's wrong with a plan before it ships, not to
  validate.

**Action commands (DIRECTIVEs) come from Nick only.** Cowork does NOT
issue DIRECTIVEs. This eliminates the entire "stale directive crossed
chat work" failure class observed 2026-05-09 / 2026-05-10.

## Files

| File | Purpose |
|---|---|
| `inbox_cowork.md` | Messages **TO** Cowork. Claude Code writes here. |
| `inbox_claude_code.md` | Messages **TO** Claude Code. Cowork writes here. |
| `inbox_cowork.flag` / `inbox_claude_code.flag` | One line, two ISO 8601 UTC timestamps: `<write_ts> <read_ts>`. For cheap unread polling. |
| `scripts/inbox_check.sh` | Returns exit 1 if the named inbox has unread content. |
| `decisions.log` | Append-only single-line entries from Claude Code when it takes a non-trivial action. Nick pastes recent lines into Cowork at session open. |

## Message types

Three types. Each is explicit about what response, if any, is expected.

- **REVIEW_REQUEST** — Claude Code → Cowork. "I'm about to do X. Here's
  the plan/diff/script. Tell me what's wrong with it." Expects exactly
  one REVIEW_RESPONSE.
- **REVIEW_RESPONSE** — Cowork → Claude Code. Structured reply to a
  REVIEW_REQUEST. Verdict: APPROVED, NEEDS_REVISION, or
  ESCALATE_TO_NICK.
- **NOTE** — either direction. Institutional memory or status update.
  No response expected by default. Used for findings, postmortems,
  observed limitations, anomalies, and heartbeats.

Heartbeats for long-running tasks are NOTEs with a fixed mini-format
— see "Long-running tasks" below.

## Status workflow

Each entry carries a status in its header, updated by appending a new
entry that references it:

- REVIEW_REQUEST: starts `OPEN`. Closed when its REVIEW_RESPONSE
  arrives with verdict `APPROVED` (→ `CLOSED`). With verdict
  `NEEDS_REVISION`, stays `OPEN` until a revised REVIEW_REQUEST is
  filed and re-approved. With verdict `ESCALATE_TO_NICK`, stays
  `OPEN` until Nick decides — Claude Code should also flag the
  escalation in chat so Nick sees it without polling.
- REVIEW_RESPONSE: starts `OPEN` if its verdict was NEEDS_REVISION or
  ESCALATE_TO_NICK; `CLOSED` if APPROVED.
- NOTE: starts `OPEN` if it embeds a question; `CLOSED` otherwise.

## Structured REVIEW_REQUEST format

```
## YYYY-MM-DD HH:MM TZ — [TOPIC] — REVIEW_REQUEST — OPEN
**From:** Claude Code

**WHAT:** [the proposed action or artifact — link or paste the diff/script/plist]
**WHY:** [intent and goal — one sentence]
**ALTERNATIVES:** [what was considered and rejected, and why — if none, say "none considered"]
**RISKS:** [what could go wrong, in your own honest assessment]
**REVIEW REQUEST:** [specific questions for Cowork to focus on]

---
```

The discipline of filling in ALTERNATIVES and RISKS in your own voice
is half the value. If you can't articulate alternatives, you haven't
considered them; if you can't articulate risks, you haven't earned the
proposal yet.

## Structured REVIEW_RESPONSE format

```
## YYYY-MM-DD HH:MM TZ — [TOPIC] — REVIEW_RESPONSE — [OPEN|CLOSED]
**From:** Cowork
**Re:** [REVIEW_REQUEST timestamp]

**Pre-mortem:** [one paragraph imagining the proposal is broken in three months — what was the cause? Then evaluate whether the proposal defends against that failure.]

**Per-item assessment:**
- [item from WHAT or REVIEW REQUEST] — AGREED | CHALLENGED | QUESTIONED
  - if CHALLENGED: severity (must-fix / should-fix / nit), specific concern, suggested resolution
  - if QUESTIONED: what additional info would resolve

**Verdict:** APPROVED | NEEDS_REVISION | ESCALATE_TO_NICK
[If ESCALATE: one-sentence summary of the disagreement for Nick.]

---
```

**Reviewer's discipline:** your job is to find what's wrong, not to
validate. The strongest version of your contribution is the strongest
case AGAINST the proposal. LLMs default to agreement — counter that
actively. If after honest review you find no must-fixes, APPROVE; do
not invent nits to look thorough.

## One-round limit

One round of review then commit. Claude Code accepts must-fixes,
justifies any rejections in a follow-up reply, and either re-requests
review (if NEEDS_REVISION) or proceeds (if APPROVED). If the agents
still disagree after that round, escalate to Nick — do not loop. Two
agents disagreeing twice means the question is genuinely ambiguous
and needs human judgment.

## When to invoke a review

Use REVIEW_REQUEST when cost-of-being-wrong is high:

- irreversible actions
- anything touching real money or unattended overnight automation
- architectural decisions with multiple viable approaches
- destructive sequences (kill / restart / migrate)
- postmortems

Skip Cowork for:

- rote ops (one-line edits, config bumps)
- decisions Nick has already made in chat
- diagnostics still in progress — request review after the diagnosis
  lands, not during

Rule of thumb: if you'd be comfortable explaining the action to your
future self in one sentence, no review needed. If you'd want to be
talked out of it, request a review.

## Bootstrap protocol — every session

When you (an AI agent) start a session in this repo:

1. Read THIS file.
2. Run `docs/agents/scripts/inbox_check.sh <your_name>`. Exit 0 →
   nothing new; skip to your task. Exit 1 → unread; proceed.
3. Read your own inbox bottom-up. Find OPEN entries.
4. **Verify before execute.** Before responding to anything, check
   whether the work has already been done in chat or another channel
   (recent git log, `decisions.log`, your own task tracker). If
   already done, append a closing entry referencing the original and
   the evidence; do not redo work.
5. Address remaining open entries in order.
6. After reading your inbox, bump the read timestamp on YOUR flag.
   After writing to the other agent's inbox, bump THEIR write
   timestamp. See "Flag-file protocol" below.
7. **Append-only.** Never edit past entries.

## In-context unread signal — UserPromptSubmit hook

Polling alone doesn't surface unread mail in-context during a long
session. Claude Code installs a `UserPromptSubmit` hook in
`.claude/settings.json` that runs `inbox_check.sh claude_code` before
each turn and prepends `INBOX UNREAD: read docs/agents/inbox_claude_code.md
before proceeding` to the prompt context if exit is 1. Without this
hook, the inbox can sit unread for hours during a long session — this
was the root cause of the 2026-05-10 comms-reliability failure.

Cowork's equivalent is the scheduled `cowork-inbox-poll` task firing
every 15 min during 7am–11pm Nick's local time.

## Flag-file protocol

Each flag file: one line, `<last_write_iso_utc> <last_read_iso_utc>`.

**Clock skew is real.** Cowork's sandbox clock can run tens of minutes
ahead of real UTC. Both bumps use a `max()` rule so the protocol
tolerates skew in either direction:

```
# After WRITING to inbox_<other>.md
read W R < docs/agents/inbox_<other>.flag
NOW="$(date -u +%FT%TZ)"
NEW_W=$([[ "$NOW" > "$R" ]] && echo "$NOW" || date -u -j -v+1S -f "%FT%TZ" "$R" +%FT%TZ)
printf "%s %s\n" "$NEW_W" "$R" > docs/agents/inbox_<other>.flag

# After READING inbox_<self>.md
read W R < docs/agents/inbox_<self>.flag
NOW="$(date -u +%FT%TZ)"
NEW_R=$([[ "$NOW" > "$W" ]] && echo "$NOW" || echo "$W")
printf "%s %s\n" "$W" "$NEW_R" > docs/agents/inbox_<self>.flag
```

Polling: `[[ "$W" > "$R" ]] && echo UNREAD || echo CLEAN`. Or call
`scripts/inbox_check.sh <self>` — exit 1 means unread.

## Long-running tasks — heartbeats

For autonomous processes >4h wall-clock (data downloads, paper
trading, multi-day backtests), the owning agent appends progress NOTEs
to the other agent's inbox:

| Expected duration | Cadence |
|---|---|
| 4–12h | one mid-task NOTE at ~50% |
| 12–48h | NOTE every 4–6h |
| > 48h | NOTE every 8h |

Each ≤ 5 lines: process + PID, elapsed, current step / progress
fraction, anomalies since last heartbeat, ETA to next milestone.
Mark `NOTE — OPEN` if a question is embedded, `NOTE — CLOSED`
otherwise.

For tasks <1h, a single completion NOTE is fine — no heartbeats
needed.

## Reducing Nick's routing burden

Two practices to keep him out of the middle:

1. **One channel per topic.** At the start of work on a topic, pick
   chat OR inbox. Don't mix. If a topic moves channels, write a NOTE
   flagging the move so context follows.
2. **Decisions log.** When Claude Code takes a non-trivial action
   (script installed, config changed, plist loaded, supervisor
   reconfigured), append a single line to `docs/agents/decisions.log`:

   ```
   2026-05-10T14:22Z claude_code daily_restart.sh installed at ~/Library/LaunchAgents/com.nick.daily-restart.plist (StartCalendarInterval 01:15)
   ```

   When Nick opens Cowork, he pastes the recent lines so Cowork knows
   what's already done. Replaces ad hoc verify-via-git-log for
   routine status.

## Tone and discipline

- **Be specific.** "What do you think of the script?" is bad. "Should
  `ThrottleInterval=300` be raised given the 5-min worst-case respawn
  delay observed in test B?" is good.
- **Brief.** Reviews longer than 3 paragraphs are usually drift into
  documentation — write a journal entry instead and link to it.
- **No chit-chat.** No "hi", "thanks", "let me know if I can help".
  Both ends are agents — pleasantries are noise.
- **Append-only.** Never edit past entries. The only safe pattern when
  two writers share files.
- **Always end an entry with `---`** so the next appender finds the
  boundary cleanly.

## Asynchrony — known limitation

Neither agent gets pinged when the other writes. The flow is "I write
→ next time the other agent has a session, they read it." For
high-frequency back-and-forth, paste-mediated chat is faster. The
file inbox shines for substantive review and findings that need to
survive context resets.

## Maintenance

- Inbox files are append-only and grow. If they exceed ~300KB,
  archive CLOSED entries to a dated archive (e.g.
  `inbox_cowork.archive_2026Q2.md`) and start the live file fresh.
  Don't archive OPEN entries.
- All inbox + flag + log files are tracked in git. Commit messages:
  terse, e.g. "agents: cowork → claude_code re: daily-restart review".

## What changed from v1

For the agent reading this who knew the old protocol:

- ASK / REPORT / DIRECTIVE → REVIEW_REQUEST / REVIEW_RESPONSE / NOTE.
- DIRECTIVEs no longer come from Cowork. Action commands originate
  with Nick (in chat, to Claude Code).
- REVIEW_REQUEST / REVIEW_RESPONSE have structured formats (WHAT /
  WHY / ALTERNATIVES / RISKS / REVIEW REQUEST → pre-mortem +
  per-item assessment + verdict).
- One-round limit on review threads; escalate to Nick on second
  disagreement.
- "Verify before execute" is now step 4 of bootstrap, not optional.
- `decisions.log` added to reduce Nick's routing burden.
- UserPromptSubmit hook is required for Claude Code (the missing
  piece that caused the 2026-05-10 failure).
