# Divide and conquer (recursive fan-out)

Break a large goal into pieces; hand a piece that is itself a sub-project to a
manager that splits it the same way; aggregate results back up, one level at a time.

Shape: deep tree (leader → managers → workers, any depth) · report up + pull ·
barrier at each level · aggregate at each level · usually homogeneous voices.

## Who does what
- **Leader** — cut the goal into a few coarse pieces. Per piece, decide: atomic
  concrete work → a **worker**; still a sub-project → a **manager**. Spawn them,
  wait on your direct children only, aggregate their deliverables into the final
  answer, present it to the user.
- **Manager** — run the *same loop* on your mandate: split, decide worker vs
  sub-manager per piece, spawn, wait on your direct children, aggregate into ONE
  deliverable, `send-message parent`. Your parent never sees your subtree.
- **Worker** — does its atomic piece, reports back (see `twicc-orchestration-worker`).

Leader and manager are the same role at different altitude: the leader holds the
whole goal and answers to the user; a manager holds one slice and answers to its parent.

## Protocol (every orchestrating level applies this)
1. Split your mandate into pieces that are independent *within this level*.
2. Per piece, pick the mode and brief it (mandate, skills to load, report-back —
   see the hub's briefing): atomic → worker; nested → manager.
3. Barrier on your direct children, named by id:
   `sessions wait-reply <CHILD_ID>... --since 2000-01-01 --wait-timeout 300`.
   If this level runs several annotated batches, name only the ids of the batch
   you wait on (e.g. the ones spawned with `--annotation phase=audit`).
4. Collect each deliverable (push as it lands, or pull `session <id> messages --tail 1`;
   bulky → a file in the shared scratch).
5. Aggregate into ONE deliverable, then report up / present.

## Depth & load
- Add a level only when a piece truly needs its own decomposition — each level
  costs coordination. A flat scatter-gather beats a pointless 3-level tree.
- Don't fan out 40 children at once: launch in waves (spawn K, `wait`, spawn K),
  or insert a manager layer to spread the width.

## Use it when
The goal nests naturally and pieces are independent within a level
(codebase → services → files; report → chapters → sections).
Not when pieces are sequential (→ pipeline) or all decide one question (→ quorum/debate).

## Pitfalls
- **Wait on your own children only** — a leader waits on its managers, never on a
  manager's workers. Reaching into grandchildren breaks encapsulation and races the manager.
- A manager **must spawn and report up** — read-only can do this only via the
  `mcp__twicc__*` tools (never the CLI), so prefer an executor unless you're
  deliberately relying on MCP (see twicc-orchestration › Permission modes).
- **Aggregate at every level** — a manager hands up a synthesis, not a raw dump of
  its workers' outputs.
- An uneven split stalls the barrier — split the heavy piece further or give it a manager.

Examples: `examples/codebase-audit.md`, `examples/ship-feature.md`.
