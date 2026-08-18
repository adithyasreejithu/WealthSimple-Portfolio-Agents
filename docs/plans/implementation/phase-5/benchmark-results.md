# Phase 5 Benchmark Results — Pilot (PLTR, ENB, OUST)

**Status:** Pilot complete, including the stability check (§5) and a
single-ticker model-choice check (§6). Scale-up to L, XNDU, SMH deferred at
user request (see `README.md`, task #6) — this document covers the 3-ticker
pilot only. No verdict (full replacement / dual system / targeted adoption)
should be read into this pilot; that's recorded in `HANDOFF.md` once the
full 6-ticker set is in.

Both paths were run independently per the plan — the legacy path's
`stock-analyst` output was **not** committed via `kb-intake`; all legacy
artifacts are inert files under `exports/stock-recommendations/`.

---

## 1. Per-ticker verdicts

| Ticker | New path (Investment Analyst) | Legacy path (stock-analyst) | Legacy's own dissent (Analyst View) |
|---|---|---|---|
| **PLTR** | `fundamental_rating: neutral`, `valuation_stance: demanding`, `thesis_confidence: medium` | **Hold**, High confidence, Medium-term, score 3.84/5 (unchanged vs. 2026-08-03) | Analyst agreed with Hold but flagged the rubric's own valuation dimension as *floored* — momentum (uncapped) is doing more work than valuation (capped at 2), so the score will keep drifting up as the stock rallies regardless of multiple expansion. Recommended a rubric-tuning fix. |
| **ENB** | `fundamental_rating: neutral`, `valuation_stance: indeterminate`, `thesis_confidence: medium` | **Sell** (gate-forced by `dividend_integrity` fail — GAAP payout ratio 147.7%), Medium confidence, score 2.64/5 (unchanged vs. 2026-07-14) | Analyst **dissented from its own Sell**: GAAP payout is structurally >100% for midstream pipeline operators (depreciation-heavy), calls it a rubric-modeling gap, and states an honest read of "Hold pending DCF verification." Flagged for `author-decision-rubric` — second consecutive run tripping the same gate for the same structural reason. |
| **OUST** | `fundamental_rating: neutral`, `valuation_stance: demanding`, `thesis_confidence: medium` | **Hold**, High confidence, Medium-term, score 2.96/5 (unchanged vs. 2026-07-21) | Analyst agreed with Hold but noted the score is flat while facts underneath materially worsened (20% rally into a 91%-miss quarter) — options positioning flipped from defensive to complacent, and flagged a re-score trigger if November misses again. |

**Notable convergence:** on all three tickers, the legacy path's own free-text
"Analyst View" dissent lands closer to the new path's `neutral` synthesis
than the legacy path's own mechanical rubric action does. The new path
reaches that same "neutral, mixed evidence" read directly and structurally
(no gate to dissent from); the legacy path reaches it only via the human/LLM
narrative layered on top of a mechanical score. This is worth weighing
against the bias-check finding in §3 below.

---

## 2. Rubric dimensions (`05-phase-4-side-by-side-benchmark.md`)

| Dimension | New path (Investment Analyst) | Legacy path (stock-analyst) |
|---|---|---|
| **Factual accuracy** | No fabricated figures found on read-through; all cited numbers trace to the worksheet. Not independently re-verified against source APIs. | Same — no fabrications found on read-through, not independently re-verified. |
| **Citation quality** | Every claim cites a specific `evidence_id` (only 2 exist per run — the worksheet and the bundle — so citation is coarse-grained at the artifact level, not per-field). Valuation/scenario blocks are explicitly self-labeled as "placeholder sensitivity bands, not real research" in all 3 runs — an honest epistemic flag the legacy path's valuation *score* doesn't carry. | Cites specific worksheet fields inline (e.g. `forwardPE`, `derived.fcf_yield`) — finer-grained than the new path's evidence-ID citation, but not schema-enforced. |
| **Coverage** | 100% scoped section completeness on all 3 (schema requires ~16-19 sections; none skipped). | All 8 rubric dimensions + all gates scored or explicitly marked unknown on all 3; ENB's `options_activity` correctly came back `unknown` (no options chain) rather than silently scored. |
| **Unknown discipline** | Structured `unknowns` list (14–16 items each) with a required `most_important_unknown` field. ENB's most-important-unknown was dividend/coverage data — matching exactly what tripped the legacy path's gate. | Narrative "Unknowns" section in the Analyst View, consistently present and thoughtful, but not schema-enforced. |
| **Stability** | PLTR re-run in progress at time of writing — see `README.md` note; will be appended. | Not tested this pilot (would require an identical re-run of `stock-analyst`, not in scope). |
| **Scope isolation** | N/A — no incremental-update runs in Phase 5 scope (Phase 7). | N/A, same reason. |
| **Reasoning value** | PLTR's variant-perception style claims (e.g. OUST's "Dec-2025 quarter is an outlier, not a demonstrated inflection") read as genuine analytical judgment, not restated data. | The Analyst View sections (esp. ENB's dividend-payout dissent, PLTR's valuation-asymmetry critique) are the most valuable output of the legacy path — arguably more valuable than the rubric score itself. |
| **Cost** | PLTR 89.8k tokens / ENB 66.4k / OUST 97.4k (successful run only; each also had one failed attempt whose token cost isn't logged — see §4). Single `opus` agent per ticker. Resource-bundle build (`investment-analyst-resources`) is a deterministic script, **zero LLM cost**. | PLTR 20.6k (`stock-data-prep`, haiku) + 67.7k (`stock-analyst`, opus) = 88.3k / ENB 19.9k+58.8k=78.7k / OUST 18.7k+56.9k=75.5k. Token totals are comparable to the new path, but the legacy path splits cheap-model (haiku) data-gathering from expensive-model (opus) scoring, so **$ cost is likely lower** than raw token counts suggest — the new path runs everything on `opus`, including the data-adjacent worksheet-reading step. See §6 for a check of whether that gap can be closed by downgrading the new path's judgment stage to `sonnet`. |
| **Latency** | PLTR 7.0 min / ENB 6.9 min / OUST 7.9 min — consistent, ~7 min end-to-end. | ENB 5.2 min / OUST 4.8 min — faster than the new path — but **PLTR took 24.8 min**, a clear outlier (no obvious cause in the transcript; the analysis itself wasn't unusually long). Legacy path has higher variance; new path is more consistent. |
| **Operational reliability** | 3 of 3 investment-analyst runs **failed on first attempt** this session (transient API streaming error — "response stopped arriving" / stream stall), all succeeded cleanly on retry with zero data corruption (nothing partial was left on disk). | 2 of 3 `stock-analyst` runs (ENB, OUST) failed the same way; `stock-data-prep` (3/3) and PLTR's `stock-analyst` succeeded first try. Failures were session/infra-wide, hit both agent types equally, and are **not attributable to either path's code**. |
| **Resource TRACE quality** | Formal completeness trace: PLTR 98.3%, ENB 98.1%, OUST 98.3%. Correctly marks domains as `not_applicable` rather than `missing` when a field genuinely can't exist (e.g. ENB's options-derived fields — no listed options chain — all marked `not_applicable`, not penalized). | No formal trace. `stock-data-prep` reports groups-ok/groups-empty qualitatively (e.g. "options: empty" for ENB) but doesn't distinguish "genuinely not applicable" from "should have been fetched but wasn't" the way the new path's TRACE does. |

---

## 3. Analyst-bias check

**Paired comparison (this pilot, n=3):**

| Ticker | New path `fundamental_rating` | Legacy path mechanical action | Legacy path Analyst View (dissent) |
|---|---|---|---|
| PLTR | neutral | Hold (neutral) | neutral |
| ENB | neutral | **Sell (negative)** | Hold-leaning (neutral) |
| OUST | neutral | Hold (neutral) | neutral, trending-negative ("would not add," flagged a re-score trigger) |

New path: **0/3 negative, 3/3 neutral, 0/3 positive.**
Legacy path (mechanical action): **1/3 negative (33%), 2/3 neutral (67%), 0/3 positive.**
Legacy path (own Analyst View dissent): 0/3 clearly negative — closer to the new path's distribution than its own mechanical output is.

**Broader legacy baseline** (current Decision History row across all 24
`Knowledge-Base/stocks/*.md` pages, real historical runs, not benchmark-only):

| Action | Count | % |
|---|---|---|
| Hold (neutral) | 16 | 66.7% |
| Sell + Trim (negative) | 4 | 16.7% |
| Add (positive) | 4 | 16.7% |

**Reading:** with n=3 this is not statistically decisive, but it is directionally
consistent with the concern flagged in Phase 4 HANDOFF.md §5: the new path's
`fundamental_rating` has **not yet produced a negative verdict** across the pilot,
including on ENB, where the legacy path's *mechanical* rubric forced Sell over
a specific, checkable gate (dividend payout >100%). Two structural reasons
stand out, not just "the agent is biased toward positive framing":

1. **The new path has no gate mechanism.** The legacy rubric can be forced to
   a negative action by a single failed gate, independent of the LLM's own
   synthesis. The new path has no equivalent hard-stop — it only has the
   agent's own judgment converging on a rating, and "neutral" is the
   natural resting point when evidence is genuinely mixed (as it honestly is
   for ENB: real payout-ratio stress vs. a structurally normal midstream
   depreciation profile).
2. **The new path's own evidence discipline already surfaces the same
   concern that would drive a negative call** — ENB's new-path thesis named
   dividend/coverage data as its `most_important_unknown`, the same fact the
   legacy gate keys on. It just has no vocabulary slot to convert "the most
   important unknown is unfavorable" into an unattractive rating; `neutral`
   is the only structurally available resting state for "evidence is mixed
   and a key input is missing."

This should be tracked into L/XNDU/SMH once scale-up resumes, and the
broader KB baseline should be re-checked at that point too — a 6-ticker
paired set is still thin, but 0/6 negative would be a much stronger signal
than 0/3.

---

## 4. Other findings worth carrying into `HANDOFF.md`

- **OUST/IREN data-consistency issue (pre-existing, not a Phase 5 defect):**
  both tickers show a real DB position (`position_ledger.running_quantity =
  1.0`, opened 2026-07-10) that is absent from the generated
  `Knowledge-Base/portfolio/holdings.md` (stale relative to the DB — a
  `kb-sync-portfolio` staleness gap) despite their KB pages' front matter
  correctly stating a non-`active` portfolio status. OUST was still usable
  as the "unowned/watchlist" fixture since its KB page treats it as
  research/watchlist, but it is not a *pure* zero-position case. Worth a
  separate ticket, not a Phase 5 blocker.
- **Duplicate worksheet artifacts on retry:** both the ENB and OUST retried
  `investment-analyst` runs independently flagged the same thing — a
  worksheet/analyst-context pair from the failed first attempt is left in
  the run, unreferenced by the saved thesis but byte-identical to the
  retry's own worksheet (the builder is deterministic — same hash both
  times). Not a correctness bug (the saved thesis's `worksheet_ref` points
  only at the correct pair), but worth deciding whether a rebuild should
  supersede rather than append, for run hygiene.
