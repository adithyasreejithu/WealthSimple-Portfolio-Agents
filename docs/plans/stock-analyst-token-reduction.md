# Stock-Analyst Token/Turn Reduction

> On approval, save this plan verbatim to `docs/plans/stock-analyst-token-reduction.md`
> (repo convention per CLAUDE.md) as the first implementation step.

## Context

`docs/plans/token-usage-optimization.md` (Phases 1-5) already fixed worksheet *payload
size* (evidence caps, derived metrics, trimmed history) and the batch-commit /
model-tiering waste. It worked: worksheets dropped from ~500KB to 15-33KB. But a
2026-07-16 portfolio run still burned far more than the ~500K-token-per-analyst target
(`logs/AgentSkillUsage.txt`): DGRO analyst = 1.68M tokens/~25 turns, DRAM analyst =
6.01M tokens/~60 turns — with the ETF `model: sonnet` override confirmed active on both
(per each agent's `.meta.json`), so this is not a model-tiering miss.

Turn-by-turn transcript analysis (`~/.claude/projects/.../84e6b2d3.../subagents/`) found
the artifact was correct on the **first** `validate_recommendation.py` run in both cases —
there is no retry-loop problem. Instead, 40-50+ turns are burned **before** the single
`Write` + single validate, on three repeated behaviors, each re-sending the growing
context on every turn:

1. **Reverse-engineering the mechanical verdict.** No script answers "given these gate
   results and dimension scores, what will `weighted_score`/`action`/`confidence` come
   out to?" So the analyst does ~10+ separate `python -c` imports of `rubric.py`
   internals per ticker to predict what the validator will accept.
2. **Re-reading static reference material every invocation** — the full
   `decision-rubric.yml`, `decision-framework.yml`, `recommendation-contract.md`, and even
   the *source code* of `scoring_worksheet.py`/`validate_recommendation.py` — material
   that barely changes between tickers and, for three of those fields, is **already
   embedded in the worksheet** (see Confirmed facts below) and doesn't need re-reading at all.
3. **Reconstructing prior-decision context by archaeology** — digging through old dated
   artifacts under `exports/stock-recommendations/` and the thesis page's Decision
   History table to work out `verdict_vs_previous`, instead of that being handed to it
   directly.

Target outcome: collapse the ~60-turn DRAM-shaped run to ~10-15 turns by giving the
analyst (a) one command for the mechanical verdict instead of ad-hoc reverse-engineering,
(b) prior-decision context pre-assembled in the worksheet, and (c) explicit guardrails
against the over-reading habit — with no change to rubric weights, verdict bands, or any
genuine analyst judgment.

## Confirmed facts (file:line, from direct investigation)

- **`rubric.py`** (`.claude/skills/evaluate-stock-decision/scripts/rubric.py`) exposes
  only low-level pieces, no composed verdict function: `weighted_score(scores, rubric, *,
  dividend_payer, income_role, is_etf=False)` (L302-323), `lookup_action(score, *,
  gate_failed, held, rubric)` (L326-338), `evaluate_confidence(*, unknown_dimensions,
  unknown_gates, groups_ok, rubric, is_etf=False)` (L350-366), `default_time_horizon`
  (L369-374), `applicable_dimensions`/`applicable_gates` (L282-299).
- **`validate_recommendation.py`**'s `validate_recommendation()` (L118-245) calls exactly
  those three functions (L188, L197, L204) against a **full** artifact and only reports
  pass/fail + error list (`main()`, L290-320) — no partial/precompute mode exists today.
- **`scoring_worksheet.py::build_worksheet()`** (`_entry_slot`, L588-607) **already
  embeds** `fail_when` (L599), `section` (L603), and `anchors` (L604) per gate/dimension —
  the analyst does not need to re-read the rubric YAML for these three fields.
- **`position`** (`_position_context`, L656-684) only carries a flat `current_decision`
  string from an optional `--current-decision` CLI arg (L823) that **nothing currently
  wires** (grep-confirmed: referenced only in `scoring_worksheet.py`,
  `tests/test_scoring_worksheet.py`, `recommendation-contract.md`) — safe to replace, not
  a breaking change.
- **Decision History row shape** (`ingest_recommendation.py` L294-301,
  `_decision_history_dates` L71-87): table columns Date | Action | verdict | Note, verdict
  being the enum `new/stronger/weaker/unchanged/broken`
  (`thesis-contract.md` L13-24). **Confidence is not in the row** — it lives only in the
  page's `## Status` block (`_update_status_block`, L54-68, keys `Decision`/`Confidence`/
  `Time Horizon`/`Last Updated`). No single existing source carries all four together.
