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

## `append_log.py` columns

| `--log` | Columns |
|---|---|
| `decision` | Date, Tickers, Action, Verdict, Note |
| `research` | Date, Tickers, Action, Sources, Note |
| `update` | Date, Action, Page, Note |
