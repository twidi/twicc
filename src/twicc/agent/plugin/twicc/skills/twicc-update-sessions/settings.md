# `update-sessions settings` — change agent settings on a batch

Change agent settings on every targeted session, each against its own provider. MCP tool: `mcp__twicc__update_sessions_settings`. Read `SKILL.md` first: it resolves `$TWICC` and holds the session selection, output and exit codes.

## Usage

```bash
$TWICC update-sessions settings [SESSION_ID...] [FLAGS] [--spawned-by X|--descendants X]
```

- Same flags as `update-session settings` (skill: `twicc-update-session`): `--preset`, `--model`, `--effort`, `--permission-mode`, `--thinking`, `--claude-in-chrome`, `--fast-mode`, `--question-widget`, `--context-max`, `--unset <field>`.
- Patch by default; `--preset` switches to replace mode. At least one flag or `--unset` is required.
- Resolution is **per session against its own provider**, so a mixed-provider batch is trivial:
  - Provider-agnostic aliases resolve to each provider's concrete value: `--model max`, `--effort max`, `--permission-mode open`, `--context-max max` (and `min`, `strict`, `auto`, ...; full list in `twicc-create-session`).
  - A flag the session's provider does not support (e.g. `--thinking` on Codex) is silently ignored for that session, no error. With nothing left to apply, the session reports `noop`.
  - A genuinely invalid value on a supported field (e.g. `--model opus` on a Codex session) yields a per-id `validation_error` (`invalid_choice` / `invalid_format` / `invalid_preset`); the other sessions proceed.

### When changes apply

- Claude Code startup settings (`effort`, `thinking`, `claude-in-chrome`, `fast-mode`, `question-widget`) apply on the next restart.
- Codex Fast mode applies on its next turn.
- No agent is interrupted mid-turn.
- Codex `question-widget` is a startup setting with **no automatic restart**: run `$TWICC sessions stop <ids>` (skill: `twicc-sessions`), then `$TWICC send-messages <ids>` (skill: `twicc-send-messages`) to apply the new value.

## Errors

- A malformed flag shared by all ids fails the whole command (exit 1): `unknown_unset_field`, `invalid_format`, `unset_conflict`, `no_op`.
- Settings not applicable to a session's provider give a per-id `validation_error`: `provider_disabled`, `invalid_choice`, `invalid_format`, `invalid_preset`.

## Examples

```bash
$TWICC update-sessions settings --spawned-by self --effort high
$TWICC update-sessions settings abc123 def456 --model opus
$TWICC update-sessions settings --descendants self --preset 'deep think'
```

## Related commands

- `$TWICC update-session <ID> settings` — one session. Skill: `twicc-update-session`.
- `$TWICC info` — models, presets and agent-settings choices. Skill: `twicc-info`.
- `$TWICC update-sessions hide` — requires a non-interactive `permission_mode` and `question_widget` disabled. File: `hide.md`.
