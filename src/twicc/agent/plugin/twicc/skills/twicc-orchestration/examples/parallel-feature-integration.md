# Parallel feature with single-writer integration

Patterns: `plan-then-execute` + `single-writer-integration` + `produce-refute`.
Shape: leader plans slices → producers work in parallel → one integrator applies
and verifies → refuter tries to break the result.

## Walkthrough

1. Plan the feature into slices and identify overlapping files or shared contracts.
   If the plan needs research, spawn a planner session first; the leader or
   manager reads the plan, validates it, then decides the actual slice split.
2. Spawn producer workers per slice with `--annotation role=producer --annotation slice=<name>`.
   If overlap is likely, producers write patch plans or scratch diffs instead of applying.
3. Barrier on the producers' ids only:
   `sessions wait-reply <PRODUCER_ID>... --wait-timeout 300`.
4. Pull producer outputs and spawn one executor integrator:
   `--annotation role=integrator`, briefed with the accepted producer outputs and target checks.
5. The integrator applies changes in one working tree, resolves conflicts, runs checks, and reports one result.
6. Spawn a refuter/tester on the integrated result; loop fixes back to the integrator if needed.
7. Report the final change, checks run, and any producer output rejected during integration.

## Notes

- This avoids parallel writers racing on the same files.
- Producers can be read-only if they only inspect and propose.
- The integrator must have write permission and enough context to say no.
- **Let producers align a shared contract directly.** When two slices share an
  interface (an API shape, a type, a fixture), the producers that define and consume
  it can settle it **between themselves** — `send-message <sibling_id>` or
  `send-messages --siblings self` — before they produce, instead of each guessing and
  leaving the integrator to reconcile two mismatched takes. Discover peers with
  `sessions --siblings self`. Integration still funnels through the single writer;
  this is just cheaper agreement up front. See `patterns/peer-coordination.md`.
