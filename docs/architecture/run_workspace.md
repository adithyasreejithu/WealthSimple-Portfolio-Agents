# Run workspace

A **run** is one directory owning one investment-research question: the
request, its inputs, the evidence gathered for it, deterministic
calculations, agent outputs, and an append-only audit log. The point is that
an answer is reproducible and auditable *as a unit* — you can look at a run
months later and see what was asked, what was found, what was missing, and
what was concluded.

Implemented in `src/workspace/`. CLI: `python src/app.py run <subcommand>`
(see `docs/reference/cli.md`).

## Why this exists

Before it, artifacts from different stages accumulated in shared directories
under `exports/` with nothing tying an artifact to the question that produced
it. That made three things impossible: telling which pull backed which
decision, replaying a run, and knowing whether a gap in the data was noticed
or silently filled. It also turned `exports/` into an inter-agent message
queue, which is not what a directory named "exports" should be.

## Three directories, three jobs

| Directory | Holds | Lifetime |
|---|---|---|
| `workspace/` | Working state for one request | Per run; archived, never deleted |
| `Knowledge-Base/` | Durable curated research the owner maintains | Permanent |
| `exports/` | Data exported out of the pipeline for humans and downstream scripts | Regenerated |

A run is *not* a knowledge base. Promotion from one to the other is a
deliberate, reviewed step — see [Knowledge-base promotion](#knowledge-base-promotion).

## Layout

```text
workspace/
├── runs/
│   └── 2026-08-08T020013Z_portfolio_check_SYNTH_d1124c/
│       ├── request.yaml            what was asked
│       ├── run_metadata.json       status, timestamps, component availability
│       ├── context_manifest.yaml   the contract handed to the next stage
│       ├── inputs/                 user-supplied files
│       ├── evidence/
│       │   ├── sources.jsonl       the evidence registry (append-only)
│       │   └── <artifacts>         what was actually fetched
│       ├── calculations/           deterministic computed output
│       │                           (first producer: the security-technicals skill)
│       ├── agent_outputs/          one envelope per specialist stage
│       ├── final/                  decision proposals
│       ├── tmp/                    disposable agent drafts (cleared at archive)
│       ├── cache/                  disposable bulk payloads (cleared on a
│       │                           terminal set-status; never registered
│       │                           as evidence -- see "Retention" below)
│       └── audit_log.jsonl         append-only event history
└── archive/
    └── 2026-08/<run_id>/
```

`workspace/` is covered by `.gitignore`'s leading `*`, same as `exports/` and
`logs/` — local, not versioned.

**Run ID:** `YYYY-MM-DDTHHMMSSZ_<mode>_<subject>_<short>`. The timestamp sorts
lexicographically, mode and subject make a directory listing readable, and a
random suffix prevents same-second collisions. Caller-supplied mode and
subject are sanitized to `[A-Za-z0-9._-]`, so nothing in a run ID can traverse
a path. A caller may also supply its own ID (`q3-review`), which is what makes
the fan-out below work.

## Two ways a run begins

**Explicitly**, from a written request — `run create --request <path>`. This is
the real workflow: a human (or an orchestrator) states a question, and the run
exists to answer it.

**Implicitly and by default**, from a data-collection skill. Any invocation of
`investment-analyst-resources` or `market-analyst-resources` that actually
produces evidence (`--mode read`, or the default gate→refresh→read /
refresh→read sequence) attaches to a run workspace automatically, **creating
it if it does not exist**, with no flag required:

```powershell
# no --run-id, no setup step -- a run still exists after this
... investment_analyst_resources.py --ticker PLTR --mode read
# note: created run workspace 2026-08-08T025918Z_data_pull_PLTR_4108db
```

This is default-on, not opt-in, and that was a deliberate correction. It
started as an opt-in `--run-id` flag; that failed for exactly the reason an
audit trail cannot tolerate: whether a run existed depended on whether
whoever composed the command line remembered to pass it, and the first
version of this document's skill guidance ("omit for the unchanged `exports/`
behavior") actively invited skipping it. An absent run looked identical to
"no pull happened." Flipping the default moved the decision out of the
command line and into the code that always runs — the same way the
completeness trace already behaves (`if not no_trace`, not `if trace`).

Read-only or write-only invocations that produce no evidence artifact never
attach: `--mode gate` (a freshness verdict, nothing to register) and
`--mode refresh` (writes to the DB, not to a run). Two more cases opt out
explicitly, and **both are recorded, not silent** — the trace's `workspace`
field always says what happened, so a skip is a positive line in
`logs/SkillTrace.jsonl` rather than an absence indistinguishable from nothing
having run:

| Skip reason | When |
|---|---|
| `"--no-run"` | the caller passed `--no-run` |
| `"non-default-db"` | `--db-path` points somewhere other than the configured database -- the existing test/debug convention, reused so the test suite never populates the real `workspace/runs/` |

`--no-run` and `--run-id` together is a usage error — naming a run and
refusing to attach to one are contradictory.

All of this goes through **one function**, `workspace.run.ensure_run` —
attach if the run exists, create if it does not. Neither skill reimplements
it, so there is a single definition of what an auto-created run looks like no
matter which producer opens it, and a single place a create/attach race is
handled (below).

### Why an explicit ID matters: fan-out

`ensure_run` is idempotent on the ID, so an orchestrator can hand the same ID
to N skill invocations without caring which lands first — the first creates,
the rest attach:

```powershell
... --ticker PLTR --run-id q3-review   # note: created run workspace q3-review
... --ticker RTX  --run-id q3-review   # (silent -- attached)
```

One run, one audit log, two evidence records.

### What an auto-created run says about itself

A run opened this way has no stated question, and its stored `request.yaml`
says exactly that rather than inventing one:

```yaml
mode: data_pull
request:
  question: Ad-hoc data pull for PLTR; no research question was recorded.
  context: This run was opened by a data-collection skill rather than from a
    submitted request. Evidence here is not yet tied to a stated question.
```

Fabricating a plausible-sounding question here would be worse than useless — a
later reader would take it for the owner's actual intent. The restrictions
block is unchanged and still un-weakenable.

### The cost of create-on-miss

A **mistyped ID silently becomes a new empty run** instead of erroring. That is
the deliberate trade for removing the setup step. The mitigation is that
creation always prints `note: created run workspace <id>` to stderr — an
unfamiliar ID scrolling past is the only signal that a typo happened rather
than an attach. Two things are still hard errors: an ID that could traverse a
path, and a directory that exists but is half-built (missing metadata), which
is never attached to as though it were a usable run.

### The cost of default-on: run sprawl

Every evidence-producing pull now makes a directory, tagged `mode: data_pull`
and `trigger: <skill-name>`. A daily portfolio-wide pull adds on the order of
250 run directories a year. Runs are never deleted (below), so this is
accepted, not incidental — audit completeness was judged worth the sprawl.
One `--run-id` shared across a coordinated multi-ticker pull keeps that pull
to one directory instead of N; a prune policy for `mode: data_pull` runs, if
sprawl becomes a real problem, can be added later without re-tagging anything
already on disk.

### Concurrent writers to one run

`ensure_run` itself handles the *create* race: two invocations sharing one
explicit `--run-id` can both see the directory missing and both attempt to
create it; the loser catches the winner's `RunExistsError` and attaches
instead of failing. What is **not** handled is concurrent *writes* once
attached — parallel processes appending to the same run's
`evidence/sources.jsonl` do so without a lock. This follows the precedent
already set for `kb-intake` in `CLAUDE.md` ("the wiki helpers are not
concurrency-safe" → serialize the commit step) rather than adding locking: a
single invocation with multiple `--ticker` arguments is unaffected, since it
appends sequentially within one process.

## Two rules that hold everywhere

**Never invent.** Missing evidence is *registered*, with status `missing` and
a note saying why. That is the whole reason `register-evidence --missing`
exists: a downstream stage must be able to tell "we looked and it wasn't
there" from "nobody looked." No stage substitutes a value it could not
obtain.

**Never execute.** `Restrictions` refuses to let a request enable
`execute_trades` or disable `human_review_required`; `DecisionProposal` pins
`trade_executed` to `False` at the type level; and `validate` scans every
agent output and proposal at any nesting depth for order/fill/broker fields,
failing the run if it finds one. This is decision support, not trading.

The one deliberate, narrow exception is `DecisionProposal.order_guidance`
(`models.OrderGuidance`, Phase 11): advisory order-mechanics guidance --
`order_type` (Market/Limit/Stop-Limit/Stop-Market) plus one to three cited
price levels, for a live buy/sell action only. It does not weaken this
guarantee: `trade_executed`/`human_approval_required` remain pinned exactly
as above, and its field names (`order_type`, `reference_price`,
`trigger_price`, `limit_price`) were checked against the scanner's key list
above and against `analysis_models.FORBIDDEN_FIELD_NAMES` (the *thesis*-side
ban, invoked only from `InvestmentThesis`'s own validator) and collide with
neither. Every cited price is re-derived against the actual thesis/policy
worksheet on disk and rejected if it doesn't match
(`decision_validation.check_order_guidance_prices_are_grounded`) -- the same
"never invent" discipline above, applied to prices instead of evidence. See
`docs/agents/investment-portfolio-manager/architecture.md`'s "Order
guidance" section for the full mechanism.

## How information moves

```text
request.yaml
    ↓  create-run
run_metadata.json + empty registry + audit log
    ↓  scripts collect data
evidence/<artifacts> + sources.jsonl rows (present AND missing)
    ↓  build-manifest
context_manifest.yaml   ← the contract: what may be used, what is absent
    ↓  specialist stages (investment-analyst; market-researcher deferred)
agent_outputs/*.json    ← must cite registered evidence IDs
    ↓  portfolio stage (investment-portfolio-manager)
final/*.json            ← a proposal, never an order
    ↓  human review
completed → archive
```

The manifest is **regenerated, never hand-edited**. A manifest that drifted
from the registry would be worse than none, because a stage would trust it.

## Lifecycle

```text
created ──→ in_progress ──→ awaiting_input ──→ in_progress
                        └─→ awaiting_human_review ──→ completed | failed
        completed | failed ──→ archived
```

`src/workspace/state.py` holds this as data. Any transition not in the table
raises `InvalidTransitionError` — a run cannot skip from `created` straight to
`completed`. `archived` is terminal.

## Evidence

One JSONL row per piece of evidence in `evidence/sources.jsonl`.

| Status | Meaning |
|---|---|
| `pending` | Expected, not yet collected |
| `available` | Collected, artifact on disk |
| `partial` | Collected, but with known gaps |
| `missing` | Looked for, not obtainable |
| `stale` | Present but past its freshness window |
| `invalid` | Present but failed validation |

`artifact_path` is stored **run-relative** so a record survives archiving, and
a sha256 `content_hash` is computed by the registry itself — never trusted
from the caller. `validate` re-hashes every artifact, so a tampered or
truncated file is detected.

Registering the same evidence ID twice, or the same artifact twice, is
refused: two rows claiming the same bytes would let a manifest cite either.

### `evidence/` vs `calculations/`

Both directories hold artifacts and both are registered in the same registry,
but they answer different questions and the split is worth keeping clean:

| | `evidence/` | `calculations/` |
|---|---|---|
| Holds | Facts obtained from **outside** the process — a yfinance pull, an ingested document | **Deterministic arithmetic** over data the run already holds |
| Provenance means | Where the fact came from, and whether it can be trusted | Which inputs and which formula produced the number |
| Reproducible from the run alone? | No — re-fetching may return something different | Yes — same inputs, same output, always |
| First producer | `investment-analyst-resources` | `security-technicals` |

A calculation artifact is still registered in `sources.jsonl` (with its own
`evidence_type` -- `security_technicals`, `security_status`, or
`policy_worksheet`, one per producer) so it gets a content hash, an audit
event, and a place in the manifest — the registry tracks *every* artifact's
provenance, not only fetched ones. What the directory split preserves is the
ability to answer "what did this run learn from the outside world?" without
derived numbers muddying the answer.

`context_manifest.yaml` lists them separately: `evidence` entries versus
`calculation_paths`. A downstream stage finds a calculation artifact through
`calculation_paths` rather than by guessing a filename.

Registering a calculation is **best-effort** in the producing skill: a registry
failure warns to stderr but never discards a calculation that succeeded. The
artifact on disk is the deliverable; the registry entry is bookkeeping about it.

## Validation

`python src/app.py run validate --run-id <id>` returns
`{ok, errors, warnings, counts}`. pydantic checks each document's shape;
`src/workspace/validation.py` checks what a single schema cannot see:

- run ID consistency between request and metadata
- every manifest and agent-output evidence citation resolves to a registered ID
- every referenced path stays inside the run (no traversal)
- artifact content hashes still match
- no trade-execution fields anywhere
- malformed JSONL lines in either append-only log

Errors mean the run is not trustworthy. Warnings mean it is incomplete but
coherent — a fresh run legitimately has no agent outputs yet.

## Retention and archiving

```text
active run → validation → human review → completion
           → optional knowledge-base promotion → archive
```

**Runs are never deleted.** `archive-run` moves the directory, with its
structure intact, into `workspace/archive/<YYYY-MM>/<run_id>/`. Only `tmp/`
and `cache/` are discarded. An existing archive slot is never overwritten.

A run that fails validation is refused by default, so archiving cannot be used
to hide a broken run — but `--no-validate` archives it anyway, because an
abandoned or failed run must still be *retained*. There is no automatic
deletion in this phase.

### `cache/`: bulk payloads, purged before archive

`evidence/` never shrinks — `evidence.register` computes a content hash and
`validation.validate_run` re-verifies it forever, so a registered artifact can
never be deleted without turning every future `run validate` into a permanent
error. Combined with "runs are never deleted," a bulk raw payload embedded in
a registered artifact would sit at full size in `workspace/runs/` forever.
`cache/` (`src/workspace/cache.py`) exists to hold exactly that kind of
payload — full price history, financial statements, raw options/news
blobs — **outside** the evidence registry, so it can be deleted safely.

Two rules make this work:

- **A `cache/` payload is never registered as evidence.** Only the small
  bundle that references it (via a `{"cached": true, "cache_path": "cache/..."}`
  pointer — see `investment_analyst_resources.build_bundle`) is registered
  and lands in `evidence/`. A stage that needs the full payload back (the
  worksheet builder) resolves the pointer by reading the file, the same way
  it already reads the bundle itself — never a fetch, never a database read.
- **Purge is automatic, not something an agent remembers to call.** `run
  set-status` purges `cache/` the moment a run enters `completed`, `failed`,
  or `insufficient_evidence` (`state.TERMINAL_STATUSES`); `run archive`
  purges it too, covering a run archived directly from a non-terminal status.
  `awaiting_human_review` is deliberately excluded — an analyst's cache must
  survive for the portfolio-manager stage that reads the same run next.
  `run gc --older-than-days N` sweeps `workspace/runs/` for a run that
  crashed before ever reaching a terminal status.

Each purge writes `cache/cache_manifest.json` — `{filename, sha256, bytes,
source, fetched_at, purged_at}` per deleted file — so the payload's
provenance is detectable after deletion, even though the bytes are not.
**This is a conscious non-reproducibility trade:** a purged run's thesis can
no longer be re-derived from its original inputs, only re-fetched at
today's prices. Purge is idempotent — calling it on an already-purged or
never-populated `cache/` is a clean no-op.

A run shared by a ticker fan-out has **one status for the whole run**, not
one per ticker. A caller must not set a terminal status until every ticker
in the fan-out has finished, or the first to finish purges cache the others
still need.

## Knowledge-base promotion

**Deferred, deliberately.** The boundary is defined here so it is not
improvised later. Only these should ever be promoted from a run into
`Knowledge-Base/`, and only after human review:

- validated durable facts
- an updated investment thesis
- a final reviewed decision
- source references
- historical reports

Everything else — raw pulls, intermediate calculations, superseded drafts —
stays in the run. Nothing is copied automatically, and no duplicate
knowledge-base structure is created inside `workspace/`.

## What is implemented today

**Working:** run creation with rollback, the state machine, the evidence
registry, manifest generation, full validation, the audit log, archiving, the
ephemeral `cache/` layer with automatic purge and a `run gc` sweeper, the
CLI, `investment-analyst-resources` writing evidence into a run (and its bulk
`db`/`live` payloads into `cache/`), `security-technicals` writing
calculation artifacts into `calculations/`, the shared, read-only
`security-status` skill resolving a ticker's owned/wishlist/avoid/retired
status for both specialist agents, the `investment-analyst` agent writing
`investment-thesis.v1` into `agent_outputs/`, and the
`investment-portfolio-manager` agent (Phase 11) — backed by the deterministic
`policy_worksheet.py` layer reading `Knowledge-Base/ref/policy_v1_1.yaml` and
`src/analytics.py`'s exposure functions — writing `DecisionProposal` into
`final/`.

**Deferred:** the market-researcher agent (`market_analyst_resources` data
layer exists; no judgment stage built on top of it yet); knowledge-base
promotion; migrating the `stock-data-prep → stock-analyst → kb-intake` chain.

`run_metadata.available_components` records which of these were reachable for
each run, so a reader can always tell "not run" from "ran and found nothing".

`run log-event` (`docs/reference/cli.md`) lets an orchestrating agent
(`investment-orchestrator`, `kb-orchestrator`) append its own steps into the
run it is coordinating — a preflight check result, which stage it dispatched,
its final report — the same `audit_log.jsonl` the specialist stages already
write to via `save-thesis`/`save-decision`. Since each ticker gets its own
run (see "One run per subject" in `investment-orchestrator`'s guardrails),
this history is inherently split by stock: a run's audit log holds only the
orchestration story for that one ticker, from the preflight that let its run
get created through to the final decision. Event names follow this
package's existing `<noun>_<past-tense-verb>` convention (`run_created`,
`manifest_built`, `cache_purged`, `pm_drafted`) — `investment-orchestrator`
uses a fixed six: `preflight_passed`, `analyst_dispatched`,
`analyst_completed`, `pm_dispatched`, `pm_completed`,
`orchestration_completed` (`docs/agents/investment-orchestrator/architecture.md`),
so the same stage reads the same way across every run, not a differently
phrased ad hoc string each time.

## Worked example (synthetic)

> The values below are a fixture — ticker `SYNTH` is not a real security and
> no market data is involved.

`tests/fixtures/synthetic_request.yaml`:

```yaml
schema_version: "1.0"
mode: "portfolio_check"
subject:
  type: "security"
  identifiers:
    ticker: "SYNTH"
request:
  question: "Should this synthetic position be held, trimmed, or exited?"
required_analysis: [business_quality, valuation, portfolio_impact]
restrictions:
  research_only: true
  execute_trades: false
  do_not_invent_data: true
  human_review_required: true
```

```powershell
uv run python src/app.py run create --request tests/fixtures/synthetic_request.yaml
# -> {"run_id": "2026-08-08T020013Z_portfolio_check_SYNTH_d1124c", ...}

# Record a gap rather than inventing a price:
uv run python src/app.py run register-evidence --run-id <id> `
    --missing --type price_quote --source synthetic --status missing `
    --note "no live pull in this fixture"

uv run python src/app.py run build-manifest --run-id <id> --target-stage investment_analyst
uv run python src/app.py run validate --run-id <id>
uv run python src/app.py run set-status --run-id <id> --status in_progress
uv run python src/app.py run archive --run-id <id>
```

The resulting manifest carries the gap forward explicitly:

```yaml
evidence:
- evidence_id: ev_2acc684e9b5d
  evidence_type: price_quote
  status: missing
  path: null
missing_information:
- 'price_quote from synthetic: missing'
validation_status: incomplete
```

`validation_status: incomplete` is the manifest refusing to claim readiness it
cannot demonstrate — which is exactly what a downstream stage needs in order
not to guess.

## Related

- `docs/architecture/usage_tracking.md` — the shared skill trace written
  alongside run audit events
- `docs/architecture/knowledge_base.md` — the durable side of the boundary
- `docs/reference/cli.md` — the `run` command reference
