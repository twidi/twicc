# `update-sessions hide` / `unhide` — hide or unhide a batch

Hide or unhide every targeted session. MCP tools: `mcp__twicc__update_sessions_hide`, `mcp__twicc__update_sessions_unhide`. Read `SKILL.md` first: it resolves `$TWICC` and holds the session selection, output and exit codes.

## Usage

```bash
$TWICC update-sessions hide [SESSION_ID...] [--spawned-by X|--descendants X]
$TWICC update-sessions unhide [SESSION_ID...] [--spawned-by X|--descendants X]
```

- `hide` — each session needs a non-interactive `permission_mode` and `question_widget` disabled, on both providers (see `hide.md` of the `twicc-update-session` skill). A session that does not qualify is `rejected` individually; the rest are hidden.
- `unhide` — no preconditions.

## Examples

```bash
$TWICC update-sessions hide abc123 def456 ghi789
$TWICC update-sessions hide --descendants self
$TWICC update-sessions unhide --spawned-by self --annotation role=worker
```

## Related commands

- `$TWICC update-sessions settings --permission-mode ... --no-question-widget` — make sessions qualify for `hide`. File: `settings.md`.
- `$TWICC update-sessions mute` — independent of hidden visibility. File: `mute.md`.
