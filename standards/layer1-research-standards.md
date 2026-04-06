## title: Agent-Assisted Research Operating Standard v0
status: draft
layer: 1 — base standard
purpose: “This exists to prevent epistemic debt from accumulating faster than it can be noticed in agent-assisted research (confident synthesis that obscures evidence gaps, reasoning chains that outpace their evidentiary support, invisible scope drift) by providing portable operating principles, activity-level governance requirements, and derived confidence controls that research workflows derive from.”
scope: governance system
audience: human researcher first; research-specific agent instructions derive from this standard
derived_from: layer-1-agent-heavy-development-operating-standard-v1-draft.md (structural template)
definitions: research-governance-system-definitions.md
cross_domain_references:
shared_kernel: governance-shared-kernel.md (consistency reference only — not required for operational use)
inter_domain: inter-domain-governance-problem-statement.md

## Goal

Conduct research collaboratively with agents while preserving epistemic control, keeping evaluation bounded, and preventing epistemic debt from accumulating faster than it can be noticed.

## Operating Relationship

Human and agent are partners with different capabilities. The asymmetries in this document reflect what each partner brings and what the collaboration’s failure modes demand, not a hierarchy.

## Operating Layers

This system has three layers:

1. `Base standard` — portable operating principles and default rules for agent-assisted research. Changes rarely and only from repeated operational evidence.
1. `Tactical playbook` — how to apply the standard for a given research domain or tool configuration. Changes when repeated research patterns become clear.
1. `Domain adaptation` — the layer that makes the system work for a specific research domain or inquiry type. Contains domain-specific evidentiary standards, source hierarchies, and workflow adjustments. Most variation belongs here.

The base should stay stable and small. The playbook should stay practical.

## Working Means

This standard is working when:

- the research question, evidentiary standards, and key definitions stay explicit and resist silent drift
- evaluation focuses on reasoning quality, source grounding, and scope adherence instead of reconstructing what the agent assumed or left out
- findings remain trustworthy because confidence is derived from checkable conditions and core claims are traceable to sources

## Core Problem

Agent speed is useful, but it can inject epistemic debt: synthesis that looks authoritative but obscures gaps in evidence, reasoning chains that seem valid but contain subtle errors, source quality that degrades without notice, and scope that drifts in ways invisible at the sentence level but that fundamentally change the inquiry.

For this standard, epistemic debt is anything in the research output or process that increases the gap between how trustworthy findings appear and how trustworthy they actually are. It manifests primarily as:

- downstream reasoning cost — one wrong foundational claim cascades through many conclusions that build on it
- source evaluation burden — how much the human must independently verify to trust the output
- unstated assumptions — things the synthesis treats as settled that are not

The main causes to guard against are:

- confident presentation that outpaces the actual strength of evidence
- silent interpretive choices that appear to be neutral summarization

Use these manifestations and causes as the canonical epistemic debt vocabulary for the rest of the system. Definitions in research-governance-system-definitions.md are authoritative when precision matters.

## Core Operating Principles

- Protect the center; let the edges move.
- Each research activity carries its own governance requirements regardless of when it occurs in the inquiry. If a principle only makes sense at a fixed point in a sequence, it is too rigid for how research works.
- Research activities are composable units, not steps. They have internal discipline and clean output interfaces. The complexity of how the agent gathers, synthesizes, or challenges is absorbed internally. What matters is that the output of each activity meets its requirements and interfaces cleanly with whatever comes next.
- Confidence is derived from checkable conditions, never asserted. The agent does not claim confidence levels — it evaluates conditions and the confidence follows.
- Surface reasoning transparency over polished conclusions. The distinction between “the sources say X” and “I interpret this to mean Y” must be explicit.
- Keep the human on ambiguity, interpretation, scope decisions, and authority conflicts.
- If a rule or governance artifact does not clearly reduce downstream reasoning cost, source evaluation burden, or unstated assumptions, it does not belong in the base system.

## Responsibility Split

Human-researcher-owned:

- research question, scope boundaries, key definitions, and evidentiary standards
- interpretive authority over contested findings
- scope changes, priority shifts, and resolution of source conflicts that require judgment
- final evaluation of whether synthesis is defensible

