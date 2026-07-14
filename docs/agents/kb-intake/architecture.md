# KB Intake Agent

This document is the human-readable companion to the Claude Code agent defined at
[`.claude/agents/kb-intake.md`](../../../.claude/agents/kb-intake.md).
The original design rationale is preserved in [`plan.md`](plan.md).

## Purpose

The agent is the sole writer of the research wiki (`Knowledge-Base/`,
excluding `ref/`, `README.md`, and `CHANGELOG.md`): it ingests external
documents, creates and updates stock thesis pages following the goal doc's
thesis-update logic, changes portfolio status, and syncs generated pages
from the classification workflow. It corresponds to the goal doc's "Wiki
Update Agent" plus document intake.

## Runtime Settings

- `model: sonnet` — the deliberate exception to this repo's usual `haiku`
  agents. Comparing new information against an existing thesis (is it
  stronger, weaker, unchanged, or broken?) and deciding what belongs in
  Updated Thesis vs. what must never touch Original Thesis is a judgment
  task, not a fixed script.
- `tools: ["Bash", "Read", "Edit", "Write"]` — the only KB agent with write
  access. `Bash` runs the fixed skill scripts (create/validate/set-status/
  intake/sync); `Read`/`Edit`/`Write` handle the prose sections of thesis
  pages that the scripts deliberately leave to agent judgment.

## Skill Dependencies

```yaml
skills:
  - kb-search
  - kb-intake-document
  - kb-update-thesis
  - kb-sync-portfolio
```

| Skill | Role | Used by |
|---|---|---|
| `kb-search` | Find existing pages before writing (mandatory first step for every request) | `kb-intake` agent, always first |
| `kb-intake-document` | markitdown conversion, filing under `sources/`, optional companion note | `kb-intake` agent (document ingestion requests) |
| `kb-update-thesis` | Deterministic edges of thesis lifecycle: create/validate/set-status, append-only logs, and `ingest_recommendation.py` (commit a stock-analyst recommendation) | `kb-intake` agent (thesis create/update/status-change and recommendation-ingest requests) |
| `kb-sync-portfolio` | Regenerate `portfolio/holdings.md` and `portfolio/portfolio-overview.md` from the classification JSON | `kb-intake` agent (sync requests) |

## Workflow

1. `kb-intake` is the entrypoint for every wiki-writing request.
2. It runs `kb-search` first, unconditionally, to check what already exists
   — this prevents duplicate pages and silent contradictions with prior
   research.
3. Based on the request, it invokes exactly one of `kb-intake-document`,
   `kb-update-thesis`, or `kb-sync-portfolio`'s scripts for the
   deterministic parts of the work.
4. For thesis work specifically, it then uses `Read`/`Edit` directly on the
   stock page to fill in or update prose sections — the scripts create
   structure and enforce immutability elsewhere, but do not write thesis
   content themselves (see `kb-update-thesis/references/thesis-contract.md`).
   For recommendation ingestion, `ingest_recommendation.py` does the
   deterministic Status-block/Decision-History/log updates from a validated
   `stock-analyst` artifact (never changing Portfolio Status), and the agent
   transcribes the artifact's narratives — including the Analyst View — into the
   prose sections. See
   [`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md).
5. It runs `thesis_page.py validate` before finishing any thesis edit, and
   appends the relevant log rows.
6. It reports a Wiki Update Summary (pages touched, thesis verdict, log
   entries, what to monitor next).

## Guardrails

- Writes only inside `Knowledge-Base/`, only in the subfolders each skill
  is scoped to; never touches `ref/*.yaml` or `CHANGELOG.md`.
- Never deletes pages or log rows; never rewrites Original Thesis.
- Every mutation gets an `update-log.md` entry.
- No fabricated data — cites sources or records gaps as Open Questions.
- Declines pure-lookup requests (see Handoffs).
- Never processes multiple tickers concurrently — `kb-update-thesis`'s
  index/log helpers (`src/kb_pages.py`) read a shared file whole and
  overwrite it whole with no locking, so concurrent commits across tickers
  can silently drop another ticker's row. Commit tickers one at a time. See
  "Multi-ticker / batch runs" in
  [`docs/architecture/decision_support_flow.md`](../../architecture/decision_support_flow.md).

## Handoffs

| Label | Agent | Prompt |
| --- | --- | --- |
| Redirect pure lookups | kb-discovery | "This is a read-only lookup with no write intent — kb-discovery searches the knowledge base without writing." |

## Code Location

- `.claude/skills/kb-intake-document/scripts/intake_document.py` — markitdown
  conversion is imported lazily (`from markitdown import MarkItDown` inside
  a function) so the module loads even in environments without `markitdown`
  installed, and so tests can monkeypatch the conversion call without the
  dependency present.
- `.claude/skills/kb-update-thesis/scripts/thesis_page.py` and
  `scripts/append_log.py` — the deterministic edges (page scaffolding,
  structural validation, status transitions, append-only log rows).
- `src/kb_pages.py` — shared front-matter/index/log helpers used by all four
  skills; lives in `src/` (not one skill's `scripts/`) per
  [`docs/architecture/claude_agent_skill_structure.md`](../../architecture/claude_agent_skill_structure.md),
  the same reasoning as `kb-discovery`'s skill dependency.
