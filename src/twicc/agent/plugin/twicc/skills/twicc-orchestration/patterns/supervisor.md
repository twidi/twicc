# Supervisor (watch and steer)

A controller watches running children and intervenes — redirect, unblock, or kill —
without waiting for them to finish.

Shape: star · pull + steering · continuous (no barrier) · — · —.

## Who does what
- **Leader or manager** — after spawning children, don't just block on a barrier:
  poll their state and progress, and act. Steer a drifting child mid-run, restart a
  stuck one, stop a runaway, answer a child that signalled it's blocked.
- **Workers / managers** — work normally; surface blockers early (in their reply or
  via `send-message parent`) so the supervisor can act.

## Protocol
1. Spawn the children (per whatever distribution pattern you're running).
2. Poll, don't just wait: `topology self` for the map,
   `sessions --spawned-by self --active --full` for direct child states
   (`--full` carries `process.last_state_change_at`), and
   `session <id> messages --tail 2` for progress.
3. Intervene:
   - drifting → `send-message <id>` mid-`assistant_turn` to redirect (no restart);
   - stuck/looping (stale `process.last_state_change_at`, repeating output) → `session <id> stop`
     for one child, or tag a batch and `sessions stop --spawned-by self --annotation status=runaway --timeout <N>`, then re-spawn or re-brief;
   - blocked child → answer it.
4. Aggregate as the underlying pattern dictates.

## Use it when
Children run long, may drift or hang, or the cost of a runaway is high.
Overkill for short, reliable fan-outs (just barrier).

## Pitfalls
- Supervise your **direct** children; a manager supervises its own subtree.
- Steering needs an executor child — you can't unblock one stuck on a UI dialog
  (which is why orchestration children run `--hidden` and non-interactive).
- Distinguish "slow but progressing" from "hung" before killing — confirm the output
  actually changed.

Examples: `examples/long-running-watchdog.md`, `examples/large-migration.md`.