- **Insider-data token cost:** PLTR's run flagged that
  `ownership_and_capital_allocation.insider` in the worksheet is a raw
  ~1,400-line pandas-orient dump that consumed most of a 25k-token read
  budget and forced scripted extraction to use it correctly (distinguishing
  genuine open-market insider buys from derivative-conversion noise). A
  pre-summarized insider block would cut analyst cost materially — a
  candidate follow-up for the worksheet builder, not a Phase 5 scope item.
- **Both paths' data-gathering stages were 100% reliable** (3/3
  `investment-analyst-resources` bundle builds, 3/3 `stock-data-prep` runs)
  — all instability this session was in the LLM judgment stages, and was
  session-wide/transient rather than path-specific.

---

## 5. Stability check (complete — weak-positive result)

A second `investment-analyst` run on PLTR (same run-id, same underlying
bundle, no `save-thesis` on the second pass so the first saved artifact is
untouched) rebuilt its own worksheet independently — it came back
**byte-identical** (`sha256:663d474c...`) to the first pass's worksheet,
confirming the deterministic builder produces the same computed inputs both
times, as it also did for ENB and OUST during their retries (§4).

| Field | First pass | Second pass | Match |
|---|---|---|---|
| `fundamental_rating` | neutral | neutral | yes |
| `valuation_stance` | demanding | demanding | yes |
| `thesis_confidence` | medium | medium | yes |
| `thesis_1` (key claim) | "Genuine operating leverage, not just growth: revenue 883.9M → 1,632.6M (+84.7% YoY)... operating margin expanded monotonically 19.9% → 46.2%..." | Same framing, same anchor figures, same critical/fact/high classification; adds intermediate margin steps and the FCF figure. Asserts nothing the first pass contradicts. | yes |

