"""Tests for the Claude Code agent/skill usage tracker hook script."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / ".claude" / "hooks" / "usage_tracker.py"


def _load_tracker():
    spec = importlib.util.spec_from_file_location("usage_tracker", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tracker = _load_tracker()


def _transcript_line(usage=None, timestamp=None):
    entry = {"type": "assistant"}
    if usage is not None:
        entry["message"] = {"usage": usage}
    if timestamp is not None:
        entry["timestamp"] = timestamp
    return json.dumps(entry)


class ParseTranscriptUsageTests(unittest.TestCase):
    def _write_transcript(self, lines):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        self.addCleanup(Path(handle.name).unlink)
        handle.write("\n".join(lines))
        handle.close()
        return handle.name

    def test_sums_usage_across_entries(self):
        path = self._write_transcript(
            [
                _transcript_line(
                    usage={
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 1000,
                        "cache_creation_input_tokens": 200,
                    },
                    timestamp="2026-07-13T18:03:10.000Z",
                ),
                _transcript_line(),  # entry without usage is skipped
                _transcript_line(
                    usage={"input_tokens": 10, "output_tokens": 5},
                    timestamp="2026-07-13T18:05:32.000Z",
                ),
            ]
        )
        usage = tracker.parse_transcript_usage(path)
        self.assertEqual(usage["input"], 110)
        self.assertEqual(usage["output"], 55)
        self.assertEqual(usage["cache_read"], 1000)
        self.assertEqual(usage["cache_write"], 200)
        self.assertEqual(usage["total"], 1365)
        self.assertEqual(usage["duration_seconds"], 142)

    def test_missing_timestamps_omit_duration(self):
        path = self._write_transcript(
            [_transcript_line(usage={"input_tokens": 1, "output_tokens": 2})]
        )
        usage = tracker.parse_transcript_usage(path)
        self.assertEqual(usage["total"], 3)
        self.assertNotIn("duration_seconds", usage)

    def test_missing_file_returns_none(self):
        self.assertIsNone(tracker.parse_transcript_usage("no-such-file.jsonl"))

    def test_malformed_lines_are_skipped(self):
        path = self._write_transcript(
            [
                "not json at all",
                '"a bare string"',
                _transcript_line(usage={"input_tokens": 7}),
            ]
        )
        usage = tracker.parse_transcript_usage(path)
        self.assertEqual(usage["total"], 7)

    def test_transcript_without_usage_returns_none(self):
        path = self._write_transcript([_transcript_line(), _transcript_line()])
        self.assertIsNone(tracker.parse_transcript_usage(path))


class ParseTranscriptSpanTests(unittest.TestCase):
    def _write_transcript(self, lines):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        self.addCleanup(Path(handle.name).unlink)
        handle.write("\n".join(lines))
        handle.close()
        return handle.name

    def test_computes_span_across_timestamps(self):
        path = self._write_transcript(
            [
                _transcript_line(timestamp="2026-07-13T18:02:11.000Z"),
                _transcript_line(timestamp="2026-07-13T18:10:00.000Z"),
                _transcript_line(timestamp="2026-07-13T18:39:51.000Z"),
            ]
        )
        self.assertEqual(tracker.parse_transcript_span(path), 2260)

    def test_single_timestamp_returns_none(self):
        path = self._write_transcript(
            [_transcript_line(timestamp="2026-07-13T18:02:11.000Z")]
        )
        self.assertIsNone(tracker.parse_transcript_span(path))

    def test_no_timestamps_returns_none(self):
        path = self._write_transcript([_transcript_line(), _transcript_line()])
        self.assertIsNone(tracker.parse_transcript_span(path))

    def test_missing_file_returns_none(self):
        self.assertIsNone(tracker.parse_transcript_span("no-such-file.jsonl"))

    def test_malformed_lines_are_skipped(self):
        path = self._write_transcript(
            [
                "not json at all",
                _transcript_line(timestamp="2026-07-13T18:00:00.000Z"),
                _transcript_line(timestamp="2026-07-13T18:00:45.000Z"),
            ]
        )
        self.assertEqual(tracker.parse_transcript_span(path), 45)


class FormatDurationTests(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(tracker._format_duration(45), "45s")
        self.assertEqual(tracker._format_duration(0), "0s")

    def test_minutes_and_seconds(self):
        self.assertEqual(tracker._format_duration(125), "2m05s")

    def test_hours_minutes_seconds(self):
        self.assertEqual(tracker._format_duration(3900), "1h05m00s")


class FormatEventTests(unittest.TestCase):
    def test_session_start_block(self):
        entry = tracker.format_event(
            {
                "hook_event_name": "SessionStart",
                "session_id": "a1b2c3d4e5f6",
                "source": "startup",
                "model": "claude-fable-5",
            }
        )
        lines = entry.strip().split("\n")
        self.assertEqual(lines[0], "=" * 64)
        self.assertEqual(lines[2], "=" * 64)
        self.assertIn("=== SESSION START ", lines[1])
        self.assertIn("id=a1b2c3d4", lines[1])
        self.assertIn("source=startup", lines[1])
        self.assertIn("model=claude-fable-5", lines[1])

    def test_session_end_line(self):
        entry = tracker.format_event(
            {"hook_event_name": "SessionEnd", "session_id": "a1b2c3d4", "reason": "other"}
        )
        self.assertIn("| SESSION END |", entry)
        self.assertIn("reason=other", entry)

    def test_session_end_without_transcript_logs_unknown_duration(self):
        entry = tracker.format_event(
            {"hook_event_name": "SessionEnd", "session_id": "a1b2c3d4", "reason": "other"}
        )
        self.assertIn("duration=unknown", entry)

    def test_session_end_with_bad_transcript_path_logs_unknown_duration(self):
        entry = tracker.format_event(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "a1b2c3d4",
                "reason": "other",
                "transcript_path": "no-such-transcript.jsonl",
            }
        )
        self.assertIn("duration=unknown", entry)

    def test_session_end_with_transcript_logs_duration(self):
        transcript = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        self.addCleanup(Path(transcript.name).unlink)
        transcript.write(
            "\n".join(
                [
                    _transcript_line(timestamp="2026-07-13T18:02:11.000Z"),
                    _transcript_line(timestamp="2026-07-13T18:39:51.000Z"),
                ]
            )
        )
        transcript.close()
        entry = tracker.format_event(
            {
                "hook_event_name": "SessionEnd",
                "session_id": "a1b2c3d4",
                "reason": "other",
                "transcript_path": transcript.name,
            }
        )
        self.assertIn("duration=37m40s", entry)

    def test_skill_line(self):
        entry = tracker.format_event(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "classify-portfolio", "args": ""},
            }
        )
        self.assertIn("| SKILL  |", entry)
        self.assertIn("name=classify-portfolio", entry)
        self.assertIn("args=-", entry)
        self.assertNotIn("agent=", entry)

    def test_skill_line_inside_subagent(self):
        entry = tracker.format_event(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "evaluate-stock-decision", "args": "AAPL"},
                "agent_type": "stock-analyst",
            }
        )
        self.assertIn("args=AAPL", entry)
        self.assertIn("agent=stock-analyst", entry)

    def test_skill_args_are_truncated_and_flattened(self):
        entry = tracker.format_event(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Skill",
                "tool_input": {"skill": "kb-search", "args": "x" * 500 + "\nnew line"},
            }
        )
        args_field = [f for f in entry.strip().split(" | ") if f.startswith("args=")][0]
        self.assertLessEqual(len(args_field), len("args=") + 120)
        self.assertNotIn("\n", args_field)
        self.assertTrue(args_field.endswith("..."))

    def test_non_skill_tool_is_ignored(self):
        entry = tracker.format_event(
            {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}}
        )
        self.assertIsNone(entry)

    def test_subagent_start_line(self):
        entry = tracker.format_event(
            {
                "hook_event_name": "SubagentStart",
                "agent_type": "stock-data-prep",
                "agent_id": "f9e8a7b6c5",
            }
        )
        self.assertIn("| AGENT+ |", entry)
        self.assertIn("type=stock-data-prep", entry)
        self.assertIn("id=f9e8a7b6", entry)

    def test_subagent_stop_with_transcript(self):
        transcript = tempfile.NamedTemporaryFile(
            "w", suffix=".jsonl", delete=False, encoding="utf-8"
        )
        self.addCleanup(Path(transcript.name).unlink)
        transcript.write(
            _transcript_line(
                usage={"input_tokens": 40, "output_tokens": 60},
                timestamp="2026-07-13T18:00:00Z",
            )
        )
        transcript.close()
        entry = tracker.format_event(
            {
                "hook_event_name": "SubagentStop",
                "agent_type": "kb-discovery",
                "agent_id": "abc123",
                "agent_transcript_path": transcript.name,
            }
        )
        self.assertIn("| AGENT- |", entry)
        self.assertIn("tokens=100 (in=40 out=60 cache_read=0 cache_write=0)", entry)
        self.assertIn("duration=0s", entry)

    def test_subagent_stop_without_transcript_logs_unknown(self):
        entry = tracker.format_event(
            {
                "hook_event_name": "SubagentStop",
                "agent_type": "kb-discovery",
                "agent_id": "abc123",
                "agent_transcript_path": "missing.jsonl",
            }
        )
        self.assertIn("tokens=unknown", entry)
        self.assertNotIn("duration=", entry)

    def test_unknown_event_is_ignored(self):
        self.assertIsNone(tracker.format_event({"hook_event_name": "Stop"}))


class MainTests(unittest.TestCase):
    def setUp(self):
        self.log_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.log_dir.cleanup)
        self.log_path = Path(self.log_dir.name) / "logs" / "AgentSkillUsage.txt"

    def _run_main(self, payload_text):
        with mock.patch.object(tracker, "LOG_PATH", self.log_path), mock.patch.object(
            tracker.sys, "stdin", io.StringIO(payload_text)
        ):
            return tracker.main()

    def test_appends_entry_and_creates_log_dir(self):
        exit_code = self._run_main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "abc123",
                    "source": "startup",
                }
            )
        )
        self.assertEqual(exit_code, 0)
        content = self.log_path.read_text(encoding="utf-8")
        self.assertIn("=== SESSION START ", content)
        self.assertIn("id=abc123", content)

    def test_untracked_event_writes_nothing(self):
        exit_code = self._run_main(
            json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash"})
        )
        self.assertEqual(exit_code, 0)
        self.assertFalse(self.log_path.exists())

    def test_invalid_stdin_never_raises(self):
        self.assertEqual(self._run_main("not valid json"), 0)
        self.assertEqual(self._run_main("[1, 2, 3]"), 0)
        self.assertFalse(self.log_path.exists())


if __name__ == "__main__":
    unittest.main()
