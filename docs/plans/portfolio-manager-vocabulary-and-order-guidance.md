# Investment Analyst track: correctness fixes + wishlist classification, Buy/Watch/Wait/Pass, advisory order mechanics

*Merged plan. Combines the feature plan (wishlist classification, new decision
vocabulary, order guidance) with the "Wishlist Track Post-Mortem" audit
(findings F1-F6, recommendations 01-08, orchestration-agent spec).*

## Context

Two documents describe work on the same track and overlap in ways that matter:

- **The feature plan** (this document's original content) responds to a
  direct request: make wishlist classification durable, change the not-owned
  decision vocabulary to Buy/Watch/Wait/Pass, and add advisory order-mechanics
  guidance to the Portfolio Manager's output.
- **The post-mortem** audits a parallel MP/WST/BSX run and reports six
  findings, of which three (**F1**, **F2**, **F3**) directly collide with
  the feature plan.

All three collisions were verified against the code before merging. The
verification changed the plan's *ordering* (F1/F2 must precede Feature C)
and *cancelled one of the post-mortem's own recommendations* (rec 05, see
Conflict 2).

### Verified findings that reshape the plan

**F1 is real and confirmed (Critical).** `investment_worksheet.py:540` calls
`_find_latest_evidence(run_dir, "derived_calculation", ticker)`. Three
producers now register that exact evidence type:

- `.claude/skills/security-status/scripts/security_status_cli.py:220`
- `.claude/skills/security-technicals/scripts/security_technicals_cli.py:299`
- `src/workspace/policy_worksheet.py:214`

`_find_latest_evidence`'s own docstring (`investment_worksheet.py:460-465`)
still names only two producers and matches on *ticker-appears-in-artifact_path*.
Since the documented analyst workflow runs `security-status` (step 2) before
`build-worksheet` (step 4), the status artifact is registered first and is the
only candidate whenever technicals is absent. The result is exactly the chain
the post-mortem prints: `technicals` becomes a non-`None` dict whose
`technicals`/`benchmark` keys are absent, so the `else` branch in
`_build_price_and_market_context` (`investment_worksheet.py:229-236`) that
would set `technicals_gap = "no security-technicals artifact registered in
this run"` never runs. **A declared gap is converted into a silent absence,
and a false provenance citation lands in a hash-verified artifact that
`run validate` passes.**

**F2 is real (High).** `security-technicals` is absent from the analyst's
documented workflow, so it never ran in any of these runs. Because of F1 its
absence produced no gap, no warning, no trace entry.