**Caveat — this is a weak-positive signal, not a clean blind replication.**
The second-pass agent's task prompt disclosed the first run's verdict fields
and `thesis_1` opening text before it drafted (included so its own report
could self-diff against them), so the convergence is not fully independent
evidence. The agent itself flagged this. A stricter re-test for the
full/scale-up benchmark should withhold the prior verdict entirely and diff
independently afterward — worth doing at least once before recording a final
verdict in `HANDOFF.md`.

The agent also noted the verdict is partly *structurally* pinned regardless
of disclosure — `valuation_stance` follows directly from the worksheet's raw
multiples, and `thesis_direction` follows from `analysis_mode` plus "no
prior thesis." The genuinely free judgment calls are `fundamental_rating`
(attractive-on-business-quality vs. neutral-once-multiple-is-weighed) and
`thesis_confidence` — a future stability check should focus there.

**Minor doc-vs-schema finding:** the second pass hit one avoidable
validation round-trip because `docs/architecture/investment_thesis_schema.md`
§7's indentation makes `valuation.currency` look block-level, but the
pydantic model only accepts `currency` inside each `valuation.methods` entry
(`extra_forbidden` otherwise). Worth a doc clarification, not a code
change.

---

## 6. Model-choice check: Opus vs Sonnet (investment-analyst judgment stage)

