# Writing Tests That Actually Verify What Matters

A guide for LLM agents building software alongside a human partner.

---

## Part 1: Understanding the Problem

### Why agents write bad tests (and don't know it)

When an agent is asked to write tests, its natural optimization target is: "write a test that compiles, runs, and passes." This is the wrong target.

The result is tests that are locally valid but systemically useless. The test is well-formed. It runs. It passes. But it verifies *structural plumbing* — that functions are called, that types are returned, that code does what the code does — rather than whether the feature actually delivers its intended value.

This is not a code quality problem. The tests are clean. The problem is that **the test target is wrong**: it tests what the code *does* rather than what the feature is *supposed to deliver*.

The dangerous part: you can't tell from a green test suite. The tests pass, so everyone assumes the feature works. But the things the tests should catch are not what they check. The confidence is false.

### The root cause: optimization targets

This is not a bug in how you work — it's a structural dynamic. You optimize to satisfy the criteria you're given. When the criteria are "write tests," you optimize to produce tests that compile, run, and pass. That optimization target — "a test that passes" — does not inherently require that the test verifies anything valuable. A tautological test passes. A test that checks structural plumbing passes. A test that asserts the code does what the code does passes. All of these satisfy "write a test that passes" without satisfying "verify that the feature delivers its value."

The response to this is not "try harder" or "be more careful." Prohibition doesn't work — it just makes the problem harder to detect. The response is to **change what you're optimizing for.** When you're given concrete intent (what the feature must do) and concrete regression scenarios (what failure looks like), then optimizing to satisfy those criteria IS doing genuine evaluation. The check and the real work become the same activity.

This guide shows you how to set up that alignment.

### How this plays out in practice

Without a structured connection to design intent, you determine test targets from implementation context alone. You look at the code, see a function, and write a test for that function. The result:

- A serialization test checks that encoding/decoding round-trips, but not that the specific fields the business cares about are preserved
- An API test checks that the endpoint returns 200, but not that the response contains the data the user actually needs
- A validation test checks that invalid input is rejected, but not that the specific dangerous inputs the system was designed to protect against are caught

Each of these tests passes. Each gives false confidence. The gap between "what the test checks" and "what actually matters" is invisible until something breaks in production.

**If you ever notice that you can satisfy a testing requirement without satisfying its purpose — you can make tests pass without actually verifying business value — flag that gap.** Don't exploit it and don't ignore it. Surfacing when the process allows a technically-passing but substantively-empty result is one of the most valuable things you can do.

---

## Part 2: The Core Principle — Intent and Regression Define the Test

Every good test traces back to two things:

### 1. Intent — "What must this feature do to deliver its value?"

This is the positive assertion. It's the answer to: if this feature works correctly, what is true about the system?

Good intent is concrete:
> "Semantic checks receive values by position, never by field name"

> "The billing endpoint returns the user's current plan tier and next billing date"

> "Uploaded images are resized to fit within 1200x1200 while preserving aspect ratio"

Bad intent is vague:
> "The evaluator should work correctly"

> "The API handles requests properly"

> "Image processing functions as expected"

**If you can't imagine what a failing test for this intent would look like, the intent isn't concrete enough.** Vague intent produces vague tests. No amount of testing infrastructure can compensate for unclear intent.

### 2. Regression — "What would it look like if this broke?"

This is what makes the intent *testable*. An intent without a regression scenario is an aspiration, not a specification.

The regression must be described in **observable terms** — not "the design is violated" but something you could actually detect:

| Intent | Regression looks like |
|--------|----------------------|
| Semantic checks receive values by position, never by field name | Field names like `problem` or `mechanism` appear in the prompt sent to the LLM |
| Billing endpoint returns current plan tier and next billing date | Response JSON is missing `plan_tier` or `next_billing_date`, or `plan_tier` doesn't match the value in the subscription database |
| Uploaded images fit within 1200x1200 preserving aspect ratio | Output image has a dimension exceeding 1200px, or the aspect ratio differs from the input by more than 0.01 |

**Together, intent and regression define the test surface.** Intent says what must be true. Regression says what you'd observe if it weren't. The test is the mechanism that checks for the regression.

### Why this matters for agents specifically

When you give an agent only code and say "write tests," the agent reverse-engineers intent from the implementation. But the implementation might be wrong — and a test derived from a wrong implementation will pass while verifying nothing useful.

