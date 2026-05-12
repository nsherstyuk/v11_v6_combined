# Standing instructions for Claude Code

This file is the entry point for every Claude Code session in this
repo. Read it first.

## What this project is

Algorithmic trading system (`v11`) running on Nick's Mac, paper-trading
through Interactive Brokers Gateway on port 4002. Active strategy:
XAUUSD ORB. EURUSD strategies (Darvas, 4H Level Retest) suspended
pending data integrity audit. Equity historical data download
(SPY/QQQ/IWM/DIA + mega-caps) running in parallel for upcoming ORB
research.

Nick is a sophisticated trader, not a developer. He directs the work
in chat; you execute. Decisions about strategy, risk, money, and
upgrades escalate to him.

## Bootstrap — every session

1. Read THIS file.
2. Read `docs/PROJECT_STATUS.md` — current state, what's in flight, open
   questions. The "Current state" section at the top is the source of
   truth for where things stand today.
3. Read the latest entries in `docs/journal/` (most recent date) for
   handoff context from the previous session.
4. Read `docs/workflow.md` if you need a refresher on how to work in
   this repo (self-review discipline, plans/reviews/specs structure,
   when to escalate). For routine work you may not need to.
5. If you are about to do something high-stakes, follow the workflow
   in `docs/workflow.md` — write a plan, self-review it (pre-mortem),
   then execute. Don't skip this for irreversible actions.

## Hard constraints — never break

- **Do not touch `.env`** — contains the xAI/IBKR credentials. Nick
  manages it.
- **Do not touch `~/ibc/config.ini` credentials section** —
  `IbLoginId` and `IbPassword` stay untouched.
- **Do not trigger live trading.** Anything on port 4001, anything
  with `--live` against the real account, is off-limits unless Nick
  explicitly directs it for that specific action.
- **Do not modify v11 source code without explicit Nick approval.**
  v11 is the production strategy code. Backtest scripts and ops
  scripts are fair game; `v11/core/`, `v11/live/`, `v11/execution/`,
  `v11/llm/` are not.
- **Do not make financial, business, or upgrade decisions on Nick's
  behalf.** Always escalate.
- **Do not skip self-review before high-stakes execution** (see
  `docs/workflow.md`).

## Where things live

| Purpose | Path |
|---|---|
| Living progress doc (read at session start) | `docs/PROJECT_STATUS.md` |
| How to work in this repo (self-review, plans, reviews) | `docs/workflow.md` |
| Per-session handoff entries | `docs/journal/YYYY-MM-DD_*.md` |
| Plans for upcoming work | `docs/superpowers/plans/` |
| Reviews of plans / code | `docs/superpowers/reviews/` |
| Design specs | `docs/superpowers/specs/` |
| Operational runbooks | `docs/ops/` |
| Mac migration status (still active) | `docs/ops/MIGRATION_STATUS.md` |
| IBC + Gateway test plan | `docs/ops/IBC_TEST_PLAN.md` |
| Standards (operating principles, research, tests) | `standards/` |
| v11 architecture (center/edge map) | `v11/ARCHITECTURE.md` |
| Strategy design notes | `docs/V11_DESIGN.md` |

The `docs/agents/` directory contains the deprecated two-agent inbox
protocol. The inbox files are kept for historical record but the
protocol is no longer in use. See `docs/workflow.md` for the current
single-agent workflow.

## Communication with Nick

Use chat for back-and-forth. Use the journal for what happened. Use
plans/reviews/specs for upcoming substantive work. No inbox files,
no agent-to-agent messaging.

For ad-hoc second opinions on a high-stakes plan, Nick can open a
fresh Cowork session, paste the plan, and ask for adversarial review.
He may also paste the plan into a different model (ChatGPT, Gemini)
for cross-model review. These happen on his initiative; you don't
need to coordinate with another agent.
