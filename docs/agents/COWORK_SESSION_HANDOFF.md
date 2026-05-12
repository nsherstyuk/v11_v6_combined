# Cowork ad-hoc review playbook

> **Updated 2026-05-10.** This file used to be the bootstrap for a
> persistent Cowork session coordinating with Claude Code via inbox
> files. That setup is retired (see `docs/workflow.md` and
> `CLAUDE.md`). Cowork now plays a narrower role: ad-hoc external
> review on high-stakes plans when Nick wants a second pair of eyes.
> The original handoff content is preserved at the bottom of this
> file for historical context.

## When to open a Cowork session

Open a fresh Cowork session when you (Nick) have a high-stakes plan
from Claude Code and want adversarial review before executing.
Examples:

- A new strategy module about to ship to paper trading
- A daily-restart cron about to run unattended
- An architectural change to v11
- A backtest result that looks too good to be true
- A postmortem you want challenged

For routine ops or one-line edits, skip Cowork — just let Claude
Code execute. Single-pass self-review (per `docs/workflow.md`) is
enough.

For the highest-value second opinion on truly important decisions,
consider cross-model review (paste the plan into ChatGPT or Gemini)
in addition to or instead of Cowork. Different model families have
genuinely different blindspots; two Claude instances tend to agree.

## How to open a Cowork session

1. Tell Cowork the folder: `/Users/mykolasherstyuk/code/ibkr_grok_wing_agent`
   (Cowork can request directory access if not already mounted).
2. Paste the plan you want reviewed, or point Cowork at the plan file
   (e.g. `docs/superpowers/plans/2026-05-10-daily-restart.md`).
3. Paste a one-paragraph context note: what's already been done in
   chat with Claude Code, what's already in the codebase, what's
   already been decided. Cowork can't see your chat with Claude Code,
   so without this context it may flag things that aren't actually
   open issues.
4. Ask explicitly for adversarial review. The framing matters —
   "review this plan" produces softer feedback than "tell me what's
   wrong with this plan, including the strongest case against it."

## What to expect from Cowork

Cowork will:

- Read the plan and the cited supporting files.
- Pre-mortem: imagine the plan caused a real problem in three months,
  identify the cause, check whether the plan defends against it.
- Surface specific challenges with severity (must-fix / should-fix /
  nit) and suggested resolutions.
- Give a verdict: APPROVED, NEEDS_REVISION, or ESCALATE_TO_NICK
  (i.e. "I can't resolve this without you deciding").

Cowork will not:

- Issue directives to Claude Code.
- Coordinate continuously across sessions.
- Execute the work itself.
- Touch the codebase unless you specifically ask it to.

After review, Cowork's output is yours to act on. Paste it back into
the chat with Claude Code, or tell Claude Code in chat what to
adjust. The Cowork session can be closed once the review is delivered.

## What Cowork can't help with

- Running real backtests on your data — Cowork has no access to your
  tick_vault or v11 codebase state unless you upload it, and even
  then re-grounding adds enough overhead that Claude Code is faster
  end to end. Use Cowork for review, not for parallel computation.
- Real-time ops — anything that needs action within minutes is faster
  in chat with Claude Code.
- Anything where the value is independence-of-perspective — for that,
  cross-model review is genuinely better than another Claude
  instance.

## Hard rules that always apply

These bind Cowork as much as they bind Claude Code:

- DO NOT touch `.env` or `~/ibc/config.ini` credentials.
- DO NOT trigger live trading (port 4001, anything `--live` against
  the real account).
- DO NOT modify v11 source code without explicit Nick approval.
- DO NOT make financial / business / upgrade decisions on Nick's
  behalf — always escalate.

---

## Historical context — the previous two-agent setup

Before 2026-05-10, this file was the bootstrap for a persistent
Cowork session that coordinated with Claude Code through inbox files
in `docs/agents/`. Cowork polled the inbox every 15 min, wrote
DIRECTIVEs, reviewed Claude Code's REPORTs, etc.

The setup ran for ~2 days and produced one good architectural review
(the daily-restart cron) plus a comms-reliability incident: 6 OPEN
inbox messages went unread for hours because Claude Code had no
in-context unread signal, and two of Cowork's directives crossed
work Nick had already given Claude Code in chat. Retrospective
discussion (visible in the `comms-reliability-retro` thread of
`inbox_claude_code.md` and `inbox_cowork.md`) concluded that:

- The two agents being the same model class (both Claude) meant
  "second perspective" was partially illusory.
- Coordination overhead through Nick (who was the routing layer
  between agents that couldn't see each other's chats) ate most of
  the value.
- The actual benefit was structured review discipline, which can be
  done by a single agent reviewing its own plan, by Nick, or by a
  cross-model review — none of which require persistent two-agent
  infrastructure.

The single-agent workflow in `docs/workflow.md` is the result. This
file's new purpose is the narrow Cowork-as-ad-hoc-reviewer use case
described above.

The inbox files (`inbox_cowork.md`, `inbox_claude_code.md`) and flag
files in this directory are kept as historical record of the
2026-05-08 → 2026-05-10 IBC supervisor work, which was the only
substantive thing the inbox protocol produced.
