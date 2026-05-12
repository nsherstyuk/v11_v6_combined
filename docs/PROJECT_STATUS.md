# Project Status — All Trading Systems

**Last refreshed at top:** 2026-05-10 (Mac migration + IBC supervisor +
daily-restart architecture; equity download in flight). The
"Current state" section immediately below is the source of truth for
where things stand today. Sections further down (starting at
"Documentation Structure") are older and contain Windows-era paths
and pre-migration framing — they remain useful for strategy-research
history but should not be relied on for current operational state.

**Maintenance rule:** Update the "Current state" section at the end of
every session that changes project state. The historical sections are
immutable — append journal entries instead of editing them.

---

## Current state — 2026-05-10

**Platform.** v11 trading system migrated from Windows to macOS. Repo
lives at `~/code/ibkr_grok_wing_agent/`. Python 3.11.15 via Homebrew,
`.venv` in repo root, all production deps installed (incl.
`ib_insync` and `pytest-asyncio` added during migration). 550+ tests
passing.

**Migration status.** Phases 1–4 done (prereqs, repo+venv, `.env`,
IBC + Gateway with launchd supervision). Phase 6 (v11 paper
proof-of-life on Mac) pending the next 03:00 ET trade window or
earlier when `daily_restart.sh` brings v11 up. Phase 7 (Windows
decommission) blocked on Phase 6. See
`docs/ops/MIGRATION_STATUS.md` for the phase-by-phase detail.

**Active strategy.** XAUUSD ORB only. EURUSD strategies (Darvas, 4H
Level Retest) suspended pending data integrity audit — the
`eurusd_1m_tick.csv` was modified 2026-04-13 without documentation
and all EURUSD research is invalidated until that's resolved (open
question 19 in the historical section below).

**IBC + Gateway lifecycle.** Single source of truth is the daily
01:15 ET cron (`daily_restart.sh` + `~/Library/LaunchAgents/com.nick.daily-restart.plist`),
which kills/restarts IBC, Gateway, and v11 in sequence. Gateway UI
auto-logoff at 01:00 ET, auto-restart OFF, IBC `AutoRestartTime` and
`ColdRestartTime` empty. Mid-day safety net: launchd KeepAlive on
`~/Library/LaunchAgents/com.ibc.gateway.plist` (ThrottleInterval=300).
Background and rationale: `docs/journal/2026-05-09_ibc_launchd_supervisor.md`
and `docs/journal/2026-05-10_daily_restart_architecture.md`.

**Equity download — COMPLETE 2026-05-11 ~04:00 EDT.** 15/15 tickers,
29.6M total rows on disk at `tick_vault_data/us_equities/*.csv`.
14 marked `ok`, TSM marked `partial` (2 mid-chunk reconnects during
the 01:15 EDT daily-restart cycle resolved cleanly via the bumped
`RECONNECT_MAX_ATTEMPTS=10`; data is complete). Universe:
SPY/QQQ/IWM/DIA + AAPL/MSFT/NVDA/META/AMZN/GOOGL/TSLA/INTC/MU/AMD/TSM.
Script: `v11/backtest/download_us_equities_sequential.py`.

**Equity ORB research — COMPLETE 2026-05-11. No tradeable edge.**
Pre-registered breakout template (range 09:30–10:30 ET, RR=2.5,
range% 0.10–2.00%, costs $0.03/sh) and inverse-fade alternative
hypothesis both run on the 15-ticker universe. IS/OOS gate identified
three OOS-survivors (META, AMZN, TSLA), but the year-by-year
breakdown revealed the edge ran from 2018–2022 (5 consecutive
positive years) and has been **net-negative every year since 2024**.
Fade variant clearly losing across all years. This is a structural
regime change, not noise. The 15-ticker dataset remains useful as a
baseline for future research but does not currently reveal a deployable
strategy. Full diagnosis + recommendation: `docs/journal/2026-05-11_orb_us_equities_regime_change.md`.
Scripts: `v11/backtest/orb_us_equities_grid.py`,
`v11/backtest/orb_us_equities_fade_grid.py`,
`v11/backtest/orb_us_equities_year_breakdown.py`.

**Workflow change 2026-05-10.** Project moved from a two-agent
inbox-based comms protocol (Cowork ↔ Claude Code via
`docs/agents/inbox_*.md`) to a single-agent workflow with
structured self-review. See `docs/workflow.md` for the protocol and
`CLAUDE.md` for the session-bootstrap entry point. The
`docs/agents/` directory is preserved as historical record but is
no longer the active coordination channel.

**Phase 6 proof-of-life — partial result 2026-05-11.** XAUUSD ORB on
Mac paper, full lifecycle ran end-to-end for the first time:
- Range computed correctly: 4648.21–4705.56
- LLM gate passed (passthrough auto-approve per `--no-llm`)
- Brackets placed at 07:42:04 EDT (BUY 4705.56, SELL 4648.21, OCA)
- 4h-pending guard fired at 11:42:03 EDT per design
- State machine ran correctly through IDLE → RANGE_READY →
  ORDERS_PLACED → DONE_TODAY

