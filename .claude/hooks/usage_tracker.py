"""Claude Code hook script that logs agent/skill usage to logs/AgentSkillUsage.txt.

Registered in .claude/settings.json for SessionStart, SessionEnd,
PostToolUse (Skill), SubagentStart, and SubagentStop. Each invocation
receives one JSON payload on stdin and appends at most one entry to the
usage log. The script must never fail loudly: a broken tracker must not
block tool use, so every path degrades to writing nothing and exiting 0.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = REPO_ROOT / "logs" / "AgentSkillUsage.txt"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
SEPARATOR = "=" * 64
ARGS_MAX_LEN = 120
ID_LEN = 8


def _now() -> str:
    return datetime.now().strftime(DATE_FORMAT)


def _short_id(value: object) -> str:
    text = str(value or "").strip()
    return text[:ID_LEN] if text else "-"


def _clean_args(value: object) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return "-"
    if len(text) > ARGS_MAX_LEN:
        text = text[: ARGS_MAX_LEN - 3] + "..."
    return text


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_transcript_usage(path: object) -> dict | None:
    """Sum token usage across a subagent transcript JSONL file.

    The transcript format is internal to Claude Code and undocumented, so
    any surprise (missing file, changed schema, unreadable lines) returns
    None and the caller logs tokens=unknown instead of raising.
    """
    try:
        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        first_ts: datetime | None = None
        last_ts: datetime | None = None
        seen_usage = False
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                timestamp = _parse_timestamp(entry.get("timestamp"))
                if timestamp is not None:
                    if first_ts is None:
                        first_ts = timestamp
                    last_ts = timestamp
                message = entry.get("message")
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                seen_usage = True
                totals["input"] += int(usage.get("input_tokens") or 0)
                totals["output"] += int(usage.get("output_tokens") or 0)
                totals["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
                totals["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)
        if not seen_usage:
            return None
        totals["total"] = sum(totals.values())
        if first_ts is not None and last_ts is not None and last_ts >= first_ts:
            totals["duration_seconds"] = int((last_ts - first_ts).total_seconds())
        return totals
    except Exception:
        return None


def _format_tokens(usage: dict | None) -> str:
    if not usage:
        return "tokens=unknown"
    return (
        f"tokens={usage['total']} (in={usage['input']} out={usage['output']} "
        f"cache_read={usage['cache_read']} cache_write={usage['cache_write']})"
    )


def _format_session_start(payload: dict) -> str:
    fields = [
        f"id={_short_id(payload.get('session_id'))}",
        f"source={payload.get('source') or '-'}",
    ]
    model = payload.get("model")
    if model:
        fields.append(f"model={model}")
    header = f"=== SESSION START {_now()} | " + " | ".join(fields)
    return f"{SEPARATOR}\n{header}\n{SEPARATOR}\n"


def _format_session_end(payload: dict) -> str:
    reason = payload.get("reason") or payload.get("source") or "-"
    return f"{_now()} | SESSION END | id={_short_id(payload.get('session_id'))} | reason={reason}\n"


def _format_skill(payload: dict) -> str | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    name = tool_input.get("skill") or "-"
    fields = [f"name={name}", f"args={_clean_args(tool_input.get('args'))}"]
    agent_type = payload.get("agent_type")
    if agent_type:
        fields.append(f"agent={agent_type}")
    return f"{_now()} | {'SKILL':<6} | " + " | ".join(fields) + "\n"


def _format_subagent_start(payload: dict) -> str:
    fields = [
        f"type={payload.get('agent_type') or '-'}",
        f"id={_short_id(payload.get('agent_id'))}",
    ]
    return f"{_now()} | {'AGENT+':<6} | " + " | ".join(fields) + "\n"


def _format_subagent_stop(payload: dict) -> str:
    usage = None
    transcript_path = payload.get("agent_transcript_path")
    if transcript_path:
        usage = parse_transcript_usage(transcript_path)
    fields = [
        f"type={payload.get('agent_type') or '-'}",
        f"id={_short_id(payload.get('agent_id'))}",
        _format_tokens(usage),
    ]
    if usage and usage.get("duration_seconds") is not None:
        fields.append(f"duration={usage['duration_seconds']}s")
    return f"{_now()} | {'AGENT-':<6} | " + " | ".join(fields) + "\n"


def format_event(payload: dict) -> str | None:
    """Return the log entry for a hook payload, or None if untracked."""
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        return _format_session_start(payload)
    if event == "SessionEnd":
        return _format_session_end(payload)
    if event == "PostToolUse" and payload.get("tool_name") == "Skill":
        return _format_skill(payload)
    if event == "SubagentStart":
        return _format_subagent_start(payload)
    if event == "SubagentStop":
        return _format_subagent_stop(payload)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        entry = format_event(payload)
        if entry:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as handle:
                handle.write(entry)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