**Not part of the legacy-vs-new-path comparison above** — a separate,
narrower check of whether the new path's judgment stage specifically needs
`opus`, prompted by §2's cost note that the new path is the more expensive
one because it runs everything (including the data-adjacent worksheet read)
on `opus`. Run under `phase5-bench-ENB`: `investment-analyst`'s workflow was
re-run on the **same ticker, same underlying worksheet content**
(byte-identical hash to the ENB pilot run in §1/§2/§3) with the model
swapped to `sonnet` (Claude Sonnet 5, extended thinking enabled), producing
a draft thesis that was validated with `check-thesis` but deliberately
**not saved** — the original Opus-generated thesis (`agent_outputs/ENB-2026-08-11T212627Z-thesis.json`)
remains the run's only committed artifact.

**n=1 ticker, single run — directional signal only, not a statistically
decisive result.** Treat this the same way as the §5 stability check's
caveat: real evidence, but thin.

| Field | Opus (original) | Sonnet 5 (comparison draft, unsaved) | Match |
|---|---|---|---|
| `fundamental_rating` | neutral | neutral | yes |
| `valuation_stance` | indeterminate | indeterminate | yes |
| `thesis_direction` | initial | initial | yes |
| `thesis_confidence` | medium | medium | yes |
| `validation.status` | valid | valid (via `check-thesis`) | yes |

**Verdict fields matched exactly.** The differentiator was underneath them:

