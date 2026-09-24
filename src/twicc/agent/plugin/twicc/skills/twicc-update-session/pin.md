# `update-session <SESSION_ID> pin` / `unpin` — pin or unpin

Pin a session in a visibility scope, or clear the pin. MCP tools: `mcp__twicc__update_session_pin`, `mcp__twicc__update_session_unpin`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' pin <MODE>
$TWICC update-session '<SESSION_ID>' unpin
```

- `MODE` — `project`, `workspace`, or `all`.
- Switching scope is just another `pin`. Idempotent.

## Errors

- Local (exit 1): `invalid_pin_mode` — MODE not in `{project, workspace, all}`.
- Server (exit 3): the local code above, re-checked server-side.

## Examples

```bash
$TWICC update-session 4a8352fb-... pin project
$TWICC update-session 4a8352fb-... pin all
$TWICC update-session 4a8352fb-... unpin
```

## Related commands

- `$TWICC update-sessions pin` / `unpin` — several sessions at once. Skill: `twicc-update-sessions`.
- `archive` — auto-unpins when `autoUnpinOnArchive` is on. File: `archive.md`.
