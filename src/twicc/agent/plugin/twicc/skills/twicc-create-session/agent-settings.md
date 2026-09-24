# `create-session --model/--effort/...` — agent settings

Choose the model, effort, permission mode and the other per-session agent settings. MCP tool: `mcp__twicc__create_session`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC create-session [--preset NAME] [--model VALUE] [--effort VALUE] [--permission-mode VALUE] [--thinking|--no-thinking] [--claude-in-chrome|--no-claude-in-chrome] [--fast-mode|--no-fast-mode] [--context-max VALUE] '<PROMPT>'
```

All optional. Resolution of a field you omit (and the `--preset` does not set): the target project's `default_agent_settings` for the chosen provider (inherited through parent projects / git worktree main repo — see `twicc-project`), then the user's global default. The resolved values are frozen onto the session at creation, exactly like a session created from the UI. With a worktree flag they resolve against `--project`, the source (file: `worktree.md`).

`$TWICC info models agent-settings` gives the authoritative model lists, valid values, and per-value restrictions (skill: `twicc-info`). The lists below are indicative.

- `--model VALUE` — Claude Code: `fable`, `opus`, `sonnet`, `fable-5`, `opus-5`, `opus-4.8`, `opus-4.7`, `opus-4.6`, `opus-4.5`, `sonnet-4.6`, `sonnet-4.5`. Codex: `gpt-astra`, `gpt-sol`, `gpt-terra`, `gpt-luna`, `gpt`, `gpt-sol-5.6`, `gpt-luna-5.6`.
- `--effort VALUE` — Claude Code: `low`, `medium`, `high`, `xhigh`, `max`. Codex: `low`, `medium`, `high`, `xhigh`, `max` (`max` needs a GPT-6 or GPT-5.6 model; silently demoted otherwise).
- `--permission-mode VALUE` — Claude Code: `default`, `auto`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`. Codex: `read_only`, `strict`, `auto`, `autonomous`, `auto_review`, `yolo`.
- `--thinking / --no-thinking` — Claude Code only.
- `--claude-in-chrome / --no-claude-in-chrome` — Claude Code only (lets the agent manipulate browser tabs, take screenshots, etc.).
- `--fast-mode / --no-fast-mode` — supported Claude Code and Codex models; increases credit usage.
- `--context-max VALUE` — Claude Code: `200k` or `1m` (silently capped to 200k on unsupported models). Codex: `272k` (fixed by the model; a divergent value is silently pinned to the model's window).
- `--question-widget / --no-question-widget` — Claude Code and Codex. File: `session-behavior.md`.

A flag the chosen provider does not support (e.g. `--thinking` on Codex) is silently ignored (no-op), so one command works across a mix of providers.

### Aliases

Provider-agnostic values, resolved to each provider's concrete value — for cross-provider scripting without knowing the exact values:

- `--model` — `max`/`strongest` → top family, `medium`/`balanced` → middle family, `min`/`fastest`/`cheapest` → lightest family.
- `--effort`, `--context-max` — `max` → highest/largest, `min` → lowest/smallest.
- `--permission-mode` — `min`/`strict`/`safe` → most-locked (non-interactive), `max`/`open`/`full`/`yolo`/`bypass` → most permissive (non-interactive), `auto` → balanced (interactive).

### Untrusted projects

In a project whose trust is *untrusted* — or not yet decided (unknown counts as untrusted) — `permission_mode` is restricted to a safe subset: `bypassPermissions` (Claude Code) / `yolo` (Codex) are unavailable.

- `--permission-mode` resolves against that subset: `min`/`safe`/`max` still work, `max` → the most permissive *allowed* mode (Claude Code `acceptEdits`, Codex `auto_review`).
- An out-of-subset value (e.g. `bypass`) is clamped to the project's untrusted default, with a note on stderr.
- `--permission-mode` omitted: the session seeds from the project chain's `permission_mode_if_untrusted` default, then the global untrusted default.
- `twicc info agent-settings` → `permission_mode_if_untrusted` lists the subset + its aliases.
- Project trust is a human-only decision; agents never set it.

## Errors

- `invalid_choice` (exit 1) — value out of the provider's allowed set (a typo on a supported field; an unsupported flag is silently ignored instead).

## Examples

```bash
$TWICC create-session --provider claude_code --preset 'deep think' --effort low 'Quick review of last commit'
$TWICC create-session --model max --effort min --permission-mode safe 'Summarize the README'
```

## Related commands

- `$TWICC info models agent-settings` / `$TWICC info presets` — valid values and presets. Skill: `twicc-info`.
- `$TWICC update-session <session_id> settings` — change settings mid-session. Skill: `twicc-update-session`.
- `$TWICC project <project_id>` — the project's agent defaults. Skill: `twicc-project`.
