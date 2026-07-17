# Agent Development Lessons Learned

Retrospective from the `Agent-Development` branch (portfolio classifier,
holdings reconciliation, and the stock decision-support system: `kb-discovery`,
`kb-intake`, `stock-data-prep`, `stock-analyst`). Written for planning the next
agent/skill project — what worked, what broke, and what to design for up front
rather than retrofit later.

This is a retrospective, not a plan — it captures decisions already made and
their rationale, for reference when scoping new work. See `docs/plans/` for
the actual approved plans this draws from.

## 1. Separate "mechanical" from "judgment" as two agents, not one

The single biggest structural decision: one Claude Code agent runs on one
model, so any workflow mixing cheap deterministic steps with expensive
reasoning should be **two agents**, not one agent doing both.

- `stock-data-prep` (haiku) — fetch data, read context, build a worksheet.
  Zero judgment.
- `stock-analyst` (opus) — score the worksheet against a rubric, write
  narratives. Only judgment.

Applied the same way to ETF vs. equity scoring: fund-track judgment is
materially simpler than company judgment, so ETF-batch `stock-analyst` runs
get a `model: sonnet` override on the Agent tool call rather than a separate
agent definition. A deterministic validator recomputes the actual math
regardless of which model scored it, so verdict quality can't drift with the
model choice.

**Design up front:** identify the mechanical/judgment boundary before writing
any agent, and put a deterministic validator on the judgment side so the
model's output is checked, not trusted.

## 2. The LLM applies a rubric; it never predicts anything

The core decision-support principle: the model was never asked to predict
markets. The owner authors an explicit, versioned rubric
(`decision-rubric.yml` — hard gates + weighted 1-5 dimensions with written
anchors), and the LLM's only job is "does this cited evidence meet this
written criterion." A deterministic script (`rubric.py`) recomputes the
weighted score, verdict, and confidence from the model's scores — the model
cannot talk its way to a different verdict than the math produces.

The model still gets a place for genuine opinion (`analyst_view`), but it's
explicitly separate from the mechanical verdict and never overrides it.
Persistent disagreement between the two is treated as a signal to retune the
rubric, not to trust the model over the math.

**Design up front:** if a workflow involves any kind of scored judgment, put
the scoring weights/thresholds in a versioned, human-edited config file the
agents can only read — never let the model's output directly become the
decision without a recompute step.

## 3. Constrain agents by tool access and script wrappers, not prompt wording

The `portfolio-classifier` agent's access model: no arbitrary SQL, no
database path input, no raw ticker lists, no yfinance field/mode selection —
all enforced via `tools: ["Bash"]` restricting it to script wrappers, and the
wrapper scripts hard-coding the allowed queries/modes internally. The
constraint lives in the executable, not in the system prompt telling the
model not to do something.

**Design up front:** for any agent touching a database or external API with a
narrower legitimate use than the tool's full surface, write a wrapper script
that only exposes the allowed operations, and restrict the agent's `tools:`
to just what invokes that wrapper.

## 4. Agent-owned code lives beside the agent, not in `src/`

`src/` is for pipeline code with no single owner. Code used by exactly one
skill/agent belongs in that skill's `scripts/` directory (e.g.
`classify-portfolio/scripts/`, `evaluate-stock-decision/scripts/`). This was
a repo convention set early and held throughout — it kept `src/` from
accumulating one-off logic and made each skill's dependencies self-contained.

Shared parsing/helpers that multiple skills need (e.g. thesis-page table
parsing used by both the worksheet builder and the ingest script) still
belong in `src/` as a shared module — the line is "used by one skill" vs.
"used by more than one."

## 5. Token/context cost is a first-class design constraint, not an afterthought

This was the most expensive lesson, discovered only after running the system
at scale (a 25-ticker portfolio run burned 50–70M tokens before optimization).
Root causes, in order of impact:

1. **Uncapped raw data embedded in payloads.** A multi-year analyst-ratings
   table was embedded in full, twice, in every worksheet (~500KB each). Fix:
   trim at fetch time to a relevant window, replace raw tables with derived
   summary metrics, and put a hard cap (row count + char count) on any
   embedded evidence value as a permanent backstop — not just a one-time fix
   for the table that happened to be the problem.
2. **Cheap agents forced to read expensive data to report on it.** The Haiku
   prep agent had no scripted way to summarize "did the fetch succeed,"
   so it read the full 245–671KB research JSON itself. Fix: the script that
   produces the data also prints a compact stdout summary; the agent's
   Output Format is defined as "whatever the script printed," and the agent
   is explicitly told never to Read the large JSON directly.
