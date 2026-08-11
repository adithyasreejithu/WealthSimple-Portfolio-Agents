# Phase 4 Handoff — Key Changes and Context for Future Sessions

**Date:** 2026-08-11
**Status:** Phase 4 complete

---

## What Phase 4 Built

The Investment Analyst agent (`.claude/agents/investment-analyst.md`) — the
one required LLM judgment stage in the rebuilt workflow — plus its three
deterministic CLI stages (`run build-worksheet`, `run check-thesis`,
`run save-thesis`, all `src/workspace/cli.py` subcommands wrapping new
functions in `src/workspace/thesis_validation.py`). Given a run with a
registered `investment-analyst-resources` bundle, the agent reads the
Phase-3 worksheet (never the raw bundle), drafts an `investment-thesis.v1`
artifact, iterates against a side-effect-free validation loop, and finalizes
through a save step that authoritatively overwrites `policy_version`/
`artifact_id`/`validation{}` and registers the result as evidence.

**Read `design-decisions.md` before touching any of this again.** It records
seven decisions, two of them binding beyond this phase: the `state.py`
addition (`insufficient_evidence`) and a pre-existing Phase 3 Windows
newline/hash bug this phase's new check exposed and fixed.

## Critical Things to Know

### 1. A scope correction was applied and is binding

The prompt that started this phase (garbled by truncation in transit)
described building an agent that scores the worksheet against
`Knowledge-Base/taxonomy/decision-rubric.yml` and emits a `buy/sell/hold/
trim/add/watchlist/avoid` decision. **That is not what was built, and must
never be built inside this agent.** The already-locked `investment-thesis.v1`
contract (`docs/architecture/investment_thesis_schema.md` §2) hard-forbids a
`decision-framework.yml` action anywhere in the artifact tree —
`analysis_models.py`'s `find_forbidden_fields()` rejects it structurally, at
any nesting depth, not by convention. `decision-rubric.yml` is retained
*only* as a separate legacy benchmark path (`00-overview.md` §11.5/§14),
never invoked by the rebuilt runtime. What was actually built: an agent
emitting `fundamental_rating`/`valuation_stance`/`thesis_direction`/
`thesis_confidence`, validated by the unmodified Phase 1 validator. If a
future session is ever asked to "add a decision/action field" to this
agent's output, that request conflicts with a decision this repository has
already locked in writing — flag it rather than implementing it silently.

### 2. A pre-existing Phase 3 bug was found and fixed

`investment_worksheet.py::build_worksheet_for_run` wrote the worksheet JSON
via `Path.write_text()`, which silently translates `\n` → `\r\n` on Windows.
The hash returned to callers (computed from the in-memory, LF-only string)
therefore never matched the actual bytes on disk once re-hashed. This was
invisible until Phase 4 added the first code that ever checks
`worksheet_ref.hash` against the live file
(`thesis_validation.check_worksheet_ref`). **Fixed by switching to
`write_bytes()`** for both the worksheet JSON and the sibling
`analyst-context.md`. If you see a `worksheet_ref.hash no longer matches`
error on a *freshly built* worksheet again, this is the first thing to
check — verify the fix is still in place, and check for any other
`write_text()` call in this package whose output is later hashed.

### 3. `check-thesis`/`save-thesis` policy semantics, precisely

`confidence_cap()` (unchanged, Phase 1) has two independent branches: **any**
required evidence domain graded `missing` in the worksheet's
`evidence_health.domain_status` forces a `low` cap *unconditionally* —
this does not depend on whether the run's `critical_gap_policy` is `stop` or
`proceed_with_gap_disclosure`. The completeness-percentage threshold check
(50%/80% bands → `low`/`medium`) only applies when no required domain is
outright missing. In other words: `critical_gap_policy` controls whether the
*run itself* halts before drafting (`evidence_health.blocking`); the
confidence cap is a separate, always-active check on the *draft* regardless
of that policy. Both are enforced this phase — one by the agent's own
halt-on-blocking instruction, the other unconditionally by
`save_thesis`/`check_thesis_draft`.

### 4. What the agent is instructed to never do

