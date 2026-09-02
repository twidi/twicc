---
name: twicc-update-session
description: Update an existing TwiCC session — change settings, title, annotations, archive/unarchive, pin/unpin, hide/unhide, or mute/notify. Use when you or the user want to change a session without sending a message.
argument-hint: <session_id|self> {settings|title|annotations|archive|unarchive|pin|unpin|hide|unhide|mute|notify} [ARGS / OPTIONS]
---

# TwiCC Update Session

Eleven sub-commands: `settings`, `title`, `annotations`, `archive`, `unarchive`, `pin <MODE>`, `unpin`, `hide`, `unhide`, `mute`, `notify`. To stop the live agent without touching the row, use `$TWICC process <SESSION_ID> stop` (skill: `twicc-process`). To apply the same change to several sessions at once (every sub-command except `title`), use `$TWICC update-sessions` (skill: `twicc-update-sessions`).

All sub-commands share the `--timeout SECONDS` output flag (default 30).

## When to use

- Change model, effort, permission mode, or other settings → `settings`.
- Rename a session → `title`.
- Edit free-form session annotations → `annotations`.
- Archive (also stops the agent) / unarchive → `archive` / `unarchive`.
- Pin to project / workspace / globally, or unpin → `pin <MODE>` / `unpin`.
- Hide from all listings and broadcasts / unhide → `hide` / `unhide`.
- Suppress or restore finished-working notifications → `mute` / `notify`.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### Session id argument

Every sub-command takes a session id as its first positional argument:

- `SESSION_ID` — id of the session to update.
- `self` — the current TwiCC session.

### `settings`

```bash
$TWICC update-session '<SESSION_ID>' settings [OPTIONS]
```

**Patch mode** (no `--preset`): only explicitly passed flags are written; everything else keeps its current value. Pass `--unset <field>` to reset a field to NULL (= user defined default). At least one flag or `--unset` is required.

**Replace mode** (`--preset NAME`): every settings field is rewritten. The preset defines some fields; absent fields become NULL. Per-flag options and `--unset` override the preset.

Agent settings flags (all optional; use `$TWICC info models agent-settings presets` to discover the supported models, valid agent settings values per provider and the presets, — skill: `twicc-info`):

