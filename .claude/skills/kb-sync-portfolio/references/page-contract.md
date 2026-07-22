# Page contract

Both pages are produced entirely from the classify-portfolio workflow's JSON
(`exports/portfolio-classification/portfolio-classification.json` by
default) plus `Knowledge-Base/ref/policy_v1_1.yaml` for allocation targets.
No DuckDB access, no network access.

## `portfolio/holdings.md`

Front matter: `type: portfolio-page`, `status: generated`, `tickers` = every
held ticker, `generated_at` = the date the page was last regenerated.

Body: one table row per holding, sorted by descending
`current_weight_percent` -- ticker, company name (linked to
`stocks/<TICKER>.md` if that page exists), primary group, weight %, market
value, quantity, dividend yield %, classifier confidence. A trailing "Needs
Review" section lists tickers with `review_needed: true`.

## `portfolio/portfolio-overview.md`

Front matter: same shape as above, no `tickers`.

Body: holding/classified/review counts and total market value; a group
allocation table (current % vs `ref/policy_v1_1.yaml` target/min/max per
approved group, excluding `Needs Review`); a currency split table (USD/CAD/
etc. market value and % of portfolio); a "Needs Review" ticker list.

## Determinism

`generated_at` is a date (`YYYY-MM-DD`), not a timestamp, so re-running
against unchanged input on the same day produces byte-identical output --
this is what makes `--check` a meaningful dry run.

## What this script never does

- Read `src/database.py` / DuckDB directly.
- Edit `Knowledge-Base/ref/*.yaml` or `CHANGELOG.md`.
- Invent figures not present in the classification JSON or policy YAML.