- **`recommendation-contract.md`**: `weighted_score`, `proposed.action`,
  `proposed.confidence` are all mechanically derivable from filled-in gates+dimensions+
  position via the three `rubric.py` functions above. `proposed.verdict_vs_previous`
  remains genuine judgment — only its *input* (a ready-made prior-decision block) is
  being pre-assembled; the comparison itself stays with the analyst.
- `src/kb_pages.py` already owns page/table parsing shared across skills
  (`parse_page_file` L115-116, `KBPageError` L83) — the right home for new shared parsing
  per CLAUDE.md's "shared modules belong in `src/`, skill-only code stays in
  `scripts/`" rule.
- `docs/agents/stock-data-prep/architecture.md` exists (alongside `plan.md`) and must be
  kept aligned per CLAUDE.md's docs-move-with-behavior rule.

## Root cause → fix mapping

| # | Root cause | Fix | Stage |
|---|---|---|---|
| 1 | No script answers "what will the verdict be" | `--precompute-only` mode on `validate_recommendation.py`, reusing its own helpers | Phase 1 |
| 2 | Analyst re-reads rubric YAML, framework, contract, and script *source* every run | Guardrails: these fields are already embedded; use precompute instead of reading source | Phase 3 |
| 3 | Analyst digs through old artifacts + Decision History table for prior context | Deterministic `position.prior_decision` computed by `scoring_worksheet.py` from the current thesis page | Phase 2 |

---

## Phase 1 — Precompute helper: `validate_recommendation.py --precompute-only`

Extend `validate_recommendation.py` rather than add a new script — it already contains
every helper needed (`applicable_gates`/`applicable_dimensions`, `weighted_score`,
`lookup_action`, `evaluate_confidence`, `_groups_ok`); a standalone script would
duplicate all of it.

File: `.claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py`

1. **Extract a pure function** `compute_verdict(artifact: dict, rubric: dict, framework:
   dict) -> dict`, inserted after `_groups_ok` (~L288). It needs only
   `artifact["position"]`, `artifact["gates"]` (id + result), `artifact["dimensions"]`
   (id + score), and `artifact["research_sources"][*]["groups_ok"]` — no citation
   resolution, no narrative checks, no re-reading of research/classification source
   files. Rebuild `held`/`dividend_payer`/`income_role`/`is_etf` the same way
   `validate_recommendation()` does today (L127-131); compute `expected_gates`/
   `expected_dims` via `applicable_gates`/`applicable_dimensions`; build `seen_gates`/
   `seen_dims`/`scores` from the artifact (same shape as the existing L142-176 logic,
   minus citation-error branches); compute `gate_failed`, `unknown_gates`,
   `unknown_dims`; call the three rubric functions plus `default_time_horizon`. Return:
   ```python
   {
     "weighted_score": ..., "action": ..., "confidence": ...,
     "default_time_horizon": ...,
     "gate_failed": ..., "unknown_gates": ..., "unknown_dimensions": ...,
     "groups_ok": ...,
     "missing_gates": [...], "missing_dimensions": [...],
   }
   ```
2. **Refactor `validate_recommendation()`** (L118-245) to call `compute_verdict()` for
   the recomputed-math block currently inlined at L187-210, so there's one
   implementation instead of two. The citation-resolution loops (which need `sources`)
   stay separate and unchanged — `compute_verdict` never touches `sources`.
