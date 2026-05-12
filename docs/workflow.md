# Workflow — single-agent edition

How Claude Code works in this repo, post the two-agent retrospective
(2026-05-10). The previous protocol (`docs/agents/README.md`) tried
to coordinate two AI agents through file-based inboxes; in practice
the coordination overhead exceeded the value. This doc replaces it.

The shape: one executor (Claude Code), structured self-review for
high-stakes work, occasional ad-hoc external review when the cost of
being wrong is high. Nick directs; Claude Code executes; the journal
records.

## The discipline

Two execution modes, picked by cost-of-being-wrong, not by complexity:

**Routine mode** — for one-line edits, config bumps, mechanical
changes, anything where being wrong costs minutes and is reversible.
Just do the work. Update the journal at session end if it's worth a
record.

**High-stakes mode** — for irreversible actions, anything touching
real money or unattended automation, architectural choices with
multiple viable shapes, destructive sequences (kill / restart /
migrate), or plans that span multiple sessions. Use the plan → review
→ execute pipeline below.

Rule of thumb: if you'd be comfortable explaining the action to your
future self in one sentence, routine. If you'd want to be talked out
of it, high-stakes.

## High-stakes pipeline

For high-stakes work, four steps:

1. **Plan.** Write a short plan to
   `docs/superpowers/plans/YYYY-MM-DD-topic.md`. Use the plan template
   below. Filling in the ALTERNATIVES and RISKS sections honestly is
   half the value — if you can't articulate alternatives, you haven't
   considered them; if you can't articulate risks, you haven't earned
   the proposal.

2. **Self-review.** Append a self-review to the same file (or a
   companion file in `docs/superpowers/reviews/`). Use the review
   template below. The pre-mortem is the centerpiece — imagine the
   plan is broken in three months and write down what caused it.
   Then check: does the plan defend against that failure?

3. **Optional external review.** If the stakes are high enough that
   one set of eyes feels insufficient, tell Nick. He can:
   - Paste the plan into a different model (ChatGPT, Gemini) for
     cross-model adversarial review — this is the highest-value form
     of second opinion because the perspective is genuinely
     independent.
   - Open a fresh Cowork session, paste the plan, ask for review.
     Lower-value than cross-model (same model class) but useful
     when the plan is repo-specific and benefits from fresh eyes
     without your reasoning.
   - Decide that single-pass self-review is enough.

4. **Execute, then journal.** After execution, write or append to
   `docs/journal/YYYY-MM-DD_topic.md` with what actually happened,
   what changed vs. the plan, and any institutional memory worth
   preserving. Update `docs/PROJECT_STATUS.md` if the work changes
   project state.

## Plan template

```markdown
# YYYY-MM-DD — [topic]

**Status:** PROPOSED | UNDER_REVIEW | APPROVED | EXECUTING | DONE | ABANDONED

## What
[The proposed action or artifact. Link or paste the diff/script/plist if
the plan is to install something concrete.]

## Why
[Intent and goal — one or two sentences. Why does this need to happen now?]

## Alternatives considered
[What else was on the table. Why was each rejected?
If "none considered" is the honest answer, write that — but it's a sign
to slow down.]

## Risks
[What could go wrong, in your honest assessment. Don't sandbag this
section to make the plan look safer.]

## Verification
[How will you know it worked? What's the explicit pass criterion?]

## Rollback
[If this lands and turns out wrong, how do you back it out?
"Not rollback-able" is a real answer that changes how careful to be.]
```

## Self-review template

Append to the same plan file under a `## Self-review` section, or
create `docs/superpowers/reviews/YYYY-MM-DD-topic-review.md` for
larger work.

```markdown
## Self-review

**Pre-mortem.** Imagine it's three months from now and this plan
caused a real problem. What was the cause?
[One paragraph.]

Does the plan as written defend against that failure?
[YES / PARTIALLY / NO. If not YES, revise the plan before executing.]

**Per-item check:**
- WHAT — is the proposed action the simplest thing that solves the
  problem? Is anything bundled in that should be a separate plan?
- WHY — is the goal still right after writing the plan, or did the
  plan reveal a different goal?
- ALTERNATIVES — was each rejection well-reasoned, or does one
  deserve another look?
- RISKS — is anything missing? In particular: silent failure modes,
  things that only manifest under load or overnight, things that
  require Nick at the keyboard.
- VERIFICATION — is the pass criterion observable without ambiguity,
  or does it rely on subjective judgment?
- ROLLBACK — if rollback is hard, is the plan worth the cost?

**Verdict:** PROCEED | REVISE | ABANDON | NEEDS_NICK_INPUT
```

