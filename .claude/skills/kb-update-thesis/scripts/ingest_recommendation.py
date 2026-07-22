"""Commit a validated stock-recommendation artifact into a thesis page.

All the judgment happened upstream in the stock-analyst agent (Opus). This
script deterministically applies the validated artifact to the thesis page:

  1. re-validate the artifact against the rubric (reusing the analyst's own
     validator, so a hand-edited artifact cannot slip through),
  2. create the page if missing (seeded from the artifact's position context
     and yfinance research data),
  3. transcribe narrative prose from the artifact's narratives sections
     mechanically (no LLM call),
  4. update the Status block's Decision/Confidence/Time Horizon/Last Updated,
  5. append Decision History row and activity logs.

Never edits Original Thesis on an existing page. Never overwrites prose on
an update that a human may have hand-edited outside the rubric flow.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "evaluate-stock-decision" / "scripts"))
sys.path.insert(0, str(ROOT / ".claude" / "skills" / "kb-update-thesis" / "scripts"))

import kb_pages  # noqa: E402
import rubric as rubric_mod  # noqa: E402
import validate_recommendation as vr  # noqa: E402
import thesis_page as thesis_mod  # noqa: E402

VERDICTS = ("new", "stronger", "weaker", "unchanged", "broken")
NOTE_MAX = 120


class IngestError(ValueError):
    pass


def _load_artifact(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IngestError(f"artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise IngestError(f"invalid JSON in {path}: {exc}") from exc


def _update_status_block(body: str, action: str, confidence: str, horizon: str, today: str) -> str:
    replacements = {
        "- Decision:": f"- Decision: {action}",
        "- Confidence:": f"- Confidence: {confidence}",
        "- Time Horizon:": f"- Time Horizon: {horizon}",
        "- Last Updated:": f"- Last Updated: {today}",
    }
    lines = body.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        for prefix, new_line in replacements.items():
            # Only touch the Status block's own fields (Portfolio Status is left alone).
            if stripped.startswith(prefix):
                lines[idx] = new_line
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")


def _decision_history_dates(body: str) -> list[str]:
    """Dates from existing rubric-driven Decision History rows (Note ~ 'Rubric')."""
    return [row["date"] for row in kb_pages.decision_history_rows(body) if "Rubric" in row["note"]]


def _append_decision_history_row(body: str, row: str) -> str:
    lines = body.splitlines()
    section_idx = None
    for idx, line in enumerate(lines):
        if line.strip().startswith("## Decision History"):
            section_idx = idx
            break
    if section_idx is None:
        raise IngestError("page is missing a '## Decision History' section")
    last_table_idx = None
    for idx in range(section_idx + 1, len(lines)):
        if lines[idx].strip().startswith("## "):
            break
        if lines[idx].strip().startswith("|"):
            last_table_idx = idx
    if last_table_idx is None:
        raise IngestError("Decision History section has no table to append to")
    lines.insert(last_table_idx + 1, row)
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")


def _infer_company_name(artifact: dict) -> str:
    """Try to extract company name from the artifact's yfinance research data."""
    sources = artifact.get("research_sources") or {}
    yf_source = sources.get("yfinance") or {}
    yf_path = yf_source.get("path")
    if not yf_path:
        return ""
    try:
        yf_file = Path(yf_path)
        if not yf_file.is_absolute():
            yf_file = (Path(artifact.get("generated", ".")) / yf_path).resolve()
        if not yf_file.exists():
            return ""
        data = json.loads(yf_file.read_text(encoding="utf-8"))
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        if isinstance(data, dict):
            overview = data.get("data", {}).get("overview", {})
            return overview.get("longName") or ""
    except Exception:
        pass
    return ""


