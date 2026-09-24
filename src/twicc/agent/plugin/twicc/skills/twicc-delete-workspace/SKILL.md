---
name: twicc-delete-workspace
description: Delete a TwiCC workspace by id. Projects are NOT deleted — only the grouping disappears. Use when you or the user want to drop a workspace.
argument-hint: <workspace_id>
---

# TwiCC Delete Workspace

Delete a workspace. **Projects are not affected** — they remain in TwiCC and in any other workspace that lists them. Only the grouping disappears. The operation is immediate and has no undo (the workspace can be recreated with `$TWICC create-workspace`).

## When to use

- You or the user want to delete a workspace.
- A script needs to clean up stale workspaces.

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
$TWICC delete-workspace '<WORKSPACE_ID>'
```

### Arguments

- `WORKSPACE_ID` — id of the workspace (the slug — does NOT match the display name verbatim).

### Options

- `--timeout SECONDS` — Seconds to wait for the server's response (default 30).

## Errors

### Local (exit 1)

- `workspace_not_found`

### Server (exit 3)

- `workspace_not_found` — race: workspace deleted between local check and server write.

## Output format

```json
{"status":"deleted","workspace_id":"backend","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"WORKSPACE_ID","code":"workspace_not_found","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Workspace deleted
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout

## Examples

```bash
$TWICC delete-workspace scratch
# → {"status":"deleted","workspace_id":"scratch","request_uuid":"..."}
```

## Related commands

- `$TWICC workspace <ID>` / `$TWICC workspaces` — inspect or list. Skill: `twicc-workspace` / `twicc-workspaces`.
- `$TWICC create-workspace <NAME>` — recreate a workspace. Skill: `twicc-create-workspace`.
- `$TWICC update-workspace <ID> --archive` — archive instead of deleting (reversible). Skill: `twicc-update-workspace`.

## How to present results

1. On success, confirm the deletion and remind the user their projects are unaffected.
2. On `workspace_not_found`, the user likely used the display name instead of the slug — suggest `$TWICC workspaces` to find the right id.
