# Handoff prompt for Claude Code

Copy the block below as the first message to Claude Code after running
`cd ~/code/ibkr_grok_wing_agent && claude`. It catches the agent up to
the state of the Mac migration as of 2026-05-05 without you having to
re-explain anything.

---

```
Hi. I'm Nick. You're picking up a Mac migration that's already partway
done. Before doing anything, please read these files in order:

1. docs/ops/MIGRATION_STATUS.md  — current state, what was prepared,
   exact step-by-step for everything left
2. docs/ops/macbook_migration.md — the original runbook (source of truth)
3. CLAUDE.md (if present) and standards/ — house rules
4. docs/journal/2026-05-01_paper_zero_trades_root_cause_and_ibc_fixes.md
   — the most recent live incident; understand WHY AutoRestartTime=22:00
   and the port-listening guard exist before touching either

Quick context so you're not surprised:

- Phases 1 and 2 of the migration are done: Python 3.11, repo cloned,
  .venv created, all key packages installed.
- A separate Anthropic tool (Cowork) just produced three new files in
  docs/ops/: MIGRATION_STATUS.md, ibc_config.mac.template.ini, and
  gatewaystart_port_guard.snippet.sh. Use them; don't redo them.
- I am NOT a software developer. Explain in plain English. Don't dump
  syntax explanations unless I ask. Show one step, run it, summarize the
  result, then propose the next step.

Hard rules — do not break:

- DO NOT read, write, paste, or echo the contents of .env or
  ~/ibc/config.ini. Both contain secrets. Tell me when YOU need me to
  edit them; never edit them yourself.
- DO NOT enter my IBKR password anywhere. The ibc config is the only
  place it goes, and I put it there myself.
- DO NOT trigger live trading. v11 is paper only (port 4002). If
  anything in this session would touch port 4001 or `--live` against a
  real account, stop and ask.
- DO NOT run `python -m v11.live.run_live` without confirming with me
  first. It connects to IB Gateway and starts the strategy loop.

Strategic context (background, no action needed yet):

- The reason the migration matters: there is exactly ONE validated v11
  strategy, XAUUSD ORB, and it has produced ZERO paper fills since the
  2026-05-01 IBC fix. Proving paper actually fills on the Mac is the
  unfinished business of the whole project.
- After the migration is green, the next research direction is adding
  US large-cap equity ORB as a v11 research module (mirror the
  pre-registered template approach used in v11/backtest/orb_fx_grid.py,
  not the QuantConnect single-instrument-iterate approach). Don't start
  on that until paper is fill-confirmed.

Now: please read the files above and tell me where exactly we are and
what the next step is. Then walk me through it.
```

---

## Notes for Nick

- After Claude Code reads those four docs, the first concrete step it
  should propose is the venv sanity check from MIGRATION_STATUS.md
  (Step 1) — three commands, ~30 seconds.
- If Claude Code suggests skipping any of the hard rules above, push
  back. Those exist for real reasons.
- If Claude Code can't find one of the files, paste the actual error
  back at it — don't try to summarize.
