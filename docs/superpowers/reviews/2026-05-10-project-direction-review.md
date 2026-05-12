# 2026-05-10 — Project Direction Review for Coding Agent

## Purpose

This document is a handoff/review for a coding agent working in `ibkr_grok_wing_agent` after the 2026-05-10 project review. It summarizes the current project direction, what should be considered active vs historical, and what changes are recommended or explicitly not recommended.

The intended use is to prevent a coding agent from reviving stale paths, expanding live scope prematurely, or treating old documentation as current truth.

## Current Source of Truth

Read these first, in this order:

1. `CLAUDE.md`
2. `docs/PROJECT_STATUS.md`, especially the top `Current state — 2026-05-10` section
3. `docs/workflow.md`
4. Latest files in `docs/journal/`
5. This review document

If older documentation conflicts with the top `Current state` section in `docs/PROJECT_STATUS.md`, treat the top current-state section as authoritative unless Nick explicitly says otherwise.

## Explicit Do-Not-Do List

A coding agent should not:

- Touch `.env`.
- Touch `~/ibc/config.ini` credentials.
- Use port `4001` or real-money live account actions.
- Run `python -m v11.live.run_live` without Nick’s explicit approval.
- Modify protected `v11` production code without Nick’s explicit approval.
- Re-enable Darvas or 4H Level Retest.
- Add GBPUSD, equities, or trend following to live/paper config.
- Revive the root Grok stock-picker as the main stock strategy.
- Use LLM gating as a default price-action filter.
- Change risk limits, order semantics, trigger methods, bracket behavior, or strategy parameters without approval.
- Broadly refactor `run_live.py` before paper stability.
- Add auto-restart / auto-recovery behavior to a health-check script unless Nick explicitly approves that design.

## Executive Summary

The project should **not** be thrown away. The useful direction is clear:

- Active production focus is `v11`, not the original root `main.py` swing agent.
- Active strategy is **XAUUSD ORB only**, paper trading through IB Gateway on port `4002`.
- EURUSD Darvas and 4H Level Retest are disabled pending EURUSD data integrity audit.
- LLM price-only trade gating has repeatedly performed poorly / anti-selected and should not be expanded without new evidence.
- The next research direction, after Mac paper proof-of-life, is **pre-registered US large-cap / ETF equity ORB research**, not reviving the root Grok stock-picker loop.

The project’s strongest qualities are research discipline, incident-driven hardening, and willingness to kill weak strategies. The main risk is stale historical code/docs being mistaken for current direction.

## Active vs Historical Systems

### Active: `v11`

`v11` is the active trading platform.

Current posture:

- Paper account only.
- IB Gateway paper port `4002`.
- Active strategy: XAUUSD ORB via V6 adapter.
- Darvas disabled by default.
- EURUSD strategies suspended.
- No real-money/live account actions unless Nick explicitly directs that exact action.

Important protected areas:

- `v11/core/`
- `v11/live/`
- `v11/execution/`
- `v11/llm/`

Do **not** modify these without explicit approval from Nick. `CLAUDE.md` is authoritative on this constraint.

### Historical / Secondary: root `main.py` swing agent

The root swing agent is the original IBKR + Grok stock-picking prototype.

It should be considered historical / prototype unless Nick explicitly asks to work on it.

Key limitations:

- Grok picks all trades directly.
- Static watchlist.
- No proven mechanical edge.
- No broker-side bracket lifecycle.
- No robust exit management.
- Portfolio tracking is stub-like compared to `v11`.
- Risk state is not sufficiently broker-truth-driven for real-money automation.

Recommendation: do not develop this path toward real money. If touched, first add clear documentation/banners marking it as legacy/prototype.

## Strategy Assessment

### XAUUSD ORB

This remains the only validated v11 strategy.

Strengths:

- Best-supported evidence base in this repo.
- Live/backtest divergences have been actively closed.
- Operational hardening has focused on the real failure modes: order rejection, reconnects, stale feeds, naked positions, Gateway supervision.
- Current direction of paper-first validation is correct.

Risks:

- Single strategy / single instrument concentration.
- Edge is real but not extremely fat.
- Operational proof is still incomplete until paper brackets/fills/SL-TP lifecycle are confirmed on Mac.
- Broker behavior around fills, stop triggers, reconnect, and protection orders must be proven empirically.

