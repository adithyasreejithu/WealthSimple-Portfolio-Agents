"""Validate an edited decision rubric and summarize what changed.

The rubric (`Knowledge-Base/taxonomy/decision-rubric.yml`) is hand-curated by
the portfolio owner. This script is the guard rail around that editing: it
re-runs the full structural validation from `rubric.py`, and (given the previous
version via git or an explicit path) prints a diff summary -- weight changes,
band changes, added/removed gates and dimensions -- plus whether a version bump
is required. It never edits the rubric itself.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "evaluate-stock-decision" / "scripts"))

import rubric as rubric_mod


def _load_previous(current_path: Path, previous_path: Path | None) -> dict | None:
    if previous_path is not None:
        return rubric_mod.load_yaml(previous_path)
    # Fall back to the committed version via git show.
    try:
        rel = current_path.resolve().relative_to(ROOT)
    except ValueError:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{rel.as_posix()}"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    import yaml

    try:
        data = yaml.safe_load(result.stdout)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _weights(rubric: dict) -> dict[str, float]:
    return {d["id"]: d["weight"] for d in rubric.get("dimensions", []) if isinstance(d, dict) and "id" in d}


def _bands(rubric: dict) -> dict:
    return rubric.get("verdict_bands", {})


def _gate_ids(rubric: dict) -> set[str]:
    return {g["id"] for g in rubric.get("gates", []) if isinstance(g, dict) and "id" in g}


def diff_summary(previous: dict, current: dict) -> tuple[list[str], bool]:
    """Return (human-readable change lines, version_bump_required)."""
    lines: list[str] = []
    bump = False

    prev_w, cur_w = _weights(previous), _weights(current)
    for did in sorted(set(prev_w) | set(cur_w)):
        if did not in prev_w:
            lines.append(f"+ dimension '{did}' added (weight {cur_w[did]})")
            bump = True
        elif did not in cur_w:
            lines.append(f"- dimension '{did}' removed (was weight {prev_w[did]})")
            bump = True
        elif prev_w[did] != cur_w[did]:
            lines.append(f"~ dimension '{did}' weight {prev_w[did]} -> {cur_w[did]}")
            bump = True

    prev_g, cur_g = _gate_ids(previous), _gate_ids(current)
    for gid in sorted(cur_g - prev_g):
        lines.append(f"+ gate '{gid}' added")
        bump = True
    for gid in sorted(prev_g - cur_g):
        lines.append(f"- gate '{gid}' removed")
        bump = True

    if _bands(previous) != _bands(current):
        lines.append("~ verdict_bands changed")
        bump = True

    if previous.get("sources", {}).keys() != current.get("sources", {}).keys():
        lines.append("~ research sources changed")
        bump = True

    prev_v, cur_v = previous.get("version"), current.get("version")
    if prev_v != cur_v:
        lines.append(f"~ version {prev_v} -> {cur_v}")

    if not lines:
        lines.append("no material changes detected")
    return lines, bump


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the decision rubric and summarize changes.")
    parser.add_argument("--path", type=Path, default=rubric_mod.DEFAULT_RUBRIC_PATH)
    parser.add_argument("--previous", type=Path, default=None, help="Explicit previous rubric to diff against (else git HEAD).")
    args = parser.parse_args(argv)

    try:
        current = rubric_mod.load_rubric(args.path)
    except rubric_mod.RubricError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    errors = rubric_mod.validate_rubric(current, rubric_mod.load_framework())
    if errors:
        print("INVALID rubric:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"Valid: {args.path} (version {current.get('version')})")

    previous = _load_previous(args.path, args.previous)
    if previous is None:
        print("No previous version available to diff (new file or git unavailable).")
        return 0

    lines, bump = diff_summary(previous, current)
    print("\nChanges vs previous:")
    for line in lines:
        print(f"  {line}")

    if bump:
        prev_v, cur_v = previous.get("version"), current.get("version")
        if prev_v == cur_v:
            print(
                f"\nVERSION BUMP REQUIRED: gates/weights/bands/sources changed but version is still "
                f"'{cur_v}'. Bump it (e.g. v1.0 -> v1.1) and record the change in logs/update-log.md.",
                file=sys.stderr,
            )
            return 1
        print(f"\nVersion bumped {prev_v} -> {cur_v}. Remember to append a logs/update-log.md row.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
