---
name: twicc-sessions
description: List sessions tracked by TwiCC, or batch-look up specific session_ids. Use when you or the user want to browse sessions, find a session ID, filter by project, or batch-fetch metadata for known ids.
---

# TwiCC Sessions

List sessions, or batch-look up specific ones with `sessions get`. Only returns valid sessions (with a creation date and at least one user message), unless you use `get` (which returns anything you name explicitly).

## When to use

- You or the user want to list or browse sessions.
- You need to find a session ID.
- You have a list of known session_ids and want to batch-fetch their metadata — use `sessions get <ID>...` (returns subagents, archived, and hidden too since you named them explicitly).

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
$TWICC sessions [OPTIONS]
```

Results are ordered by most recently active.

- `--project <PROJECT>` — filter by project (path or id; **drop the leading dash** on ids). A normal project also returns its git worktrees' sessions (a worktree's sessions belong to its main repository); a worktree project returns only its own. Non-existent project returns empty, no error.
- `--workspace ID` — filter to sessions of projects in the given workspace, including each member project's git worktrees. Mutually exclusive with `--project`.
- `--limit N` — max results (default: 20; 50 with `--paginated`).
- `--offset N` — skip first N for pagination (default: 0).
- `--slim` — reduced projection: identity, state, cost, and the `has_*` flags (`has_tasks`, `has_goals`, `has_plan`, `has_artifacts`, `has_workflows`) saying what else there is to fetch. Drops the per-session payloads (`tasks`, `plan_paths`, `goals`, `layout`), the redundant timestamps and paths, and the agent-settings bundle. **About 80% lighter** — prefer it whenever you are scanning rather than inspecting one session. `sessions get` takes it too, placeholders included, so a batch lookup keeps one shape.
- **Live process state:** every row carries a `process` block, on by default (`--no-processes` drops the key). `{state}` under `--slim`, plus `id`, `started_at`, `last_state_change_at` and `pid` otherwise. `state` is one of `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead` — note `awaiting_user_input`, the non-obvious stop: the agent is blocked on a human, not working. It answers **what TwiCC is running**, so `dead` means no TwiCC-managed process, which is also the answer for the many sessions TwiCC indexed but never started (a `claude` run straight from a terminal keeps generating and still reads `dead`). `dead` also covers a TwiCC that is not running at all: an agent does not outlive its backend, so "TwiCC is down" and "TwiCC runs nothing for this session" are the same fact. The block is `null` for one case only — a subagent, which runs inside its parent's process and never has one of its own; `parent_session_id` identifies those.
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`, so you know whether another page follows instead of guessing from the page size. Without an explicit `--limit` the page size becomes **50**. **Before 2026-09-15 the flag is opt-in and a call without it is unchanged; from that date the envelope is the only shape and the flag is an accepted no-op.** Passing it works on both sides.
- `--include-archived` — include archived sessions (excluded by default).
- `--include-hidden` — include hidden sessions (excluded by default).
- `--only-hidden` — only hidden sessions. Mutually exclusive with `--include-hidden`.
- `--spawned-by <ID|self|parent>` — filter to direct child sessions spawned by the given session ID. `self` is the current session (= my children); `parent` is the session that spawned the current one (= my siblings, myself included). Implies `--include-hidden`. Mutually exclusive with `--spawn-tree` and `--descendants`.
- `--spawn-tree <ID|self>` — filter to every session in the spawn tree that contains the given session ID. Any id in the tree works — root, middle, or leaf: the CLI looks it up and resolves to the tree it belongs to. A standalone session (no children, never spawned) queried by its own id is returned as a single-node tree. `self` resolves to the tree that contains the current session. Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--descendants`.
- `--descendants <ID|self|parent>` — filter to the proper descendants of the given session (every session transitively spawned by it, target excluded). `self` is the current session; `parent` is the current session's spawner (= my siblings, their subtrees, and my own subtree). Implies `--include-hidden`. Mutually exclusive with `--spawned-by` and `--spawn-tree`. Use this when you want "everything under X" but not X itself — `--spawn-tree` gives the whole tree containing X (root included), `--spawned-by` only direct children.
- `--siblings <ID|self>` — filter to the siblings of the given session: the *other* sessions spawned by the same parent, **target always excluded**. `self` is the current session's peers. `parent` is **not** supported (siblings are relative to a node's own parent). Implies `--include-hidden`. Mutually exclusive with `--spawned-by`, `--spawn-tree` and `--descendants`. This is the direct way to list your peers; `--spawned-by parent` selects the same set but includes yourself.
- `--annotation KEY[OP]VALUE` — filter to sessions whose `annotations` object matches the expression. Repeatable; multiple flags are AND-combined. Does **not** imply `--include-hidden` (orthogonal). Five operators:
  - `KEY=VALUE` — annotation key equals VALUE.
  - `KEY!=VALUE` — annotation key differs from VALUE (or key absent).
  - `KEY:exists` — annotation key is present (any value).
  - `KEY:not-exists` — annotation key is absent.
  - `KEY:in:V1,V2,...` — annotation key equals one of the listed values; escape a literal comma with `\,`.
  - Values are inferred as typed: `true`/`false` → boolean, `null` → null, integers and floats parsed numerically, everything else → string (same rules as `create-session --annotation`).
  - Example: `--spawn-tree self --annotation role=implementer`

### Batch lookup

```bash
$TWICC sessions get <SESSION_ID> [<SESSION_ID>...]
```

Returns one entry per id in input order (duplicates collapsed). No filter flags — all session types returned.

## Output format

### Listing

```json
[
  {
    "id": "abc123-def456",
    "project_id": "-home-twidi-dev-myproject",
    "provider": "claude_code",
    "parent_session_id": null,
    "last_line": 150,
    "mtime": 1741654800.0,
    "created_at": "2025-03-10T14:30:00+00:00",
    "last_started_at": "2025-03-10T14:30:00+00:00",
    "last_updated_at": "2025-03-10T15:45:00+00:00",
    "last_stopped_at": "2025-03-10T15:50:00+00:00",
    "last_new_content_at": "2025-03-10T15:45:00+00:00",
    "last_viewed_at": "2025-03-10T16:00:00+00:00",
    "stale": false,
    "title": "Implement user authentication",
    "slug": null,
    "user_message_count": 12,
    "compute_version_up_to_date": true,
    "context_usage": 85000,
    "self_cost": 1.234,
    "subagents_cost": 0.567,
    "total_cost": 1.801,
    "cwd": "/home/twidi/dev/myproject",
    "git_branch": "feature/auth",
    "git_directory": "/home/twidi/dev/myproject",
    "model": {"raw": "claude-opus-4-20250514", "family": "opus", "version": "4"},
    "archived": false,
    "pinned": null,
    "permission_mode": "default",
    "selected_model": null,
    "effort": null,
    "thinking_enabled": null,
    "claude_in_chrome": false,
    "fast_mode": false,
    "context_max": 200000,
    "compacted": false,
    "hidden": false,
    "spawned_by": null,
    "spawn_root": null,
    "annotations": {"role": "reviewer"}
  }
]
```

### Key fields

- `provider` — `"claude_code"` or `"codex"`. Determines item schema and supported settings.
- `slug` — provider short id (e.g. Codex subagent nickname), or `null`.
- `parent_session_id` — `null` for regular sessions, set for subagents.
- `model` — `{"raw": "...", "family": "...", "version": "..."}`.
- `context_max` / `context_usage` — max context window and current usage in tokens.
- `compacted` — whether the session has been compacted at least once.
- `last_new_content_at` — most recent item appended.
- `last_viewed_at` — when the user last opened the session in TwiCC.
- `hidden` — whether the session is hidden from all listings and broadcasts.
- `spawned_by` — session ID that spawned this session, or `null`.
- `spawn_root` — root session ID for the spawned-session tree, or `null` before a session joins one.
- `annotations` — free-form JSON object attached at session creation.

### Batch lookup (`get`)

Same shape per entry, plus `known` boolean. When `known: false`, the session fields are `null` — but `process` is not: a `ProcessRun` row exists before the watcher writes the `Session` row, so a live block on an unknown id is a session that just started.

```json
[
  {"id": "abc123-def456", "title": "Implement user authentication", ..., "known": true},
  {"id": "typo-or-unknown", "title": null, ..., "known": false}
]
```

## Examples

```bash
$TWICC sessions
$TWICC sessions --project .
$TWICC sessions --project /home/twidi/dev/myproj
$TWICC sessions --workspace backend
$TWICC sessions --include-archived
$TWICC sessions --spawned-by self
$TWICC sessions --spawned-by parent
$TWICC sessions --spawn-tree self
$TWICC sessions --descendants self
$TWICC sessions --descendants parent
$TWICC sessions --siblings self
$TWICC sessions --limit 50 --offset 20
$TWICC sessions --annotation role=implementer
$TWICC sessions --spawn-tree self --annotation role=implementer
$TWICC sessions --annotation status:exists --annotation priority:in:high,critical
$TWICC sessions get abc123-def456
$TWICC sessions get abc123 def456 ghi789
```

## Related commands

- `$TWICC session <session_id>` — full metadata for one session (exit 1 if missing). Skill: `twicc-session`.
- `$TWICC topology <ID|self>` — map a spawned-session tree. Skill: `twicc-topology`.
- `$TWICC project <PROJECT>` / `$TWICC projects` — project details or listing. Skill: `twicc-project` / `twicc-projects`.
- `$TWICC search "<query>"` — full-text search across sessions. Skill: `twicc-search`.

## How to present results

1. Show session title, date, and message count.
2. If `--paginated` reports `has_more: true`, offer to fetch the next page with `--offset`.
3. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
4. Only include cost and model info if explicitly asked.
5. For `get` output: flag `known: false` entries as unknown (typo or already cleaned up).
