---
name: twicc-update-project
description: Update an existing TwiCC project — rename, change color, archive/unarchive, set its default provider, worktree directory or saved browser URLs, or edit per-provider agent-settings defaults. The directory is immutable; projects are archived, never deleted.
argument-hint: <project> [--name X|--unset-name] [--color X|--unset-color] [--archive|--unarchive] [--default-provider X|--unset-default-provider] [--worktree-directory X|--unset-worktree-directory] [--add-browser-url X [--browser-url-label L] [--set-default]] [--remove-browser-url X] [--set-default-browser-url X] [--default-browser-url X] [--unset-default-browser-url] | <project> settings --provider P [field flags]
---

# TwiCC Update Project

Patch an existing project. Two forms:

- **Flat patch** — `name`, `color`, `archived`, `default_provider`, `worktree_directory`, and the saved `browser_urls` are mutable; all flags applied atomically. The directory (and therefore the id) is immutable. There is no delete: use `--archive` to hide a project from default listings instead.
- **`settings` sub-command** — edit one provider's bundle inside the project's `default_agent_settings` (the defaults that seed NEW sessions created in this project; never affects existing sessions).

## When to use

- You or the user want to rename, recolor, or archive/unarchive a project.
- You or the user want new sessions in a project to default to a given provider, model, permission mode, etc.
- A script needs to flip archive flags based on activity.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage — flat patch

```bash
$TWICC update-project '<PROJECT>' [OPTIONS]
```

### Arguments

- `PROJECT` — directory path (absolute or relative) or project ID. **Drop the leading dash** on ids — the CLI re-adds it. Prefer paths.

### Options

All patch flags are optional but at least one is required. They cannot be combined with the `settings` sub-command.

