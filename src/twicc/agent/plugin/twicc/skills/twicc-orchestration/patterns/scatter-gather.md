# Scatter–gather

Split an independent workload into N pieces, run them in parallel, merge.

Shape: star · push or pull · barrier · merge/concat · usually homogeneous.

## Who does what
- **Leader or manager** — you ARE the gather point. Split the work, fan out one
  worker per piece, barrier, merge their results into your deliverable.
- **Workers** — each does one piece and reports back. No worker depends on another.

(One level only. If a piece is itself a sub-project, that piece becomes a manager
and you are doing divide-and-conquer instead.)

## Protocol
1. Split into N pieces where no piece needs another's output.
2. Spawn one executor worker per piece (`create-session --hidden`), each briefed
   with its piece, skills to load, report-back, and `--annotation task_id=<k>`.
3. Barrier: `sessions wait-reply <CHILD_ID>... --since 2000-01-01 --wait-timeout 300`,
   repeated for the ids still on `timeout`.
4. Collect each result (push as it lands, or pull `session <id> messages --tail 1`;
   bulky → its scratch file).
5. Merge into one deliverable.

## Use it when
Pieces are independent and the merge is mechanical (concat, dedup, sum).
Not when pieces are sequential (→ pipeline), must agree on one answer (→ quorum), or
have a few real cross-dependencies the workers can resolve between themselves
(→ peer-coordination).

## Pitfalls
- A read-only worker pushes through the `mcp__twicc__*` tools; pulling is a fine
  default here anyway, and the only option when MCP is disabled.
- Wait on your own children only.
- One oversized piece stalls the barrier — split it further, or hand it to a manager.
- Don't fan out dozens at once — see worker-pool for backpressure.

Examples: `examples/research-synthesis.md`.
