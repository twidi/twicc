# `update-session <SESSION_ID> archive` / `unarchive` — archive or unarchive

Archive a session, or bring it back. MCP tools: `mcp__twicc__update_session_archive`, `mcp__twicc__update_session_unarchive`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' archive
$TWICC update-session '<SESSION_ID>' unarchive
```

- `archive` kills the live agent, and auto-unpins if `autoUnpinOnArchive` is enabled (default: on).
- `unarchive` flips the flag; no agent restart.

## Examples

```bash
$TWICC update-session 4a8352fb-... archive
$TWICC update-session 4a8352fb-... unarchive
```

## Related commands

- `$TWICC update-sessions archive` / `unarchive` — several sessions at once. Skill: `twicc-update-sessions`.
- `$TWICC session <ID> stop` — stop the agent without touching the row. Skill: `twicc-session`.