3. **A fully deterministic step routed through one LLM agent spawn per
   unit of work.** Committing N tickers to the wiki via N separate
   `kb-intake` spawns cost ~1M+ tokens each for work with no judgment in it
   at all. Fix: one agent invocation loops the deterministic script over
   the whole batch sequentially — the loop belongs in Bash, not in N agent
   contexts.
4. **No output ceiling on narrative generation.** Analyst narrative length
   varied 6K–73K output tokens with only a documented minimum, no maximum.
   Fix: add explicit maximums in both the skill instructions and a
   structural validator check, so a runaway narrative fails fast.
5. **Reasoning models re-deriving facts a script could just hand them.**
   The analyst spent 40-50+ turns per ticker reverse-engineering "what will
   the validator compute" via ad-hoc `python -c` imports, re-reading static
   rubric/contract text every invocation, and reconstructing prior-decision
   context by digging through old artifact files. Fix: a `--precompute-only`
   mode on the validator gives the exact verdict math on demand; static
   fields that don't change per-ticker get embedded directly in the
   worksheet; prior-decision context gets computed once by a script and
   handed to the model instead of making the model go find it.

**Design up front:** before scaling any agent workflow past a single manual
run, budget expected tokens per unit of work and identify (a) any payload
with an unbounded source table, (b) any agent that must read a large
artifact just to report a small summary about it, (c) any fully
deterministic step that's currently wrapped in an LLM agent spawn, and (d)
whether the model has a fast way to check its own work against the ground
truth before writing a final answer. All four showed up here and all four
were expensive to retrofit.

## 5a. Give the model a self-check loop before the real gate

The `--precompute-only` pattern is worth calling out on its own: a cheap,
partial-artifact-tolerant mode of the *same* validator function the final
gate uses, so the model can iterate against ground truth turn-by-turn instead
of guessing and finding out only at the end. This cut an agent's pre-write
turn count dramatically because it stopped needing to import internals by
hand to predict an outcome it could just ask for directly.

**Design up front:** whenever an agent's output must satisfy a deterministic
checker, add a partial/preview mode of that checker as an iteration tool, not
just a final pass/fail gate.

## 6. Fan out per-unit-of-work; never loop N items inside one agent

Observed directly as an anti-pattern on the first full-portfolio run: handing
one `stock-data-prep` agent a list of all 23 tickers to loop over internally
serialized the entire fetch/score phase into one long sequential pass,
throwing away all available parallelism.

The rule that emerged: **parallelize any step with no shared mutable state**
(one invocation per ticker, fanned out concurrently) and **serialize only the
step that has shared mutable state** (wiki commits touch shared index/log
files with no locking, so concurrent commits silently clobber each other's
rows) — but even the serialized step should be one agent looping a
deterministic script over the batch, not N agent spawns, if the step itself
has no judgment in it (see lesson 5, point 3).

**Design up front:** for any multi-item batch workflow, explicitly classify
each step as parallel-safe (unique output path, no shared file writes) or
must-serialize (shared file/index mutation), and pick the agent-fan-out
granularity per step accordingly — don't default to one shape for the whole
pipeline.

## 7. Incremental extensibility via a registry, not a schema rewrite

The rubric's `sources:` registry (currently `yfinance`, `classification`,
`derived`) was designed so a brand-new evidence source (a filings API,
ingested documents, a technical-indicators skill) can be added by registering
it and pointing criteria at its fields — the recommendation artifact's shape
never changes, and a criterion whose source isn't supplied that run degrades
to `unknown` and renormalizes rather than erroring. This was validated in
practice: the technical-analysis feature plan added a whole new dimension and
data source with zero changes to `rubric.py` or `ingest_recommendation.py`,
only two small generic-fallback fixes in the parts that had hard-coded the
three original source names.

**Design up front:** if a system is expected to grow new input sources over
time, build the "new source degrades gracefully to unknown" behavior on day
one — it's what makes every later addition non-breaking and incremental
instead of a schema migration.

## 8. Two-track scoring instead of one-size-fits-all criteria

Scoring both equities and ETFs against the same rubric criteria initially
penalized funds for structurally lacking company financials (forced to
`unknown` → capped confidence). Fix: mark dimensions/gates
`equity_only`/`etf_only` so a fund's inapplicable criteria are excluded and
weights renormalize, rather than being scored `unknown` for something that
was never going to exist for that asset type.

**Design up front:** when one rubric/workflow must judge structurally
different categories of thing, decide explicitly which criteria apply to
which category — don't let "not applicable" and "missing/unknown" collapse
into the same signal.

## 9. Documentation must move with behavior, in the same commit