def _transcribe_narratives(body: str, artifact: dict) -> tuple[str, set[str]]:
    """Transcribe narrative prose from artifact into body markdown, mechanically.

    Returns the new body and the set of section header names that were actually
    rewritten, so the caller can stamp `section_updated` for the gated ones
    (decision #16).
    """
    narratives = artifact.get("narratives") or {}
    rewritten: set[str] = set()

    # Updated Thesis (always update).
    updated = narratives.get("updated_thesis", "").strip()
    if updated:
        body = kb_pages.replace_section(body, "Updated Thesis", updated)
        rewritten.add("Updated Thesis")

    # Section updates (from the section_updates map).
    section_updates = narratives.get("section_updates") or {}
    unmatched_sections: list[str] = []
    for section_name, content in section_updates.items():
        if content and content.strip():
            try:
                body = kb_pages.replace_section(body, section_name, content.strip())
                rewritten.add(section_name)
            except kb_pages.KBPageError:
                unmatched_sections.append(section_name)
    if unmatched_sections:
        print(
            f"warning: section_updates key(s) {unmatched_sections} did not match "
            "any page header and were dropped",
            file=sys.stderr,
        )

    # Bull/Bear/Risks/Questions/Monitoring (transcribe as bullet/checkbox lists).
    for field, section_name in [
        ("bull_case", "Bull Case"),
        ("bear_case", "Bear Case"),
        ("key_risks", "Key Risks"),
        ("open_questions", "Open Questions"),
    ]:
        items = narratives.get(field) or []
        if items:
            content = "\n".join(f"- {item}" for item in items if isinstance(item, str) and item.strip())
            if content:
                try:
                    body = kb_pages.replace_section(body, section_name, content)
                    rewritten.add(section_name)
                except kb_pages.KBPageError:
                    pass

    monitoring = narratives.get("monitoring") or []
    if monitoring:
        content = "\n".join(
            f"- [ ] {item}" for item in monitoring if isinstance(item, str) and item.strip()
        )
        if content:
            try:
                body = kb_pages.replace_section(body, "Monitoring Checklist", content)
                rewritten.add("Monitoring Checklist")
            except kb_pages.KBPageError:
                pass

    # Analyst View.
    analyst = narratives.get("analyst_view", "").strip()
    if analyst:
        try:
            body = kb_pages.replace_section(body, "Analyst View", analyst)
            rewritten.add("Analyst View")
        except kb_pages.KBPageError:
            pass

    # Sources (from top-level sources list).
    sources = artifact.get("sources") or []
    if sources:
        content = "\n".join(f"- {src}" for src in sources if isinstance(src, str) and src.strip())
        if content:
            try:
                body = kb_pages.replace_section(body, "Sources", content)
            except kb_pages.KBPageError:
                pass

    return body, rewritten