Never read the raw `market_data_bundle` artifact (only the worksheet and
analyst-context); never touch `decision-rubric.yml`/`decision-framework.yml`;
never write to `Knowledge-Base/`; never hand-tune
`policy_version`/`artifact_id`/`validation{}` (overwritten regardless); never
draft when `evidence_health.blocking` is `true`. All of this is instructional
(`.claude/agents/investment-analyst.md`'s Guardrails section) except the
`policy_version`/`artifact_id`/`validation{}` overwrite and the forbidden-field
scan, which are code-enforced.

### 5. Analyst-bias concern flagged for Phase 5 benchmark

The agent's output vocabulary (`fundamental_rating`, `valuation_stance`,
`thesis_direction`, `thesis_confidence`) is symmetric — it supports
`unattractive`/`negative` conclusions equally to `attractive`/`positive` ones,
and the agent's guardrails explicitly forbid portfolio-decision language
(line 165–168 in `.claude/agents/investment-analyst.md`). However, the
agent's prompt does not explicitly push for symmetry in argumentation (e.g.,
"argue the bear case as rigorously as the bull case" or "present the case
against this investment as thoroughly as the investment case"). Combined with
the workflow framing (runs are created to "analyze TICKER"), there is a
structural risk that the agent may default to positive/constructive framing
in practice — reporting why something is attractive more thoroughly than why
to avoid it. This is not a Phase 4 code issue, but a design assumption worth
testing empirically in Phase 5's benchmark against the legacy `stock-analyst`
path. If the new agent produces significantly fewer "Sell/Avoid" conclusions
than the old path on the same set of tickers, that is evidence of bias
requiring either prompt adjustment, workflow design changes (separate "threat
assessment" triggers), or explicit acceptance as a feature (the new path is
for "research/analysis" not "sell decisions"). See the Phase 5 benchmark rubric
in `00-overview.md` §13.2/§5 for the comparison framework.

---

## Files to Remember

| File | Purpose | Notes |
|---|---|---|
| `src/workspace/thesis_validation.py` | `check_worksheet_ref`, `scope_from_worksheet_ref`, `check_thesis_draft`, `save_thesis` | `save_thesis` is the one place `agent_outputs/` gets written and `policy_version`/`artifact_id`/`validation{}` become authoritative |
| `src/workspace/cli.py` | `build-worksheet`/`check-thesis`/`save-thesis` subcommands | Thin wrappers only — no business logic here |
| `src/workspace/validation.py` | `agent_outputs` loop's new `investment-thesis.v1` branch | Must stay ahead of the generic `AgentOutput.model_validate` parse in the loop, or every thesis artifact hard-fails `run validate` |
| `src/workspace/state.py`, `run.py` | `INSUFFICIENT_EVIDENCE` status | See design-decisions.md Decision 5 for why this isn't just `FAILED` |
| `.claude/agents/investment-analyst.md` | The agent itself | `model: opus`, `tools: [Bash, Read, Write]`, no `skills:` field |
| `tests/test_investment_analyst_cli.py` | 16 tests | Uses the *real* `build_worksheet_for_run` path (not a hand-built worksheet fixture) — this is what caught Decision 6's bug and will catch a regression |
| `docs/plans/implementation/phase-4/design-decisions.md` | Binding decisions | Read before touching `thesis_validation.py`'s new functions, `cli.py`'s new subcommands, or `state.py` |

---

## What Phase 5 Depends On

Phase 5 (side-by-side benchmark against the legacy `stock-analyst` path,
roadmap row 5) can now invoke the `investment-analyst` agent end-to-end on a
real run and compare its `investment-thesis.v1` output against a separately
produced legacy recommendation for the same ticker/date, per the benchmark
rubric in `00-overview.md` §13.2/§5. Neither path invokes the other —
`00-overview.md` §3.2's reuse boundary is unchanged by this phase.

Not wired into Phase 4 (unchanged from Phase 3's own list, still accepted as
optional parameters degrading to an explicit gap when absent): a real
prior-thesis loader (Phase 5/7), a Market Researcher digest (Phase 10), and
the challenger pass (Phase 8, always `{required: false, completed: false}`
this phase).

---

## Quick Verification (for a new session)

```bash
uv run python -m unittest tests.test_investment_analyst_cli -v
uv run python -m unittest discover -s tests
git diff --stat src/portfolio_metrics.py src/analytics.py src/security_technicals.py dashboard/ .claude/skills/ Knowledge-Base/taxonomy/decision-rubric.yml   # must be empty
```

---

## Ready for Phase 5

The Phase 4 gate is met. Phase 5 can begin when approved — per the roadmap's
execution protocol, this is an explicit per-phase decision, not a default.