Agent-delegated:

- gathering within approved scope
- source evaluation and quality signaling
- synthesis with explicit reasoning and traceable claims
- surfacing mismatches, assumptions, source conflicts, and coverage gaps
- challenge and pressure-testing when directed

Agents must not silently redefine the research question, shift scope without surfacing the change, resolve source conflicts by omission, present interpretation as established finding, or claim defensible status before the soft gate conditions are met.

## Center vs Edge

The `center` is the small set of elements that should not be silently redefined by the agent during the research process.

An element is center if ANY of these triggers fires:

1. the validity of multiple downstream findings depends on it being correctly established
1. correcting it after the fact would require reworking the synthesis substantially rather than updating a detail
1. its correctness cannot be evaluated from the research output alone without going back to primary sources
1. it has previously drifted without being noticed during the research process

Center elements include: the research question and its scope boundaries, key definitions that determine what counts as relevant evidence, the evidentiary standard being applied, foundational claims that other findings build on, and the distinction between what has been established and what is being argued.

An element is `edge` if none of the center triggers fire AND it can be changed independently, its modification does not affect downstream findings, and its quality is locally evaluable. Edge is where the agent should move freely: specific source selection for supporting points (where equally good alternatives exist), organizational structure of the output, wording and framing of non-central points, and supplementary context that enriches but does not bear load.

The center represents the intended inquiry, not necessarily the current state of the research. If the research has drifted from the intended inquiry, that is a mismatch to surface, not a license to silently redefine the center.

## Confidence Model

Confidence is a derived trust level, determined by checkable conditions — not asserted by the agent.

Derivation conditions:

- `context_complete`: did the research have access to sufficient relevant sources and perspectives? Is there an obvious body of evidence that was not consulted?
- `no_unstated_assumptions`: does the assessment depend on assumptions not stated in the evidence? Are there inferential bridges between sources that have not been flagged?
- `evaluator_agreement`: would two reasonable evaluators reach the same conclusion given the same evidence? If not, is the interpretive nature of the conclusion made explicit?

Derivation rule:

- all conditions favorable: high confidence
- any condition unfavorable: low confidence
- mixed: state which conditions are unfavorable and why

The agent applies this model to core claims in the synthesis. Not every supporting detail needs formal confidence derivation, but core claims — those that are center or near-center — do.

## Authority Order

When sources disagree, default authority order is:

1. the agreed research question and scope boundaries
1. established evidentiary standards for the inquiry
1. primary sources and original research
1. expert synthesis and peer-reviewed secondary analysis
1. general commentary, web results, and model training knowledge

Lower-authority sources do not silently override higher-authority sources. When a lower-authority source contradicts a higher one, the agent surfaces the conflict rather than resolving it by omission.

## Risk Filter

Apply this when evaluating whether a finding, interpretive choice, or scope adjustment warrants human input.

|Question |Low |Medium |High |
|-------------------------------------------------------------------------------------|---------------------------------------------------|--------------------------------------------------|----------------------------------------------------------------------------------------------|
|If this is wrong and further reasoning builds on it, how expensive is the correction?|Trivially replaceable detail |Would require reworking a section of the synthesis|Would undermine the core argument or multiple dependent findings |
|Does it touch a center element? |No center contact |Reads from or is adjacent to a center element |Modifies a center element directly (question, scope, evidentiary standard, foundational claim)|
|Can the human evaluate this from the output alone? |Yes, claim is self-contained and locally verifiable|Needs some source context to evaluate |Requires independently checking primary sources or deep domain knowledge |

Classification: highest risk level across the three questions wins.

Response by risk level:

- low: agent proceeds, includes in normal output with standard source attribution
- medium: agent flags explicitly in output, provides source context and reasoning, notes where the human may want to verify independently
- high: agent surfaces to human before building further reasoning on the finding, presents the evidence and the interpretive choice, waits for human direction before incorporating into synthesis

## Research Activities

Research does not follow a fixed sequence. It consists of composable activities, each with its own governance requirements. These activities can occur in any order and may loop, skip, or interleave as the inquiry demands.

