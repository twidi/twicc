# `update-sessions annotations` — edit annotations on a batch

Apply the same annotation operations to every targeted session. MCP tool: `mcp__twicc__update_sessions_annotations`. Read `SKILL.md` first: it resolves `$TWICC` and holds the session selection, output and exit codes.

## Usage

```bash
$TWICC update-sessions annotations [SESSION_ID...] --op <OPERATION> [--op <OPERATION>...] [--spawned-by X|--descendants X]
```

- Two distinct annotation flags: `--op` is the **mutation** applied to each session; `--annotation` is a read-only **filter** on the scope.
- `--op` — at least one required; operations apply left-to-right: `clear`, `replace-file:PATH`, `merge-file:PATH`, `set:KEY=VALUE`, `unset:KEY` (same syntax as `update-session annotations`).

## Errors

- A malformed `--op` fails the whole command (exit 1).

## Examples

```bash
$TWICC update-sessions annotations --spawned-by self --op set:reviewed=true
$TWICC update-sessions annotations abc123 def456 --op set:phase=done --op unset:wip
```

## Related commands

- `$TWICC update-session <ID> annotations` — the same operations on one session. Skill: `twicc-update-session`.
- `$TWICC sessions --annotation` — list sessions by annotation. Skill: `twicc-sessions`.
