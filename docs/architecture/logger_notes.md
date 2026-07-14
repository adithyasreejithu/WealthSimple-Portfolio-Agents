# Logger Notes

These notes cover the pipeline logger (`src/system_logger.py` →
`logs/SystemLogs.txt`). Claude Code agent/skill invocations and token usage
are logged separately to `logs/AgentSkillUsage.txt` — see
`docs/architecture/usage_tracking.md`.

## Findings

- `src/system_logger.py` was present but not yet wired into `src/data_sorter.py`.
- The logger path needed to be repo-relative so the log file lands in a predictable place.
- The sorter had no logging around file selection, footer trimming, unknown rows, or move failures.

## Recommendations

- Keep the shared logger helper in `src/system_logger.py`.
- Write logs to `logs/SystemLogs.txt` under the repo root.
- Log the major pipeline stages:
  - source discovery
  - footer trimming
  - row classification
  - move success/failure
- Use `logger.exception(...)` in the top-level CLI entrypoint so failures are captured with stack traces.
