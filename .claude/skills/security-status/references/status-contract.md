# Status contract

What `security_status_cli.py` resolves, what it writes, and the conventions
every field follows.

## Boundary

| Rule | Why |
| --- | --- |
| No writes, ever | This skill only reads `tickers`, `position_snapshots`, and `security_status`. Declaring or clearing a status is a separate, human/CLI-only path (`python src/app.py database status`). No agent gets a tool that writes `security_status`. |
| Ownership always wins | `quantity > 0` in `position_snapshots` overrides any declared status. `security_status` never stores "owned" -- see `database._create_security_status_table`'s docstring in `src/database.py`. A bought wishlist ticker becomes "owned" with no write to this table. |
| Artifact goes to `calculations/`, not `evidence/` | A resolved status is arithmetic over data the run already holds (two lookups joined into one answer), not a fact obtained from outside. Same reasoning `security-technicals` documents. |
| One shared skill, no per-agent copy | Both `investment-analyst` and `investment-portfolio-manager` invoke this exact CLI. `--actor` is required and stamps every evidence row and audit event, so the two callers stay attributable without needing two implementations. |
| Declaration absence is `not_applicable`, never `missing` | Most tickers have no row in `security_status` at all -- that is the default, not a gap. Grading it as `missing` would make every ordinary lookup look incomplete. |

## Not the same vocabulary as the Knowledge-Base

`Knowledge-Base/` stock pages carry their own, older `Portfolio Status`
front-matter field: `research | watchlist | active | closed | rejected`,
written by the legacy `kb-intake` / `ingest_recommendation.py` path
(`portfolio_status = "active" if held else "watchlist"`). This table's
`declared_status` (`wishlist | avoid | retired`) is a **separate, narrower**
vocabulary for the rebuilt Investment Analyst track and is **not**
synchronized with the KB in either direction. A ticker's KB `watchlist`
status does not imply a `security_status` row, and declaring `wishlist` here
does not touch the KB. Do not treat one as authoritative for the other;
resolving a real mapping between the two, if ever wanted, is out of this
skill's scope.

## Effective status resolution

```
if quantity > 0:            status = "owned"
elif a declaration exists:  status = declaration.declared_status  # wishlist | avoid | retired
else:                        status = "unknown"
```

Implemented twice, deliberately kept in agreement rather than sharing one
function: `security_status_cli.resolve_status` (read-only connection, this
skill) and `analytics.resolve_security_status` (writable connection, for
non-skill consumers like the worksheet builder and the dashboard). The skill
cannot call the `analytics` version because it may trigger
`ensure_positions_fresh`'s recompute, which needs a write lock this skill's
read-only connection does not hold.

## Artifact schema

Written to `runs/<run_id>/calculations/<TICKER>-<date>-status-<actor>.json`
(or `--output`). The actor is embedded in the filename, not just the
timestamp, so two actors reading the same ticker in the same run never
collide on `evidence.register`'s duplicate-artifact_path check.

```json
{
  "schema": "security-status.v1",
  "ticker": "OUST",
  "as_of": "2026-08-14",
  "actor": "investment-analyst",
  "resolved": true,
  "security_name": "Ouster, Inc.",
  "status": "wishlist",
  "quantity_held": 0.0,
  "declaration": {
    "declared_status": "wishlist",
    "rationale": "LIDAR platform, watching for entry below $15",
    "declared_at": "2026-08-01T12:00:00",
    "declared_by": "cli"
  },
  "gaps": [],
  "trace": { "...": "skill_trace.Trace.to_dict()" }
}
```

An owned ticker's `declaration` field may still be non-null if one exists
(a wishlist declaration nobody cleared after the position was opened) --
`status` is still `"owned"` because ownership always wins. This is expected,
not a bug: clearing a stale declaration is a human action
(`database status --set clear`), not something this read-only skill does on
your behalf.

A ticker unresolvable in `tickers` produces:

```json
{
  "schema": "security-status.v1",
  "ticker": "ZZZZ",
  "as_of": "2026-08-14",
  "actor": "investment-analyst",
  "resolved": false,
  "status": "unknown",
  "gaps": ["ZZZZ is not in the tickers table"]
}
```

with no `trace` domains graded -- nothing was obtainable, so nothing is
graded, matching `security-technicals`' identical convention for an
unresolvable ticker.

## Digest (stdout)

Same content as the artifact -- a few hundred bytes, no raw series. Agents
read the digest and never open the artifact file.

## Trace domains

Three domains, graded per `src/skill_trace.py`'s three-way split.

| Domain | `ok` | `missing` | `not_applicable` |
| --- | --- | --- | --- |
| `identity` | `ticker_id` when the ticker resolves | — | — |
| `ownership` | `quantity` when the ticker resolves (a snapshot row is not required -- absence reads as zero, not a gap) | — | — |
| `declaration` | `declared_status` when a `security_status` row exists | — | `declared_status` when no row exists |

**A declaration is never graded `missing`.** Declaring a status is optional
by nature -- there is no expectation that every ticker should have one, so
its absence is not a failure to obtain something. This mirrors
`security-technicals`' rule for insufficient price history: only report a
gap for something that should have been obtainable and was not.

A ticker that does not resolve at all produces a `resolved: false` digest
with one gap and no trace domains -- nothing was obtainable, so nothing is
graded.
