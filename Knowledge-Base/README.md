# Knowledge Base

This directory holds two distinct zones:

1. **Classifier reference (`ref/`, plus this README and `CHANGELOG.md`)** — the
   versioned portfolio classification YAMLs described below. Loaded directly by
   `src/portfolio_classifier.py`; changes are tracked in `CHANGELOG.md`. Wiki
   agents never edit this zone.
2. **Research wiki (everything else)** — an LLM-maintained investment research
   knowledge base: per-ticker stock/thesis pages, portfolio pages, research
   notes, ingested sources, templates, and append-only logs. Start at
   [index.md](index.md). Every wiki page carries YAML front matter per
   [templates/front-matter-spec.md](templates/front-matter-spec.md); wiki
   changes are logged in [logs/update-log.md](logs/update-log.md), **not** in
   `CHANGELOG.md`.

## Portfolio Grouping YAML v1.1

This zone contains the editable portfolio classification reference files used by the deterministic classifier.

## Files

- `ref/policy_v1_1.yaml` - approved groups, portfolio role definitions, and allocation guidance.
- `ref/classification_rules_v1_1.yaml` - rule priority and decision logic.
- `ref/security_grouping_reference_v1_1.yaml` - asset-class, ETF, sector, industry, and tag reference data.
- `ref/manual_overrides_v1_1.yaml` - ticker-level overrides with the highest priority.
- `ref/required_fields_v1_1.yaml` - minimum inputs and missing-data behavior.
- `ref/group_examples_v1_1.yaml` - example classifications for validation.
- `CHANGELOG.md` - version notes for the grouping reference.

## Usage

The classifier in `src/portfolio_classifier.py` loads these YAML files directly. The app also exposes a sample command for quick inspection:

```bash
uv run python src/app.py portfolio-classify
```

The repository still keeps the local portfolio database as the runtime source of holdings and transactions. These YAML files define policy and classification behavior only.
