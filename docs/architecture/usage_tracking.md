# Agent & Skill Usage Tracking

The repository has two runtime logs:

- `logs/SystemLogs.txt` — pipeline events, written by `src/system_logger.py`
  (see `docs/architecture/logger_notes.md`).
- `logs/AgentSkillUsage.txt` — Claude Code agent and skill activity, written
  by the hook script described here. Both files are git-ignored.

## What is logged

`.claude/hooks/usage_tracker.py` is registered as a Claude Code hook in
`.claude/settings.json`. Claude Code runs it once per event, passing one JSON
payload on stdin; the script appends one entry to `logs/AgentSkillUsage.txt`.

| Hook event | Log entry |
|---|---|
| `SessionStart` | A `=== SESSION START ===` separator block with the session id, source (`startup` / `resume` / `clear` / `compact`), and model — this is the visual break between run sessions. |
| `SessionEnd` | A `SESSION END` line with the session id and reason. |
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
2026-07-13 18:40:00 | SESSION END | id=a1b2c3d4 | reason=other
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

## Tests

`tests/test_usage_tracker.py` loads the script by path (it lives under
`.claude/hooks/`, not `src/`) and covers transcript token summing, graceful
degradation on malformed input, per-event line formatting, and the
stdin-to-logfile flow. Run with:

```
python -m unittest tests.test_usage_tracker
```