Recommendation:

- Keep XAUUSD ORB as the only active paper path.
- Run without LLM gating unless explicitly testing LLM impact.
- Prioritize proof-of-life and fault drills over new strategy additions.

### EURUSD Darvas / 4H Level Retest

Current status: disabled / suspended.

Reason:

- `eurusd_1m_tick.csv` was modified on 2026-04-13 without documented provenance.
- Prior EURUSD research is invalidated until data integrity is resolved.
- Re-runs on current data did not reproduce the original edge.

Recommendation:

- Do not re-enable EURUSD strategies.
- Do not tune around the current uncertainty.
- First perform a data integrity audit and establish a clean canonical dataset.

### GBPUSD / other FX ORB

Recent research found that GBPUSD ORB had historical edge but broke in 2025; other majors were flat/negative under the tested template.

Recommendation:

- Do not add GBPUSD or other FX ORB strategies to live/paper now.
- Treat them as dropped unless Nick starts a new research cycle.

### XAUUSD trend following

Trend following on gold showed interesting results, especially longs-only, but much of the edge may be gold beta / structural uptrend exposure rather than independent alpha.

Recommendation:

- Do not add to live/paper now.
- If revisited, treat as a separate research project with buy-and-hold comparison, bear-market stress, and correlation analysis vs ORB.

### LLM filtering

LLM price-only gating has multiple negative findings:

- It can anti-select by skipping uncomfortable but high-payoff breakout days.
- It tends to reason from textbook narratives rather than actual expectancy.
- It obscures attribution during paper trading.

Recommendation:

- Do not use LLM as a generic price-action trade approver.
- Reserve LLM for areas where it may have genuine informational value:
  - event/calendar context
  - news/earnings interpretation
  - anomaly explanation
  - post-trade review
  - structured report generation

## Stock / Equity Direction

There are two separate stock directions. Do not conflate them.

### Do not prioritize: root Grok stock picker

The old stock swing agent is not the recommended stock-trading path.

It is too dependent on LLM judgment and lacks robust order lifecycle management.

### Recommended future research: US equity ORB under `v11/backtest`

The currently promising stock direction is pre-registered US equity / ETF ORB research using the downloaded 1-minute data in:

- `tick_vault_data/us_equities/*.csv`

Universe:

- SPY, QQQ, IWM, DIA
- AAPL, MSFT, NVDA, META, AMZN, GOOGL, TSLA, INTC, MU, AMD, TSM

Downloader:

- `v11/backtest/download_us_equities_sequential.py`

Recommended research standard:

- Mirror the pre-registered template approach used by `v11/backtest/orb_fx_grid.py`.
- Do not do ticker-by-ticker curve fitting.
- Pre-register range window, trade window, filters, exits, and costs before running broad tests.
- Include year-by-year OOS results.
- Include slippage/commission assumptions.
- Compare against simple baselines.
- Account for equity-specific risks: earnings, splits, halts, premarket liquidity, opening auction effects.

Important sequencing:

- Do **not** start equity ORB live/paper before XAUUSD ORB paper fill proof is complete.
- Equity ORB research can proceed only if Nick explicitly chooses to prioritize research over live proof-of-life.

## Recommended Priority Order

### P0 — Preserve live scope

Do not add strategies or instruments to live/paper.

Keep live/paper scope:

- XAUUSD ORB only
- Paper account only
- No LLM gating unless explicitly being tested

### P0 — Prove Mac paper lifecycle

The key open gate is empirical proof that XAUUSD ORB on Mac can:

1. Start under the daily-restart architecture.
2. Calculate range.
3. Place brackets during the trade window.
4. Fill entry orders.
5. Arm SL/TP correctly.
6. Reconcile broker/internal state.
7. Exit or flatten safely.

Until this is proven, avoid expanding strategy surface area.

### P1 — Add/finish health-check supervision

Implement or complete the passive health-check script and 30-minute launchd timer described in the ops docs.

The health check should be passive. Its job is to report health, not recover the system:

- read heartbeat / logs / port state
- report status
- exit nonzero on critical conditions
- do not place orders
- do not kill processes
- do not restart Gateway, IBC, or `v11`

Recovery should remain separate:

- daily restart belongs to the approved daily-restart script / schedule
- process supervision belongs to the launchd supervisor
- the health check reports failures and produces clear operator-visible signals

