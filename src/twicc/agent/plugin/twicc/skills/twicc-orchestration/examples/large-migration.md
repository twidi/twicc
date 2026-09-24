# Large staged migration

Patterns: `phase-gated-fanout` + `worker-pool` + `supervisor`.
Shape: leader/manager runs bounded waves per migration phase, validates each gate,
then advances.

## Walkthrough

1. Define phases: inventory → migrate wave N → verify wave N → summarize.
2. Pick a concurrency cap K from `usage` and risk; tag workers with
   `--annotation phase=migrate --annotation wave=<n> --annotation status=working`.
3. For each wave, spawn at most K executor workers, one module/package per worker.
4. Wait for that wave, naming only its ids:
   `sessions wait-reply <WAVE_ID>... --wait-timeout 300`.
5. Pull results and run the gate yourself, or spawn a verifier session for it:
   tests, lints, smoke checks, focused review, or manual review.
6. If a worker is hung, tag it `status=runaway` and stop the scoped batch:
   `sessions stop --spawned-by self --annotation status=runaway --timeout 30`.
7. Advance only after the gate passes; otherwise retry failed slices with smaller mandates.
8. Aggregate the migration report and known residual risks.

## Notes

- Update `wave` if you reuse sessions across waves.
- Keep writes bounded: avoid two workers touching the same module unless one is read-only.
- Use `topology self` for structure, but control live work through `--spawned-by self`.
