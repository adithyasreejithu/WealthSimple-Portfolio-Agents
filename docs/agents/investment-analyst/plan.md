# Investment Analyst Agent — Design Rationale

Companion to [`architecture.md`](architecture.md). Records why this agent is
shaped the way it is, not just what it does.

## "Python calculates, the LLM interprets"

The worksheet builder (`src/workspace/investment_worksheet.py`, Phase 3)
already computes every valuation range, scenario template, and TRACE-derived
completeness figure this agent needs. The agent's job is narrower than it
might look: interpret and cite, never recompute. This is why the agent has no
`skills:` dependency and no direct database/network access — everything it
needs either already exists on disk (the worksheet, the analyst context) or
is one CLI call away (`build-worksheet`, `check-thesis`, `save-thesis`). An
agent that could fetch its own data or invent its own valuation numbers would
defeat the entire point of putting a deterministic worksheet stage ahead of
it.

## Why the legacy-rubric framing was corrected

The task description that kicked off this phase (truncated/garbled in
transit) described scoring the worksheet against
`Knowledge-Base/taxonomy/decision-rubric.yml` and emitting a `buy/sell/hold/
trim/add/watchlist/avoid` decision. That is a different, already-built system
— the `stock-analyst` agent — which this rebuild deliberately does not reuse
or extend (`00-overview.md` §3.2: "Any other existing skill... Any existing
agent... Do not invoke or extend in the rebuilt runtime"). More importantly,
the locked artifact contract (`investment_thesis_schema.md` §2) hard-forbids
that exact vocabulary anywhere in this agent's output — `FORBIDDEN_FIELD_NAMES`
and `DECISION_FRAMEWORK_ACTIONS` in `analysis_models.py` reject it
structurally, not just by convention. Building the agent as originally
described would have produced an artifact the repo's own Phase 1 validator
refuses on the first run. The correction was necessary, not optional — it
brings the implementation back in line with a contract this repo had already
locked and committed to before this session began.

## Why `state.py` gained `insufficient_evidence` rather than reusing `failed`

`analysis_scope_schema.md`'s `critical_gap_policy` documentation already
names this exact status as what a blocked run becomes — that commitment
predates this phase's code. `failed` means something broke (an exception, a
bug); `insufficient_evidence` means the pipeline worked correctly and found a
real, expected data gap. Conflating them would make "how many runs hit a real
evidence gap vs. actually broke" unanswerable from `run list` without
re-reading every note by hand, which defeats `state.py`'s own stated purpose
of keeping a run's legal history readable in one place. The change is small
and additive (one constant, one tuple entry, transitions modeled directly on
`FAILED`'s), so it carries none of the cost a larger state-machine change
would.

## Why `policy_version`/`artifact_id`/`validation{}` are CLI-authoritative, not agent-typed

Mirrors the legacy `stock-analyst` agent's own precedent: "the rubric verdict
stands... never hand-tune it." An agent has no reliable way to know whether
the policy file changed between when it started drafting and when it
finishes, and a self-reported `validation{}` block is exactly the kind of
field an agent could get wrong under pressure to look done. `save_thesis()`
re-validates independently every time and overwrites all three fields from
its own fresh computation — the agent's draft values for them are placeholders
the agent is explicitly told not to spend effort on. This is the same pattern
`evidence.register()` already uses for `content_hash` (computed by the
registry itself, never trusted from a caller) and `run.py::archive_run`
(re-validates rather than trusting a stale status) — deterministic code is the
one source of truth for anything checkable in this codebase.

## Why `check-thesis` and `save-thesis` are separate commands

`check-thesis` writes nothing and can be called an unbounded number of times
while a draft is being fixed — cheap, side-effect-free, no audit log spam.
`save-thesis` is the one commit point: it re-validates independently (never
trusting a prior `check-thesis` call saw identical bytes — the agent could
have edited the file in between) and is the only place an `agent_outputs/`
artifact, an evidence record, or an audit event gets written. Splitting these
mirrors the legacy `stock-analyst` agent's own
`validate_recommendation.py --precompute-only` / full-validate split, which
this repo has already observed to control agent iteration cost well in
practice.
