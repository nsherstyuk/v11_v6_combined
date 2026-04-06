# Standards Summary for Coding Agents

**Source documents:** `C:\ibkr_grok-_wing_agent\standards\`  
**Purpose:** Actionable reference for any agent starting a session on this project. Read this before writing code.

---

## 1. Center vs Edge (All Three Standards)

**Center elements** must not be changed without explicit human approval. An element is center if ANY of:
- 3+ other components depend on its current definition
- Changing it incorrectly requires > 1 day of repair
- Correctness cannot be verified from a diff alone
- It defines the contract between two or more ownership boundaries
- It has previously drifted silently

**In this project, center elements include:**
- Darvas detection rules (`core/darvas_detector.py`)
- LLM response schema (`llm/models.py`)
- Trade execution logic (`execution/trade_manager.py`)
- Safety limits (`config/live_config.py` — max_daily_trades, max_daily_loss, max_entry_drift_atr)
- Core types / shared data contracts (`core/types.py`)
- Research question, scope, key definitions, evidentiary standards

**Edge elements** — move freely:
- Prompt wording (`llm/prompt_templates.py`)
- Logging format
- UI/output formatting
- Local helper functions not shared across modules
- Internal refactors that don't change interfaces

**Rule:** Protect the center, let the edges move. If implementation and intended architecture disagree, that's a mismatch to surface — not a license to silently redefine what's intended.

---

## 2. Risk Assessment (Before Every Change)

Ask three questions. Take the HIGHEST risk level:

| Question | Low | Medium | High |
|---|---|---|---|
| If wrong and unnoticed for 2 weeks, how expensive is repair? | < 1 hour | 1 hour – 1 day | > 1 day or irreversible |
| Does it touch a center element? | No | Reads from / near boundary | Modifies center directly |
| Can correctness be judged from the diff alone? | Yes | Needs some context | Requires deep hidden context |

**Low:** Proceed. Verify locally. Hand off with evidence.  
**Medium:** Surface what center elements or boundaries might be touched. State assumptions. Be explicit about what your checks can and cannot evaluate.  
**High:** STOP. Get human approval before implementing. Explain what you're touching, why, and blast radius if wrong.

If risk increases mid-flight, pause and re-assess. Don't push through.

---

## 3. Testing Standards (Two-Phase, Intent + Regression)

### Phase 1: Specification (before writing any test code)

For each feature/component, answer:
1. What are the important **design decisions**?
2. For each, what's the **intent**? (concrete: "what must be true")
3. For each, what does **regression** look like? (observable: "what would I see if it broke")
4. Does every important decision have a corresponding test?

### Phase 2: Implementation

Each test must satisfy ALL five criteria:
1. **Asserts business-meaningful behavior**, not structural plumbing
2. **Would catch a real bug** if the implementation regressed
3. **Traces to a defined spec** (architecture doc, design decision, API contract)
4. **Is not tautological** (doesn't verify "code does what code does")
5. **Boundary tests are legitimate only when the boundary itself matters**

### Critical Rules

- **Tests are locked to design decisions.** A failing test means the implementation may have drifted, NOT that the test is outdated. Changing a test = the design decision changed → that's a design conversation, not maintenance.
- **Never silently fix a test to match implementation.** This is the single most damaging thing you can do — it destroys evidence of a bug.
- **Coverage = design decisions covered**, not lines of code. An important decision with no test is the most dangerous gap.
- **Don't mock away what should be tested.** Mock external services you don't control. Don't mock boundaries that are part of the system's contract.
- **Derive assertions from "what regression looks like"**, not from current implementation output.

### Anti-Patterns to Avoid

- Testing implementation sequence instead of outcomes
- Mocking everything then asserting mocks were called (circular)
- `expect(result).toEqual(functionUnderTest(input))` — tautological
- 20 edge-case tests + 2 core logic tests — inverted priority
- Happy-path-only testing

---

## 4. Deep Modules and Code Structure

When creating or modifying a module boundary, answer three questions:
1. **What specific design decision does this boundary hide?** ("nothing specific" = boundary may not be needed)
2. **Why is that decision likely to change?** (if never, boundary costs more than it saves)
3. **Is the interface narrower than the implementation?** (if not, it's just indirection)

**Do:**
- Prefer simple interfaces with complex implementations
- Absorb complexity inside modules, don't leak it

**Don't:**
- Create abstractions for things used only once
- Add pass-through layers that just forward calls
- Widen interfaces without clear need
- Extract helpers that don't actually hide a decision

---

## 5. Mismatch Rule (All Three Standards)

When code, docs, tests, or instructions disagree:

1. **Stop** treating the path as settled
2. **Surface** the mismatch explicitly — state what disagrees with what
3. **Classify** severity:
   - *Cosmetic:* naming/formatting, no behavioral consequence → note and move on
   - *Behavioral:* sources disagree about what the system should DO → stop, resolve via authority order
   - *Architectural:* disagreement about intended architecture/ownership → escalate to human
4. **Do not silently pick a winner.** Visible contradiction is always better than hidden improvisation.

When uncertain about severity, classify UP.

---

## 6. Authority Order

When sources disagree, default priority (highest to lowest):

1. Explicit project rules and agent instructions
2. Approved task brief / scope for current work
3. Contracts, schemas, and boundary definitions in code
4. Documentation about where truth lives
5. Implementation and runtime behavior (evidence of current behavior, NOT authority over intended behavior)

Implementation does not silently override what the system is *supposed* to do.

---

## 7. Responsibility Split

**Human partner owns:**
- Architecture boundaries and source-of-truth decisions
- Core invariants and non-negotiable rules
- Task framing and risk framing
- Approval of center changes
- Completion authority (deciding when work is done)

**Agent owns:**
- Implementation inside approved scope
- Local verification and evidence gathering
- Surfacing mismatches, assumptions, and boundary contact

**Agents must NOT:**
- Silently redefine the research question or scope
- Resolve source conflicts by omission
- Present interpretation as established finding
- Expand scope without surfacing the change
- Claim work is "complete" — only "reviewable"

---

## 8. Handoff Requirements

When finishing work, always state:
- What you changed and why
- What checks you ran and what they show
- What assumptions you made
- What mismatches you noticed
- **What your checks CANNOT evaluate** (limits of your own verification)

### Completion Vocabulary

- **Exploring** — still investigating, picture not yet coherent
- **Reviewable** — coherent enough for human review, not yet confirmed complete
- **Complete** — agreed completion gate has passed (human confirms)

---

## 9. Chesterton's Fence

Before removing or simplifying existing code (especially in protected areas):

1. Identify the specific problem the complexity addresses
2. Provide evidence the problem no longer exists
3. State the blast radius if you're wrong

**If you cannot articulate the purpose, it stays.** Surface it as a question.

---

## 10. Confidence Model (Research Standard)

Confidence is derived from checkable conditions, never asserted:

- **context_complete:** Did we have access to sufficient relevant sources?
- **no_unstated_assumptions:** Does the assessment depend on unstated assumptions?
- **evaluator_agreement:** Would two reasonable evaluators reach the same conclusion?

All favorable → high confidence.  
Any unfavorable → low confidence.  
Mixed → state which conditions are unfavorable and why.

---

## Quick Reference Checklist (Per Task)

```
Before coding:
  □ Identify center elements in the area I'm touching
  □ Assess risk (3 questions → low/medium/high)
  □ If high risk → get human approval first
  □ Read relevant docs, contracts, boundaries

While coding:
  □ Stay within approved scope
  □ If scope needs to grow → surface, don't expand silently
  □ If I find a mismatch → stop and surface it
  □ Deep modules: simple interfaces, complex implementations

Testing:
  □ Phase 1: identify design decisions → write intent + regression specs
  □ Phase 2: implement tests from specs
  □ Every important decision has a test
  □ Tests assert behavior, not implementation
  □ Never fix a failing test without confirming the design changed

Handoff:
  □ What changed and why
  □ What I checked and what it shows
  □ What I assumed
  □ What I could NOT verify
  □ Status: exploring / reviewable / complete
```
