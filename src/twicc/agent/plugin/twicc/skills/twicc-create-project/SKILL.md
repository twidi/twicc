---
name: twicc-create-project
description: Create a new TwiCC project from a directory path — optionally with a display name and a color. One project per directory. Use when you or the user want to register a directory as a TwiCC project before any session has run in it.
argument-hint: <directory> [--name X] [--color X] [--create-directory]
---

# TwiCC Create Project

Register a directory as a TwiCC project. One project per directory — creating a duplicate is rejected.

## When to use

- You or the user want to register a directory as a TwiCC project.
- A script preps several projects in batch.

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
$TWICC create-project '<DIRECTORY>' [OPTIONS]
```

### Arguments

- `DIRECTORY` — path of the project's working directory (absolute or resolvable). Symlinks are resolved to their canonical target.

### Options

- `--name VALUE` — Display name. Trimmed; ≤ 25 characters; globally unique (collision → `duplicate_name`). Defaults to the directory basename in the UI.
- `--color VALUE` — CSS hex color (`#rgb`, `#rrggbb`, or `#rrggbbaa`).
- `--create-directory` — Create the directory (and missing parents) if it doesn't exist. Without this flag, a missing directory is rejected.
- `--timeout SECONDS` — Seconds to wait for the server's response (default 30).

## Errors

### Local (exit 1)

- `invalid_directory` — path not absolute, or exists but is not a directory.
- `directory_not_found` — directory doesn't exist and `--create-directory` was not passed.
- `project_already_exists`
- `invalid_name` — name exceeds 25 characters after trim.
- `duplicate_name`
- `invalid_color`

### Server (exit 3)

Same codes plus `directory_creation_failed` — `--create-directory` set but `mkdir` failed (permissions, read-only fs, etc.).

## Output format

```json
{"status":"created","project_id":"-home-twidi-dev-newproj","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"DIRECTORY","code":"project_already_exists","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Project created
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout

## Examples

```bash
$TWICC create-project /home/twidi/dev/newproj
$TWICC create-project /home/twidi/dev/newproj --name 'New Project' --color '#4a90d9'
$TWICC create-project /home/twidi/dev/scratch --create-directory
$TWICC create-project /home/twidi/dev/newproj
# → {"status":"created","project_id":"-home-twidi-dev-newproj","request_uuid":"..."}
```

## Related commands

- `$TWICC update-project <PROJECT>` — rename, recolor, archive/unarchive. Skill: `twicc-update-project`.
- `$TWICC project <PROJECT>` / `$TWICC projects` — inspect or list. Skill: `twicc-project` / `twicc-projects`.
- `$TWICC update-workspace <ID> --add-project <PROJECT>` — add to a workspace (auto-add patterns may already handle this). Skill: `twicc-update-workspace`.

## How to present results

1. On success, give the project_id and a clickable link: `[link text](/project/{project_id})`.
2. On `project_already_exists`, surface the existing project id — updating or inspecting it is likely the right next step.