1. **Sonnet made a repeated, checkable arithmetic error that `check-thesis`
   did not catch.** The worksheet's free-cash-flow series shows three
   consecutive quarter-over-quarter declines (2025-Q3, Q4, 2026-Q1), preceded
   by a roughly flat quarter (2025-Q2, +0.3% QoQ). Sonnet's draft asserted
   "four consecutive quarterly declines" in six separate places (executive
   conclusion, case-against, most-important-risk, a critical key claim,
   unknowns, and monitoring items). Opus's original thesis states the
   correct count ("declined in each of the three most recent sequential
   quarters"). `check-thesis` validates schema shape, citation resolution,
   forbidden fields, scenario arithmetic, and worksheet-hash matching — it
   does **not** fact-check narrative prose against the source figures it
   cites, so this error reached `valid` status cleanly. This is worth
   noting as a general limitation of the validator, independent of the
   model question: **`"valid"` means structurally sound, not arithmetically
   checked.**
2. **Opus unified two anomalous ratios into one hypothesis; Sonnet treated
   them separately.** The worksheet's `ratios_ref` block reports both
   ROIC (1.6%) and net-debt-to-EBITDA (21.5x) — both implausible for a
   large midstream operator. Opus's original thesis grouped both as likely
   sharing one root cause (an unstated/probably-non-annualized period basis)
   in a single supporting claim. Sonnet's draft flagged only the leverage
   ratio as a likely data-quality artifact and took the ROIC figure at face
   value elsewhere in the draft — a less internally consistent read of the
   same underlying anomaly.
3. **Opus's variant-perception section posed a sharper, falsifiable
   hypothesis.** Opus framed the central open question as "is the
   revenue-growth/cash-flow-decline combination a capital-deployment or
   acquisition cycle converting into contracted cash flow, versus early
   genuine economic deterioration?" and tied the neutral rating directly to
   that question being unresolved. Sonnet's equivalent section stated that
   no variant perception could be established and restated the same
   growth/FCF tension without proposing a specific causal hypothesis to
   adjudicate.
4. **Opus's `unknowns` list was more complete:** 15 items vs. Sonnet's 9,
   with Opus separately surfacing (among others) the capex growth/maintenance
   split, the unstated period basis behind `ratios_ref`, the fact that the
   worksheet's "last reported" earnings record is actually a *future*
   scheduled event (not a past result), and the absence of buyback/issuance
   and macro-sensitivity data — gaps Sonnet's draft mostly left folded into
   section prose rather than enumerated.
5. **Where the two were at parity:** guardrail compliance (no forbidden
   fields, no fabricated evidence IDs), the valuation-is-a-placeholder
   framing, and — independently, by both models — catching the same
   currency-labelling inconsistency (`valuation_methods` tagged `USD` while
   the security reports and trades in CAD on the TSX).

**Reading:** for this judgment stage, extended thinking got Sonnet to the
same top-level verdict as Opus, but did not close the gap on arithmetic
care or synthesis depth — including a factual slip a human reviewer relying
on the artifact could easily inherit. This is a direct, if thin, answer to
the §2 cost question: **downgrading the new path's judgment stage to
`sonnet` is not recommended** on this evidence; the agent config already
pins `model: opus` (`.claude/agents/investment-analyst.md`) and no change
is being made as a result of this check. If the $-cost gap versus the
legacy path (§2) is revisited later, the candidate lever is the
data-adjacent worksheet-reading step or resource-bundle interpretation, not
this judgment stage — and any such change should re-run this same
opus-vs-sonnet comparison across more than one ticker before being adopted.

---

## Appendix: draft artifacts referenced in §6

- Sonnet 5 comparison draft (unsaved, disposable):
  `workspace/runs/phase5-bench-ENB/tmp/ENB-thesis-draft-sonnet5.json`
- Opus original (saved, authoritative):
  `workspace/runs/phase5-bench-ENB/agent_outputs/ENB-2026-08-11T212627Z-thesis.json`
