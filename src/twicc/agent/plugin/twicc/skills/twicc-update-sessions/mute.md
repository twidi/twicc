# `update-sessions mute` / `notify` — suppress or restore notifications for a batch

Suppress or restore finished-working notifications and unread state for every targeted session. MCP tools: `mcp__twicc__update_sessions_mute`, `mcp__twicc__update_sessions_notify`. Read `SKILL.md` first: it resolves `$TWICC` and holds the session selection, output and exit codes.

## Usage

```bash
$TWICC update-sessions mute [SESSION_ID...] [--spawned-by X|--descendants X]
$TWICC update-sessions notify [SESSION_ID...] [--spawned-by X|--descendants X]
```

- `mute` — suppresses finished-working notifications and the unread state (unread icon, project/workspace badges, favicon) for each session. Questions and approvals still notify.
- `notify` — restores each per-session notification and unread path; global notification settings still apply.
- Both are independent of hidden visibility and do not restart agents.

## Examples

```bash
$TWICC update-sessions mute --spawned-by self
$TWICC update-sessions notify abc123 def456
```

## Related commands

- `$TWICC update-sessions hide` — hide sessions from listings instead. File: `hide.md`.
