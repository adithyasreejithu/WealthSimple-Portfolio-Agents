# Agent & Skill Usage Tracking

The repository has three runtime log surfaces, all git-ignored, all sharing
the same pipe-delimited `key=value` convention:

| File | Answers | Written by |
|---|---|---|
| `logs/SystemLogs.txt` | What did the pipeline do? | `src/system_logger.py` (see `docs/architecture/logger_notes.md`) |
| `logs/AgentSkillUsage.txt` | Which agents and skills ran, and what did they cost? | `.claude/hooks/usage_tracker.py` — described below |
| `logs/SkillTrace.txt` + `.jsonl` | How good was the data a skill collected? | `src/skill_trace.py` — see [Skill completeness trace](#skill-completeness-trace) |

## What is logged

`.claude/hooks/usage_tracker.py` is registered as a Claude Code hook in
`.claude/settings.json`. Claude Code runs it once per event, passing one JSON
payload on stdin; the script appends one entry to `logs/AgentSkillUsage.txt`.

| Hook event | Log entry |
|---|---|
| `SessionStart` | A `=== SESSION START ===` separator block with the session id, source (`startup` / `resume` / `clear` / `compact`), and model — this is the visual break between run sessions. |
| `SessionEnd` | A `SESSION END` line with the session id, reason, and total session duration. |
| `PostToolUse` (matcher `Skill`) | A `SKILL` line with the skill name and its arguments (truncated to 120 chars). If the skill ran inside a subagent, `agent=<type>` is appended. |
| `SubagentStart` | An `AGENT+` line with the agent type and short agent id. |
| `SubagentStop` | An `AGENT-` line with the agent type, short agent id, token usage, and duration. |

## Line format

Pipe-delimited `key=value` fields, matching the `SystemLogs.txt` convention:

```
================================================================
=== SESSION START 2026-07-13 18:02:11 | id=a1b2c3d4 | source=startup | model=claude-fable-5
================================================================
2026-07-13 18:02:45 | SKILL  | name=classify-portfolio | args=-
2026-07-13 18:03:10 | AGENT+ | type=stock-data-prep | id=f9e8a7b6
2026-07-13 18:05:32 | AGENT- | type=stock-data-prep | id=f9e8a7b6 | tokens=28737 (in=1200 out=3500 cache_read=24037 cache_write=0) | duration=142s
2026-07-13 18:06:01 | SKILL  | name=evaluate-stock-decision | args=AAPL | agent=stock-analyst
2026-07-13 18:40:00 | SESSION END | id=a1b2c3d4 | reason=other | duration=37m49s
```

## Token usage: how it works and its caveat

Claude Code hook payloads do not include token counts. On `SubagentStop`, the
payload does include `agent_transcript_path` — the subagent's transcript JSONL
file — and the tracker sums the `message.usage` fields
(`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`) across its entries. `duration` comes from the
first and last transcript timestamps.

The transcript format is **internal to Claude Code and undocumented**, so it
may change between versions. If parsing fails for any reason (missing file,
schema drift), the entry logs `tokens=unknown` instead of failing — the hook
never blocks tool use and always exits 0.

Skills carry no token figure at all: a skill runs inside the calling
conversation, not as a separate context, so its cost is not attributable.
A skill invoked by a subagent is covered by that subagent's `AGENT-` total.

## Session length

Every hook payload includes `transcript_path` — the main session's own
transcript JSONL. On `SessionEnd`, the tracker reads that file's first and
last message timestamps the same way `SubagentStop` derives subagent
duration, and reports the span as `duration=`. No state is persisted between
`SessionStart` and `SessionEnd` to compute this. If the transcript is
missing, unreadable, or has fewer than two timestamped entries, the entry
logs `duration=unknown` instead of failing.

## Operational notes

- Hook configuration is read at session start; changes to
  `.claude/settings.json` or the script's registration take effect in the
  **next** Claude Code session.
- The script is stdlib-only and resolves the repo root from its own location,
  so it does not depend on `$CLAUDE_PROJECT_DIR` expansion or the invoking
  shell. The registered command uses a repo-relative path
  (`python .claude/hooks/usage_tracker.py`); hooks run with the project
  directory as the working directory.
- To track a new hook event, add a `_format_<event>` function and a branch in
  `format_event()` in `.claude/hooks/usage_tracker.py`, register the event in
  the `hooks` block of `.claude/settings.json`, and cover it in
  `tests/test_usage_tracker.py`.

## Skill completeness trace

`usage_tracker.py` answers *which* skills ran and what they cost. The trace
answers a different question: **was the data any good?** Without it,
degradation in a source is invisible until an analysis quietly scores against
half-empty data.

Written by `src/skill_trace.py`. Producers today: `investment-analyst-resources`,
`market-analyst-resources`, `security-technicals`, `security-status`, and the
(non-skill) `investment_worksheet` module.

The first two collect data; `security-technicals` and `security-status`
collect none — the former computes over stored prices, the latter resolves a
ticker's owned/wishlist/avoid/retired status from stored state. Both emit a
trace anyway because the question the trace answers ("was the data any
good?") applies equally to a calculation or lookup whose inputs may be thin:
a ticker with sixty days of history cannot produce an SMA-200, and that fact
belongs in the same place a missing options chain does.
`investment_worksheet.build_worksheet_for_run` follows the same reasoning one
stage later: it records whether the technicals/prior-thesis/market-context
inputs a worksheet can carry were actually present, so a silently absent
input (see the evidence-type collision this module's `_find_latest_evidence`
used to be vulnerable to, fixed by giving `security_technicals`/
`security_status`/`policy_worksheet` distinct evidence types) shows up here
too, not just in the worksheet's own `unknowns` list. The rule is therefore
**any skill whose output quality depends on input
coverage**, not "any skill that fetches".

### The three-way split

A field is one of:

| Outcome | Meaning | Counts toward completeness? |
|---|---|---|
| `ok` | Obtained | Yes |
| `missing` | Should have been obtained, wasn't | Yes (as a failure) |
| `not_applicable` | Nothing existed to obtain | **No** — reported separately |

Only `ok + missing` forms the denominator. This distinction is the reason the
module exists. Previously "not applicable" was counted as "missing", so a
healthy run on a TSX ticker with no options chain and no analyst coverage
reported:

```
completeness_pct=81.4 | missing=derived.put_call_oi_ratio,derived.put_call_volume_ratio,
derived.atm_iv_near,derived.atm_iv_far,derived.iv_skew,derived.max_oi_call_strike,
derived.max_oi_put_strike,derived.upgrades_90d,derived.downgrades_90d,
derived.net_revisions_365d,derived.net_insider_shares
```

Eleven "gaps", none of them real, burying anything that mattered. The same run
now reads:

```
2026-08-05 21:25:04 | TRACE | skill=investment-analyst-resources | subject=L | kind=stock | pct=100.0 | ok=48 | graded=48 | failed=0 | missing=- | n_a=derived.options[7],derived.analyst[3],derived.insider[1]
```

Groups are collapsed to counts so the line stays scannable; the `.jsonl`
sidecar keeps every field name plus the per-domain breakdown:

```json
{"ts": "...", "skill": "investment-analyst-resources", "subject": "L", "kind": "stock",
 "completeness_pct": 100.0, "fields_ok": 48, "fields_graded": 48, "fields_not_applicable": 11,
 "missing": [], "not_applicable": ["derived.options.iv_skew", "..."],
 "domains": {"position": {"ok": ["quantity"], "missing": [], "not_applicable": []}}}
```

### Where traces are written

- `logs/SkillTrace.txt` — one scannable line per run, every invocation
- `logs/SkillTrace.jsonl` — the same run with full structure, for trend queries
- the run's `audit_log.jsonl` — additionally, when the skill attached to a run
  workspace, so an archived run carries its own data-quality history
  (`docs/architecture/run_workspace.md`)

Writing never raises: a logging failure must not sink a data pull that
already succeeded, the same discipline `usage_tracker.py` follows.

### The `workspace` field: was this pull even auditable?

Every trace also carries a `workspace` block reporting what happened to the
run-workspace attachment for that invocation:

```json
"workspace": {"status": "created", "run_id": "2026-08-08T...Z_data_pull_PLTR_...", "skip_reason": null}
```

or, when attachment was skipped:

```json
"workspace": {"status": "skipped", "run_id": null, "skip_reason": "--no-run"}
```

This exists because attaching to a run workspace used to be opt-in
(`--run-id`), and an absent run looked identical to "no pull happened" — a
gap an audit trail cannot tell apart from silence. Both producer skills now
attach by default for any invocation that produces evidence, and the text
line always names the outcome so a skip is a positive, greppable fact rather
than an absence:

```
... | run=2026-08-08T...Z_data_pull_PLTR_4108db
... | run=skipped(--no-run)
```

See "Two ways a run begins" in `docs/architecture/run_workspace.md` for the
full default-on decision and its trade-offs.

### Adding a producer

Build a `skill_trace.Trace`, add one domain per logical group with its `ok` /
`missing` / `not_applicable` fields, and call `skill_trace.emit`. Skills that
already track per-entry statuses rather than per-field presence can use
`skill_trace.from_status_map` instead — that is how `market-analyst-resources`
maps its registry statuses (`fetched` → ok, `stale`/`overdue`/`no_data` →
missing, `not_configured` → not-applicable, since a `<TBD>` registry stub is
not data a run failed to fetch).

## Tests

`tests/test_usage_tracker.py` loads the script by path (it lives under
`.claude/hooks/`, not `src/`) and covers transcript token summing, graceful
degradation on malformed input, per-event line formatting, and the
stdin-to-logfile flow. `tests/test_skill_trace.py` covers the completeness
arithmetic, the text/JSONL formats, and the run-audit integration.

```
uv run python -m unittest tests.test_usage_tracker tests.test_skill_trace
```
