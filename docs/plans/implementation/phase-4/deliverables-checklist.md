# Phase 4 Deliverables Checklist

**Status:** completed and ready for review
**Date completed:** 2026-08-11
**Companion:** [`../../investment-analyst-rebuild-roadmap.md`](../../investment-analyst-rebuild-roadmap.md) (Phase 4 row), [`design-decisions.md`](design-decisions.md) (binding decisions this phase followed)

---

## Summary

Phase 4 built the Investment Analyst agent — the one required LLM judgment
stage in the rebuilt workflow — plus the three deterministic CLI stages it
drives (`run build-worksheet`, `run check-thesis`, `run save-thesis`) and the
`thesis_validation.py` functions behind them. Also fixed one pre-existing
Phase 3 defect this phase's new hash check surfaced (design-decisions.md
Decision 6), and added a small, deliberate extension to the run-lifecycle
state machine (`insufficient_evidence`, Decision 5) that the locked Phase 0
contract had already named but no code implemented yet.

**A scope correction was applied at the start of this phase.** The task
description that kicked it off (garbled by truncation) described scoring the
worksheet against `Knowledge-Base/taxonomy/decision-rubric.yml` and emitting
a `buy/sell/hold/trim/add/watchlist/avoid` decision — the legacy
`stock-analyst` path, which the locked `investment-thesis.v1` contract
explicitly and structurally forbids (`analysis_models.py`'s
`FORBIDDEN_FIELD_NAMES`/`DECISION_FRAMEWORK_ACTIONS`). What was actually
built instead matches the repo's own committed architecture: an agent
producing `fundamental_rating`/`valuation_stance`/`thesis_direction`/
`thesis_confidence`, validated by the already-built Phase 1 validator. See
`HANDOFF.md` for the full record.

---

## Files Created

| File | Purpose | Status |
|---|---|---|
| **`.claude/agents/investment-analyst.md`** | The agent: `model: opus`, `tools: [Bash, Read, Write]`, no skill dependency. Reads the worksheet + analyst-context, drives the three CLI stages, halts on a blocking evidence gap. | ✅ Complete |
| **`docs/agents/investment-analyst/architecture.md`** | Purpose, runtime settings, inputs/outputs, the halt-on-blocking and confidence-cap contracts, section-state rules, the three CLI stages' argument shapes, explicit "what this agent does not do" list. | ✅ Complete |
| **`docs/agents/investment-analyst/plan.md`** | Design rationale: "Python calculates, the LLM interprets", the legacy-rubric correction, why `insufficient_evidence` was added, why `save-thesis` is authoritative. | ✅ Complete |
| **`tests/test_investment_analyst_cli.py`** | 16 tests: happy path (build→check→save), rejection surfacing (fabricated evidence, worksheet-hash mismatch, ticker mismatch), the blocking scenario (a required domain fully failed TRACE forces a `low` confidence cap regardless of stated completeness), the warning-threshold scenario (no domain missing, but stated completeness in the 50–80% band caps at `medium`), a placeholder-labeling regression guard, `run validate`'s new schema-aware branch actually engaging, and full CLI wiring via `cli.main()`. | ✅ Complete, all passing |
| **`docs/plans/implementation/phase-4/design-decisions.md`**, **`README.md`** | Phase tracking, mirroring the phase-0/1/2/3 folder pattern. | ✅ Complete |

## Files Modified