## When to escalate to Nick

Escalate (in chat, not in a file) when:

- Self-review surfaces a concern you can't resolve.
- The plan touches money, live trading, or anything irreversible
  beyond what the original directive covered.
- You're about to deviate from a directive Nick gave in chat.
- A directive crossed work that's already been done (verify first
  via journal / git log / `decisions.log`; if confirmed, surface it
  in chat before re-doing).
- A plan you self-reviewed comes back NEEDS_NICK_INPUT.

Escalation is a chat message, brief and specific. "Daily-restart
plan: rollback story is weak — if v11 fails to start at 01:18 there's
no automated alert until morning. Want me to add a 30-min
post-restart health check before installing the cron?"

## What to journal

The journal is the durable record of what happened. One entry per
session at minimum, more if multiple distinct topics were touched.

Format: `docs/journal/YYYY-MM-DD_topic.md` — date + a short topic
slug (e.g. `2026-05-10_daily_restart_architecture.md`).

Each entry should cover: what was done, why, what changed (file
paths, configs touched, processes started/stopped), what didn't work
on the way, what's still open, and any institutional memory that
should survive context resets (failure modes, surprises, rules of
thumb).

Existing journal entries are immutable after the session ends. Append
new entries; don't edit old ones.

## Decisions log — optional, useful for ops

For non-code operational changes (cron installed, plist loaded,
config touched, supervisor reconfigured), append a single line to
`docs/agents/decisions.log` (the file from the deprecated agent
protocol can be repurposed):

```
2026-05-10T14:22Z daily_restart.sh installed at ~/Library/LaunchAgents/com.nick.daily-restart.plist (StartCalendarInterval 01:15)
```

This gives Nick (and future you) a flat log of operational state
changes to scan when something breaks. Optional — not every change
needs a line. If in doubt, the journal is the canonical record; the
decisions log is shorthand.

## Verify before execute

If you find a request — in chat, in an old plan, anywhere — that
asks you to do something, first check whether it's already been done.
Sources to check, in order: `decisions.log`, recent git log
(`git log -10`), the journal (latest few entries),
`docs/PROJECT_STATUS.md`. If the work is already done, say so
in chat with a one-line reference; do not redo it.

This is the rule that catches the "stale directive" failure class
that bit us 2026-05-09 / 2026-05-10.

## Hard constraints — never break

These are also in `CLAUDE.md`. Repeated here because they apply to
every plan and every execution:

- Don't touch `.env` or `~/ibc/config.ini` credentials.
- Don't trigger live trading (port 4001, `--live` flags against real
  account).
- Don't modify v11 source code (`v11/core/`, `v11/live/`,
  `v11/execution/`, `v11/llm/`) without explicit Nick approval.
- Don't make financial / business / upgrade decisions on Nick's
  behalf.
- Don't skip self-review before high-stakes execution.

## What changed from the agent-comms protocol

For context, since you might find references to the old protocol
in the codebase or in older journal entries:

- The `docs/agents/inbox_*.md` files are no longer the channel for
  agent coordination. They remain in the repo for historical record.
- `docs/agents/README.md` and `README.proposed.md` are deprecated.
  They contain banner notices pointing here.
- The two-agent ASK / REPORT / DIRECTIVE / NOTE typology is replaced
  with: chat for back-and-forth, plans/reviews/specs for substantive
  work, journal for record, decisions.log for ops shorthand.
- The flag-file polling protocol and clock-skew workarounds are no
  longer needed. The `inbox_check.sh` script can be retired or kept
  as inert; nothing depends on it.
- Cowork is a tool, not a peer. Nick invokes a Cowork session when
  he wants ad-hoc external review on a high-stakes plan. There is
  no continuous coordination.