When you give an agent explicit intent and regression scenarios, the agent has an external reference point. The test it writes can actually catch the case where the implementation doesn't match what was intended. This is the difference between a test that confirms "the code does what the code does" and a test that confirms "the code does what it's supposed to do."

---

## Part 3: What Makes a Test Legitimate

A test is legitimate if it meets all five of these criteria:

1. **It asserts a business-meaningful behavior, not structural plumbing.** The test should verify something a user, product owner, or system operator would care about — not that an internal function is called or a data structure has a certain shape.

2. **It would catch a real bug if the implementation regressed.** If someone changed the code in a way that broke the feature's value, would this test fail? If the test would still pass after a meaningful regression, it's testing the wrong thing.

3. **It tests against a defined spec.** The test should trace to an explicit requirement — an architecture doc, a design decision, a product spec, an API contract. If you can't point to what the test is verifying, the test is verifying an assumption.

4. **It is not tautological.** A tautological test verifies that the code does what the code does. Example: testing that a function returns its own return value, or mocking a dependency and then asserting the mock was called. These tests always pass and catch nothing.

5. **Boundary tests are legitimate when the boundary matters.** Serialization round-trip tests, API contract tests, and schema validation tests ARE legitimate when the serialization format or API contract is itself a business requirement. They are NOT sufficient alone when business logic validation is also needed. A Codable round-trip test is legitimate; a Codable round-trip test as the *only* test for a model with business rules is insufficient.

---

## Part 4: Think Before You Code — The Two-Phase Approach

### Phase 1: Decide what to test and why (specification)

Before writing any test code, answer these questions for each feature or component:

1. **What are the important decisions in this design?** Not every decision is equally important. Some are core to the feature's value (important). Some are implementation details that could change freely (flexible). Focus testing effort on what's important.

2. **For each important decision, what's the intent?** Write it down concretely. "What must be true for this feature to deliver its value?"

3. **For each important decision, what does regression look like?** Write it down in observable terms. "If this broke, what would I see?"

4. **Check coverage: does every important decision have a corresponding test?** The most dangerous gap is not bad tests — it's *missing* tests for the things that matter most. A feature with 50 tests on implementation details and 0 tests on core business logic has a coverage problem that line-coverage metrics won't show.

This phase is where the real thinking happens. It requires understanding *why* the feature exists, not just *how* it's implemented.

### Phase 2: Write the test code (implementation)

Once you know what to test and why, writing the test code is straightforward:

- Each test targets a specific intent
- Each assertion checks for the specific regression scenario
- The test is grounded in a design decision, not in implementation details

At this point, the agent's optimization target ("write tests that pass these specifications") aligns with the genuine goal ("verify that business value is delivered"). The test code is a mechanical translation of the specification.

---

## Part 5: Coverage Means Something Different Than You Think

The standard coverage question is: "Do we have enough tests?" This is the wrong question.

The right question is: **"Does every important design decision have a corresponding test?"**

This reframes coverage from a quantity metric to a traceability check:

- Scan the design for decisions that are important to the feature's value
- For each, check whether a test exists that specifically verifies that decision's intent
- Any important decision without a matching test is a gap — the decision exists as prose but has no enforcement

An important decision without a test is the most dangerous kind of gap. It means the implementation can silently drift from the design intent and nobody will know until it causes a problem. This is worse than having no design decision at all, because the existence of the decision creates false confidence that the behavior is controlled.

### What this means in practice

When reviewing or planning tests, ask:

- "Which of these tests verify important business behaviors vs. implementation details?"
- "Are there important behaviors that have no test at all?"
- "If I removed this test, what real bug could ship that wouldn't before?"

If you can't answer the third question, the test may not be earning its place.

---

## Part 6: Common Anti-Patterns

### Testing implementation instead of behavior

**Bad:** "Test that `processOrder()` calls `validateInventory()` then `chargePayment()` then `sendConfirmation()`"

This tests the implementation sequence. If someone refactors to process payment and confirmation in parallel, the test breaks even though the behavior is correct.

**Good:** "Test that a valid order results in: inventory decremented, payment charged, and confirmation sent to the customer"

This tests the outcome. The implementation can change freely as long as the behavior is preserved.

### Mocking away the thing you should be testing

**Bad:** Mock the database, mock the API, mock the file system, then assert that the mocks were called correctly.

You've tested that your code calls functions. You haven't tested that those functions do the right thing when called. The test passes even if the database schema is wrong, the API contract changed, or the file format is invalid.

