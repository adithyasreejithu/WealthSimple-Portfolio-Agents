# Phase 4 Design Decisions

**Date:** 2026-08-10 / 2026-08-11
**Status:** Decided during implementation. Binding on whoever next touches
`thesis_validation.py`, `cli.py`'s new subcommands, or `state.py`.

Agent-level design rationale (why the `investment-analyst` agent is shaped
the way it is) lives in
[`docs/agents/investment-analyst/plan.md`](../../../agents/investment-analyst/plan.md).
This document covers implementation-level decisions instead.

---

## Decision 1 — CLI stages, not a new skill

Per `00-overview.md` §3.2/§4.1 ("Non-resource deterministic work belongs in
Python modules or CLI stages rather than Claude skills" /
"`build-investment-worksheet` becomes a new deterministic module/stage, not a
skill" / "`validate-investment-thesis` becomes a new deterministic
module/stage, not a skill"), the three new operations
(`build-worksheet`/`check-thesis`/`save-thesis`) are `src/workspace/cli.py`
subcommands, following the file's own thin-wrapper pattern exactly
(`_cmd_*` functions calling into a library module, printing JSON via
`_print()`). No new `.claude/skills/*` directory was created. The business
logic lives in `thesis_validation.py` (`check_worksheet_ref`,
`scope_from_worksheet_ref`, `check_thesis_draft`, `save_thesis`), not in
`cli.py` itself.

## Decision 2 — `check-thesis` / `save-thesis` split, not one command

Mirrors the legacy `stock-analyst` agent's own
`validate_recommendation.py --precompute-only` / full-validate split, which
this repo has already observed to control agent iteration cost well.
`check-thesis` is called an unbounded number of times while a draft is being
fixed and writes nothing; `save-thesis` re-validates independently (never
trusting a prior `check-thesis` call saw identical bytes) and is the only
place that writes `agent_outputs/`, registers evidence, or appends an audit
event.

## Decision 3 — `save-thesis` is authoritative over `policy_version`,
`artifact_id`, and `validation{}`

The agent's draft values for these three fields are discarded, not merged.
`save_thesis()` always overwrites them from its own fresh computation:
`policy_version` from `analysis_models.POLICY_VERSION`, `artifact_id` freshly
generated (`th_<hex>`), and `validation{}` from the call's own
`validate_thesis()` result. Mirrors `evidence.register()`'s own
`content_hash` (computed by the registry, never trusted from a caller) and
`stock-analyst.md`'s "the rubric verdict stands... never hand-tune it."

## Decision 4 — evidence type and audit events

`evidence_type="investment_thesis"` (matches `00-overview.md` §8.3's own
`artifact_type: investment_thesis`), `source_name="investment_analyst"`,
`collection_method="llm_judgment"` — deliberately distinct from the
worksheet's `"deterministic_transform"`, so the registry makes LLM-authored
vs. mechanically-derived evidence greppable. New audit events:
`analyst_drafted` (on a successful save) and `validated` (every save
attempt, success or failure). Not `challenged`/`reviewed`/`promoted` — those
belong to later, unbuilt phases (8, human review, promotion). `check-thesis`
deliberately appends no audit event, to avoid log spam across many
iterations.

## Decision 5 — `state.py` gains `insufficient_evidence`

Added as a new first-class status rather than reusing `failed`.
`analysis_scope_schema.md`'s `critical_gap_policy` documentation already
commits to this exact term ("the run's status becomes
`insufficient_evidence`") as part of the locked Phase 0/1 contract — not
implementing it literally would leave a permanent doc/code mismatch. It is
also semantically distinct from `failed` (a bug/exception): this is an
expected, correct outcome when the pipeline meets a real data gap.
Transitions are modeled directly on `FAILED`'s (reachable from
`created`/`in_progress`; exits to `in_progress` for a human-supplied-evidence
reopen, and to `archived`); it is included in `TERMINAL_STATUSES`.
`run.py::set_status` routes a note into `metadata.errors` (not `warnings`)
and sets `completed_at` for this status, matching `FAILED`'s handling. No
model change was needed beyond `state.py` — `RunMetadata.status`'s validator
already reads `state_module.STATUSES` dynamically.

## Decision 6 — found and fixed: a Windows newline bug in Phase 3's worksheet writer

`investment_worksheet.py::build_worksheet_for_run` wrote the worksheet JSON
via `Path.write_text(worksheet_json, encoding="utf-8")`. On Windows,
`write_text` performs universal-newline translation on write (`\n` ->
`\r\n`), so the sha256 computed from the in-memory string (LF-only) never
matched the sha256 of the bytes actually on disk (CRLF) once re-hashed. This
was a genuine, pre-existing Phase 3 defect — nothing before this phase ever
cross-checked `worksheet_hash` against the live file, since evidence
registration computes its own hash independently from disk bytes rather than
trusting the returned value. Phase 4's `check_worksheet_ref` (the first code
to compare `worksheet_ref.hash` against the live file) surfaced it
immediately: every `save_thesis`/`check_thesis_draft` call against a
freshly-built worksheet failed with a hash mismatch. **Fixed by switching to
`Path.write_bytes(worksheet_json.encode("utf-8"))`** for both the worksheet
JSON and the sibling `analyst-context.md`, which writes the exact encoded
bytes with no platform-dependent translation. `tests/test_investment_analyst_cli.py`
exercises the real `build_worksheet_for_run` path end-to-end (not a hand-built
worksheet fixture), so a regression here would fail loudly again.

