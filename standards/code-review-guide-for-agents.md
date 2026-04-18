# Code Review for Trading Systems

A guide for LLM agents reviewing code in this project.

---

## Part 1: Two Layers of Review

### Structural review (what code reviews typically catch)

This is the standard layer: bugs in logic, missing error handling, type mismatches, API misuse, race conditions, test coverage gaps. Most code review focuses here and it's important.

### Runtime/integration review (what gets missed)

Trading systems have a second class of problems that don't show up in code inspection alone. The code is technically correct — no crashes, no type errors — but the **system doesn't work in practice**. These bugs are silent: no error messages, no exceptions, just a system sitting idle when it should be trading.

The patterns below come from real bugs found in this codebase during live operation (2026-04-16 session). They are observations to keep in mind, not a rigid checklist. A good review considers these patterns AND anything else that seems off.

---

## Part 2: Patterns to Watch For

### 1. Silent deadlocks in state machines

A state machine can be correct in structure but deadlocked in practice. The question isn't "does this transition work?" but "what prevents this transition from ever firing, and is there an escape?"

**Example from this codebase:** V6 ORB stays in `RANGE_READY` waiting for velocity ≥ 168 ticks/min. On a quiet day, velocity never reaches 168. The stale-breakout check exists but only fires AFTER velocity passes — creating a deadlock where neither condition is ever met. The system sits in RANGE_READY forever with price already outside the range.

**What to look for:**
- Every persistent state (one that lasts more than one cycle): what's the exit condition? What if that condition never occurs?
- Transitions that depend on a gate: does the gate have a timeout or fallback?
- Chains of conditions where step N requires step N-1 to complete, but step N-1 can get stuck

### 2. Bar-count parameters vs. wall-clock reality

Parameters expressed in "number of bars" look reasonable in code but may be implausible when converted to actual time for the instrument's timeframe.

**Example from this codebase:** `level_left_bars=10` on 4H bars means 10 × 4 hours = 40 hours of monotonic decline on each side of a swing point. That's 80 hours total. EURUSD rarely trends monotonically for 40 hours, so the detector found zero levels despite processing thousands of bars.

**What to look for:**
- Any parameter in "bars" — convert to wall-clock time for the relevant timeframe
- Ask: is this duration realistic for the instrument's typical behavior?
- Particularly watch for parameters originally tuned on one timeframe (1-min) that are now applied to a different timeframe (4H)

### 3. Invisible waiting states

A system can be "working correctly" but an operator has no way to know what it's doing or why it's stuck. Every state that persists should report what it's waiting for and how close it is to proceeding.

**Example from this codebase:** ORB status showed "brackets eligible" for hours. That's accurate — LLM approved, brackets are eligible — but it doesn't tell you WHY brackets aren't actually placed. The operator sees "eligible" and assumes orders are live. They aren't.

**What to look for:**
- Status messages that describe what HAS happened ("eligible", "ready") vs. what's PREVENTING the next step
- Any state where the system is waiting for a condition: is the current value of that condition visible?
- Can an operator distinguish "working, waiting for X" from "broken, stuck on X"?

### 4. Adapter/frozen-code contract gaps

When adapter code wraps frozen or unmodifiable code, the adapter must handle cases the frozen code doesn't. The frozen code's assumptions may not hold in the integrated system.

**Example from this codebase:** V6 ORB strategy (frozen) assumes velocity will eventually exceed threshold during the trade window. That's a reasonable assumption for standalone operation with a dedicated tick feed. But in V11's integrated system, the velocity threshold may never be reached, and the frozen code has no escape for that case. The adapter must add the escape.

**What to look for:**
- What assumptions does the wrapped code make about its operating environment?
- Which of those assumptions might not hold in the integrated system?
- Does the adapter handle the case where the wrapped code's expected flow never completes?

### 5. Stale state from previous sessions

State persisted to disk can become stale between sessions. If the system restarts and doesn't clean up, it can operate on incorrect state.

**Example from this codebase:** `emergency_shutdown.json` from a previous session persisted across restart, causing the dashboard to show "EMERGENCY SHUTDOWN" even though the system was running normally.

**What to look for:**
- Any file-based state that persists across restarts: is it cleaned up or validated on startup?
- State that represents a transient condition (emergency, error, pause) vs. permanent configuration
- What happens if the system crashes and restarts — does it recover cleanly?

---

## Part 3: End-to-End Scenario Walkthrough

Beyond reading code line-by-line, trace complete scenarios through the live system. This catches integration gaps that neither unit tests nor code inspection find.

**How to do it:**

1. Pick a realistic market scenario (quiet day, volatile day, gap up, slow drift)
2. Trace the full path: data arrives → strategy processes → decision made → order placed → fill detected → position managed → exit
3. At each step, ask: what if this step never completes? What if it takes much longer than expected?
4. Pay special attention to paths where the system is "waiting" — these are where silent deadlocks hide

**Example walkthrough (quiet gold day):**
```
1. Range forms (00:00-06:00) ✓
2. LLM approves ✓
3. Velocity = 50/min, threshold = 168 → STUCK (no escape)
4. Price drifts below range → still STUCK (stale check gated on velocity)
5. End of day → DONE_TODAY (missed the breakout entirely)
Bug found: steps 3-4 are a deadlock with no escape
```

---

## Part 4: Important Caveat

This guide captures patterns from specific bugs found in this codebase. It is NOT exhaustive. The next important bug will likely be something not listed here.

The purpose of this guide is to **expand** what a reviewer considers, not to **limit** review to these patterns. If something seems wrong but doesn't fit any pattern here, trust that instinct and investigate.

A good review covers both layers:
- **Structural:** logic errors, missing error handling, type safety, test coverage
- **Runtime/integration:** deadlocks, invisible state, parameter plausibility, adapter contracts, stale state, scenario walkthroughs

Neither layer alone is sufficient. The bugs that hurt most in production are the ones where the code is correct but the system doesn't work.
