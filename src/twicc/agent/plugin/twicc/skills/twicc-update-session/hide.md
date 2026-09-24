# `update-session <SESSION_ID> hide` / `unhide` — hide or unhide

Hide a session from all listings and broadcasts, or show it again. MCP tools: `mcp__twicc__update_session_hide`, `mcp__twicc__update_session_unhide`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' hide
$TWICC update-session '<SESSION_ID>' unhide
```

- `hide` requires both:
  - a non-interactive `permission_mode` (Claude Code: `bypassPermissions`/`dontAsk`; Codex: `yolo`/`strict`; alias `open` or `strict`);
  - `question_widget` disabled (both providers).
- If not met, update `settings` first (file: `settings.md`).
- `unhide` has no preconditions.

## Errors

- Local (exit 1): `hidden_constraint_violation` — non-interactive permission_mode or question_widget constraint not met.
- Server (exit 3): the local code above, re-checked server-side.

## Examples

```bash
$TWICC update-session 4a8352fb-... hide
$TWICC update-session 4a8352fb-... unhide
```

## Related commands

- `$TWICC update-session <ID> settings --permission-mode strict --no-question-widget` — one way to meet the `hide` preconditions. File: `settings.md`.
- `$TWICC update-sessions hide` / `unhide` — several sessions at once. Skill: `twicc-update-sessions`.
- `mute` — independent of hidden visibility. File: `mute.md`.
