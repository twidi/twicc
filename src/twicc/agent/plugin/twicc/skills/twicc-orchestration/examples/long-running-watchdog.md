# Long-running migration with a watchdog

Patterns: `supervisor` + retry/escalation.
Shape: leader supervises a set of long-running executor workers (no barrier).

## Walkthrough
1. Spawn the workers for the long job (e.g. migrate N modules), `--annotation job=migrate`.
2. Don't just block — supervise in a loop:
   `sessions --spawned-by self --annotation job=migrate --active --full` for states
   (`--full` carries `process.last_state_change_at`); `session <id> messages --tail 2` for progress.
3. Intervene:
   - drifting → `send-message <id>` mid-run to redirect (no restart);
   - hung (stale `process.last_state_change_at`, output unchanged) → `session <id> stop` for one,
     or tag several as `status=runaway` and `sessions stop --spawned-by self --annotation status=runaway --timeout <N>`, then re-spawn with smaller mandates;
   - a worker that escalates "stuck" → answer it or re-delegate its remainder.
4. Collect results as each finishes; aggregate when the set is done.

## Notes
- Supervise your direct children only; a manager watches its own subtree.
- Tell "slow but progressing" from "hung" before killing — confirm the output changed.
- Keep workers `--hidden` + non-interactive so steering isn't blocked by a UI dialog.