### P1 — Run controlled paper fault drills

Recommended drills:

1. Kill Gateway while flat.
2. Kill Gateway while entry orders are resting.
3. Kill Gateway shortly after fill.
4. Kill Gateway while in position.
5. Manually cancel SL in TWS paper and confirm naked-position invariant.
6. Confirm reconnect and reconciliation behavior.

Every failed drill should become a journal entry and a regression test or runbook item.

### P1 — Complete equity download

Let the current US-equity downloader finish. Do not mutate the pre-registered universe unless Nick explicitly approves a new pre-registration.

### P1 — Near-term automation watchpoints

The daily-restart architecture is new. The first real unattended production fire is expected on 2026-05-11 at 01:15 ET.

Watchpoints:

- Confirm the daily restart fires at the scheduled time under real conditions.
- Confirm IB Gateway returns without unexpected 2FA/operator intervention.
- Confirm the running equity downloader survives the restart using `RECONNECT_MAX_ATTEMPTS = 10`.
- Confirm `v11` restart behavior if it is part of the active supervised stack.
- Confirm heartbeat/logs make the restart sequence observable after the fact.

Known single points / fragile areas to watch:

- Gateway UI auto-logoff / auto-restart setting is currently a critical manual setting; future Gateway updates may reset it.
- `launchctl list | grep com.ibc.gateway` may show PID `-` because the wrapper exits after forking; supervision may still work, but do not assume the PID display means the child is absent.
- If the port is down, launchd `KeepAlive` recovery can lag. Treat this as an operational watchpoint, not an immediate reason to add new auto-recovery logic elsewhere.

### P2 — Documentation cleanup

Recommended documentation changes:

- Strongly consider moving stale historical sections of `docs/PROJECT_STATUS.md` into `docs/PROJECT_STATUS.archive.md`, leaving the active status document lean and current.
- Add a clear banner to root `README.md` / root `main.py` docs marking the swing agent as legacy/prototype.
- Update `v11/README.md` so it no longer presents Darvas + LLM as the active live path.
- Update architecture docs to reflect XAUUSD ORB, Gateway/IBC supervision, and ORB executor safety as current center elements.
- Reduce stale Windows-era or superseded strategy claims in quick-start paths, or add explicit stale-history warnings.

### P2 — Data integrity audit for EURUSD

Before any EURUSD strategy work:

- Identify what changed in `eurusd_1m_tick.csv` on 2026-04-13.
- Compare row counts/date ranges if prior copy exists.
- Decide whether the current data becomes canonical or whether fresh IBKR data is needed.
- Re-run research only after canonical data is established.

### P3 — Avoid speculative `run_live.py` refactors

Do not refactor `run_live.py` before the current paper path is proven stable.

After stability, refactor only when there is a concrete pain point:

- a test cannot be written cleanly because logic is too entangled
- a bug fix requires touching unrelated concerns
- a repeated operational task needs a narrow public interface
- a single extraction can be made behavior-preserving and tested

Do not extract named service classes just because the file is large. Premature abstraction can make the next live-safety change harder if the new boundary is wrong.

## Cross-Model Review Guidance

For highest-stakes changes, follow `docs/workflow.md` and seek independent cross-model review before execution.

Examples that merit cross-model review:

- daily-restart architecture changes
- IBC / Gateway supervisor changes
- order lifecycle or bracket-order semantics
- risk limits or position-flattening behavior
- any move from paper toward real-money live behavior

Prefer a genuinely independent review path, not only same-family self-review, because same-family reviews tend to share blind spots.

## Suggested Agent Task Framing

If Nick asks a coding agent to continue from this review, the agent should respond with a small plan and ask which branch to execute:

1. Operational proof / health check / fault drills
2. Documentation cleanup
3. Equity download status / research preparation
4. EURUSD data audit

The default recommendation is option 1 unless Nick says otherwise.

## Final Opinion

The project’s direction is good if it stays disciplined:

- Mechanical edge first.
- Broker safety second.
- LLM only where it adds information, not as chart intuition.
- Paper proof before expansion.
- Pre-registered research before any new strategy.

The project does **not** need a rewrite. It needs sharper boundaries between active and historical systems, continued operational proof, and restraint against adding new strategies before the current one proves it can trade safely end-to-end.