## Decision 7 — test fixtures build real worksheets, not hand-authored stand-ins

`tests/test_investment_analyst_cli.py`'s `RunDirCase` calls the real
`investment_worksheet.build_analysis_scope` + `build_worksheet_for_run`
against the existing PLTR fixture bundle, then builds an
`investment-thesis.v1` draft that copies the *real* returned
`worksheet_ref`/`valuation_methods`/`scenario_inputs`/`unknowns` verbatim
(`build_draft_from_worksheet`), rather than hand-authoring a synthetic
worksheet reference the way `test_investment_thesis_validation.py`'s
`build_valid_thesis_payload()` does. This is deliberate: Phase 4's new
`check_worksheet_ref` check makes the worksheet-artifact-must-really-exist
constraint load-bearing for the first time, and only a real
`build_worksheet_for_run` call exercises the exact byte-for-byte path an
agent's `run check-thesis`/`run save-thesis` calls actually take (this is
also how Decision 6's bug was caught). One pre-existing test in
`test_investment_thesis_validation.py`
(`ValidateThesisEndToEndTest.test_valid_thesis_with_registered_evidence_and_scope_is_valid`)
needed a small fix for the same reason: its synthetic `worksheet_ref` pointed
at a file that never existed on disk, which `check_worksheet_ref` now
correctly flags. Fixed by adding a `_register_worksheet()` helper that writes
a real (minimal) file and returns its true hash.

---

## Summary of the file boundary for Phase 4

| Action | Files |
|---|---|
| **Create** | `.claude/agents/investment-analyst.md`; `docs/agents/investment-analyst/architecture.md`, `plan.md`; `tests/test_investment_analyst_cli.py` |
| **Modify** | `src/workspace/thesis_validation.py` (new checks + `check_thesis_draft`/`save_thesis`), `src/workspace/cli.py` (three new subcommands), `src/workspace/validation.py` (schema-aware `agent_outputs` branch), `src/workspace/state.py` + `run.py` (`insufficient_evidence` status), `src/workspace/investment_worksheet.py` (Decision 6's newline fix), `docs/reference/cli.md`, `tests/test_run_workspace.py`, `tests/test_investment_thesis_validation.py` |
| **Read, never write** | `Knowledge-Base/taxonomy/decision-rubric.yml`, `decision-framework.yml` — the agent never touches either (see the roadmap-scope correction in `docs/plans/implementation/phase-4/HANDOFF.md`) |
| **Must not appear in the diff** | `src/portfolio_metrics.py`, `src/analytics.py`, `src/security_technicals.py`, `dashboard/**`, `.claude/skills/**` |