3. **New CLI flag** `--precompute-only` (store_true) in `main()` (~L290-316). When set:
   load the artifact at `--path`, load rubric + framework, run `validate_rubric` (cheap
   sanity check), but **skip `_load_sources()` entirely** — precompute needs none of it.
   Call `compute_verdict()`. Print a compact block via a new `_format_precompute(verdict:
   dict) -> str` (same style as `scoring_worksheet.py`'s `_format_summary`):
   ```
   Precompute (math only -- citations/narratives NOT checked):
     weighted_score: 3.62 | action: Hold | confidence: Medium
     default_time_horizon: Long-term
     gate_failed: False | unknown_gates: 0 | unknown_dimensions: 1 | groups_ok: 9
     still unaddressed -- gates: [] | dimensions: [options_activity]
   ```
   Exit 1 only on JSON-parse/rubric-load errors (same as today); otherwise exit 0 — this
   is advisory, not a gate. The real gate stays the existing full validate run.

**CLI shape** (decided — file path only, no stdin):
```
python .claude/skills/evaluate-stock-decision/scripts/validate_recommendation.py \
    --path exports/stock-recommendations/AAPL-2026-07-16.json \
    --precompute-only
```
The analyst runs this against the **same draft file** it is incrementally writing — it
already must `Write` that file before the final validate step, so there is no new input
surface. Precondition to call out in guardrails: `compute_verdict`'s confidence is only
correct once `artifact["research_sources"]` is populated, so the analyst should copy the
worksheet's `research_sources` block into its draft verbatim, first thing.

---

## Phase 2 — Prior-decision context embedded in the worksheet

Compute `position.prior_decision` inside `scoring_worksheet.py` from a new
`--thesis-page <path>` argument. This eliminates the Decision-History/Status
archaeology without adding any new file read anywhere — the path is just forwarded as
one more CLI arg, same as `--source yfinance=...` is today.

### 2a. New shared parsing helpers in `src/kb_pages.py`

- `parse_status_block(body: str) -> dict` — walk the `## Status` section's `- Key:
  value` lines (same line-prefix-matching `ingest_recommendation.py._update_status_block`
  already uses, L54-68) and return `{"portfolio_status", "portfolio_role", "decision",
  "confidence", "time_horizon", "last_updated"}`, omitting absent keys.
- `decision_history_rows(body: str) -> list[dict]` — walk the `## Decision History`
  table (reuse the section-scan + `|`-split pattern from `ingest_recommendation.py.
  _decision_history_dates`, L71-87) and return every row as `{"date", "action",
  "verdict", "note"}`.
- `last_decision_history_row(body: str) -> dict | None` — `decision_history_rows(body)[-1]`
  if non-empty, else `None`.
- **Refactor `ingest_recommendation.py._decision_history_dates`** (L71-87) to build on
  `kb_pages.decision_history_rows` instead of its own independent `|`-split
  implementation — one table-walk, two call sites.

### 2b. `scoring_worksheet.py` changes

File: `.claude/skills/evaluate-stock-decision/scripts/scoring_worksheet.py`

1. Add explicit `sys.path.insert(0, str(ROOT / "src"))` near the top (currently only
   works as a side effect of importing `rubric_mod`); `import kb_pages`.
2. New function near `_classification_freshness` (~L615):
   ```python
   def _load_prior_decision(path: Path | None) -> dict | None:
       """Deterministically extract the last recorded decision from the ticker's
       current thesis page: last Decision History row (date, action, verdict) +
       Status block's Confidence/Time Horizon. None if no path, missing file, or
       unparseable page. History fields are individually None if the page has a
       Status block but no history rows yet (new page)."""
       if not path or not Path(path).exists():
           return None
       try:
           _, body = kb_pages.parse_page_file(Path(path))
       except kb_pages.KBPageError:
           return None
       row = kb_pages.last_decision_history_row(body) or {}
       status = kb_pages.parse_status_block(body)
       if not row and not status:
           return None
       return {
           "date": row.get("date"), "action": row.get("action"),
           "verdict": row.get("verdict"),
           "confidence": status.get("confidence"),
           "time_horizon": status.get("time_horizon"),
       }
   ```
   (Decided: partial dict with nulled history fields when Status exists but history is
   empty — never silently drop real Status data just because history rows don't exist yet.)
3. In `_position_context` (L656-684): replace `"current_decision": args.current_decision`
   with `"prior_decision": _load_prior_decision(args.thesis_page)`.
4. If `args.thesis_page` is given and `--page-exists` was not explicitly overridden,
   infer `page_exists` from `Path(args.thesis_page).exists()` (mirrors the existing
   explicit-override-wins pattern for `--role`/`--held`/`--asset-class`, L666-673).
5. CLI: add `--thesis-page` (`type=Path, default=None`) in `main()`'s argparse block
   (~L823); **remove** `--current-decision` (confirmed unused anywhere, so this is a
   clean removal, not a deprecation).
6. `_format_summary` (~L752): print the prior-decision line, e.g. `prior decision:
   2026-05-01 Hold (unchanged, Medium confidence)` or `prior decision: none (new page)`.

### 2c. Worksheet JSON shape

```json
"position": {
  "held": true, "portfolio_role": "Quality", "weight_pct": 3.2,
  "dividend_payer": true, "income_role": false,
  "asset_class": "stock", "is_etf": false,
  "page_exists": true,
  "prior_decision": {
    "date": "2026-05-01", "action": "Hold", "verdict": "unchanged",
    "confidence": "Medium", "time_horizon": "Long-term"
  }
}
```
`prior_decision` is `null` for a brand-new page. `stock-analyst` still performs the
judgment of stating `proposed.verdict_vs_previous` — it now reasons from a ready-made
comparison point instead of assembling one itself.

### 2d. `stock-data-prep` wiring

`.claude/agents/stock-data-prep.md` Workflow step 3 (`scoring_worksheet.py` invocation)
gains `--thesis-page Knowledge-Base/stocks/<TICKER>.md` whenever `kb-search`/step 1
confirms the page exists.

### 2e. Move the thesis-page Read from stock-data-prep to stock-analyst (decided)

`stock-data-prep.md` Workflow step 1 currently Reads `stocks/<TICKER>.md` to capture
the Status block and Original Thesis text. The Status/Decision-History purpose is now
fully replaced by `prior_decision`. Cut that Read from step 1 entirely — step 1 becomes
purely the existence check (does the page exist, for `page_exists`/`--thesis-page`
wiring). Instead, `stock-analyst` Reads the **current** `stocks/<TICKER>.md` at most
once, only if it needs Original Thesis prose for narrative continuity when writing
`updated_thesis`. One clear owner; matches the new guardrail that old dated exports are
never read.

---

## Phase 3 — Guardrails: stop over-reading static material

### `.claude/agents/stock-analyst.md`

- Workflow step 1 — replace "If a page exists, also read the Original Thesis so you can
  state a `verdict_vs_previous`" with: *"The worksheet's `position.prior_decision`
  already carries the last recorded date/action/verdict/confidence — use it directly for
  `verdict_vs_previous` reasoning; do not scan the thesis page's Decision History table
  or Status block yourself. If you need the Original Thesis prose itself for narrative
  continuity, `Read` the current `stocks/<TICKER>.md` once — never an older dated
  artifact."*
- New guardrail bullets:
  - *"Static reference material is already embedded in the worksheet — `fail_when` (per
    gate), `anchors` and `section` (per dimension) are copied in by
    `scoring_worksheet.py`. Do not re-read `decision-rubric.yml`, `decision-framework.yml`,
    or `recommendation-contract.md` for these fields."*
  - *"Never read the source code of `scoring_worksheet.py`, `validate_recommendation.py`,
    or `rubric.py`, and never run `python -c` to import their internals to predict the
    verdict. Run `validate_recommendation.py --path <your-draft> --precompute-only`
    instead — it prints the exact `weighted_score`/`action`/`confidence`/
    `default_time_horizon` your final artifact must match, from the same code the final
    validator uses."*
  - *"Never read old dated artifacts under `exports/stock-recommendations/` (any
    `<TICKER>-<older-date>*.json`). Prior-decision context is in the current worksheet's
    `position.prior_decision`."*
