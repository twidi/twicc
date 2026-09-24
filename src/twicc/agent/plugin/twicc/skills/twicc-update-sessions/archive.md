# `update-sessions archive` / `unarchive` — archive or unarchive a batch

Archive or unarchive every targeted session. MCP tools: `mcp__twicc__update_sessions_archive`, `mcp__twicc__update_sessions_unarchive`. Read `SKILL.md` first: it resolves `$TWICC` and holds the session selection, output and exit codes.

## Usage

```bash
$TWICC update-sessions archive [SESSION_ID...] [--spawned-by X|--descendants X]
$TWICC update-sessions unarchive [SESSION_ID...] [--spawned-by X|--descendants X]
```

- `archive` — kills each session's live agent; auto-unpins if `autoUnpinOnArchive` is on.
- `unarchive` — flips the flag back; no agent restart.

## Examples

```bash
$TWICC update-sessions archive abc123 def456 --timeout 60
```

## Related commands

- `$TWICC update-sessions unpin` — unpin without archiving. File: `pin.md`.
- `$TWICC update-sessions hide` — hide instead of archive. File: `hide.md`.
