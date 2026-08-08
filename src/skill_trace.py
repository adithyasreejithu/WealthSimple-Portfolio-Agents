"""One trace format for every data-collecting skill.

Skills that gather data need a way to say how good the pull was, so
degradation is visible over time rather than discovered when an analysis
quietly scores against half-empty data. Before this module each skill had its
own answer: `investment-analyst-resources` wrote a bespoke log file,
`market-analyst-resources` computed a differently-shaped trace and wrote no
log at all. This is the shared writer both now use.

The central idea is a three-way split, not a two-way one. A field is `ok`
(we got it), `missing` (we should have got it and did not), or
`not_applicable` (there was nothing to get). Lumping the third into the second
is what made a healthy run on a ticker with no options chain report 81%
complete with eleven "missing" fields -- noise that buried the one gap that
actually mattered. Only `ok + missing` forms the denominator; N/A is reported
alongside so nothing is hidden, just correctly classified.

Writes three places:
  - `logs/SkillTrace.txt`   pipe-delimited, matching the `AgentSkillUsage.txt`
                            and `SystemLogs.txt` convention (one scannable line)
  - `logs/SkillTrace.jsonl` the same run with the per-domain breakdown the text
                            line collapses, for querying trends
  - the run's `audit_log.jsonl`, when the skill was invoked with a `--run-id`,
    so an archived run carries its own data-quality history

See `docs/architecture/usage_tracking.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import config

TEXT_LOG_PATH = config.LOG_FOLDER / "SkillTrace.txt"
JSONL_LOG_PATH = config.LOG_FOLDER / "SkillTrace.jsonl"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class DomainTrace:
    """One domain's field-level outcome.

    `not_applicable` holds fields that could not exist for this subject --
    options metrics for a ticker with no chain, financials for an ETF, a
    registry indicator with no source configured yet. They are named, not just
    counted, so "why is this N/A" stays answerable.
    """

    name: str
    ok: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)

    @property
    def graded(self) -> int:
        """Fields that count toward completeness. N/A is excluded by design."""
        return len(self.ok) + len(self.missing)


@dataclass
class WorkspaceOutcome:
    """Whether this pull's evidence landed in a run, and why not if it didn't.

    Exists because an absent run used to be invisible -- nothing distinguished
    "the workspace was skipped on purpose" from "no pull happened." Recording
    the outcome on every trace turns a skip into a positive, greppable line in
    `logs/SkillTrace.jsonl` instead of a silent gap.
    """

    status: str  # "created" | "attached" | "skipped"
    run_id: str | None = None
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "run_id": self.run_id, "skip_reason": self.skip_reason}


@dataclass
class Trace:
    """A whole run's completeness, assembled from per-domain results."""

    skill: str
    subject: str
    kind: str | None = None
    domains: list[DomainTrace] = field(default_factory=list)
    workspace: WorkspaceOutcome | None = None

    def add(
        self,
        name: str,
        *,
        ok: list[str] | None = None,
        missing: list[str] | None = None,
        not_applicable: list[str] | None = None,
    ) -> DomainTrace:
        domain = DomainTrace(
            name=name,
            ok=list(ok or []),
            missing=list(missing or []),
            not_applicable=list(not_applicable or []),
        )
        self.domains.append(domain)
        return domain

    @property
    def fields_ok(self) -> int:
        return sum(len(domain.ok) for domain in self.domains)

    @property
    def fields_missing(self) -> int:
        return sum(len(domain.missing) for domain in self.domains)

    @property
    def fields_not_applicable(self) -> int:
        return sum(len(domain.not_applicable) for domain in self.domains)

    @property
    def fields_graded(self) -> int:
        return self.fields_ok + self.fields_missing

    @property
    def completeness_pct(self) -> float:
        """Share of *obtainable* fields obtained.

        A run where everything obtainable was obtained reads 100.0 even if
        most of the manifest was N/A for this subject -- that is the point.
        A run with nothing graded at all is 100.0 rather than 0.0: there was
        nothing to fail at, and `domains_*` counts tell the fuller story.
        """
        if not self.fields_graded:
            return 100.0
        return round(100.0 * self.fields_ok / self.fields_graded, 1)

    @property
    def domains_ok(self) -> int:
        return sum(1 for d in self.domains if d.graded and not d.missing)

    @property
    def domains_partial(self) -> int:
        return sum(1 for d in self.domains if d.ok and d.missing)

    @property
    def domains_failed(self) -> int:
        return sum(1 for d in self.domains if d.missing and not d.ok)

    @property
    def domains_not_applicable(self) -> int:
        return sum(1 for d in self.domains if not d.graded)

    def missing_fields(self) -> list[str]:
        return [f"{d.name}.{name}" for d in self.domains for name in d.missing]

    def not_applicable_fields(self) -> list[str]:
        return [f"{d.name}.{name}" for d in self.domains for name in d.not_applicable]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "subject": self.subject,
            "kind": self.kind,
            "completeness_pct": self.completeness_pct,
            "fields_ok": self.fields_ok,
            "fields_missing": self.fields_missing,
            "fields_graded": self.fields_graded,
            "fields_not_applicable": self.fields_not_applicable,
            "domains_ok": self.domains_ok,
            "domains_partial": self.domains_partial,
            "domains_failed": self.domains_failed,
            "domains_not_applicable": self.domains_not_applicable,
            "missing": self.missing_fields(),
            "not_applicable": self.not_applicable_fields(),
            "domains": {
                d.name: {"ok": d.ok, "missing": d.missing, "not_applicable": d.not_applicable}
                for d in self.domains
            },
            "workspace": self.workspace.to_dict() if self.workspace else None,
        }