- Workflow step 4 ("Validate") — rewrite to describe the loop: iterate with
  `--precompute-only` while filling gates/dimensions, then run the full validate (no
  flag) once complete, and fix until it exits 0.

### `.claude/skills/evaluate-stock-decision/SKILL.md`

- Update the 4-script snippet to show `--precompute-only` as an interim step between
  "(LLM) Read the worksheet, score..." and the full validate call.
- Add a short callout ("Reference material is already in the worksheet — don't re-read
  it") stating the same three points as above, since skill docs are the durable source
  of truth (agent files can get summarized).
- Document `--thesis-page` and the `position.prior_decision` shape in the workflow step
  1 description.

### `.claude/agents/stock-data-prep.md`

Per Phase 2e: remove "capture the current Status block... and the Original Thesis text"
from step 1; step 1 becomes the existence check only; step 3 gains `--thesis-page`.

---

## Phase 4 — Tests

### `tests/test_validate_recommendation.py`
- `ComputeVerdictTest`: fixture artifact (reuse `_decision_fixtures.py` patterns, matching
  `tests/test_ingest_recommendation.py`'s style) with fully-scored gates/dimensions;
  assert `compute_verdict()`'s four values match hand-computed expectations, and that
  `validate_recommendation()`'s recomputed-value errors are unchanged after the refactor
  (regression against existing fixtures already in the file).
- `PrecomputeCLITest`:
  - artifact with only `position` + partial `gates`/`dimensions` (no `narratives`,
    no citations) → `--precompute-only` exits 0, prints the verdict block.
  - artifact whose `research_sources[...].path` points at a nonexistent file →
    precompute still succeeds (proves `_load_sources` is skipped), while a full validate
    on the same broken path would fail.
  - partial artifact with 2 of 3 applicable gates filled → `missing_gates` lists the
    third.

### `tests/test_scoring_worksheet.py`
- `PriorDecisionTest` (write thesis-page fixtures to temp files via `tempfile`, reusing
  the `STOCK_PAGE`-style constants already used in `tests/test_kb_pages.py`/
  `tests/test_ingest_recommendation.py`):
  - page with a 2+-row Decision History table + Status block → `_load_prior_decision`
    returns the **last** row's date/action/verdict plus Status confidence/time_horizon.
  - page with a Decision History section but zero data rows (new page) → returns
    `{"date": None, "action": None, "verdict": None, "confidence": <value>,
    "time_horizon": <value>}` (per the decided partial-dict contract).
  - `--thesis-page` pointing at a nonexistent path → `position.prior_decision` is
    `None`, `page_exists` false (when not explicitly overridden).
  - `build_worksheet()` end-to-end: `position["prior_decision"]` shape appears in full
    output.
  - `_format_summary` includes the new prior-decision line.

### `tests/test_kb_pages.py`
Add direct unit tests for `parse_status_block` and `decision_history_rows`/
`last_decision_history_row` against markdown fixtures, independent of the worksheet
builder.

### `tests/test_ingest_recommendation.py`
Regression-check only: after refactoring `_decision_history_dates` to use
`kb_pages.decision_history_rows`, existing tests must pass unchanged (no intended
behavior change, including the idempotence-guard behavior in `thesis-contract.md`).

---

## Phase 5 — Docs

- **`.claude/skills/evaluate-stock-decision/references/recommendation-contract.md`**:
  update the `position` example (currently `"current_decision": "Hold"`, ~L39-42) to the
  `prior_decision` object shape; note it's informational context for the analyst, not
  itself validated.
- **`docs/architecture/decision_support_flow.md`**: mention `prior_decision` as part of
  what `scoring_worksheet.py` resolves deterministically; add a subsection documenting
  `--precompute-only` and why it exists; note the guardrail that the analyst never reads
  old dated exports.
- **`docs/agents/stock-analyst/architecture.md`**: note the `--precompute-only` iteration
  loop; add the three new guardrails; cross-reference `position.prior_decision` in place
  of any mention of reading Original Thesis for comparison purposes.
- **`docs/agents/stock-data-prep/architecture.md`**: reflect the removed thesis-page
  Status/Original-Thesis capture from step 1 and the new `--thesis-page` pass-through in
  step 3.

---

## Verification (end-to-end)

1. `uv run python -m unittest discover -s tests` — full suite green, including new/
   refactored `kb_pages`, `scoring_worksheet`, `validate_recommendation`, and unchanged
   `ingest_recommendation` tests.
2. Build a worksheet for a ticker with an existing thesis page (e.g. the AAPL or DRAM
   ticker) with `--thesis-page` supplied; confirm `position.prior_decision` matches the
   page's actual last Decision History row + Status confidence by manual inspection.
3. Manually simulate the analyst loop: write a partial draft artifact, run
   `--precompute-only`, confirm the printed verdict matches hand-computed expectations,
   complete the artifact, confirm the full validate agrees with the last
   `--precompute-only` output.
4. Run one real single-ticker evaluation through the actual agents (prep → analyst →
   validate → kb-intake) and compare **turn count and token usage** against the DRAM
   (6.01M/~60 turns) and DGRO (1.68M/~25 turns) baselines in `logs/AgentSkillUsage.txt` —
   target a majority reduction in pre-write turns, not just raw token count.
5. Confirm `ingest_recommendation.py`'s idempotence-guard tests still pass after the
   `_decision_history_dates` refactor.

## Out of scope

- Any change to verdict bands, weights, gate `fail_when` text, or rubric structure (all
  rubric edits stay behind `author-decision-rubric`).
- Automating `proposed.verdict_vs_previous` itself — remains genuine analyst judgment;
  only its input (`prior_decision`) is pre-assembled.
- Model tiering / batch-commit changes — already covered by
  `docs/plans/token-usage-optimization.md`.
