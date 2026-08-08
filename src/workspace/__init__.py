"""Run workspace: one directory per investment-research request.

A run owns everything about a single question -- the request, its inputs, the
evidence gathered for it, deterministic calculations, agent outputs, and an
append-only audit log -- so that the answer is reproducible and auditable as a
unit. This is working state, distinct from `Knowledge-Base/` (durable curated
research) and `exports/` (data exported out of the pipeline).

Two rules hold everywhere in this package:

- **Never invent.** Missing evidence is registered explicitly with a `missing`
  status. No stage substitutes a value it could not obtain.
- **Never execute.** Restrictions and the decision-proposal schema pin this to
  research only; `validation.py` fails any run whose artifacts contain
  trade-execution fields.

See `docs/architecture/run_workspace.md`.
"""

from __future__ import annotations

from . import audit, evidence, manifest, models, paths, run, state, validation
from .models import (
    AgentOutput,
    ContextManifest,
    DecisionProposal,
    EvidenceRecord,
    Request,
    RunMetadata,
)
from .paths import PathEscapeError, WorkspaceError, resolve_in_run
from .run import RunExistsError, RunNotFoundError, archive_run, create_run, resolve_run
from .state import InvalidTransitionError

__all__ = [
    "audit", "evidence", "manifest", "models", "paths", "run", "state", "validation",
    "AgentOutput", "ContextManifest", "DecisionProposal", "EvidenceRecord", "Request", "RunMetadata",
    "WorkspaceError", "PathEscapeError", "InvalidTransitionError",
    "RunExistsError", "RunNotFoundError",
    "create_run", "resolve_run", "archive_run", "resolve_in_run",
]
