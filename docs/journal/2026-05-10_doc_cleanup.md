# Documentation cleanup — PROJECT_STATUS lean, README banners

**Date:** 2026-05-10
**Status:** Done.
**Trigger:** P2 item in `docs/superpowers/reviews/2026-05-10-project-direction-review.md`.

`docs/PROJECT_STATUS.md` was 772 lines — the live "Current state"
section in the first ~100 lines was authoritative, but the file then
continued for ~670 lines of Windows-era paths, pre-migration framing,
and prior strategy research. A fresh agent reading top-to-bottom would
pick up stale context that contradicts the live section.

Moved the historical content (lines 100–772) verbatim into
`docs/PROJECT_STATUS.archive.md` with an opening banner stating it's
a historical archive and naming the live source of truth. Trimmed
`PROJECT_STATUS.md` to lines 1–99 + a short pointer block referring
readers to the archive. Confirmed via grep that nothing in `docs/`,
`v11/`, `standards/`, `CLAUDE.md`, or `README.md` linked into specific
historical sections via anchor — only plain file references, which
still resolve to the lean file (now load-bearing only for current
state).

Added banners to two more docs that the review flagged:

- Root `README.md` got a `LEGACY / PROTOTYPE` banner at the very top
  identifying the original swing-agent (`main.py` + Grok stock picker)
  as legacy, naming the active path as `v11/` XAUUSD ORB, and pointing
  to `CLAUDE.md` and the new `PROJECT_STATUS.md`'s current-state
  section. Body preserved unchanged for historical accuracy.
- `v11/README.md` had a title and architecture section that presented
  Darvas + Volume Imbalance + LLM Filter as the active live path.
  Replaced the title and added a `2026-05-10 active state` banner
  clarifying that XAUUSD ORB via the V6 adapter is the only active
  strategy; Darvas / EURUSD strategies are suspended; LLM-as-trade-gate
  is not used on the active path. Updated the architecture diagram to
  show both the legacy Darvas chain and the current V6 ORB chain
  side-by-side. Quick-start and parameter tables left intact since
  they're still accurate for the legacy path; the banner makes clear
  they don't describe production configuration.

Logged in `decisions.log`.

## Watchpoints

- The archive file is now authoritative for prior strategy research
  context. If a future direction wants to cite or revive any of that
  work (e.g. EURUSD Darvas backtests, V8 reference, V11 build phase
  history), it stays accessible at the same content but a different
  path.
- If `PROJECT_STATUS.md` grows back past ~150 lines because we keep
  appending to "Current state", consider applying the same archive
  pattern again at the next round of cleanup (e.g. dated rolling
  archive). Not needed yet.
- The README banners are written so they age cleanly — they reference
  the dated review for the assessment, not the current calendar
  state. If the active strategy changes, the banners need an update
  but they won't silently mislead in the meantime.
