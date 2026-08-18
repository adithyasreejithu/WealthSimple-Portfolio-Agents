# Phase 5 Handoff — Side-by-side benchmark (deferred)

**Status:** Pilot complete; full benchmark deferred until Portfolio Manager exists (Phase 11)

---

## Scope Correction

Phase 5 was planned as a complete side-by-side benchmark: run both the new
Investment Analyst track and the legacy `stock-analyst` track on the same
six tickers, compare outputs, and decide between full replacement / dual
system / targeted adoption.

**The pilot (PLTR, ENB, OUST) revealed that a fair comparison is not possible yet.**

The two paths produce **different output types at different pipeline stages**:

- **New path:** Investment Analyst → `fundamental_rating` (attractive/neutral/unattractive)
  — a thesis about business quality, **not** a portfolio decision
- **Legacy path:** stock-analyst → Buy/Sell/Hold/Trim/Add/Watchlist/Avoid 
  — a **portfolio decision** with rubric dimensions and gate results

The new path is explicitly scoped (per Phase 4 HANDOFF.md) to produce
"fundamental attractiveness, not a portfolio action." That work is deferred
to the Portfolio Manager (Phase 11), which will convert thesis + portfolio
constraints → portfolio decision.

**Comparing these outputs is apples-to-oranges**: the new path is half a
pipeline (judgment layer only), the legacy path is a complete pipeline
(data → score → action). A fair benchmark must run both end-to-end and
measure the same final output (portfolio decision) against ground truth.

---

## What the pilot did accomplish

The 3-ticker pilot (PLTR, ENB, OUST) with both paths running independently
validated:

1. **The new path works end-to-end.** All three tickers produced valid,
   schema-compliant theses after rebuild+retry on transient failures.
   Deterministic worksheet builder confirmed (byte-identical rebuild hashes).

2. **Risk detection parity.** Both paths surfaced the same red flags:
   - ENB: dividend/payout stress (new path: `most_important_unknown`; legacy:
     gate-forced Sell)
   - OUST: insider selling + operating-margin inversion (both paths noted it)
   - PLTR: valuation multiples with no margin of safety (both paths noted it)

3. **Structural differences are design choices, not biases.** New path stays
   neutral when evidence is mixed (no gates to force a call). Legacy path
   forces a call via rubric gates, then the analyst often dissents. Both
   approaches are coherent; they're not better/worse, just different. The
   new path's "0/3 negative" is not bias, it's architecture.

4. **Stability check (weak-positive).** A second PLTR pass produced identical
   verdict fields (`fundamental_rating`, `valuation_stance`, `thesis_confidence`)
   and materially same key claims. Caveat: the second pass was not blind
   (prompt disclosed the first answer). Proper stability testing deferred
   to Phase 5 Round 2.

5. **Cost and reliability are comparable.**
   - Token costs: new path ~80k/ticker (Opus), legacy ~75-90k/ticker (haiku+Opus)
   - Latency: new path ~7 min (consistent), legacy ~5-25 min (higher variance)
   - Reliability: both had equal failure rates on first attempt this session
     (transient API error, session-wide, not path-specific)

6. **Both paths have tuning opportunities:**
   - New path: no gate mechanism (can't force negative calls even when 
     critical red flags exist); no independent valuation (all methods are
     ±15% bands around current price)
   - Legacy path: rubric has structural gaps (dividend-integrity gate 
     mismodels midstream operators; valuation dimension asymmetry where 
     momentum is uncapped but valuation is floored); most valuable output
     (Analyst View) is the LLM contradicting its own rubric (sign of a
     broken gate, not a feature)

---

## Phase 5 Round 2 — deferred to after Phase 11

Once the Portfolio Manager exists and both paths can run end-to-end
(thesis → portfolio decision), run a proper benchmark:

**Setup:**
- Same 6-ticker fixture set (PLTR, ENB, OUST already done; L, XNDU, SMH deferred)
- Both paths end-to-end: new path runs Investment Analyst → Portfolio Manager;
  legacy path runs stock-analyst → (built or adapted Portfolio Manager if needed)
- Both outputs: Buy/Sell/Hold/Trim/Add/Watchlist/Avoid (same vocabulary)
- Same decision gates: both paths apply the same portfolio constraints / 
  risk limits / sizing rules

**Measurement:**
- Do they reach the same conclusion on the same ticker? (agreement rate)
- For disagreements, which call was more defensible? (forensic audit)
- Accuracy: did the recommendation work? (ground truth from actual outcomes)
- Risk detection: which path caught risks the other missed? (retrospective)
- Cost/speed: token usage and latency (already measured, comparable)

**Bias check — revisit at scale:**
The pilot's 0/3 negative for the new path is too thin to call bias. With
6 tickers, 0/6 negative would be stronger evidence. But even then, it might
reflect the Portfolio Manager's sizing / risk-limit constraints rather than
analyst bias — a properly built PM might output Hold on a nominally-neutral
thesis if the position is already sized large. Defer bias assessment to
after PM is wired up.

---

## What's next

**Phases 6–10:** Build the remaining workflow layers (company research,
incremental updates, challenger pass, expectations history, market researcher)
in parallel. None block Phase 11.

**Phase 11 (Portfolio Manager):** Once the PM is built and both paths feed
into it, return to Phase 5 Round 2 for the real benchmark. At that point,
you can measure:
- Which end-to-end recommendation was better?
- Which path catches what the other misses?
- Which is cheaper/faster at the same output quality?

That's when the verdict (full replacement / dual system / targeted adoption)
can be recorded with confidence. Until then, both paths remain independently
valid for different use cases: the new path is better for deep thesis
research (unknowns structured, evidence traced), the legacy path is better
for fast standardized screening (gates force decisions, rubric-tuned dims
quantified).

**The scale-up (L, XNDU, SMH) is deferred.** No point running those three
until the comparison framework is fair. Pick them back up in Phase 5 Round 2,
either as part of a full 6-ticker end-to-end run, or as early validation
once the Portfolio Manager is wired in.

---

## Pilot findings for reference

See `benchmark-results.md` for the detailed rubric-dimension scorecard,
cost/latency data, stability check details, and operational findings
(worksheet artifact hygiene, insider-data token waste, duplicate evidence
pairs on retry).

Key takeaway: **both paths work. Neither is broken. They're just at
different stages of completeness, so a fair comparison must wait.**