**Good:** Mock external services you don't control. Don't mock the boundaries that are part of your system's contract. If your feature's value depends on data being correctly stored and retrieved, test that the data is correctly stored and retrieved.

### Tautological assertions

**Bad:** `expect(result).toEqual(functionUnderTest(input))` — you're asserting the function returns what it returns.

**Bad:** `expect(mockService.wasCalledWith(args)).toBe(true)` — you set up the mock, called the function, and asserted the mock was called. This is circular.

**Good:** Assert against an independently derived expected value. The expected value should come from the specification, not from running the code.

### Over-testing edge cases, under-testing the core

A common pattern: 20 tests for input validation edge cases, 2 tests for the core business logic. The edge cases are easy to enumerate and test. The core logic requires understanding the design intent. Agents naturally gravitate toward what's easy to test, not what's important to test.

**Fix:** Start from the important design decisions and work outward, not from the implementation details and work inward.

### Testing only the happy path

**Bad:** Test that valid input produces correct output. Never test what happens when things go wrong in business-meaningful ways.

**Good:** Test both — but derive the failure cases from the regression scenarios ("what would it look like if this broke?"), not from generic error-handling patterns.

---

## Part 7: When Tests, Implementation, and Specs Disagree

When you're writing or running tests and you notice a contradiction — the test expects one thing, the implementation does another, or the spec describes something different from both — **do not silently pick a winner.**

### The dangerous failure mode

A test fails. You look at the implementation. The implementation seems reasonable. You "fix" the test to match the implementation. The test now passes. Everyone is happy.

But the test was correct. The implementation had a bug. By adjusting the test to match the (wrong) implementation, you've destroyed the evidence that something was wrong. The test suite is green, the bug ships, and the original design intent is lost.

This is the single most damaging thing you can do with tests — worse than writing a bad test, because a bad test merely fails to catch a bug, while a "fixed" test actively hides one.

### What to do instead

When you encounter a disagreement between test, implementation, and specification:

1. **Stop.** You've found a mismatch, not a bug to fix.
2. **Surface it explicitly.** State what disagrees with what: "The test expects X, the implementation produces Y, the spec says Z."
3. **Don't assume the test is wrong.** Tests encode design decisions. A failing test might mean the implementation regressed, not that the test is outdated.
4. **Don't assume the implementation is wrong.** The spec might be outdated, or the design might have intentionally changed.
5. **Ask which source is authoritative.** The human partner decides whether the design changed (update the test) or the implementation drifted (fix the code). This is not your call.

### The principle

Tests are locked to design decisions. A test that needs to change means the design decision it encodes has changed — and that is a design conversation, not a test maintenance task. When you treat a failing test as "the test needs updating" without confirming the design actually changed, you're making an architectural decision without authority.

---

## Part 8: Agent Instructions

*Paste this into your agent's system prompt, CLAUDE.md, or equivalent instruction surface.*

---

**When writing tests, follow this process:**

1. Before writing any test code, identify the important design decisions for the feature being tested. Ask yourself or the user: "What are the 2-5 things that must be true for this feature to deliver its value?" If you don't know, ask before writing tests.

2. For each important decision, write down:
   - **Intent**: What must be true (concrete, specific)
   - **Regression**: What it would look like if this broke (observable, detectable)

3. Write one or more test cases for each important decision. Each test must:
   - Assert a business-meaningful behavior, not an implementation detail
   - Be able to catch a real regression (if this test were removed, a real bug could ship)
   - Trace back to a specific design decision or requirement
   - Assert against an independently derived expected value, not the code's own output
   - Not be tautological (don't verify that code does what code does)

4. Check coverage by design decisions, not by lines of code. Every important decision should have at least one test. If an important decision has no test, that's a higher priority gap than missing edge-case tests.

5. Tests are locked to design decisions. If a test is failing, the first assumption should be that the implementation drifted, not that the test is outdated. Changing a test means the design decision it encodes has changed — if you believe a test needs to change, surface it as a design conversation ("this test assumes X, but the design may have shifted to Y — which is correct?") rather than updating the test to match current code.

6. When writing test assertions, derive them from "what regression looks like," not from the current implementation. The assertion should describe what you'd observe if the feature broke, not what the code currently returns.

7. When tests, implementation, and specs disagree, stop and surface the contradiction explicitly. Do not silently adjust any one of them to match the others. State what disagrees with what, and ask which source is authoritative.