- `--name NEW_NAME` — Display name. Trimmed; ≤ 25 characters; globally unique. Mutually exclusive with `--unset-name`.
- `--unset-name` — Clear the display name (UI falls back to directory basename). Mutually exclusive with `--name`.
- `--color VALUE` — CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). Mutually exclusive with `--unset-color`.
- `--unset-color` — Clear the color. Mutually exclusive with `--color`.
- `--archive` — Mark as archived. Mutually exclusive with `--unarchive`.
- `--unarchive` — Mark as not archived. Mutually exclusive with `--archive`.
- `--default-provider VALUE` — Provider a NEW session in this project defaults to (`claude_code`, `codex`). Inherited by sub-projects and git worktrees. Mutually exclusive with `--unset-default-provider`.
- `--unset-default-provider` — Back to inherit (parent chain, then the global default). Mutually exclusive with `--default-provider`.
- `--worktree-directory PATH` — Absolute base directory under which new git worktrees of this project are created from the UI (free-form, not required to live under the git root). Mutually exclusive with `--unset-worktree-directory`.
- `--unset-worktree-directory` — Back to the global default (composed against the git root). Mutually exclusive with `--worktree-directory`.
- `--add-browser-url URL` — Add a URL to the project's saved Browser-tab URLs (http(s) only, e.g. the dev server; idempotent). The first saved URL becomes the default (Home target).
- `--browser-url-label LABEL` — Optional label for the added URL (shown in the Browser pane's menus). Only with `--add-browser-url` / `--default-browser-url`.
- `--set-default` — Flag the added URL as the default. Only with `--add-browser-url`.
- `--remove-browser-url URL` — Remove a saved URL (idempotent).
- `--set-default-browser-url URL` — Flag an already-saved URL as the default; fails if not saved.
- `--default-browser-url URL` — Shorthand for `--add-browser-url URL --set-default`. When the project saves no URL at all, the Browser tab inherits (parent chain, then a containing workspace's saved URLs).
- `--unset-default-browser-url` — Clear ALL saved URLs (back to inherit). Mutually exclusive with the other browser-URL flags.
- `--timeout SECONDS` — Seconds to wait for the server's response (default 30).

## Usage — agent settings defaults

```bash
$TWICC update-project '<PROJECT>' settings --provider <PROVIDER> [OPTIONS]
```

Edits one provider's bundle in `default_agent_settings`. Patch semantics: only the fields you touch change; other fields — and the other providers' bundles — are untouched. A field absent from the bundle inherits from the parent chain (worktree main repo / path ancestors), then the global default.

### Options

- `--provider VALUE` — **Required.** Provider whose bundle to edit (`claude_code`, `codex`). A disabled provider is accepted (a project may keep defaults for it).
- `--model VALUE` — Default model (provider-specific value, e.g. `opus`, `gpt`; aliases `max`/`min` resolve per provider).
- `--effort VALUE` — Default reasoning effort (aliases `min`/`max`).
- `--permission-mode VALUE` — Default permission mode used when the project is **trusted** (full range, `bypassPermissions`/`yolo` allowed; aliases `min`/`safe`/`max`/`open`...).
- `--permission-mode-if-untrusted VALUE` — Default permission mode used when the project resolves **untrusted** (or unknown trust). Restricted to the provider's untrusted-allowed modes; aliases `min`/`safe` (most locked), `max` (most permissive untrusted-allowed).
- `--thinking/--no-thinking` — Claude Code only.
- `--claude-in-chrome/--no-claude-in-chrome` — Claude Code only.
- `--fast-mode/--no-fast-mode` — Supported Claude Code and Codex models; increases credit usage.
- `--context-max VALUE` — Default context window (`200k`, `1m`, or aliases `min`/`max`).
- `--unset FIELD` — Remove a field from the bundle (back to inherit). Repeatable. Tokens: `model`, `effort`, `permission-mode`, `permission-mode-if-untrusted`, `thinking`, `claude-in-chrome`, `fast-mode`, `context-max`.
- `--timeout SECONDS` — default 30.

Fields the provider doesn't support are silently ignored (`{"status":"noop"}` if nothing remains). There is no `--preset`; to clear a whole bundle, `--unset` every field.

## Errors

### Local (exit 1)

- `conflicting_flags` — mutually exclusive flags passed together.
- `no_op` — no patch flag passed.
- `project_not_found` — PROJECT doesn't resolve to an existing project.
- `invalid_name` — name exceeds 25 characters after trim.
- `duplicate_name`
- `invalid_color`
- `invalid_provider` — unknown `--default-provider` value.
- `invalid_value` — empty `--worktree-directory`, or an empty/non-http(s) browser-URL flag.
- `url_not_found` — `--set-default-browser-url` on a URL that is not saved (server-side, exit 3).
- `unknown_provider` / `invalid_choice` / `unknown_unset_field` / `unset_conflict` / `invalid_format` — settings sub-command validation.

### Server (exit 3)

Same codes, re-checked under the DB write lock.

## Output format

```json
{"status":"updated","project_id":"-home-twidi-dev-newproj","request_uuid":"..."}
{"status":"noop","project_id":"-home-twidi-dev-newproj"}
{"status":"validation_error","errors":[{"field":"--name","code":"duplicate_name","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Update applied (or silent no-op)
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — A flat flag combined with the `settings` sub-command (e.g. `update-project --archive <PROJECT> settings`)

## Examples

```bash
$TWICC update-project . --name 'My Project'
$TWICC update-project /home/twidi/dev/myproj --name 'My Project'
$TWICC update-project home-twidi-dev-myproj --name 'My Project'  # by id, dash dropped
$TWICC update-project /home/twidi/dev/myproj --unset-name
$TWICC update-project /home/twidi/dev/myproj --color '#ff9900'
$TWICC update-project /home/twidi/dev/legacy --archive
$TWICC update-project /home/twidi/dev/legacy --unarchive --color '#4a90d9'
$TWICC update-project . --default-provider codex
$TWICC update-project . --unset-default-provider
$TWICC update-project . --worktree-directory /home/me/dev/myproj/.worktrees
$TWICC update-project . --unset-worktree-directory
$TWICC update-project . --default-browser-url http://localhost:3000
$TWICC update-project . --add-browser-url http://localhost:6006 --browser-url-label Storybook
$TWICC update-project . --set-default-browser-url http://localhost:6006
$TWICC update-project . --remove-browser-url http://localhost:6006
$TWICC update-project . --unset-default-browser-url

# Agent settings defaults (seed NEW sessions only)
$TWICC update-project . settings --provider claude_code --model opus --effort high
$TWICC update-project . settings --provider claude_code --permission-mode max --permission-mode-if-untrusted safe
$TWICC update-project . settings --provider codex --context-max max
$TWICC update-project . settings --provider claude_code --unset model --unset effort
```

## Related commands

- `$TWICC create-project <DIRECTORY>` — create a new project. Skill: `twicc-create-project`.
- `$TWICC project <PROJECT>` / `$TWICC projects` — inspect or list (shows `default_provider` / `default_agent_settings`). Skill: `twicc-project` / `twicc-projects`.
- `$TWICC create-session --project <PROJECT>` — new sessions inherit these defaults. Skill: `twicc-create-session`.
- `$TWICC update-workspace <ID> --add-project <PROJECT>` / `--remove-project <PROJECT>` — add/remove from a workspace. Skill: `twicc-update-workspace`.

## How to present results

1. On success, give a clickable link: `[link text](/project/{project_id})`.
2. On `conflicting_flags` or `no_op`, the error is self-explanatory.
3. On `project_not_found`, suggest `$TWICC projects` to find the right id.
4. After editing settings defaults, remind that they apply to NEW sessions only.
