# Phase 6 — Incremental update and safe KB promotion

**Priority:** high  
**Dependency:** stable full thesis contract

Tasks:

1. Implement deterministic mode-to-section mapping.
2. Generate `changed/unchanged/not_evaluated` state.
3. Record prior thesis hash in run context.
4. Add thesis diff renderer.
5. Build a new deterministic, human-gated promotion component for v2 section patches.
6. Add optimistic concurrency check.
7. Add review-status requirement.
8. Append run ID, artifact hash, rating change, and change summary to history.
9. Do not invoke or extend `kb-update-thesis`, `kb-intake`, or any other legacy agent/non-resource skill.

Acceptance criteria:

- earnings update cannot rewrite Company Profile unless that section is in scope;
- stale-base promotion fails safely;
- unapproved artifact cannot be promoted;
- promotion is sequential and produces valid KB front matter/indexes;
- rerunning promotion is idempotent or rejected with a clear already-applied status.
- promotion dependency tests prove the rebuilt path does not call legacy agents or non-resource skills.

[Back to overview](00-overview.md)