**BUT no fill happened.** Price crossed the BUY stop at ~09:06 EDT
and went $40+ above it for the next 2.5h, but the order never
triggered. Same failure mode as the 2026-04-24 incident; the
"fix" (`triggerMethod=7`) was in code with regression tests but
had never been validated end-to-end live because the 2026-05-01
Gateway-restart issue had eaten the trade window for 2 weeks
after April 24. Today was the first real-world fire of that
code path, and it failed. Full analysis +
proposed fixes: `docs/journal/2026-05-11_orb_no_fill_bug_and_code_review.md`.

**Fixes applied 2026-05-11 evening** (with explicit Nick approval to
modify `v11/v6_orb/`):
- **(B)** `self.{buy,sell}_entry_id` set before `ib.sleep(1)` so the
  orderStatusEvent listener catches early-lifecycle transitions
- **(A)** Post-placement diagnostic — `ib.openTrades()` query logs
  every order's actual attributes (type, aux, lmt, trigger, transmit,
  oca, status) after both `placeOrder` calls return. Tomorrow's
  placement will produce this smoking-gun data
- **(C1)** Entry orders changed `STP` → `STP LMT` with explicit
  `lmtPrice = auxPrice ± max(0.5, 0.1 × range_width)`. Trigger
  half retains `triggerMethod=7`. Gives the post-trigger order a
  defined fill envelope, which paper handles more predictably
- 525 / 525 v11 tests pass after the change

**Architecture hardening landed 2026-05-11 late evening** (per
reviewer's revised plan in
`docs/superpowers/reviews/2026-05-11-v11-orb-remediation-plan-reviewer-reply.md`):
- **Phase 5a** — minimal fake-IB lifecycle harness
  (`v11/tests/lifecycle_harness.py`). Stateful `FakeIB` +
  `FakeIBKRConnection.simulate_reconnect()` that reproduces the
  verified `self.ib = IB()` reassignment at
  `v11/execution/ibkr_connection.py:77`. Enables honest
  reconnect/lifecycle tests.
- **Phase 2** — `ORBAdapter._on_fill` converges strategy + risk-manager
  state for `ORPHAN_FLATTEN`, `NAKED_FLATTEN`, and `CLOSED`. V6's
  frozen `on_fill` ignores these; without the fix, broker flat +
  strategy `IN_TRADE` was possible. Includes a PnL-zero guard for the
  "no entry basis" case (executor adopting a broker-side orphan).
- **Phase 1** — `ORBAdapter.rebind_ib(ib, contract)` +
  `MultiStrategyRunner.rebind_orb_connections()`. Called from the
  main loop after reconnect detection, before `_reconcile_positions`.
  Swaps ib+contract on adapter/context/executor, re-subscribes the
  V6 tick stream on the new ib, re-hooks `pendingTickersEvent`,
  resets `_hooked_status_event` so the next `set_orb_brackets`
  re-hooks `orderStatusEvent`.
- **Phase 3** — `ORBAdapter.emergency_close(reason)` delegates to the
  executor's broker-truth-aware `close_at_market()` unconditionally
  (does NOT consult `has_position()` — that's the whole point).
  Called from `V11LiveTrader._emergency_shutdown` after
  `cancel_all_orders`, before the legacy TradeManager reconnect-
  and-close block.
- **548 / 548 v11 tests pass** (+23 net new). Full writeup in
  `docs/journal/2026-05-11_orb_remediation_phases_1_2_3_5a.md`.

**Open work — short list.**

- **Phase 6 proof-of-life completion** — pending tomorrow's
  2026-05-12 04:00–12:00 EDT trade window. Either a clean fill on
  a breakout proves the STP LMT fix worked, or the diagnostic block
  reveals what's wrong (Mac Gateway version, contract type, paper
  account behavior).
- **Remediation Phase 0** — tomorrow morning verification: check
  `~/.daily_restart.log` first thing for the 01:15 EDT unattended
  fire, confirm v11 is running the new code, capture the first
  `DIAG: openTrades after placement` block at ~04:00 EDT, verify
  `bars` counter increments during the trade window. Cheap; defines
  the data point Phase 4 reasons from.
- **Remediation Phase 4** — order-placement diagnosis informed by
  tomorrow's DIAG. Includes the C1 exit clause (revert STP LMT if
  plain STP would have worked), `STP_LMT_MISSED_LIMIT` classification
  if trigger fires but limit doesn't fill, both-legs-`transmit=True`
  as a candidate fix if BUY leg was permanently staged.
- **Remediation Phase 5b** — expand the lifecycle harness to the
  full normal/abnormal matrix (TP/SL fills, BE adjust, naked-flatten,
  emergency-shutdown with broker orphan, reconnect-while-in-position).
  After Phases 1–3 are stable in production.