def ingest(artifact_path: Path, *, kb_root: Path | None = None) -> dict:
    kb_root = kb_root or kb_pages.KB_ROOT
    artifact = _load_artifact(artifact_path)

    # 1. Re-validate against the rubric (same check the analyst ran).
    rubric = rubric_mod.load_rubric()
    framework = rubric_mod.load_framework()
    rubric_errors = rubric_mod.validate_rubric(rubric, framework)
    if rubric_errors:
        raise IngestError("rubric is invalid: " + "; ".join(rubric_errors))
    sources = vr._load_sources(artifact, artifact_path.resolve().parent)
    errors = vr.validate_recommendation(artifact, rubric, framework, sources)
    if errors:
        raise IngestError("recommendation failed validation:\n  " + "\n  ".join(errors))

    ticker = str(artifact["ticker"]).upper()
    page_path = kb_root / "stocks" / f"{ticker}.md"
    page_exists = page_path.exists()
    page_created = not page_exists

    proposed = artifact.get("proposed") or {}
    action = proposed.get("action")
    confidence = proposed.get("confidence")
    horizon = proposed.get("time_horizon")
    verdict = proposed.get("verdict_vs_previous")
    if verdict not in VERDICTS:
        raise IngestError(f"proposed.verdict_vs_previous '{verdict}' not in {VERDICTS}")

    generated = artifact.get("generated") or kb_pages.today()
    today = kb_pages.today()

    # 2. Auto-create page if missing.
    if not page_exists:
        position = artifact.get("position") or {}
        held = position.get("held", False)
        portfolio_status = "active" if held else "watchlist"
        role = position.get("portfolio_role") or "Unassigned"
        company_name = _infer_company_name(artifact)

        page_content = thesis_mod.render_new_page(ticker, company_name, portfolio_status, role, today)
        page_path.write_text(page_content, encoding="utf-8", newline="\n")

        kb_pages.rebuild_index(kb_root / "stocks" / "index.md")
        kb_pages.rebuild_thesis_views(kb_root)
        kb_pages.rebuild_main_index(kb_root)
        kb_pages.append_update_log(
            kb_root, "created", f"stocks/{ticker}.md", f"New stock page, status={portfolio_status}.", on=today
        )

    meta, body = kb_pages.parse_page_file(page_path)

    # Idempotence guard: refuse an artifact older than the latest rubric row, and
    # refuse re-ingesting one dated the same as an existing rubric row (a same-day
    # re-run would otherwise append a duplicate Decision History row).
    prior_dates = _decision_history_dates(body)
    if prior_dates and generated < max(prior_dates):
        raise IngestError(
            f"artifact generated {generated} is older than the latest rubric Decision "
            f"History row ({max(prior_dates)}); refusing to ingest a stale recommendation"
        )
    if generated in prior_dates:
        raise IngestError(
            f"a rubric Decision History row for {generated} already exists on "
            f"stocks/{ticker}.md; refusing to append a duplicate same-day recommendation"
        )

    rewritten_sections: set[str] = set()

    # 3a. On first creation only, seed Company Overview and Original Thesis from
    # the artifact's narratives. Gated on real file creation in THIS run (never
    # the artifact's self-reported page_exists), so Original Thesis is only ever
    # written once and never overwritten on an update.
    if page_created:
        narratives = artifact.get("narratives") or {}
        for key, section in (("company_overview", "Company Overview"), ("original_thesis", "Original Thesis")):
            text = str(narratives.get(key) or "").strip()
            if text:
                try:
                    body = kb_pages.replace_section(body, section, text)
                    rewritten_sections.add(section)
                except kb_pages.KBPageError:
                    pass

    # 3b. Transcribe prose from narratives mechanically.
    body, transcribed = _transcribe_narratives(body, artifact)
    rewritten_sections |= transcribed

    # 4. Update Status block (Decision/Confidence/Time Horizon/Last Updated only).
    body = _update_status_block(body, action, confidence, horizon, today)
    rewritten_sections.add("Status")

    # 5. Decision History row.
    summary = str((artifact.get("narratives") or {}).get("executive_summary") or "").strip()
    score = artifact.get("weighted_score")
    score_text = f"score {score:.2f}" if isinstance(score, (int, float)) else "no numeric score"
    note = f"Rubric {rubric.get('version')} {score_text}; {summary}"
    note = note[:NOTE_MAX].rstrip()
    row = "| " + " | ".join(kb_pages._cell(c) for c in (today, action, verdict, note)) + " |"
    body = _append_decision_history_row(body, row)

    # Validate the page before writing.
    try:
        meta, test_body = kb_pages.parse_page_file(page_path)
    except kb_pages.KBPageError as exc:
        raise IngestError(f"page structure error: {exc}") from exc

    meta = kb_pages.touch_updated(meta, on=today)
    # Record which gated sections this run rewrote, so the staleness gate
    # (decision #16) can tell what is fresh without re-reading the prose.
    meta = kb_pages.stamp_sections_updated(meta, rewritten_sections, on=today)
    page_path.write_text(kb_pages.serialize_page(meta, body), encoding="utf-8", newline="\n")

    # 6. Logs.
    kb_pages.append_log_row(kb_root / "logs" / "decision-log.md", [today, ticker, action, verdict, note])
    kb_pages.append_update_log(
        kb_root, "recommendation-ingested", f"stocks/{ticker}.md",
        f"{action}/{confidence}/{horizon} from {artifact_path.name}", on=today,
    )

    return {"ticker": ticker, "page": str(page_path), "action": action, "verdict": verdict}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a stock-recommendation artifact into a thesis page.")
    parser.add_argument("--path", type=Path, required=True, help="Path to the recommendation JSON.")
    args = parser.parse_args(argv)
    try:
        result = ingest(args.path.resolve())
    except (IngestError, rubric_mod.RubricError, kb_pages.KBPageError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Ingested {result['action']} ({result['verdict']}) into {result['page']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
