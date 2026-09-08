---
name: twicc-workspaces
description: List all TwiCC workspaces, or batch-look up specific workspace_ids. Use when you or the user want to browse workspaces, find a workspace ID, or batch-fetch metadata for known ids.
---

# TwiCC Workspaces

List all workspaces, or batch-look up specific ids with `workspaces get`.

## When to use

- You or the user want to list or browse workspaces.
- You need to find a workspace ID for use with other commands.
- You have a list of known workspace_ids and want to batch-fetch their metadata — use `workspaces get <ID>...` (one entry per id).

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### List

```bash
$TWICC workspaces [OPTIONS]
```

- `--limit N` — max results (default: 20).
- `--offset N` — skip first N for pagination (default: 0).
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`, so you know whether another page follows instead of guessing from the page size. Without it the output shape is unchanged.
- `--include-archived` — include archived workspaces (excluded by default).

### Batch lookup

```bash
$TWICC workspaces get <WORKSPACE_ID> [<WORKSPACE_ID>...]
```

Returns one entry per id in input order (duplicates collapsed). No filter flags — archived workspaces are returned like active ones.

## Output format

### Listing

```json
[
  {
    "id": "backend",
    "name": "Backend",
    "archived": false,
    "color": "#4a90d9",
    "projectIds": ["-home-twidi-dev-api", "-home-twidi-dev-workers"],
    "autoProjectPatterns": ["/home/twidi/dev/api*"]
  }
]
```

### Batch lookup (`get`)

Same shape per entry, plus a `known` boolean. When `known: false`, all other fields are `null`.

```json
[
  {"id": "backend", "name": "Backend", ..., "known": true},
  {"id": "typo-or-unknown", "name": null, ..., "known": false}
]
```

### Fields

- `id` — slug derived from the name at creation time; never changes on rename.
- `name` — display name (mutable).
- `color` — may be `null`.
- `projectIds` — project IDs (leading dash included). **Drop the leading dash** when passing on the command line; prefer directory paths when possible.
- `autoProjectPatterns` — auto-add patterns (`*` wildcard). A pattern without `*` is treated as a directory prefix.

## Examples

```bash
$TWICC workspaces
$TWICC workspaces --limit 50 --include-archived
$TWICC workspaces get backend
$TWICC workspaces get backend frontend devops
```

## Related commands

- `$TWICC workspace <ID>` — full details for one workspace (exit 1 if missing). Skill: `twicc-workspace`.
- `$TWICC create-workspace <NAME>` — create a workspace. Skill: `twicc-create-workspace`.
- `$TWICC update-workspace <ID>` — rename, recolor, add/remove projects or patterns. Skill: `twicc-update-workspace`.
- `$TWICC delete-workspace <ID>` — delete a workspace. Skill: `twicc-delete-workspace`.
- `$TWICC projects` — list projects (each has a `workspaces` field). Skill: `twicc-projects`.

## How to present results

1. Show workspace name and project count (length of `projectIds`).
2. If `autoProjectPatterns` is set, mention the workspace auto-grows for matching directories.
3. You are in TwiCC — link to a workspace: `[link text](/projects?workspace={workspace_id})`.
4. If `--paginated` reports `has_more: true`, offer to fetch the next page with `--offset`.
5. For `get` output: flag `known: false` entries as unknown (typo or never existed).
