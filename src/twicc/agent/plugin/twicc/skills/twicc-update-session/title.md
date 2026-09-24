# `update-session <SESSION_ID> title` — rename the session

Set a new title on one session. MCP tool: `mcp__twicc__update_session_title`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' title '<NEW_TITLE>'
```

- Trimmed; non-empty; ≤ 200 characters.
- Also written to the provider's own session store.
- No batch form: `update-sessions` has no `title`.

## Errors

- Local (exit 1): `invalid_title` — empty after trim.
- Server (exit 3): the local codes above, re-checked server-side, plus `invalid_title` — title too long.

## Examples

```bash
$TWICC update-session 4a8352fb-... title 'Better title'
```
