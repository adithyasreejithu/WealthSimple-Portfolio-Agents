# KB Discovery Agent

> **Retired.** This agent was archived 2026-07-16 ("Phase 0: Archive
> Agent-Development agents/skills as reference") and, unlike `kb-intake`/
> `stock-analyst`/`stock-data-prep` from that same archive, was never
> restored. It is not invokable today. The rest of this page is kept as
> historical reference for the design, not as current documentation. A pure
> read-only lookup now invokes the `kb-search` skill directly instead of a
> dedicated subagent -- see `docs/reference/knowledge_base_workflow.md` and
> `docs/architecture/knowledge_base.md`.

This document was the human-readable companion to the Claude Code agent
formerly defined at
[`.claude/agents/_archive/kb-discovery.md`](../../../.claude/agents/_archive/kb-discovery.md).
The original design rationale is preserved in [`plan.md`](plan.md).

## Purpose

The agent answers "what does the research wiki already know about X" and
gathers pre-research context before another agent (`kb-intake`) writes new
pages or updates a thesis. It stays narrow, deterministic, and strictly
read-only.

## Runtime Settings

- `model: haiku` — search-and-report is not a judgment-heavy task; a fast,
  low-cost model is sufficient.
- `tools: ["Bash", "Read"]` — least privilege. `Bash` runs the fixed search
  scripts; `Read` opens the specific files those scripts return. No
  `Write`/`Edit` access at all, so the agent cannot mutate the wiki even by
  mistake.

## Skill Dependencies

```yaml
skills:
  - kb-search
```

| Skill | Role | Used by |
|---|---|---|
| `kb-search` | Front-matter-aware search plus wiki validation (`kb_search.py`, `validate_kb.py`) | `kb-discovery` agent (search); `kb-intake` agent runs this first before any write |

## Workflow

1. The `kb-discovery` agent is the entrypoint for lookup and pre-research
   context requests.
2. It runs `kb_search.py` with the narrowest filters likely to work first
   (a `--ticker` or `--query` lookup), widening only if needed.
3. It `Read`s the specific pages the search returned — never files it
   guessed at.
4. It returns a Context Report (see the agent body's Output Format) rather
   than raw script output.

## Guardrails

- No write tools; cannot edit, move, or create wiki pages.
- Never touches `Knowledge-Base/ref/*.yaml` or `CHANGELOG.md`.
- Declines create/update/ingest/sync requests (see Handoffs).
- States explicitly when nothing matches instead of fabricating content.

## Handoffs

| Label | Agent | Prompt |
| --- | --- | --- |
| Route write requests | kb-intake | "This request needs to create/update a page, ingest a document, or sync portfolio data — kb-intake handles writes to the knowledge base." |

## Code Location

All logic lives in the `kb-search` skill
(`.claude/skills/kb-search/scripts/kb_search.py`,
`.claude/skills/kb-search/scripts/validate_kb.py`), which import shared
front-matter/index helpers from `src/kb_pages.py` (used by multiple KB
skills, so it lives in `src/` rather than beside one skill, per
[`docs/architecture/claude_agent_skill_structure.md`](../../architecture/claude_agent_skill_structure.md)).
The agent itself contains no logic beyond invoking that skill and reading
its results.
