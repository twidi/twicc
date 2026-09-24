# Phase-gated fan-out

Run independent work in phases. Each phase fans out, waits on only its own
children, then gates the next phase on a validation step.

Shape: star per phase · push/pull + scoped wait · gated waves · merge/advance · usually homogeneous.

## Who does what

- **Leader or manager** — define the phases, spawn children for the current phase,
  validate the phase output, then decide whether to advance, retry, or stop.
- **Workers** — execute one phase task and report back.

## Protocol

1. Define phase names (`plan`, `audit`, `implement`, `verify`) and the pass/fail gate.
2. Spawn phase workers with `--annotation phase=<name>` and task-specific metadata.
3. Wait only on that phase, naming only its workers' ids:
   `sessions wait-reply <PHASE_ID>... --wait-timeout 300`.
4. Pull/collect outputs and validate the gate.
5. If accepted, start the next phase; if rejected, re-run only the failed slice or stop the phase batch.

## Use it when

The next set of agents must not start until the current phase is complete and
checked: migrations, staged refactors, audits before edits, generate then verify.
Not when all pieces are independent and one barrier is enough (→ scatter-gather).

## Pitfalls

- Keep phase annotations current if you reuse sessions.
- Don't let one phase mutate shared files while another phase is still writing.
- Define the gate before spawning; otherwise "done" becomes ambiguous.

Examples: `examples/large-migration.md`.