Recurring rule, enforced throughout: `docs/agents/<agent>/architecture.md`
and `docs/architecture/*.md` are updated in the same change that alters the
behavior they describe, not as a follow-up. This was checked repeatedly
(token-optimization plan, technical-analysis plan, token-reduction plan each
have an explicit "Phase: docs" step touching the same set of files). The
payoff: `CLAUDE.md` and the architecture docs stayed accurate as a fast-moving
reference throughout the branch instead of drifting.

**Design up front:** bake a "docs updated in this same change" checklist item
into the plan template itself, not just a norm to remember.

## 10. Explicit `## Handoffs` section for cross-agent chains

No agent in this repo holds the `Task` tool, so agents cannot invoke each
other — any "agent A's output feeds agent B" relationship has to be written
down for the orchestrating session (or user) to execute. The convention:
one `## Handoffs` table per agent file (after Guardrails, before Output
Format) naming the trigger, the target agent, and the exact prompt to send —
and agents with no downstream handoff say so explicitly rather than omitting
the section, so its absence is never ambiguous with an oversight.

**Design up front:** decide the handoff-documentation convention before
writing the second agent in a chain, and make "no handoff" an explicit
statement, not silence.

## 11. Guardrails against over-reading are worth writing down explicitly

Agents will, left unguided, re-read static reference material (rubric YAML,
contract docs, even script source code) on every single invocation even when
that material barely changes and is already embedded in what they were
handed. The fix wasn't just providing the data more efficiently — it also
required an explicit negative guardrail ("never read the source of X; never
read old dated artifacts under Y") because the model's default instinct is to
verify by reading more, not less.

**Design up front:** whenever a script pre-computes and embeds something for
the model, pair it with an explicit "don't re-derive this yourself" guardrail
in the agent file — the data being available doesn't stop the model from also
re-reading it.

## 12. Testing conventions that held up

- `unittest` throughout, deterministic fixtures, network always mocked
  (yfinance, email/IMAP) — never a live-service test.
- New skill scripts get a fixture-based test file following the same
  `sys.path.insert` pattern as the sibling skill under test, and a shared
  fixtures module (`tests/_decision_fixtures.py`) reused across the
  decision-support test suite rather than each test file rebuilding its own
  sample artifacts.
- Rubric changes are tested structurally (weights sum to 1.0, version bump
  detected) as an automatic side effect of the schema/weight tests, not a
  manually-remembered check.

## 13. Output format preference: summarize for humans, don't dump raw JSON

Independent of architecture: when a skill's result is shown to the user in
chat, present a formatted markdown summary (tables, sections) rather than the
raw JSON payload. This was corrected once and then held for the rest of the
branch. It's a small thing but shapes every skill's "what do I show the user"
step.

## 14. Don't re-propose a rejected refactor unprompted

One specific piece of process feedback worth generalizing: a request to
"make edits" is not blanket permission for a large rewrite — if a
multi-file refactor is rejected once, let alone twice, stop proposing that
exact shape again unprompted. Come back with smaller, one-at-a-time changes
if the topic resurfaces, rather than re-submitting the same large diff.

## Summary checklist for a new agent/skill project

- [ ] Identify the mechanical/judgment split up front; assign models per step, not per project.
- [ ] Any scored/graded output: rubric lives in a versioned config file, agents read-only, deterministic recompute step, model's opinion kept structurally separate from the mechanical verdict.
- [ ] Any agent touching a sensitive resource: wrapper scripts enforce the boundary; `tools:` restricts to just the wrapper.
- [ ] Skill-only code goes in that skill's `scripts/`; shared code goes in `src/`.
- [ ] Before scaling past a manual single run: budget tokens, cap embedded payload sizes, give cheap agents a stdout-summary instead of a raw-file read, and never route a deterministic step through one LLM spawn per item.
- [ ] Give the model a partial/preview self-check mode against any deterministic gate it must pass.
- [ ] Classify every batch-workflow step as parallel-safe or must-serialize before deciding fan-out granularity.
- [ ] If new input sources are expected over time, design the registry/graceful-degradation pattern before the second source is added.
- [ ] If judging structurally different categories, decide applicability per category explicitly (don't let "N/A" collapse into "unknown").
- [ ] Docs update in the same change as the behavior, not after.
- [ ] Cross-agent chains get an explicit `## Handoffs` section; "no handoff" is stated, not omitted.
- [ ] Pair every pre-computed/embedded convenience with an explicit "don't re-derive this" guardrail.
- [ ] Tests: deterministic fixtures, mocked network, shared fixture modules, structural checks for config invariants.
- [ ] User-facing output: formatted summary, not raw JSON.