- v11 needs to be restarted to pick up the new code. Currently
  running PID 89839 holds the OLD compiled module. Nick will restart
  manually before tonight; otherwise tomorrow's 01:15 EDT cron will
  cycle v11 and load the new code.
- EURUSD data integrity / migration — required before re-enabling
  EURUSD strategies. (The April audit happened on Windows; the
  remaining work is Mac-side data migration, which is a Nick-owned
  data decision — see `docs/agents/decisions.log` 2026-05-10.)

**Closed since 2026-05-09:** equity download (15/15 complete, 29.6M
rows); ORB analysis on the equity universe (no tradeable edge, edge
died ~2024); `RECONNECT_MAX_ATTEMPTS` bumped to 10;
`com.nick.daily-restart.plist` validated end-to-end (`AbandonProcessGroup`
fix landed + production-equivalent kickstart test passed);
`ibkr_health.sh` + 30-min launchd timer installed; v11 PGRP-kill bug
fixed; Order-placement code reviewed, 3 fixes applied (A + B + C1).

**Known limitations / things to flag if they recur.**

- Gateway UI Auto-Restart value is now the single point of failure
  for unattended overnight uptime. If a future Gateway update resets
  it, the system silently regresses. The `daily_restart.sh` cron is
  the safety net; an explicit grep-and-NOTE check for the UI value
  is still pending.
- launchd's supervision handle on `com.ibc.gateway` is on the bash
  wrapper, not the Java process. `launchctl list` shows PID `-`.
  Functionally adequate (port-down → launchd respawns wrapper →
  fresh Java) but a cleaner version would `exec` Java instead of
  forking. Filed as known limitation, no urgent fix.

**Where to look next as a fresh session.**

1. `CLAUDE.md` — bootstrap and hard constraints
2. This section — current state
3. `docs/journal/` — most recent entries for handoff context
4. `docs/workflow.md` — how to work in this repo
5. `docs/ops/MIGRATION_STATUS.md` — migration phase detail if
   working on Mac/IBC ops

**For an external reviewer — recent journal entries in chronological
order** (post-2026-05-09 only; older history is in
`docs/PROJECT_STATUS.archive.md` and earlier journals).

| Date | File | Topic |
|---|---|---|
| 2026-05-09 | `docs/journal/2026-05-09_ibc_launchd_supervisor.md` | First IBC supervisor + launchd plist after the 2026-05-08 silent overnight outage |
| 2026-05-10 | `docs/journal/2026-05-10_daily_restart_architecture.md` | Switch from autorestart-token to deterministic 01:15 ET cron — current architecture |
| 2026-05-10 | `docs/journal/2026-05-10_workflow_change.md` | Project moved from two-agent inbox protocol to single-agent self-review |
| 2026-05-10 | `docs/journal/2026-05-10_ibkr_health_check.md` | Passive 30-min IBKR health probe — report-only, no recovery actions |
| 2026-05-10 | `docs/journal/2026-05-10_doc_cleanup.md` | Trimmed `PROJECT_STATUS.md` 772→108 lines, banners on legacy paths |
| 2026-05-10 | `docs/journal/2026-05-10_data_migration_from_dropbox.md` | Migrated 315 MB of Windows-era data into `data/` |
| 2026-05-11 | `docs/journal/2026-05-11_daily_restart_v11_pgrp_kill.md` | `AbandonProcessGroup` fix to the daily-restart plist — production-validated |
| 2026-05-11 | `docs/journal/2026-05-11_orb_us_equities_regime_change.md` | ORB analysis on the 15-ticker equity universe — no tradeable edge, edge died 2024+ |
| 2026-05-11 | `docs/journal/2026-05-11_orb_no_fill_bug_and_code_review.md` | Today's no-fill bug + deep code review + 3 fixes applied (A + B + C1) |
| 2026-05-11 | `docs/journal/2026-05-11_orb_remediation_phases_1_2_3_5a.md` | Phases 1–3 + 5a implemented per reviewer's revised plan. ORB reconnect rebind, safety-flatten convergence, ORB-aware emergency close, minimal fake-IB harness. +23 tests. **Read this last; reviewer-driven architecture hardening on top of the morning's order-type fixes.** |

For reviewer context: `docs/superpowers/reviews/2026-05-10-project-direction-review.md`
is the authoritative project-direction document (revised twice with
my feedback). `docs/ops/IBC_TEST_PLAN.md` has the 6-test framework
for IBC + Gateway. `v11/ARCHITECTURE.md` has the current center/edge
map.

---

## Historical archive

The strategy-research history, Windows-era paths, and pre-migration
framing previously appended below this line have been moved to
`docs/PROJECT_STATUS.archive.md` (2026-05-10) so this file remains
lean and reflects only current state. Read the archive for project
history and prior strategy research; do not rely on it for live
operational state.
