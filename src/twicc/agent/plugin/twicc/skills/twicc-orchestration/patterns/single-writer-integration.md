# Single-writer integration

Let several children analyze, prototype, or produce patches, but give write/final
integration authority to exactly one child or to the parent.

Shape: star → one integrator · files + pull/push · barrier then integration · select/synthesize · heterogeneous or specialist voices.

## Who does what

- **Leader or manager** — spawn analysts/implementers for slices, then assign one
  integrator to apply or merge the accepted changes.
- **Producers** — return findings, patch plans, scratch diffs, or narrow edits, but
  do not race to merge overlapping work.
- **Integrator** — owns the final working tree, applies changes in order, resolves
  conflicts, runs checks, and reports one coherent result.

## Protocol

1. Split the work into slices and decide which slices may only propose.
2. Spawn producers with `--annotation role=producer` and a scratch-file deliverable.
3. Barrier on the producers' ids only: `sessions wait-reply <PRODUCER_ID>... --wait-timeout 300`.
4. Pull producer outputs and spawn or appoint one integrator with `--annotation role=integrator`.
5. Integrator applies the selected changes, runs checks, writes the final report, and reports up.

## Use it when

Multiple agents could touch the same files, decisions must be reconciled, or a
final artifact must be coherent: feature branches, large refactors, PR fixes.
Not when outputs are naturally independent and concatenable (→ scatter-gather).

## Pitfalls

- Producers must know whether they may write or only propose.
- The integrator needs enough context to reject bad producer output.
- Don't hide conflicts under "merge everything"; integration is a real task.

Examples: `examples/parallel-feature-integration.md`.
