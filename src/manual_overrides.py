"""Owner-approved ticker-level group overrides for the portfolio classifier.

`Knowledge-Base/ref/manual_overrides_v1_1.yaml` is the highest-priority input
to the classifier: an entry there pins a ticker's group regardless of what the
metadata rules would infer. It is an approved reference file, normally edited
by hand, so this module exists for exactly one caller -- the dashboard's
"classify this holding" action, where the portfolio owner resolves a
Needs Review holding from the UI instead of opening the YAML.

Two properties matter and are enforced here:

* **Validated.** A group must be one the rules file already approves, and it
  can never be a bookkeeping bucket (`Cash`) or the fallback (`Needs Review`),
  which would make the override a no-op.
* **Formatting-preserving.** Entries are spliced in as text rather than by
  re-dumping the document, so an override written from the dashboard leaves
  the rest of this hand-curated file byte-identical.

The classifier itself never writes here, and neither do the knowledge-base
agents (see CLAUDE.md) -- this is an owner-initiated edit that happens to
arrive over HTTP rather than through an editor.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from config import CLASSIFICATION_RULES_FILE, MANUAL_OVERRIDES_FILE
from system_logger import get_logger


logger = get_logger(__name__)

# Approved groups that are not a real portfolio role: pinning a ticker to
# either defeats the purpose of an override.
_NON_ASSIGNABLE_GROUPS = {"Cash", "Needs Review"}

_ENTRY_KEY_ORDER = ("ticker", "primary_group", "secondary_tags", "rationale", "active", "review_needed")


class OverrideError(ValueError):
    """Raised when a requested override is not valid for this policy."""


def load_approved_groups(rules_path: str | Path = CLASSIFICATION_RULES_FILE) -> list[str]:
    """Return `approved_groups` from the classification rules reference."""
    data = yaml.safe_load(Path(rules_path).read_text(encoding="utf-8")) or {}
    groups = data.get("approved_groups")
    if not isinstance(groups, list) or not groups:
        raise OverrideError(f"No approved_groups found in {rules_path}.")
    return [str(group) for group in groups]


def assignable_groups(rules_path: str | Path = CLASSIFICATION_RULES_FILE) -> list[str]:
    """Approved groups a holding can actually be pinned to from the dashboard."""
    return [group for group in load_approved_groups(rules_path) if group not in _NON_ASSIGNABLE_GROUPS]


def load_overrides(path: str | Path = MANUAL_OVERRIDES_FILE) -> list[dict[str, Any]]:
    """Return the current override entries, oldest first."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    overrides = data.get("overrides")
    return [entry for entry in overrides if isinstance(entry, dict)] if isinstance(overrides, list) else []


def _format_entry(entry: dict[str, Any]) -> str:
    """Render one override as an indented YAML list item.

    `default_flow_style=None` keeps short sequences inline, which is how every
    hand-written entry in the file is already shaped.
    """
    ordered = {key: entry[key] for key in _ENTRY_KEY_ORDER if key in entry}
    dumped = yaml.safe_dump([ordered], default_flow_style=None, sort_keys=False, width=100)
    return "".join(f"  {line}\n" if line.strip() else "\n" for line in dumped.rstrip("\n").split("\n"))


def _entry_span(text: str, ticker: str) -> tuple[int, int] | None:
    """Locate an existing entry's character span, or None when absent.

    An entry runs from its `- ticker:` line to just before the next list item
    at the same indentation (or end of file), so the replacement keeps the
    blank-line separation the file uses between entries.
    """
    pattern = re.compile(rf"^  - ticker:[ \t]*['\"]?{re.escape(ticker)}['\"]?[ \t]*$", re.MULTILINE)
    match = pattern.search(text)
    if match is None:
        return None
    following = re.compile(r"^  - ", re.MULTILINE).search(text, match.end())
    end = following.start() if following else len(text)
    return match.start(), end


def _atomic_write(path: Path, text: str) -> None:
    """Replace `path` in one step so a crash cannot leave a half-written file."""
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=str(path.parent), delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def upsert_override(
    ticker: str,
    primary_group: str,
    *,
    rationale: str,
    secondary_tags: list[str] | None = None,
    review_needed: bool = False,
    path: str | Path = MANUAL_OVERRIDES_FILE,
    rules_path: str | Path = CLASSIFICATION_RULES_FILE,
) -> dict[str, Any]:
    """Add or replace the override for `ticker`, returning the stored entry.

    Raises `OverrideError` for an unknown/non-assignable group, a blank ticker,
    or a missing rationale -- an override without a recorded reason is exactly
    the kind of unexplained pin this file's policy warns against.
    """
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        raise OverrideError("A ticker is required.")
    group = str(primary_group or "").strip()
    allowed = assignable_groups(rules_path)
    if group not in allowed:
        raise OverrideError(f"Group '{group}' is not assignable. Expected one of: {', '.join(allowed)}.")
    reason = str(rationale or "").strip()
    if not reason:
        raise OverrideError("A rationale is required so the override records why it exists.")

    entry = {
        "ticker": symbol,
        "primary_group": group,
        "secondary_tags": [str(tag).strip() for tag in (secondary_tags or []) if str(tag).strip()],
        "rationale": reason,
        "active": True,
        "review_needed": bool(review_needed),
    }

    target = Path(path)
    original = target.read_text(encoding="utf-8")
    block = _format_entry(entry)
    span = _entry_span(original, symbol)
    if span is None:
        updated = original.rstrip("\n") + "\n\n" + block
        action = "added"
    else:
        start, end = span
        tail = original[end:]
        # Entries are separated by a blank line; keep exactly one when another
        # entry follows, and end the file cleanly when this was the last one.
        separator = "\n" if tail else ""
        updated = original[:start] + block.rstrip("\n") + "\n" + separator + tail
        action = "replaced"

    # Re-parse before committing: a malformed splice must never reach the file
    # the classifier reads on its next run.
    reparsed = yaml.safe_load(updated) or {}
    stored = next(
        (row for row in reparsed.get("overrides", []) if isinstance(row, dict) and row.get("ticker") == symbol),
        None,
    )
    if stored is None or stored.get("primary_group") != group:
        raise OverrideError(f"Override for {symbol} did not round-trip cleanly; file left unchanged.")

    _atomic_write(target, updated)
    logger.info("Manual override %s | ticker=%s group=%s", action, symbol, group)
    return stored