**F3 is real but its blast radius is overstated (High → Medium).** The
post-mortem states "`app.py:339` runs it automatically inside every `pipeline`
invocation" and concludes "the next routine pipeline run deletes MP, WST and
UBER's classifications." **That is incorrect.** `_run_portfolio_classification`
(which contains line 339's `upload_portfolio_classifications`) is called from
exactly one place — `_run_classify_command` at `app.py:1349`. `run_pipeline`'s
docstring at `app.py:667` says so explicitly: *"Classification is a separate
command (`classify`) and is never run from here."* The hazard is real, but it
is triggered by `python src/app.py classify` or the dashboard's "Run
Classification" action — not by a routine `pipeline` run. This lowers urgency;
it does not change the fix, which Phase 1 delivers as a root-cause repair.

The rest of F3 is accurate and important: `portfolio_classifications` currently
holds 31 rows (28 held + MP/WST/UBER) written out-of-band by this session's
one-off scratchpad script, and no supported code path reproduces them. The
post-mortem independently diagnosed the very workaround Feature A formalizes.

### Confirmed decisions carried forward from the feature plan

Each was confirmed with the user via AskUserQuestion ("Recommended" chosen
every time), and none is reopened here:

- **Buy/Watch/Wait/Pass applies to not-currently-owned decisions only.** Owned
  positions keep `Buy/Hold/Trim/Sell/Add`. (Phase 0 of the rebuild already
  decided not to invent a synonym vocabulary for owned actions —
  `docs/plans/implementation/phase-0/HANDOFF.md:56-60`,
  `config/policies/investment-analysis-policy.yml:40-55`. This adds a second,
  narrower vocabulary; it does not reopen that decision.) Semantics:
  - **Buy** — policy checks pass, thesis attractive → enter now.
  - **Watch** — thesis attractive but a policy check fails → monitor for headroom.
  - **Wait** — policy checks pass, valuation/timing not attractive now.
  - **Pass** — the thesis itself is unattractive.
- **Order guidance is advisory, on `DecisionProposal` only** — never on the
  thesis. `trade_executed` stays `Literal[False]`, `human_approval_required`
  stays `Literal[True]`.
- **Prices come only from already-computed fields** — no new pricing model,
  no invented numbers.

### Forbidden-field scanners: verified, no amendment needed

The feature plan originally assumed Feature C would need a documented
exception carved out of the execution-field bans. It does not:

- `analysis_models.FORBIDDEN_FIELD_NAMES` (`analysis_models.py:167-173`,
  includes `order_type`, `execution_price`, `broker`, `fill_price`) is invoked
  **only** from `InvestmentThesis._reject_forbidden_fields`
  (`analysis_models.py:447-454`) — scoped to the thesis, never applied to
  `DecisionProposal`. Its own comment calls these fields *"reserved for
  Portfolio Manager"*, i.e. it anticipates exactly where Feature C's fields belong.
- `validation.FORBIDDEN_EXECUTION_KEYS` (`validation.py:39-43`) **does** scan
  `final/*.json` at any depth (`validation.py:243-244`), but contains only
  `order_id, executed_at, execution_id, fill_price, fill_quantity, filled_at,
  broker_order_id, broker_account, broker_reference` plus prefixes
  `broker_`/`fill_`. It does not contain `order_type`, `limit_price`,
  `stop_price`, `trigger_price`, or `reference_price`.

**Neither list needs changing.** Feature C's field names avoid both by
construction. This is recorded as a code comment in Phase 3 so nobody
re-derives it.

---

## Conflicts between the two plans, and how each is resolved

**Conflict 1 — Feature C's price sources don't exist yet (F1 + F2).**
The feature plan sources limit/stop prices from
`price_and_market_context.technicals.moving_averages.sma_50d/sma_200d`. Because
`security-technicals` never runs (F2) and its absence is silently erased (F1),
that block is `None` on every real run. Building Feature C's price-grounding
validator on it would produce a validator that can never be satisfied — or
worse, one satisfied by the status artifact's empty dict.
→ **Resolved by ordering.** F1/F2 become **Phase 0**, gated and shipped before
Feature C. Feature C's grounding validator is written against a worksheet that
provably carries real technicals or a real declared gap.

**Conflict 2 — Feature A vs. the post-mortem's recommendation 05.**
Rec 05 says *"Scope `upload_portfolio_classifications`'s DELETE to held
tickers, or make it an upsert."* Feature A makes `classify_portfolio()` emit
owned **and** wishlist rows, which makes the export authoritative for both —
the full replace stops being destructive because nothing is missing from the
export any more. Worse, **rec 05 as literally written becomes a bug once
Feature A ships**: if the DELETE were scoped to held tickers only, a ticker
whose wishlist declaration is *removed* would leave a stale classification row
in the table forever, with no code path to clear it.
→ **Resolved by rejecting rec 05 and fixing the root cause instead.** Keep the
full DELETE+reinsert. Add a guard (Phase 1) that refuses the sync when the
export's ticker set is a strict subset of what the DB already holds *and* the
export was generated before the last `security_status` write — a cheap
staleness check that catches "you synced a pre-Feature-A export."

**Conflict 3 — Feature A vs. recommendation 04.**
Rec 04 asks the owner to choose between three options for wishlist
classification: widen the classifier, add a classify-one-ticker path, or
declare `group_allocation_target` legitimately `not_applicable` for non-held
securities.
→ **No conflict; Feature A is the answer.** Option 1 (widen the classifier to
declared research tickers) was already chosen. Recorded explicitly in Phase 1
so rec 04 is closed rather than left open.

**Conflict 4 — recommendation 01 (distinct evidence types) touches Feature C's
neighbourhood.** Rec 01 splits `derived_calculation` into `security_technicals`
/ `security_status` / `policy_worksheet`. Feature C cites paths *inside* the
worksheet and thesis documents, not evidence types, so the surfaces don't
overlap.
→ **No conflict, but sequencing matters:** rec 01 is the structural fix for F1
and lands in Phase 0, before anything reads technicals for real.

**Conflict 5 — recommendation 07 / F6 (duplicate status resolution).**
F6 is listed as contributing to F1 (extra `derived_calculation` rows). Once
rec 01 gives each artifact its own type, duplicate status artifacts can no
longer be mistaken for technicals.
→ **F6 drops from "contributes to a critical bug" to "minor waste."** Deferred
to Phase 4, not treated as a blocker.

**Conflict 6 — F5 / the orchestration agent vs. Features B and C.**
The orchestration spec (see `docs/plans/orchestration-agent-specification.md`)
says the orchestrator must not paraphrase artifacts for subagents. Features B
and C make the PM's output richer (a second vocabulary, a price block), which
increases the temptation to summarise.
→ **No code conflict.** Phase 4 carries the rule forward explicitly, and the
orchestration agent is scoped as a separate track that must not start until
Phases 0-3 have settled the contracts it would orchestrate.

---

## Execution protocol

Four sequentially gated phases, per this repo's existing pattern
(`docs/plans/investment-analyst-rebuild-roadmap.md`, "Phase execution
protocol"): implement one phase, run its tests, **stop for explicit review and
a per-phase commit** before starting the next.

Phase 0 → 1 → 2 → 3 is a hard dependency chain for Phase 0 → 3 (Conflict 1)
and Phase 1 → 2 (the vocabulary gate reads `currently_held`, which is only
meaningful once wishlist tickers classify). Phase 4 is a backlog, not a gate.

---

## Phase 0 — Correctness fixes (post-mortem F1, F2; recs 01, 02, 03)

**Goal:** a missing input can never again masquerade as a present-but-empty
one, and `security-technicals` actually runs. Nothing in Phases 1-3 is
trustworthy until this lands.

### Files to change

1. **Evidence-type split (rec 01)** — replace the shared
   `derived_calculation` bucket:
   - `.claude/skills/security-technicals/scripts/security_technicals_cli.py:299`
     → `evidence_type="security_technicals"`
   - `.claude/skills/security-status/scripts/security_status_cli.py:220`
     → `evidence_type="security_status"`
   - `src/workspace/policy_worksheet.py:214`
     → `evidence_type="policy_worksheet"`
   - `src/workspace/investment_worksheet.py:540`
     → `_find_latest_evidence(run_dir, "security_technicals", ticker)`
   - Update `_find_latest_evidence`'s docstring (`investment_worksheet.py:460-465`),
     which currently claims only two producers exist.
   - Grep the whole repo for `derived_calculation` before/after; update any
     reader (evidence manifest rendering, `docs/architecture/run_workspace.md`'s
     evidence-type table, the three skills' `references/*-contract.md`).
   - **Back-compat:** existing runs on disk carry the old type. Either accept
     that old runs no longer resolve technicals (they never did — that is F1),
     or have `_find_latest_evidence` accept a tuple of types and pass
     `("security_technicals", "derived_calculation")` with the legacy value
     last. Prefer the former: silently reading legacy rows re-creates the
     ambiguity this phase removes.

2. **Schema check on the loaded artifact (rec 02)** —
   `src/workspace/investment_worksheet.py:540-547`: after loading
   `technicals_path`, verify the parsed document's `schema` field identifies a
   security-technicals artifact. On mismatch, raise `WorkspaceError` naming the
   artifact and the evidence id. On absence, fall through to the existing
   `else` branch so `technicals_gap` is set. **Present-but-empty must become
   unreachable** — this is defense in depth behind the type split, because the
   type split alone would silently regress if a fourth producer ever reused
   the name.

3. **Run `security-technicals` as a fixed stage (F2)** —
   `.claude/agents/investment-analyst.md`: insert `security-technicals` into
   the documented workflow between `security-status` and `build-worksheet`,
   matching the order defined in `docs/plans/orchestration-agent-specification.md`
   (`resources → security-status → security-technicals → build-worksheet →
   analyst → build-policy-context → portfolio-manager`). Note in the agent
   body that a ticker with too little history legitimately yields a partial
   technicals artifact — that is a declared gap, not a failure, and the skill's
   own trace already grades only what was obtainable.

4. **Worksheet-stage completeness trace (rec 08)** — extend the existing
   `src/skill_trace.py` writer to record, at worksheet-build time, whether
   technicals / prior-thesis / market-context were present or absent. This is
   what would have surfaced F1 and F2 on run one. Keep the single shared trace
   format (per `CLAUDE.md`), grading only what was obtainable.

### Tests (rec 03 — the regression test *is* the deliverable here)

New cases in `tests/test_investment_worksheet.py` (or wherever
`build_worksheet`'s I/O wrapper is covered):

- `test_status_artifact_is_not_mistaken_for_technicals` — register a
  `security_status` artifact for the ticker, register no technicals, build the
  worksheet, and assert `price_and_market_context["technicals_gap"]` is
  non-null **and** that the gap string appears in `unknowns`. This is the exact
  failure F1 describes, and it was invisible to the current suite because the
  output stayed schema-valid.
- `test_policy_worksheet_artifact_is_not_mistaken_for_technicals` — same
  shape, third producer.
- `test_real_technicals_artifact_is_loaded_and_gap_is_none`.
- `test_unrecognised_artifact_schema_raises` — rec 02's loud failure.
- `test_source_evidence_cites_the_technicals_evidence_id` — guards the false-
  provenance half of F1, which the gap assertion alone does not catch.
- Apply the same test shape to **every** optional-input gap in the builder
  (prior thesis, market context), not just technicals.

### Verification

- `uv run python -m unittest discover -s tests`
- Rebuild one worksheet in a scratch run with no technicals registered;
  confirm `technicals_gap` is set, the gap is in `unknowns`, and
  `source_evidence.technicals_evidence_id` is null rather than citing the
  status artifact.
- Rebuild with technicals registered; confirm real SMA values land in
  `price_and_market_context.technicals.moving_averages`.
- Re-run `run validate` on both; confirm the no-technicals run still validates
  (a declared gap is valid) and that the worksheet now *says so*.

---

## Phase 1 — Feature A: durable wishlist classification (closes rec 04, fixes F3)

**Goal:** `classify-portfolio` classifies owned holdings *and* declared
wishlist tickers in one run, with no manual script, marking each holding
`ownership_status: "owned" | "wishlist"`. This makes the export authoritative
for both sets, which is what makes the full-replace sync safe again.

**Rec 04 is hereby closed with option 1: widen the classifier to declared
research tickers.** Record that decision in the skill docs so it is not
re-litigated.

### Files to change

1. **`.claude/skills/read-portfolio-classification-data/scripts/read_classification_data.py`**
   - Add `ownership_status: "owned"` to the per-record synthetic-field block
     (~line 155-158, beside `record["etf_category"] = None`).
   - Add `read_wishlist_classification_data(db_path=DATABASE_PATH) -> list[dict[str, Any]]`,
     modeled on the scratchpad script's `read_wishlist_records` but sourcing
     the ticker set from the DB rather than a hardcoded list:
     `from market_data import RESEARCH_STATUSES` (`market_data.py:170`,
     `frozenset({"wishlist"})`).
   - Query: `tickers t` joined to `security_status ss`
     (`ss.declared_status IN RESEARCH_STATUSES`), `stock_details`,
     `etf_details`, and the verified-yahoo `ticker_provider_mappings` (same
     join predicate the owned query uses at lines 140-141). **Exclude** any
     ticker with a non-zero `position_snapshots.quantity` — this mirrors the
     "owned always wins over any declaration" precedence documented at
     `database.py:350` and `analytics.py:227`, so a ticker that is both owned
     and stale-declared `wishlist` is never double-classified.
   - Set the same synthetic defaults the owned path sets (`etf_category`,
     `market_cap`, `user_thesis`, `target_weight_percent` → `None`;
     `quantity=0`; `cost_basis`, `position_market_value`,
     `current_weight_percent`, `unrealized_gain_loss_percent` → `None`;
     `has_provisional_activity=False`; `data_quality_flags=[]`), plus
     `ownership_status: "wishlist"`.
   - Reuse the existing `field_provenance` construction and `_json_value`
     helpers — do not duplicate that logic.
   - Call `_validate_database` for the schema guard.
     `_validate_positions_fresh` is **not** needed: this path reads
     `position_snapshots` only as a live exclusion subquery, never for
     cached pricing. Document that reasoning inline, since the owned path's
     staleness guard is load-bearing and the asymmetry will look like an
     oversight otherwise.
   - Leave `read_classification_data()`'s signature and behavior otherwise
     unchanged.

2. **`.claude/skills/classify-portfolio/scripts/classification_workflow.py`**
   - In `classify_portfolio()` (line 84-119), after
     `records = read_classification_data(db_path)`, append
     `records += read_wishlist_classification_data(db_path)` (new import
     beside the existing one at line 17).
   - Nothing else changes: `build_enrichment_requests`, `merge_enrichment`,
     and `classify_holding` are all already ownership-agnostic.
   - `ownership_status` flows into each holding's `fields` dict automatically
     (the comprehension at line 97 does not exclude it), and
     `validate_output`'s `required.issubset(holding)` check (line 78-81) only
     requires a subset — **no schema-version bump needed**.

3. **`src/database_command.py::upload_portfolio_classifications()`** — keep
   the full DELETE+reinsert (see Conflict 2 for why rec 05 is rejected), but
   add a **staleness guard**: refuse the sync, with an actionable error, when
   the export's ticker set is a strict subset of the tickers already in
   `portfolio_classifications` **and** the export's `generated_at` predates the
   newest `security_status.declared_at`. That is precisely the "you're about to
   sync a pre-Feature-A export over good rows" case, and it is the one thing
   standing between a stale JSON and the 31 rows two live decisions rest on.
   Follow `read_classification_data`'s `_validate_positions_fresh` precedent:
   name the command that fixes it (`python src/app.py classify`).

4. **Docs** — update to reflect the new scope and close rec 04 in writing:
   - `.claude/skills/read-portfolio-classification-data/SKILL.md` and
     `.claude/skills/classify-portfolio/SKILL.md` (both currently state
     owned-holdings-only) + `.claude/skills/classify-portfolio/references/output-contract.md`
     (document `ownership_status`).
   - `.claude/skills/investment-analyst-resources/SKILL.md` — its "Subject
     scope: portfolio vs. research" section states classification is
     "portfolio only" as a *deliberate* exclusion; that is now stale.
   - `docs/architecture/knowledge_base.md` — `kb-sync-portfolio` reads the
     `holdings` list for portfolio-only pages. **Verify** whether its script
     filters on anything beyond presence-in-the-list; if not, update it to
     filter `ownership_status == "owned"` so wishlist names don't leak into
     generated portfolio pages.
   - `docs/reference/cli.md` — required by `CLAUDE.md` whenever behavior of a
     CLI command changes; `classify` / `portfolio-classify` /
     `classification-sync` all change scope here.

### Tests

- **New** `tests/test_read_classification_data.py`: returns only tickers whose
  `declared_status` is in `RESEARCH_STATUSES`; excludes a ticker that is both
  wishlist-declared and owned; sets `ownership_status="wishlist"` plus the
  synthetic defaults; and (regression) `read_classification_data()` sets
  `ownership_status="owned"` on every record.
- `tests/test_classification_workflow.py`: extend `classify_portfolio()`'s
  fixtures with one wishlist record; assert it appears in `holdings` with
  `review_needed` computed identically to an owned record and
  `fields["ownership_status"] == "wishlist"`.
- `tests/` coverage for `upload_portfolio_classifications`' new staleness
  guard: a subset-export-with-older-timestamp is refused; a full export is
  accepted; a subset export that is *newer* than the last status write is
  accepted (a genuinely removed wishlist declaration must still be syncable).

### Verification

- `uv run python -m unittest tests.test_read_classification_data
  tests.test_classification_workflow tests.test_portfolio_classifier`
- `uv run python src/app.py classify` against the real DB with MP/WST/UBER
  still declared `wishlist`. Confirm the export contains **31** rows, all three
  wishlist tickers carry a real `primary_group` and
  `ownership_status: "wishlist"`, and every previously-owned holding is
  unchanged. This is the moment F3 stops being a live hazard.
- Re-run `run build-policy-context` for one wishlist ticker; confirm
  `group_allocation_target` is no longer `unavailable`, with no scratchpad
  script involved.

---

## Phase 2 — Feature B: Buy/Watch/Wait/Pass for not-owned decisions

**Goal:** `proposed_action` accepts `Buy/Watch/Wait/Pass` when the subject is
not held and `Buy/Hold/Trim/Sell/Add` when it is — enforced by the type system
and re-derived deterministically from the on-disk policy worksheet, exactly as
the existing policy-failure and ownership gates are.

### Files to change

1. **`src/workspace/models.py`** (beside `PortfolioAction`, lines 237-242):
   - Add `WishlistAction = Literal["Buy", "Watch", "Wait", "Pass"]` with a
     docstring carrying the four confirmed semantics verbatim and stating that
     this vocabulary applies only when
     `policy_worksheet.subject.currently_held` is `false`.
   - Change `DecisionProposal.proposed_action` (line 293) to
     `PortfolioAction | WishlistAction`. A union of `Literal`s validates as the
     union of allowed strings, preserves `StrictModel`/`extra="forbid"`, and
     keeps `"Buy"` legal in both branches without duplication.
   - Do **not** touch `analysis_models.DECISION_FRAMEWORK_ACTIONS` (line 161) —
     that frozenset is the *thesis*-side ban list, unrelated to this
     vocabulary. `Watchlist`/`Avoid` stay excluded from both vocabularies
     (already covered by
     `tests/test_decision_validation.py:82`).

2. **`src/workspace/decision_validation.py`**:
   - Near lines 34-41, add `_OWNED_ACTIONS = frozenset({"Buy","Hold","Trim","Sell","Add"})`,
     `_NOT_OWNED_ACTIONS = frozenset({"Buy","Watch","Wait","Pass"})`, and
     `_NOT_OWNED_ACTIONS_ALLOWED_ON_POLICY_FAILURE = frozenset({"Watch","Wait","Pass"})`.
   - New `check_action_matches_ownership_vocabulary(decision, policy_worksheet)`
     following the existing check-function shape: read
     `currently_held = bool(policy_worksheet.get("subject", {}).get("currently_held"))`
     (identical to `check_action_requires_ownership`, line 142) and error when
     the action is outside the vocabulary that ownership selects, naming the
     allowed set. Call it from `validate_decision()` alongside the two existing
     worksheet-dependent checks (lines 183-185).
   - Branch `check_action_consistent_with_policy` (lines 108-125) by ownership:
     owned keeps `_ACTIONS_ALLOWED_ON_POLICY_FAILURE` (`Hold/Trim/Sell`);
     not-owned uses `_NOT_OWNED_ACTIONS_ALLOWED_ON_POLICY_FAILURE`. A not-owned
     security with a failing policy check therefore cannot be `Buy` — which is
     exactly the confirmed **Watch** semantics, now enforced rather than
     advised.
   - Keep `check_action_requires_ownership` (lines 128-149) for defense in
     depth; errors accumulate, and overlapping guarantees are cheap.

3. **`.claude/agents/investment-portfolio-manager.md`** — document both
   vocabularies, keyed off `currently_held` **as read from the policy
   worksheet** (never computed by the agent), plus the four Buy/Watch/Wait/Pass
   definitions so the agent's choice among them is grounded in the same
   language as the schema docstring.

4. **`Knowledge-Base/taxonomy/decision-framework.yml`** — unchanged. That is
   the legacy stock-analyst/kb-intake track's vocabulary; do not add
   Watch/Wait/Pass there.

### Tests

`tests/test_decision_validation.py` has close analogues to extend
(`test_all_five_portfolio_actions_are_accepted:89`,
`test_buy_on_a_non_owned_subject_is_valid:238`,
`test_add_is_blocked_when_group_allocation_target_fails:103`). Add:

- `test_all_four_wishlist_actions_are_accepted_when_not_held`
- `test_owned_vocabulary_rejected_when_not_currently_held`
- `test_wishlist_vocabulary_rejected_when_currently_held`
- `test_watch_is_required_when_not_owned_and_policy_check_fails` — with
  `currently_held=false` and a failing `group_allocation_target`, `Buy` is
  invalid and `Watch` is valid.
- `test_pass_and_wait_are_valid_when_not_owned_and_all_checks_pass`

**Read the existing suite in full first.** This phase converts previously
*permissive* combinations (owned-vocabulary actions such as `Add`/`Hold` on a
non-owned subject) into errors. Any existing test relying on that leniency —
`test_buy_on_a_non_owned_subject_is_valid` is fine (`Buy` is in both sets), but
others may not be — must be **updated, not merely extended**.

### Verification

- `uv run python -m unittest tests.test_decision_validation`
- End-to-end `investment-portfolio-manager` run on one owned ticker (unchanged
  Buy/Hold/Trim/Sell/Add) and one wishlist ticker (now proposes
  Buy/Watch/Wait/Pass). Specifically re-run MP or WST: both previously landed
  on `Hold` *only because* their group was over cap — under the new vocabulary
  a not-held name in that state must come out **Watch**, which is the more
  honest answer and a good end-to-end proof of the whole chain.

---

## Phase 3 — Feature C: advisory order-mechanics guidance

**Depends on Phase 0.** The technicals price sources this phase cites do not
exist on any real worksheet until F1/F2 are fixed (Conflict 1).

**Goal:** `DecisionProposal` optionally carries an advisory `order_guidance`
block, populated only for actions representing live buy/sell intent, with every
price byte-traceable to a value already present in the cited thesis or policy
worksheet.

### Files to change

1. **`src/workspace/models.py`**:
   - `OrderType = Literal["Market", "Limit", "Stop-Limit", "Stop-Market"]`.
   - `PriceLevel(StrictModel)`: `price: float`, `source: str`, `rationale: str`.
     `source` is a dotted citation path rooted at either `thesis.` or
     `policy_worksheet.` — e.g.
     `thesis.valuation.methods[fcf_yield].resulting_equity_value_per_share.low`,
     `thesis.scenarios.bear.fair_value_per_share`,
     `policy_worksheet.price_and_market_context.technicals.moving_averages.sma_200d`,
     `policy_worksheet.price_and_market_context.week52_low`. Document the two
     allowed roots in the docstring.
   - `OrderGuidance(StrictModel)`: `order_type: OrderType`,
     `reference_price: PriceLevel`, `trigger_price: PriceLevel | None = None`,
     `limit_price: PriceLevel | None = None`, `notes: str | None = None`.
     Docstring states plainly: advisory only, never an order; the parent's
     pinned `trade_executed`/`human_approval_required` remain the executable-
     intent guard. **Include the verified note** that these names were checked
     against `analysis_models.FORBIDDEN_FIELD_NAMES` (`analysis_models.py:167-173`,
     thesis-scoped only) and `validation.FORBIDDEN_EXECUTION_KEYS`
     (`validation.py:39-43`) and collide with neither — cite both file:line
     locations so no future reader re-derives it.
   - Add `order_guidance: OrderGuidance | None = None` to `DecisionProposal`
     after `sizing` (~line 294).
   - `model_validator` for internal shape: `Stop-Limit`/`Stop-Market` require
     `trigger_price`; `Limit`/`Stop-Limit` require `limit_price`. Follows
     `ArtifactRef._hash_is_sha256`'s precedent (lines 262-267).

2. **`src/workspace/decision_validation.py`**:
   - `_ACTIONS_REQUIRING_ORDER_GUIDANCE = frozenset({"Buy", "Add", "Trim", "Sell"})`
     (`Buy` is shared across both vocabularies; `Hold/Watch/Wait/Pass` are
     non-trade outcomes).
   - `check_order_guidance_present(decision)` — required for the four trade
     actions, and **forbidden** (must be `None`) for `Hold/Watch/Wait/Pass`.
     Pure shape; call unconditionally in `validate_decision()`.
   - `check_order_guidance_prices_are_grounded(decision, thesis, policy_worksheet)`
     — the "no invented numbers" guarantee. For each populated `PriceLevel`:
     parse `source`'s root, dotted-navigate the corresponding on-disk document,
     require the path to resolve to a numeric leaf, and require the cited
     `price` to match it within tolerance. **Use a relative tolerance
     (0.1%) with an absolute floor of $0.01**, so the check behaves the same
     for a $9 name and a $900 one. Any unresolvable path or mismatched value is
     an error quoting the exact `source` string. This applies
     `check_thesis_ref`'s "re-derive from disk, never trust the agent's copy"
     discipline (lines 59-75) to prices instead of hashes.
   - Add `_read_thesis(run_dir, path)` mirroring `_read_policy_worksheet`
     (lines 95-105); `validate_decision` needs the thesis *body*, not just its
     hash, and should load it only once `check_thesis_ref` has passed.

3. **`src/workspace/validation.py`** — no functional change. Add a comment at
   `FORBIDDEN_EXECUTION_KEYS` (line 39) recording that `order_type` /
   `limit_price` / `trigger_price` / `reference_price` are **deliberately**
   absent because `DecisionProposal.order_guidance` uses them as advisory
   fields, so a future contributor does not "fix" the list by adding them.

4. **`.claude/agents/investment-portfolio-manager.md`**:
   - Workflow step: once `proposed_action` is chosen, if it is
     `Buy/Add/Trim/Sell`, pick an `order_type` and cite prices strictly from
     values already present in the thesis
     (`valuation.methods[].resulting_equity_value_per_share`,
     `scenarios.{bull,base,bear}.fair_value_per_share`) or the policy worksheet
     (`price_and_market_context.week52_low/week52_high`,
     `...technicals.moving_averages.sma_50d/sma_200d`) — never computed or
     estimated.
   - Guardrail: *"`order_guidance` is advisory, not an order. Never add
     broker/fill/execution fields. Every price must be copied verbatim from an
     already-cited artifact, with its exact source path. If the worksheet
     declares a technicals gap, cite valuation or 52-week levels instead —
     never invent an SMA."* (That last clause only becomes safe because
     Phase 0 makes the gap real and visible.)

### Tests

- `test_order_guidance_required_for_buy_add_trim_sell`
- `test_order_guidance_forbidden_for_hold_watch_wait_pass`
- `test_stop_order_without_trigger_price_is_invalid` (schema level)
- `test_limit_order_without_limit_price_is_invalid`
- `test_order_guidance_price_matching_thesis_value_is_valid`
- `test_order_guidance_price_not_matching_any_cited_source_is_invalid`
- `test_order_guidance_source_path_that_does_not_resolve_is_invalid`
- `test_order_guidance_price_within_relative_tolerance_is_valid`
- **Explicit regression:** `find_execution_keys` on a payload containing
  `order_guidance.order_type` / `limit_price` / `reference_price` returns `[]`.
  This single assertion is the guarantee the whole feature rests on; it must
  not stay implicit.

### Verification

- `uv run python -m unittest tests.test_decision_validation
  tests.test_investment_thesis_validation`
- One `Buy` run: confirm `order_guidance` is present and its price is visible
  in the cited thesis/worksheet file on disk. One `Watch`/`Hold` run: confirm
  `order_guidance` is absent.
- One run against a ticker with a **declared technicals gap**: confirm the
  agent cites valuation/52-week levels and the validator accepts it — proving
  Phase 0's gap and Phase 3's grounding interoperate.
- `uv run python -m unittest discover -s tests`

---

## Phase 4 — Deferred backlog (post-mortem F4, F5, F6; recs 06, 07; orchestration agent)

Not gated on Phases 0-3 and **not** part of this plan's approval. Recorded so
the post-mortem's remaining findings are not lost.

- **F4 / rec 06 — lifecycle cannot express "analyst done, PM pending."** The
  analyst closes with `awaiting_human_review`; the PM's step 2 promotes to
  `in_progress` only from `created`, so its closing `set-status` is a silent
  no-op (`assert_transition` returns early when `current == target`). Either
  add an `analysis_complete` status between the agents, or leave the run
  `in_progress` until the final stage closes it — and make `set-status` report
  a same-state call instead of swallowing it.
- **F6 / rec 07 — duplicate status resolution.** `security-status` runs once
  per consuming agent. Let the second consumer reuse the same-run,
  hash-verified artifact. Downgraded from the post-mortem's framing (see
  Conflict 5): once Phase 0's type split lands, duplicate status artifacts can
  no longer be mistaken for technicals, so this is waste, not a correctness bug.
- **F5 + the orchestration agent.** See `docs/plans/orchestration-agent-specification.md`
  for the full specification. Summary: a read-only orchestrator with haiku model
  and Bash/Read/Task tools that runs preflight checks before invoking analyst
  and portfolio manager, reports blockers and halts rather than remediating,
  enforces stage order, and maintains a recorded question in the audit event.
  This should be planned separately, **after** Phases 0-3 settle the contracts
  it would orchestrate. Its preflight gate is the highest-value item in the
  backlog: every blocker the audited session hit was detectable up front with
  read-only queries, before ~74k tokens went into discovering them mid-flight.

---

## Cross-cutting documentation (end of Phase 3)

- `docs/architecture/run_workspace.md` — update the evidence-type table for
  Phase 0's split; extend the "Never execute" passage (~lines 191-195, 210,
  288) to name `order_guidance` as the one deliberately advisory exception and
  explain why it does not weaken the guarantee (pinned
  `trade_executed`/`human_approval_required`, plus both forbidden-key scanners
  traced in this plan's Context).
- `docs/reference/cli.md` — mandatory per `CLAUDE.md` for every command whose
  behavior changed (Phase 1's classification scope, any new flags).
- `docs/agents/portfolio-classifier/architecture.md` and
  `docs/architecture/decision_support_flow.md` — remove owned-only and
  single-vocabulary assumptions this plan invalidates.
- `docs/plans/` — per `CLAUDE.md`, this merged plan is stored in the repo at
  approval time as the record of the approved approach.

---

## Post-implementation note

One detail surfaced only during Phase 3 implementation, recorded here since
plans are not rewritten after the fact (per `CLAUDE.md`): the plan assumed
the **policy worksheet** (`portfolio-policy-worksheet.v1`, Phase 11) already
carried a `price_and_market_context` block with moving averages, mirroring
`investment_worksheet.py`'s own block of the same name. It does not — that
block is exclusive to the *investment* worksheet Phase 4 built, which the
Portfolio Manager never reads. The actual implementation added a narrower
`price_and_market_context` to the policy worksheet itself
(`policy_worksheet.py::_price_and_market_context`, reusing
`analytics.get_price_history`): `latest_close`/`week52_low`/`week52_high`
only, a trailing-365-day close range, no moving averages. `order_guidance`
citations against `policy_worksheet.price_and_market_context.*` are therefore
real and grounded, but narrower than this plan originally described; a
moving-average citation is not available from either the thesis or the
policy worksheet today. See `docs/agents/investment-portfolio-manager/architecture.md`'s
"Order guidance" section for the corrected, as-built description.
