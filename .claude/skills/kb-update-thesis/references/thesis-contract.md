# Thesis contract

## Immutability rules

- **Original Thesis** is written once, at `create` time (or on the first
  edit if the page predates this rule), and never edited again. New
  thinking always goes in **Updated Thesis**.
- **Decision History** rows are append-only. Never delete or rewrite a row;
  correct a mistake with a new row, not an edit.
- `logs/decision-log.md`, `logs/research-log.md`, `logs/update-log.md` rows
  are append-only, enforced by `append_log.py` (it only ever appends).

## Verdict vocabulary (`--verdict`, decision log; also stated in prose in Updated Thesis)

From `Knowledge-Base/taxonomy/decision-framework.yml`:

| Verdict | Meaning |
|---|---|
| `new` | First thesis for this ticker. |
| `stronger` | New information supports the existing thesis. |
| `weaker` | New information undermines part of the thesis. |
| `unchanged` | Nothing material changed. |
| `broken` | A core assumption failed; the decision must be revisited. |

Decision/Confidence/Time Horizon/Action values also come from
`decision-framework.yml`; Portfolio Role values come from
`ref/policy_v1_1.yaml`'s `primary_groups`.

## `thesis_page.py create`

Requires `--ticker` and `--status` (one of `research`, `watchlist`,
`active`, `closed`, `rejected`). Refuses if the page already exists.
Instantiates the full section skeleton (matching
`templates/stock-thesis-template.md`), pre-fills `company_name` and
`Portfolio Role` from the classification JSON if the ticker is currently
held, and defaults Decision/Confidence/Time Horizon to
`Watchlist`/`Low`/`Medium-term` — the agent must set these to their real
values while filling in the page. Rebuilds `stocks/index.md`, the relevant
`theses/<status>/index.md` view, and the main index; appends one
`update-log.md` row.

## `thesis_page.py validate`

Structural only: confirms the page parses as valid front matter, `type` is
`stock-page`, front matter passes `kb_pages.validate_meta`, and all 19
required section headings are present. It does not check prose quality or
verify Original Thesis was left untouched — that discipline is the agent's
responsibility per the workflow in `SKILL.md`.

## `thesis_page.py set-status`

Updates front-matter `status`, the Status block's `Portfolio Status` and
`Last Updated` lines, and regenerates the `theses/<status>/index.md` views
so the page appears under its new status view. Does not touch Decision,
Confidence, or Time Horizon — edit those directly with `Edit` if they also
changed.

## `ingest_recommendation.py`

Commits a validated `stock-recommendation.v1` artifact (from the `stock-analyst`
agent, under `exports/stock-recommendations/`) into an existing `stocks/TICKER.md`.
Deterministic parts only:

- Re-validates the artifact against `decision-rubric.yml` (reusing the analyst's
  `validate_recommendation.py`); refuses an invalid artifact.
- Requires the page to already exist (creation stays an explicit `create` step).
- Rewrites the Status block's **Decision / Confidence / Time Horizon / Last
  Updated** lines only. **Portfolio Status is never changed here** — a Sell
  recommendation is decision support, not a position change; only a
  user-confirmed trade drives `set-status`.
- Appends one Decision History row: `| date | Action | verdict | Rubric v1.x
  score X.XX; <exec summary> |` (append-only, like every other Decision History
  row).
- Appends one `decision-log` and one `update-log` row.
- **On first creation only**, seeds `## Company Overview` and `## Original
  Thesis` from the artifact's `narratives.company_overview` /
  `narratives.original_thesis`. This is gated on the page actually being created
  in this run, never on the artifact's self-reported `page_exists`, so Original
  Thesis is written exactly once and never overwritten on a later update.
- Idempotence guard: refuses an artifact whose `generated` date is older than the
  latest rubric-driven Decision History row already on the page, **and** refuses
  one dated the same as an existing rubric row (a same-day re-run would otherwise
  append a duplicate Decision History row).

## `backfill_page_sections.py` (one-time)

Fills blank `## Company Overview` / `## Original Thesis` on pages that predate the
creation-time seeding above. For each `stocks/TICKER.md` with an empty Company
Overview, it splices `overview.longBusinessSummary` from the matching
`exports/stock-recommendations/TICKER-<date>-research.json`, and seeds Original
Thesis from the page's own Updated Thesis **only when Original Thesis is still
empty** (never overwrites) and substantive enough; thin pages are reported for
manual attention. Idempotent, deterministic, one `update-log` row per page.

The prose sections (Updated Thesis, analysis `section_updates`, **Analyst View**,
Bull/Bear, Key Risks, Open Questions, Monitoring, Sources) are transcribed from
the artifact's `narratives` by the agent with `Edit` afterward — the script does
not write prose, and Original Thesis is never touched.

## Analyst View section

`## Analyst View` is the model's own qualitative opinion, kept distinct from the
mechanical rubric verdict in the Status block. Unlike Original Thesis and
Decision History, it is **rewritten each update** (not append-only). It may
agree with or dissent from the Decision, but it never overrides it.

## `append_log.py` columns

| `--log` | Columns |
|---|---|
| `decision` | Date, Tickers, Action, Verdict, Note |
| `research` | Date, Tickers, Action, Sources, Note |
| `update` | Date, Action, Page, Note |
