# `update-sessions pin` / `unpin` — pin or unpin a batch

Pin every targeted session with one scope, or unpin them. MCP tools: `mcp__twicc__update_sessions_pin`, `mcp__twicc__update_sessions_unpin`. Read `SKILL.md` first: it resolves `$TWICC` and holds the session selection, output and exit codes.

## Usage

```bash
$TWICC update-sessions pin [SESSION_ID...] --mode <project|workspace|all> [--spawned-by X|--descendants X]
$TWICC update-sessions unpin [SESSION_ID...] [--spawned-by X|--descendants X]
```

- `--mode` — required for `pin`; applies the same scope to every session.

## Errors

- Invalid `--mode` fails the whole command (exit 1).

## Examples

```bash
$TWICC update-sessions pin abc123 def456 --mode workspace
$TWICC update-sessions unpin --spawned-by self
```

## Related commands

- `$TWICC update-sessions archive` — may auto-unpin (`autoUnpinOnArchive`). File: `archive.md`.
