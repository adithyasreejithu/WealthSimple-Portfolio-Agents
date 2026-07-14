---
name: author-decision-rubric
description: Guide a change to the hand-curated decision rubric (Knowledge-Base/taxonomy/decision-rubric.yml) -- adjusting gate thresholds, dimension weights, anchor cutoffs, verdict bands, or confidence rules, or registering a new research source -- then validate it and require a version bump. Use when the user asks to tune, edit, or extend the stock decision rubric, e.g. "raise the Buy band", "reweight valuation", "add a moat criterion", or "register a new data source".
---

# Author Decision Rubric

`Knowledge-Base/taxonomy/decision-rubric.yml` is the human-authored policy that
turns research evidence into Buy/Sell/Hold/Trim/Add/Watchlist/Avoid decisions.
Every tunable number lives there -- gate thresholds, dimension weights, 1/3/5
anchor cutoffs, verdict bands, confidence rules. This skill is how those numbers
change: interactively, at the user's direction, validated, and versioned. KB
agents (kb-discovery, kb-intake, stock-analyst) read the rubric but never edit
it; only this skill does, and only with the user in the loop.

## When to use

- Tune a threshold or weight: "make the Buy band stricter", "weight financial
  health higher", "loosen the solvency gate".
- Add or remove a gate or dimension: "add a moat/competitive-position
  criterion", "drop the options dimension".
- Register a new research source in the `sources:` block (and any criteria that
  cite it) as new data pulls come online.

## Workflow

1. **Confirm the intent.** Restate the specific change and which field it
   touches (gate `fail_when`, a dimension `weight`, an `anchors` cutoff, a
   `verdict_bands` entry, a `confidence_rules` threshold, or the `sources:`
   registry). If a weight changes, remember the dimension weights must still sum
   to 1.0 -- adjust another weight to compensate.
2. **Edit `Knowledge-Base/taxonomy/decision-rubric.yml`** directly with `Edit`.
   Change only what the user asked for; keep the comments accurate.
3. **Bump the version** whenever gates, weights, verdict bands, or sources
   change (e.g. `version: v1.0` -> `v1.1`). Anchor wording-only tweaks that do
   not change cutoffs do not require a bump, but a bump never hurts.
4. **Validate and review the diff:**

   ```
   python .claude/skills/author-decision-rubric/scripts/check_rubric.py
   ```

   It re-runs the full structural validation (weights sum to 1.0, bands cover
   the score range, enums match `decision-framework.yml`, evidence sources are
   registered) and prints what changed versus the committed version. If it
   reports `VERSION BUMP REQUIRED`, bump the version and re-run.
5. **Log the change.** Append one row to `logs/update-log.md`:

   ```
   python .claude/skills/kb-update-thesis/scripts/append_log.py \
       --log update --action rubric-updated \
       --page taxonomy/decision-rubric.yml \
       --note "v1.0 -> v1.1: <what changed and why>"
   ```

See [references/rubric-authoring.md](references/rubric-authoring.md) for the
meaning of every tunable field and how criteria map to the fetched data.

## Guardrails

- Edit only `Knowledge-Base/taxonomy/decision-rubric.yml`. Never touch
  `Knowledge-Base/ref/*.yaml` or `CHANGELOG.md` (classifier-owned) or
  `decision-framework.yml` (the enum source of truth).
- Do not invent criteria that depend on data no registered source provides --
  either register the source first, drop the criterion, or let it score
  `unknown` (see the data-availability notes in the authoring reference).
- Every rubric change is user-directed. This skill does not retune the rubric on
  its own.
