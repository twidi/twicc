---
name: twicc-timeline
description: Use when you need event history across TwiCC sessions, a project, a worktree, a workspace, or one session, with session filters and cursor pagination.
---

# TwiCC timeline

Read recorded events, newest first. Filters use **current session metadata**, not historical session state.

## How to invoke

**Prefer `mcp__twicc__timeline`.** Search deferred tools when it is absent. MCP wraps the result in `{exit_code, result, error}`.

For a shell invocation, resolve the executable at the start of each invocation:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Run `$TWICC <args>`. **Never quote `$TWICC`**: it can contain several words.

## Scope and session filters

Use at most one primary scope. No primary scope means all projects.

| Option | Selection |
|---|---|
| `--project PROJECT` | Project ID or existing directory. A main project includes its worktrees. A worktree selects only itself. |
| `--workspace ID` | Workspace members and their worktrees. An empty workspace returns no events. |
| `--session ID` | Exactly one known session, including a native subagent. No implicit descendants. |

Unknown primary scopes fail. Session filters intersect the primary scope. Events use their recorded project; session filters use the session's current project.

The four filiation filters are mutually exclusive. They select spawned sessions, not native subagents.

| Option | Selection |
|---|---|
| `--spawned-by ID` | Direct children. |
| `--spawn-tree ID` | Complete containing spawn tree, including its root and the named session. |
| `--descendants ID` | Descendants at every depth, excluding the named session. |
| `--siblings ID` | Other sessions with the same spawning parent. |
| `--provider PROVIDER` | Current provider: `claude_code` or `codex`. |
| `--annotation EXPR` | Current annotations. Repeat for AND. Dotted keys address nested values. |
| `--state STATE` | Current process state. Repeat for OR. |
| `--active` | Every process state except `dead`, including idle `user_turn`. Exclusive with `--state`. |

Annotation operators: `key=value`, `key!=value`, `key:exists`, `key:not-exists`, `key:in:a,b`. Values are typed: booleans, null, numbers, or strings.

States: `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`. Without a backend, sessions count as `dead`.

Local CLI and internal MCP accept `self` and `parent` for `--session`, `--spawned-by`, and `--descendants`. `--spawn-tree` and `--siblings` accept `self` but reject `parent`. Remote CLI and external MCP require explicit IDs.

**Hidden sessions are excluded by default**, including exact-session and filiation selections. `--include-hidden` includes both visible and hidden sessions. `--only-hidden` selects hidden sessions. These flags are mutually exclusive.

Archived sessions and projects remain eligible. `--include-archived` is a compatibility no-op. Without filiation filters, native subagents remain eligible.

## Instance events and missing sessions

Baseline global, project, and workspace selections include instance events. Instance events have no target session. An empty workspace includes none.

An exact-session scope, provider, annotation, filiation, state, `--active`, or `--only-hidden` filter excludes instance events. These selections also exclude events whose session metadata is missing.

Baseline selections preserve events whose session metadata is missing. `--include-hidden` does not change that policy. A hidden actor can appear in the `sessions` table without making its own events eligible.

## Pagination and output

`--limit` defaults to **20** and accepts **1–100**. Events sort by `occurred_at` descending, then event ID descending.

```json
{
  "items": [],
  "pagination": {"limit": 20, "has_more": false, "next_cursor": null},
  "sessions": {}
}
```

`items` contains serialized events. `sessions` maps cited session IDs to metadata. Missing sessions have no entry.

When `has_more` is true, pass `pagination.next_cursor` unchanged as `--before`. Keep the same scope and filters. Stop when `has_more` is false. There is no `--offset` or `total`.

Newer events do not shift subsequent pages. Pagination is not a snapshot: metadata changes and late events can change subsequent results.

## Examples

Select visible sessions with an annotation in a workspace:

```bash
$TWICC timeline --workspace WORKSPACE_ID --annotation team=backend
```

Include hidden sessions in the tree containing a session:

```bash
$TWICC timeline --spawn-tree MIDDLE_ID --include-hidden
```

Read one session, including its events when hidden:

```bash
$TWICC timeline --session SESSION_ID --include-hidden
```

Select either process state:

```bash
$TWICC timeline --state assistant_turn --state awaiting_user_input
```

Fetch one event, then the next older event. Replace `CURSOR` with the first response's `pagination.next_cursor`:

```bash
$TWICC timeline --session SESSION_ID --include-hidden --limit 1
$TWICC timeline --session SESSION_ID --include-hidden --limit 1 --before 'CURSOR'
```

`--workspace` and `--session` cannot combine. To select a tree within a workspace, combine `--workspace`, `--spawn-tree`, and the session filters.

## Related commands

- `sessions`: current sessions with the shared session filters.
- `session ID`: one session's metadata and transcript.
- `search QUERY`: full-text search in session history.
