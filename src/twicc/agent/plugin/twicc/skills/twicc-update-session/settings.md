# `update-session <SESSION_ID> settings` — change agent settings

Change model, effort, permission mode or other agent settings. MCP tool: `mcp__twicc__update_session_settings`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' settings [OPTIONS]
```

- **Patch mode** (no `--preset`) — only the flags you pass are written; every other field keeps its current value. `--unset <field>` resets a field to NULL (= user defined default). At least one flag or `--unset` is required.
- **Replace mode** (`--preset NAME`) — every settings field is rewritten. The preset writes the fields it defines; absent fields become NULL. Per-flag options and `--unset` override the preset.

### Flags

All optional. Discover the supported models, the valid values per provider and the presets with `$TWICC info models agent-settings presets` (skill: `twicc-info`).

- `--model VALUE` — Claude Code: `fable`, `opus`, `sonnet`, `fable-5`, `opus-5`, `opus-4.8`, `opus-4.7`, `opus-4.6`, `opus-4.5`, `sonnet-4.6`, `sonnet-4.5`. Codex: `gpt-astra`, `gpt-sol`, `gpt-terra`, `gpt-luna`, `gpt`, `gpt-sol-5.6`, `gpt-luna-5.6`.
- `--effort VALUE` — Claude Code: `low`, `medium`, `high`, `xhigh`, `max`. Codex: `low`, `medium`, `high`, `xhigh`, `max` (`max` needs a GPT-6 or GPT-5.6 model; silently demoted otherwise).
- `--permission-mode VALUE` — Claude Code: `default`, `auto`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`. Codex: `read_only`, `strict`, `auto`, `autonomous`, `auto_review`, `yolo`.
- `--thinking / --no-thinking` — Claude Code only.
- `--claude-in-chrome / --no-claude-in-chrome` — Claude Code only; lets the agent manipulate browser tabs, take screenshots, etc.
- `--fast-mode / --no-fast-mode` — supported Claude Code and Codex models; increases credit usage.
- `--context-max VALUE` — Claude Code: `200k` or `1m`. Codex: `272k` (fixed by the model; a divergent value is silently pinned to its window).
- `--question-widget / --no-question-widget` — Claude Code (`AskUserQuestion`) and Codex (`request_user_input`): whether the agent may ask questions through a UI widget the user answers by clicking, instead of plain text.
- `--unset TOKEN` (repeatable) — reset a field to NULL. Tokens: `model`, `effort`, `permission-mode`, `thinking`, `claude-in-chrome`, `fast-mode`, `context-max`, `question-widget`. A token the session's provider doesn't support is silently ignored.
- `--preset NAME` — apply a saved preset (replace mode). `__defaults__` resets all fields to the user-configured defaults. List presets with `$TWICC info presets` (skill: `twicc-info`).

A flag the session's provider doesn't support (e.g. `--thinking` on Codex) is silently ignored (no-op).

### Aliases

Provider-agnostic aliases, resolved to the session's provider:

- `--model` — `max`/`strongest` → top family, `medium`/`balanced` → middle family, `min`/`fastest`/`cheapest` → lightest family.
- `--effort`, `--context-max` — `max` → highest/largest, `min` → lowest/smallest.
- `--permission-mode` — `min`/`strict`/`safe` → most-locked (non-interactive), `max`/`open`/`full`/`yolo`/`bypass` → most permissive (non-interactive), `auto` → balanced (interactive).

### Untrusted projects

If the session's project is *untrusted* (or unknown trust), `--permission-mode` resolves against the restricted subset:

- `bypassPermissions`/`yolo` are unavailable.
- `max` → the most permissive *allowed* mode (Claude Code `acceptEdits`, Codex `auto_review`).
- An out-of-subset value is clamped to the project's untrusted default, with a note on stderr.

See `twicc info agent-settings` → `permission_mode_if_untrusted`.

## How settings reach a live process

If the session has a process attached (a running agent), changes propagate immediately, per field category:

- *Live* (`permission_mode` on Claude Code) — applied immediately.
- *Idle* (`model`, `context_max` on Claude Code; `model`, `effort`, `permission_mode`, `context_max`, `fast_mode` on Codex) — applied on next `user_turn`.
- *Startup* (`effort`, `thinking`, `claude_in_chrome`, `fast_mode`, `question_widget` on Claude Code) — applied on the next restart. The agent is stopped (immediately if at `user_turn`, or at the end of its current `assistant_turn` if working), so the next message you send restarts it with the new settings. If currently `awaiting_user_input`, the pending dialog is lost.
- *Startup on Codex* (`question_widget`) — **no automatic restart**. The value is stored; the running process keeps the old one. To apply it: `$TWICC session <ID> stop` (skill: `twicc-session`), then send a message — the resumed thread picks up the new value. A session with no live process needs nothing.

## Errors

- Local (exit 1): `unknown_unset_field`, `invalid_choice`, `invalid_format`, `unset_conflict`, `no_op`.
- Server (exit 3): the local codes above, re-checked server-side, plus `manager_busy` — transient; retry.

## Examples

```bash
$TWICC update-session 4a8352fb-... settings --model sonnet
$TWICC update-session 4a8352fb-... settings --effort high --unset model
$TWICC update-session 4a8352fb-... settings --preset 'deep think' --effort low
$TWICC update-session 4a8352fb-... settings --model opus
# → {"status":"updated","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## Related commands

- `$TWICC update-sessions settings` — the same update on several sessions. Skill: `twicc-update-sessions`.
- `$TWICC info [models|agent-settings|presets]` — the valid values and presets. Skill: `twicc-info`.
- `$TWICC session <ID> stop` — apply a Codex startup setting. Skill: `twicc-session`.
- `$TWICC send-message <ID>` — send a message (settings unchanged); restarts a stopped agent with the new settings. Skill: `twicc-send-message`.

## How to present results

1. On `no_op` / `unset_conflict`, the error message is self-explanatory.
2. Mention agent restart only when relevant (startup settings changed).