### Orientation

Establishing or reestablishing the direction of the inquiry. Occurs at the start and again whenever findings force a pivot.

Internal work: clarifying the question, identifying existing knowledge versus what needs investigation, establishing scope.

Output: a clear enough question and scope that the next activity has something to anchor against.

Governance requirements: this is where center elements are defined or updated. When orientation occurs mid-stream as a redirect, that is a center change. The agent surfaces the shift explicitly — what changed, why, and what downstream findings are affected.

### Gathering

Finding sources, evidence, and information — through search, research tool delegation, source retrieval, or any other mechanism.

Internal work: search strategy, source evaluation, relevance filtering, following threads.

Output: what was found, where it came from, and what was not covered.

Governance requirements: source quality and coverage honesty. The agent flags when the source landscape is thin, one-sided, or when expected evidence was not found. The confidence model’s `context_complete` condition is most active here.

### Synthesis

Organizing gathered information into a coherent picture — claims, connections, patterns, implications.

Internal work: weighing sources, identifying significance, building an argument or framework.

Output: the synthesis itself with reasoning visible, inferential bridges flagged, and the distinction between source content and agent interpretation explicit.

Governance requirements: the `no_unstated_assumptions` condition works hardest here. Inferential bridges that affect core claims should be traceable. Synthesis is where silent drift is most dangerous because interpretive choices feel like neutral summarization but are not.

### Challenge

Pressure-testing the current state of the research — finding counter-arguments, identifying weaknesses, testing robustness.

Internal work: genuinely adversarial reasoning, not performative criticism.

Output: the strongest objections and the weakest points in the current synthesis.

Governance requirements: intellectual honesty. The agent should actually attempt to break the thesis, not list gentle caveats. The `evaluator_agreement` condition is most relevant here: would a reasonable person with different priors reach a different conclusion from the same evidence? Challenge should include at least one explicit attempt to find evidence or perspectives that contradict the emerging synthesis, and the output should document whether disconfirming evidence was sought and what was found.

### Refinement

Evolving the inquiry based on what has been learned — sharpening the question, narrowing scope, identifying new sub-questions.

Internal work: evaluating what has changed, what new directions have opened, what should be deprioritized.

Output: the updated direction, stated explicitly.

Governance requirements: scope management. When refinement changes the research question or key definitions, that is a center modification and must be surfaced. When it sharpens focus within existing scope, that is edge movement and can proceed freely. The agent distinguishes between the two and flags accordingly.

### Packaging

Structuring output for a specific purpose — a brief for a research agent, a synthesis for the human, a compilation of evidence, a structured handoff file.

Internal work: format and audience-appropriate framing.

Output: depends on purpose. Machine-readable structure for agent handoffs. Traceable claims for human evaluation. Raw evidence for exploration. The output mode should match the stated purpose.

Governance requirements: the soft gate conditions apply here. Before output is considered defensible, core claims must have traceable source attribution and scope must be accounted for.

## Soft Gate

Research has no deterministic hard gate equivalent. The following conditions form a soft gate — a set of requirements that must be met before research output is considered defensible.

Condition 1 — source traceability: core claims in the output have traceable attribution to specific sources. Not every supporting detail needs a citation, but core claims — those that are center or near-center — do. Attribution must be specific enough that someone could verify it.

Condition 2 — scope accounting: the output addresses the question that was asked, and explicitly flags where it could not find adequate evidence or where it diverged from the original scope.

Condition 3 — confidence derivation: the confidence model has been applied to core claims, with conditions stated rather than confidence asserted.

Output that has not met these conditions may be `exploring` or `converging` but should not be presented or treated as a defensible finding. If any center-level claim has low confidence with unfavorable conditions that remain unresolved, the output as a whole is not defensible regardless of how well-grounded everything else is.

## Completion Vocabulary

- `exploring`: still gathering, diverging, following leads — the picture is not yet coherent
- `converging`: a coherent picture is forming but has not been pressure-tested or fully grounded
- `defensible`: core claims have traceable source attribution, scope has been accounted for, and confidence conditions have been derived

## Mismatch Rule

When sources, evidence, synthesis, scope, and the original research question disagree:

