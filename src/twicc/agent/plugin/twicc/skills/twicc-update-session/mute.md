# `update-session <SESSION_ID> mute` / `notify` — mute or restore notifications

Suppress or restore a session's finished-working notifications. MCP tools: `mcp__twicc__update_session_mute`, `mcp__twicc__update_session_notify`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' mute
$TWICC update-session '<SESSION_ID>' notify
```

- `mute` suppresses only the session's finished-working notifications. Questions and approvals still notify.
- `notify` restores the per-session finished-working notification path; global notification settings still apply.
- Both are independent of hidden visibility and do not restart the agent.

## Examples

```bash
$TWICC update-session 4a8352fb-... mute
$TWICC update-session 4a8352fb-... notify
```

## Related commands

- `$TWICC update-sessions mute` / `notify` — several sessions at once. Skill: `twicc-update-sessions`.