| File | Change | Status |
|---|---|---|
| **`src/workspace/thesis_validation.py`** | Added `check_worksheet_ref` (worksheet_ref path/hash integrity check, wired into `validate_thesis`), `scope_from_worksheet_ref` (reads a worksheet's own `request_and_scope`/`evidence_health.domain_status` for a caller), `check_thesis_draft` (side-effect-free validation loop), `save_thesis` (authoritative finalize: overwrites `policy_version`/`artifact_id`/`validation{}`, writes `agent_outputs/`, registers evidence, appends audit events). | ✅ Complete |
| **`src/workspace/cli.py`** | Three new subcommands: `build-worksheet`, `check-thesis`, `save-thesis`, thin wrappers per the file's existing pattern. | ✅ Complete |
| **`src/workspace/validation.py`** | The `agent_outputs` loop now branches on `schema == "investment-thesis.v1"` before the generic `AgentOutput.model_validate` parse (which would otherwise hard-fail on every thesis document), re-running `thesis_validation.validate_thesis` as a second, independent, whole-run gate. | ✅ Complete |
| **`src/workspace/state.py`**, **`src/workspace/run.py`** | Added `INSUFFICIENT_EVIDENCE` status (Decision 5): reachable from `created`/`in_progress`, terminal, exits to `in_progress`/`archived`; `set_status` routes its note to `errors` and sets `completed_at`. | ✅ Complete |
| **`src/workspace/investment_worksheet.py`** | Fixed a Windows-only newline/hash bug (Decision 6): `write_text` → `write_bytes` for the worksheet JSON and analyst-context markdown, so the returned `worksheet_hash` always matches the live file. | ✅ Complete |
| **`docs/reference/cli.md`** | Documented the three new `run` subcommands and the `insufficient_evidence` status. | ✅ Complete |
| **`tests/test_run_workspace.py`** | Added `insufficient_evidence` state-transition tests to `StateTransitionTest`. | ✅ Complete |
| **`tests/test_investment_thesis_validation.py`** | Added `_register_worksheet()` helper and fixed one pre-existing test whose synthetic `worksheet_ref` pointed at a file that never existed on disk (now caught, correctly, by the new `check_worksheet_ref`). | ✅ Complete |

---

## Note: a pre-existing Phase 3 defect found, not a Phase 4 regression

`investment_worksheet.py::build_worksheet_for_run` computed `worksheet_hash`
from an in-memory string, then wrote that string to disk via
`Path.write_text(..., encoding="utf-8")`. On Windows, `write_text` performs
universal-newline translation (`\n` → `\r\n`) with no way to opt out short of
`write_bytes`, so the returned hash never matched the file's actual bytes on
disk once re-hashed. This was invisible before Phase 4 because nothing
previously cross-checked `worksheet_hash` against the live file — evidence
registration (`evidence.register()`) computes its own hash independently
from disk, so the *evidence record* was always internally correct; only the
*returned* `worksheet_hash` value (exactly what Phase 4's
`investment-thesis.v1`'s `worksheet_ref.hash` needs to cite) was wrong. Found
via `tests/test_investment_analyst_cli.py`'s tests, all of which exercise the
real `build_worksheet_for_run` path rather than a hand-built worksheet
fixture. Fixed in this phase's diff — see `design-decisions.md` Decision 6.

---

## Gate Verification

Roadmap gate: *"Produces a valid thesis for the PLTR fixture; passes the
Phase 1 validator; never reads the raw resource bundle directly; never
writes the KB; repeated runs on identical input are materially stable."*

```bash
uv run python -m unittest tests.test_investment_analyst_cli -v   # 16 tests, OK
uv run python -m unittest discover -s tests                      # full suite, OK
```

- **Valid thesis for the PLTR fixture, passes the Phase 1 validator:**
  `HappyPathTest` builds a real worksheet from the PLTR fixture, drafts a
  faithful `investment-thesis.v1`, and confirms both `check_thesis_draft` and
  `save_thesis` accept it — `save_thesis` is a direct call into the
  unmodified Phase 1 `validate_thesis`.
- **Evidence citations resolve:** `RejectionTest` proves a fabricated
  `evidence_id` is caught and named exactly, both via `check-thesis` (no
  side effects) and `save-thesis` (writes nothing on failure).
- **Required-domain TRACE failure blocks the artifact:**
  `BlockingScenarioTest` — a required domain fully failed under
  `initial_research`'s `stop` policy forces `confidence_cap()` to `low`
  regardless of the draft's stated completeness; a `high`-confidence draft is
  rejected, a `low`-confidence draft passes (matching the actual policy
  semantics: the domain-missing rule is unconditional, not scoped to one
  `critical_gap_policy` value — see `design-decisions.md`).
- **Completeness-threshold capping:** `WarningThresholdScenarioTest` — no
  domain missing, but a draft claiming completeness in the 50–80% warning
  band is capped to `medium`; `high` is rejected, `medium` passes.
- **Valuation/scenario placeholders never restated:**
  `PlaceholderLabelingTest` — the draft's `valuation.methods`/`scenarios`
  match the worksheet's own `valuation_methods`/`scenario_inputs`
  byte-for-byte.
- **`run validate` is a real second gate:** `RunValidateIntegrationTest` —
  passes after a clean save, and independently catches a post-hoc corrupted
  `worksheet_ref` on the same saved artifact.
- **CLI wiring itself works:** `CliWiringTest` drives the full
  `build-worksheet → check-thesis → save-thesis` sequence through
  `cli.main()` against a real run created via `run create`.
- **Never reads the raw resource bundle / never writes the KB:** enforced by
  the agent definition's guardrails (`.claude/agents/investment-analyst.md`)
  and by construction — no code path in this phase's deliverables touches
  `Knowledge-Base/` or opens a `market_data_bundle` artifact.
- **No dashboard/security-technicals/skill-layer change:** `git diff --stat`
  touches only `src/workspace/*`, the new agent/docs/test files, and
  `docs/reference/cli.md` — no `src/portfolio_metrics.py`, `src/analytics.py`,
  `src/security_technicals.py`, or `.claude/skills/**`.

---

## What Phase 5 Depends On

Phase 5 (the side-by-side benchmark against the legacy path) can invoke the
`investment-analyst` agent end-to-end on a real run once
`investment-analyst-resources` has populated it, and compare the resulting
`investment-thesis.v1` against a separately-produced legacy `stock-analyst`
recommendation for the same ticker/date per the roadmap's benchmark rubric.
Nothing in Phase 4 wires the two paths together — they remain fully
independent per `00-overview.md` §3.2's reuse boundary.

Not wired into Phase 4: a real prior-thesis loader (Phase 5/7), a Market
Researcher digest (Phase 10), and the challenger pass (Phase 8) — all remain
explicit `unknowns`/`{exists: false}`/`{required: false}` placeholders the
agent carries forward without inventing.

## Quick Verification (for a new session)

```bash
uv run python -m unittest tests.test_investment_analyst_cli -v
uv run python -m unittest discover -s tests
git diff --stat src/portfolio_metrics.py src/analytics.py src/security_technicals.py dashboard/ .claude/skills/ Knowledge-Base/taxonomy/decision-rubric.yml   # should be empty
```
