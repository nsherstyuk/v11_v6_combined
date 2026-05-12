# Workflow change — two-agent inbox protocol retired, single-agent + self-review adopted

**Date:** 2026-05-10
**Trigger:** the 2026-05-10 comms-reliability retrospective (see
`docs/agents/inbox_cowork.md` and `inbox_claude_code.md`).
**Authoritative new docs:** `CLAUDE.md` (session entry point) and
`docs/workflow.md` (the workflow itself). The substance lives there;
this entry just records the protocol shift for the journal.

The previous protocol coordinated two AI agents (Cowork on Anthropic's
web side and Claude Code in a terminal) via append-only inbox files at
`docs/agents/inbox_*.md`, polled on a 10-minute launchd timer with
flag-file freshness checks and clock-skew workarounds. The retro
surfaced multiple ways the protocol was creating more friction than
value: I dropped six unread Cowork entries because there's no clean
"session start" anchor in a long-running session for re-checking the
inbox; stale directives crossed work I'd already done in chat with
Nick; future-dated timestamps from Cowork's clock-skewed sandbox kept
the polling protocol broken without a `max(now, write_ts)` workaround;
heartbeats for long-running tasks were on me to remember and never
landed. The new shape is one executor, structured self-review for
high-stakes work (plan → pre-mortem → execute → journal under
`docs/superpowers/`), occasional ad-hoc external review when stakes
warrant it (Cowork or cross-model paste), and chat as the primary
direction channel. The inbox files (`inbox_cowork.md`,
`inbox_claude_code.md`) and `docs/agents/README.md` /
`README.proposed.md` / `COWORK_SESSION_HANDOFF.md` are kept for
historical record with deprecation banners. The inbox-poll launchd
timer was retired (`~/Library/LaunchAgents/com.nick.inbox-check-claude-code.plist`
unloaded + removed; `inbox_check.sh` and `inbox_warn.sh` left in
place as inert per Nick's instruction). Going forward: chat for
direction, plans/reviews/specs for substantive work, journal for
record, `docs/agents/decisions.log` for ops shorthand. No more inbox
writes.
