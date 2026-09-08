---
name: twicc-projects
description: List all projects tracked by TwiCC, or batch-look up specific ones by directory path or id. Use when you or the user want to browse projects, find a project ID, or batch-fetch metadata for known ones.
---

# TwiCC Projects

List all projects, or batch-look up specific ones with `projects get`.

## When to use

- You or the user want to list or browse projects.
- You need to find a project ID for use with other commands.
- You have a list of known projects (paths or ids) and want to batch-fetch their metadata

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
$TWICC projects [OPTIONS]
```

- `--limit N` — max results (default: 20; 50 with `--paginated`).
- `--offset N` — skip first N for pagination (default: 0).
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`, so you know whether another page follows instead of guessing from the page size. Without an explicit `--limit` the page size becomes **50**. **Before 2026-09-15 the flag is opt-in and a call without it is unchanged; from that date the envelope is the only shape and the flag is an accepted no-op.** Passing it works on both sides.
- `--include-archived` — include archived projects (excluded by default).
- `--workspace ID` — only projects belonging to this workspace.

### Batch lookup

```bash
$TWICC projects get <PROJECT> [<PROJECT>...]
```

Each `PROJECT` is a directory path or a project ID (**drop the leading dash** on ids — the CLI re-adds it). Returns one entry per value in input order (duplicates collapsed). No filter flags — archived projects are returned like active ones.

## Output format

### Listing

```json
[
  {
    "id": "-home-twidi-dev-myproject-abc123",
    "directory": "/home/twidi/dev/myproject",
    "git_root": "/home/twidi/dev/myproject",
    "sessions_count": 42,
    "mtime": 1741654800.0,
    "stale": false,
    "name": "My Project",
    "color": "#4a90d9",
    "archived": false,
    "total_cost": 12.345678,
    "worktree_of": null,
    "worktree_directory": null,
    "trust": null,
    "trust_propagation": false,
    "default_provider": null,
    "default_agent_settings": {"claude_code": {"permission_mode": "acceptEdits", "effort": "high"}},
    "workspaces": ["backend", "home-side-projects"],
    "worktrees": ["-home-twidi-dev-myproject--worktrees-feature-x"]
  }
]
```

### Batch lookup (`get`)

Same shape per entry, plus a `known` boolean. When `known: false`, all other fields are `null`.

```json
[
  {"id": "-home-twidi-dev-myproject", "name": "My Project", ..., "known": true},
  {"id": "-typo-or-unknown", "name": null, ..., "known": false}
]
```

### Fields

- `id` — derived from the directory path (non-alphanumeric chars replaced by dashes). **Drop the leading dash** when passing on the command line.
- `stale` — `true` if the project folder no longer exists on disk.
- `name` — may be `null`.
- `color` — may be `null`.
- `total_cost` — total cost in USD across all sessions (may be `null`).
- `worktree_of` — when this project is a git worktree, the project id of its main repository; `null` otherwise.
- `worktree_directory` — absolute base directory under which new git worktrees of this project are created from the UI; `null` = use the global default (composed against the git root). Editable via `$TWICC update-project <PROJECT> --worktree-directory PATH`.
- `worktrees` — the project ids of this project's own git worktrees (the reverse of `worktree_of`); empty when it has none. A worktree's sessions, cost and activity count toward its main repository.
- `trust` — the project's own trust decision: `true` = trusted, `false` = untrusted, `null` = no own decision (inherits from its parent directory / git root). **Read-only here, and human-only to change** — agents never set trust. A session created in an untrusted (or `null` / undecided) project runs under a restricted permission set (`bypassPermissions` / `yolo` unavailable).
- `trust_propagation` — whether an explicit `trust` decision also covers the project's sub-paths and its git worktrees.
- `default_provider` — provider a NEW session in this project defaults to; `null` = inherit from the parent chain (worktree main repo / path ancestors), ultimately the global default. Editable via `$TWICC update-project <PROJECT> --default-provider X`.
- `default_agent_settings` — per-provider agent-settings defaults that seed NEW sessions created here (`{"<provider>": {"<field>": value}}` or `null`); a missing field inherits from the parent chain, then the global default. Includes the optional `permission_mode_if_untrusted` key (the permission mode seeded instead of `permission_mode` when the project resolves untrusted). Creation-time only: changing these never affects existing sessions. Editable via `$TWICC update-project <PROJECT> settings`.
- `workspaces` — workspace IDs this project belongs to (empty if none).

## Examples

```bash
$TWICC projects
$TWICC projects --limit 50 --include-archived
$TWICC projects --workspace backend
$TWICC projects get .
$TWICC projects get /home/twidi/dev/myproj
$TWICC projects get /home/twidi/dev/a /home/twidi/dev/b
$TWICC projects get home-twidi-dev-myproj  # by id, dash dropped
```

## Related commands

- `$TWICC project <PROJECT>` — full details for one project (exit 1 if missing). Skill: `twicc-project`.
- `$TWICC create-project <DIRECTORY>` — create a new project. Skill: `twicc-create-project`.
- `$TWICC update-project <PROJECT>` — rename, recolor, archive/unarchive. Skill: `twicc-update-project`.
- `$TWICC sessions --project <PROJECT>` — list sessions for a project. Skill: `twicc-sessions`.
- `$TWICC workspace <ID>` — inspect a workspace from the `workspaces` field. Skill: `twicc-workspace`.
- `$TWICC search "<query>"` — search across sessions (filter by project with `project_id:<id>`). Skill: `twicc-search`.

## How to present results

1. Show project name (or directory if no name) and session count.
2. If `--paginated` reports `has_more: true`, offer to fetch the next page with `--offset`.
3. You are in TwiCC — link to a project: `[link text](/project/{project_id})`.
4. Only include cost information if explicitly asked.
5. For `get` output: flag `known: false` entries as unknown (typo or never existed).