- `--model VALUE` — Claude Code: `fable`, `opus`, `sonnet`, `fable-5`, `opus-4.8`, `opus-4.7`, `opus-4.6`, `opus-4.5`, `sonnet-4.6`, `sonnet-4.5`. Codex: `gpt-sol`, `gpt-terra`, `gpt-luna`, `gpt`.
- `--effort VALUE` — Claude Code: `low`, `medium`, `high`, `xhigh`, `max`. Codex: `low`, `medium`, `high`, `xhigh`, `max` (`max` needs a GPT-5.6 model; silently demoted otherwise).
- `--permission-mode VALUE` — Claude Code: `default`, `auto`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`. Codex: `read_only`, `strict`, `auto`, `autonomous`, `auto_review`, `yolo`.
- `--thinking / --no-thinking` — Claude Code only.
- `--claude-in-chrome / --no-claude-in-chrome` — Claude Code only (Allows to manipulate browser tabs, take screenshots, etc.).
- `--fast-mode / --no-fast-mode` — Supported Claude Code and Codex models; increases credit usage.
- `--context-max VALUE` — Claude Code: `200k` or `1m`. Codex: `272k` (fixed by the model; a divergent value is silently pinned to its window).
- `--question-widget / --no-question-widget` — Claude Code (`AskUserQuestion`) and Codex (`request_user_input`): decide whether the agent may ask questions through a UI widget the user answers by clicking, instead of plain text.
- `--unset TOKEN` (repeatable) — reset a field to NULL. Accepted tokens: `model`, `effort`, `permission-mode`, `thinking`, `claude-in-chrome`, `fast-mode`, `context-max`, `question-widget` (a token the session's provider doesn't support is silently ignored).
- `--preset NAME` — apply a saved preset (replace mode). `__defaults__` resets all fields to the user-configured defaults. Use `$TWICC info presets` to list available presets (skill: `twicc-info`).

### Aliases

Some settings also accept provider-agnostic aliases, resolved to the session's provider:
- `--model` — `max`/`strongest` → top family, `medium`/`balanced` → middle family, `min`/`fastest`/`cheapest` → lightest family.
- `--effort`, `--context-max` — `max` → highest/largest, `min` → lowest/smallest.
- `--permission-mode` — `min`/`strict`/`safe` → most-locked (non-interactive), `max`/`open`/`full`/`yolo`/`bypass` → most permissive (non-interactive), `auto` → balanced (interactive).

A flag the session's provider doesn't support (e.g. `--thinking` on Codex) is silently ignored (no-op).

**Untrusted projects.** If the session's project is *untrusted* (or unknown trust), `--permission-mode` resolves against the restricted subset — `bypassPermissions`/`yolo` are unavailable, `max` → the most permissive *allowed* mode (Claude Code `acceptEdits`, Codex `auto_review`), and an out-of-subset value is clamped to the project's untrusted default with a note on stderr. See `twicc info agent-settings` → `permission_mode_if_untrusted`.

**How settings reach a live process:** if a session currently has a process attached (a running agent), changes are propagated immediately per field category:
- *Live* (`permission_mode` on Claude Code) — applied immediately.
- *Idle* (`model`, `context_max` on Claude Code; `model`, `effort`, `permission_mode`, `context_max`, `fast_mode` on Codex) — applied on next `user_turn`.
- *Startup* (`effort`, `thinking`, `claude_in_chrome`, `fast_mode`, `question_widget` on Claude Code) — applied on the next restart: the agent is stopped (immediately if at `user_turn`, or at the end of its current `assistant_turn` if working), so the next message you send restarts it with the new settings. If currently `awaiting_user_input`, the pending dialog is lost.
- *Startup on Codex* (`question_widget`) — **no automatic restart**. The value is stored, the running process keeps the old one. To apply it: `$TWICC process <ID> stop` (skill: `twicc-process`), then send a message — the resumed thread picks up the new value. A session with no live process needs nothing.

### `title`

```bash
$TWICC update-session '<SESSION_ID>' title '<NEW_TITLE>'
```

Trimmed; non-empty; ≤ 200 characters. After the DB write, the server persists the title to the provider's backing store (non-critical; DB is already updated regardless).

### `annotations`

```bash
$TWICC update-session '<SESSION_ID>' annotations <OPERATION>...
```

Operations are applied left-to-right:

- `clear` — replace annotations with `{}`.
- `replace-file:PATH` — replace annotations with a JSON object file.
- `merge-file:PATH` — recursively merge a JSON object file.
- `set:KEY=VALUE` — set a scalar value; dotted keys are supported.
- `unset:KEY` — remove a key; dotted keys are supported and missing paths are ignored.

`set:` parses `true`, `false`, `null`, numbers, and strings. Use `merge-file:` or `replace-file:` for list or object values.

### `archive` / `unarchive`

```bash
$TWICC update-session '<SESSION_ID>' archive
$TWICC update-session '<SESSION_ID>' unarchive
```

`archive` kills the live agent, and auto-unpins if `autoUnpinOnArchive` is enabled (default: on). `unarchive` flips the flag; no agent restart.

### `pin` / `unpin`

```bash
$TWICC update-session '<SESSION_ID>' pin <MODE>
$TWICC update-session '<SESSION_ID>' unpin
```

`MODE` — `project`, `workspace`, or `all`. Switching scope is just another `pin`. Idempotent.

### `hide` / `unhide`

```bash
$TWICC update-session '<SESSION_ID>' hide
$TWICC update-session '<SESSION_ID>' unhide
```

`hide` requires a non-interactive `permission_mode` (Claude Code: `bypassPermissions`/`dontAsk`; Codex: `yolo`/`strict`; alias `open` or `strict`) and `question_widget` disabled (both providers). If not met, update `settings` first. `unhide` has no preconditions.

### `mute` / `notify`

```bash
$TWICC update-session '<SESSION_ID>' mute
$TWICC update-session '<SESSION_ID>' notify
```

`mute` suppresses only the session's finished-working notifications. Questions and approvals still notify. `notify` restores the per-session finished-working notification path, but global notification settings still apply. Both commands are independent of hidden visibility and do not restart the agent.

## Errors

### Local (exit 1) — common to all sub-commands

- `is_subagent` — subagents cannot be updated directly; target the parent session.
- `session_not_found`
- `session_stale`
- `project_no_directory`
- `provider_disabled`

### Local (exit 1) — sub-command specific

- `settings`: `unknown_unset_field`, `invalid_choice`, `invalid_format`, `unset_conflict`, `no_op`.
- `title`: `invalid_title` — empty after trim.
- `annotations`: `invalid_annotation_operation`, `invalid_annotation`, `invalid_annotation_path`, `annotation_path_conflict`, `annotation_non_scalar`, `invalid_annotations_file`, `no_op`.
- `pin`: `invalid_pin_mode` — MODE not in `{project, workspace, all}`.
- `hide`: `hidden_constraint_violation` — non-interactive permission_mode or question_widget constraint not met.

### Server (exit 3)

Same codes, re-checked server-side. Additionally `invalid_title` (title too long), `invalid_annotations`, and `manager_busy` (transient, `settings` only; retry).

## Output format

```json
{"status":"updated","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"noop","session_id":"..."}
{"status":"validation_error","errors":[{"field":"--unset model","code":"unset_conflict","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

`noop` means every field you touched is a no-op for this session's provider (e.g. `--thinking` on Codex): nothing was written, and it exits `0`.

### Exit codes

- `0` — Update applied (or a no-op for this provider)
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC update-session 4a8352fb-... settings --model sonnet
$TWICC update-session 4a8352fb-... settings --effort high --unset model
$TWICC update-session 4a8352fb-... settings --preset 'deep think' --effort low
$TWICC update-session 4a8352fb-... settings --model opus
# → {"status":"updated","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
$TWICC update-session 4a8352fb-... title 'Better title'
$TWICC update-session 4a8352fb-... annotations set:role=reviewer unset:temporary
$TWICC update-session 4a8352fb-... annotations set:note="Needs backend review"
$TWICC update-session 4a8352fb-... annotations unset:foo set:foo.point=bar
$TWICC update-session 4a8352fb-... annotations replace-file:/tmp/base.json merge-file:/tmp/extra.json
$TWICC update-session 4a8352fb-... annotations clear
$TWICC update-session 4a8352fb-... archive
$TWICC update-session 4a8352fb-... unarchive
$TWICC update-session 4a8352fb-... pin project
$TWICC update-session 4a8352fb-... pin all
$TWICC update-session 4a8352fb-... unpin
$TWICC update-session 4a8352fb-... hide
$TWICC update-session 4a8352fb-... unhide
$TWICC update-session 4a8352fb-... mute
$TWICC update-session 4a8352fb-... notify
$TWICC update-session self annotations set:role=worker
```

## Related commands

- `$TWICC update-sessions <op> [SESSION_ID...]` — apply the same update, including `mute` or `notify`, to several sessions at once (no `title`). Skill: `twicc-update-sessions`.
- `$TWICC info [models|agent-settings|presets]` — discover providers, models, agent-settings values and presets before editing a session. Skill: `twicc-info`.
- `$TWICC process <session_id> stop` — stop the agent without touching the row. Skill: `twicc-process`.
- `$TWICC send-message <session_id>` — send a message (settings unchanged). Skill: `twicc-send-message`.
- `$TWICC session <session_id>` — full metadata. Skill: `twicc-session`.

## How to present results

1. On success, give a clickable link: `[link text](/project/{project_id}/session/{session_id})`.
2. On `no_op` / `unset_conflict`, the error message is self-explanatory.
3. Mention agent restart only when relevant (startup settings changed).
