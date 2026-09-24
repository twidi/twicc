---
name: twicc-update-workspace
description: Update an existing TwiCC workspace — rename, change color, add/remove projects, add/remove auto-add patterns, manage its saved browser URLs, archive/unarchive. Use when you or the user want to tweak a workspace.
argument-hint: <workspace_id> [--name X] [--color X|--unset-color] [--add-project project]... [--remove-project project]... [--add-pattern P]... [--remove-pattern P]... [--add-browser-url X [--browser-url-label L] [--set-default]] [--remove-browser-url X] [--set-default-browser-url X] [--browser-url X] [--unset-browser-url] [--archive|--unarchive]
---

# TwiCC Update Workspace

Patch an existing workspace. All flags are applied atomically — if any validation fails, nothing is written. The workspace id is **immutable**: `--name` renames the display name but keeps the slug.

## When to use

- You or the user want to rename, recolor, add/remove projects or patterns, or archive/unarchive a workspace.
- A script needs to update workspace membership programmatically.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC update-workspace '<WORKSPACE_ID>' [OPTIONS]
```

### Arguments

- `WORKSPACE_ID` — id of the workspace to update (the slug; does NOT change on rename).

### Options

All patch flags are optional but at least one is required. Reordering of projects/patterns is not supported.

- `--name NEW_NAME` — Trimmed; non-empty; ≤ 20 characters; unique (case-insensitive). The id stays unchanged.
- `--color VALUE` — CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`). Mutually exclusive with `--unset-color`.
- `--unset-color` — Clear the color. Mutually exclusive with `--color`.
- `--add-project PROJECT` (repeatable) — Add a project. Either a directory path or a project ID (**drop the leading dash** on ids — the CLI re-adds it). Idempotent. The project must exist in TwiCC.
- `--remove-project PROJECT` (repeatable) — Remove a project. Same path-or-id resolution. Idempotent; no error if the project isn't in the workspace or doesn't exist.
- `--add-pattern PATTERN` (repeatable) — Add an auto-add pattern (`*` wildcard). Idempotent.
- `--remove-pattern PATTERN` (repeatable) — Remove a pattern. Idempotent.
- `--add-browser-url URL` — Add a URL to the workspace's saved Browser-tab URLs (http(s) only; a project's own saved URLs take precedence; idempotent). The first saved URL becomes the default (Home target).
- `--browser-url-label LABEL` — Optional label for the added URL (shown in the Browser pane's menus). Only with `--add-browser-url` / `--browser-url`.
- `--set-default` — Flag the added URL as the default. Only with `--add-browser-url`.
- `--remove-browser-url URL` — Remove a saved URL (idempotent).
- `--set-default-browser-url URL` — Flag an already-saved URL as the default; fails if not saved.
- `--browser-url URL` — Shorthand for `--add-browser-url URL --set-default`.
- `--unset-browser-url` — Clear ALL the workspace's saved URLs. Mutually exclusive with the other browser-URL flags.
- `--archive` — Mark as archived. Mutually exclusive with `--unarchive`.
- `--unarchive` — Mark as not archived. Mutually exclusive with `--archive`.
- `--timeout SECONDS` — Seconds to wait for the server's response (default 30).

## Errors

### Local (exit 1)

- `conflicting_flags` — `--color` + `--unset-color`, `--archive` + `--unarchive`, `--browser-url` + `--add-browser-url`, or `--unset-browser-url` with another browser-URL flag.
- `no_op` — no patch flag passed.
- `workspace_not_found`
- `invalid_name` — name empty after trim, or exceeds 20 characters.
- `duplicate_name`
- `invalid_color`
- `invalid_pattern` — an `--add-pattern` value is empty after trim.
- `project_not_found` — an `--add-project` value doesn't resolve to an existing project.
- `invalid_value` — an empty or non-http(s) browser-URL flag.
- `url_not_found` — `--set-default-browser-url` on a URL that is not saved (server-side, exit 3).

### Server (exit 3)

Same codes, re-checked under the workspaces lock.

## Output format

```json
{"status":"updated","workspace_id":"backend","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"--name","code":"duplicate_name","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Update applied
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout

## Examples

```bash
$TWICC update-workspace backend --name 'Backend (legacy)'
$TWICC update-workspace backend --color '#ff9900'
$TWICC update-workspace backend --unset-color
$TWICC update-workspace backend --add-project /home/twidi/dev/api --add-project /home/twidi/dev/workers
$TWICC update-workspace backend --add-project .
$TWICC update-workspace backend --remove-project /home/twidi/dev/old-api --add-project /home/twidi/dev/new-api
$TWICC update-workspace scratch --add-pattern '/home/twidi/scratch/*' --archive
$TWICC update-workspace scratch --unarchive --color '#4a90d9'
$TWICC update-workspace backend --browser-url http://localhost:3000
$TWICC update-workspace backend --add-browser-url http://localhost:6006 --browser-url-label Storybook
$TWICC update-workspace backend --set-default-browser-url http://localhost:6006
$TWICC update-workspace backend --remove-browser-url http://localhost:6006
$TWICC update-workspace backend --unset-browser-url
$TWICC update-workspace backend --name 'BE'
# → {"status":"updated","workspace_id":"backend","request_uuid":"..."}
```

## Related commands

- `$TWICC create-workspace <NAME>` — create a new workspace. Skill: `twicc-create-workspace`.
- `$TWICC delete-workspace <ID>` — delete a workspace. Skill: `twicc-delete-workspace`.
- `$TWICC workspace <ID>` / `$TWICC workspaces` — inspect or list. Skill: `twicc-workspace` / `twicc-workspaces`.
- `$TWICC projects` — list projects to find ids (`--add-project` accepts paths directly). Skill: `twicc-projects`.

## How to present results

1. On success, give a clickable link: `[link text](/projects?workspace={workspace_id})`.
2. On `conflicting_flags` or `no_op`, the error message is self-explanatory.
3. On exit 3 (server rejected), retry once — it usually means a race with a parallel UI write. If it persists, fetch the current state with `$TWICC workspace <ID>` and reconcile.