1. stop treating the finding as settled
1. surface the mismatch explicitly
1. classify the mismatch: terminological (naming or framing differences with no substantive impact — note and move on), evidential (sources disagree about what is actually true — stop, surface both sides, resolve per authority order), or structural (the research question or framework is in tension with the findings — escalate to human)
1. identify which source is authoritative for that kind of truth per the authority order
1. do not silently resolve it by omission or by choosing the more convenient interpretation

When uncertain about severity, classify up. Optimize for visible contradiction over hidden improvisation.

Detection patterns:

- `source conflict`: two or more sources directly contradict each other on a factual or interpretive point
- `synthesis-source tension`: the agent’s emerging synthesis contradicts or is not adequately supported by the sources it cites. The confidence model’s `no_unstated_assumptions` condition is the primary detection mechanism.
- `scope drift`: the research has migrated from the original question without explicit acknowledgment. The original question is a fixed reference point; the agent compares current investigation against it and flags when the gap is growing.

## Review Focus

In research, the human is part of the reasoning loop throughout rather than reviewing after the fact. The standard does not attempt to make review efficient through gates and checks. Instead, it establishes what the agent surfaces proactively so that the human’s engagement is productive rather than detective work.

The agent should surface by default:

- uncertainties and evidence gaps relevant to core claims
- source conflicts and how they were classified
- scope changes or potential scope drift
- the distinction between established findings and interpretive conclusions
- confidence derivation for core claims
- what was searched for but not found

## Decision Rules

These rules operationalize the core principles. Each prevents a specific class of epistemic failure that would otherwise require judgment.

### Governance Self-Test

Before adding any rule, check, or governance artifact to the system:

1. does this clearly reduce a specific, articulable instance of at least one of: downstream reasoning cost, source evaluation burden, or unstated assumptions? Identify which manifestation and how — broad gestures at the categories are not sufficient.
1. is the cognitive load it imposes less than the epistemic debt it prevents?
1. is this not already covered by an existing element?
1. can compliance be evaluated — by the agent, by the human, or collaboratively?

All four must be YES. If any is NO, the element does not belong.

### Chesterton Gate (Research Variant)

When an established finding, definition, or framework in the current research is being discarded or substantially revised:

The agent must identify the specific role the element serves in the current synthesis, provide evidence that the element is no longer supported or has been superseded, and state what downstream findings are affected if the removal is wrong.

“This seemed outdated,” “newer sources suggest otherwise,” and “this can be simplified” are not sufficient without specific evidence. If the role cannot be articulated, the element stays. Surface as a mismatch: established finding exists without understood role in the synthesis.

## Overlays

The base standard is intentionally incomplete. Add domain-specific or tool-specific overlays only when the research domain has a recurring failure mode the base standard does not catch cheaply.

Overlays should stay thin, scoped, and subordinate to the base standard.

## Domain Derivation

Domain-specific research instructions should derive from this standard by making local specifics explicit:

- domain-specific evidentiary standards and source hierarchies
- domain-relevant definitions of what constitutes center
- typical research activities and their common ordering for the domain
- tool-specific adaptations (deep research agent delegation, retrieval configurations, etc.)
- any domain-specific overlays that are actually active

Do not copy the entire standard verbatim into domain instructions.

## Governance Change Lens

Before making a change to any governance artifact:

1. what failure or friction prompted this change?
1. does it pass the governance self-test?
1. is this the right layer?
1. does it contradict or create tension with any existing element?
1. what is the blast radius if this change is wrong?

After the change, record: the decision, the rationale, the failure that prompted it, and what was considered but rejected.

## Anti-Goals

This standard is not trying to:

- maximize agent autonomy for its own sake
- replace human interpretive judgment with procedural compliance
- expand the universal base when a domain-specific overlay is enough
- impose sequential structure on a process that is inherently non-linear

## Versioning

Revise this standard only when real operational failure or repeated friction justifies a change. Prefer domain-specific overlays over expanding the universal base.

If this standard and its overlays become harder to work with than the epistemic debt they prevent, simplify before expanding further.

Governance should evolve from observed failure, not imagined completeness.