def _grouped(domains: list[DomainTrace], attribute: str) -> str:
    """Collapse per-field lists into `domain[n]` groups for the text line.

    The flat comma list this replaces ran past a thousand characters on a
    normal run, which is why the useful part was unreadable. Counts per domain
    keep the line scannable; the JSONL record keeps the field names.
    """
    parts = [
        f"{domain.name}[{len(getattr(domain, attribute))}]"
        for domain in domains
        if getattr(domain, attribute)
    ]
    return ",".join(parts) if parts else "-"


def _run_field(workspace: WorkspaceOutcome | None) -> str:
    """Render the workspace outcome for the scannable text line.

    A skip is written as `run=skipped(<reason>)`, never bare `run=skipped` --
    the reason is what lets an auditor tell a deliberate `--no-run` apart from
    a debug/test invocation without opening the JSONL sidecar.
    """
    if workspace is None:
        return "-"
    if workspace.status == "skipped":
        return f"skipped({workspace.skip_reason or 'unknown'})"
    return workspace.run_id or workspace.status


def format_line(trace: Trace, *, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now()).strftime(DATE_FORMAT)
    return (
        f"{timestamp} | TRACE | skill={trace.skill} | subject={trace.subject} | "
        f"kind={trace.kind or '-'} | pct={trace.completeness_pct} | "
        f"ok={trace.fields_ok} | graded={trace.fields_graded} | "
        f"failed={trace.domains_failed} | "
        f"missing={_grouped(trace.domains, 'missing')} | "
        f"n_a={_grouped(trace.domains, 'not_applicable')} | "
        f"run={_run_field(trace.workspace)}\n"
    )


def emit(
    trace: Trace,
    *,
    run_dir: Path | None = None,
    text_log_path: Path | None = None,
    jsonl_log_path: Path | None = None,
    run_id: str | None = None,
    workspace_status: str | None = None,
    skip_reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write the trace everywhere it belongs. Never raises.

    A logging failure must not sink a data pull that otherwise succeeded --
    same discipline as `usage_tracker.py`, which swallows everything so a hook
    can never block real work.

    `workspace_status` ("created" / "attached" / "skipped") and `skip_reason`
    let a caller report the workspace outcome without constructing a
    `WorkspaceOutcome` itself; set `trace.workspace` beforehand instead if more
    control is needed. Recording a skip here -- not just silently omitting
    `run_dir` -- is what makes an opt-out visible in `logs/SkillTrace.jsonl`
    rather than indistinguishable from "nothing ran."
    """
    if trace.workspace is None and workspace_status is not None:
        trace.workspace = WorkspaceOutcome(
            status=workspace_status, run_id=run_id, skip_reason=skip_reason
        )

    payload = trace.to_dict()
    moment = now or datetime.now()
    payload["ts"] = moment.isoformat(timespec="seconds")

    text_path = text_log_path or TEXT_LOG_PATH
    jsonl_path = jsonl_log_path or JSONL_LOG_PATH
    try:
        text_path.parent.mkdir(parents=True, exist_ok=True)
        with text_path.open("a", encoding="utf-8") as handle:
            handle.write(format_line(trace, now=moment))
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass

    if run_dir is not None:
        try:
            from workspace import audit as audit_module

            audit_module.append_event(
                run_dir,
                run_id=run_id or trace.subject,
                event="trace_recorded",
                actor=trace.skill,
                # The audit event carries the summary plus the named gaps, not
                # the full per-domain breakdown -- the run's bundle already
                # holds that, and the audit log stays a readable history.
                details={
                    "subject": trace.subject,
                    "completeness_pct": trace.completeness_pct,
                    "fields_ok": trace.fields_ok,
                    "fields_graded": trace.fields_graded,
                    "missing": trace.missing_fields(),
                    "not_applicable_count": trace.fields_not_applicable,
                },
            )
        except Exception:
            pass

    return payload


def from_status_map(
    skill: str,
    subject: str,
    entries: dict[str, str],
    *,
    ok_statuses: frozenset[str],
    not_applicable_statuses: frozenset[str],
    kind: str | None = None,
    domain_of: dict[str, str] | None = None,
) -> Trace:
    """Build a trace from `{entry_id: status}`, for skills that already track
    per-entry status rather than per-field presence.

    `market-analyst-resources` works this way: its registry entries each carry
    a status, and its unconfigured `<TBD>` stubs are exactly the N/A case this
    module exists to separate out.
    """
    trace = Trace(skill=skill, subject=subject, kind=kind)
    buckets: dict[str, DomainTrace] = {}
    for entry_id, status in sorted(entries.items()):
        domain_name = (domain_of or {}).get(entry_id, "entries")
        domain = buckets.get(domain_name)
        if domain is None:
            domain = trace.add(domain_name)
            buckets[domain_name] = domain
        if status in ok_statuses:
            domain.ok.append(entry_id)
        elif status in not_applicable_statuses:
            domain.not_applicable.append(entry_id)
        else:
            domain.missing.append(entry_id)
    return trace
