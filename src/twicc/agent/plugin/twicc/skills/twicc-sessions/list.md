# `sessions` — the listing

List sessions with their live process state. MCP tool: `mcp__twicc__sessions`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC sessions [OPTIONS]
```

- Returns only valid sessions: a creation date and at least one user message. (`sessions get` returns anything you name. File: `get.md`.)
- Ordered by most recently active.

### Filters

`wait-reply` and `stop` reuse these; their files say which ones they accept and what they change.

- `--project <PROJECT>` — path or id (**drop the leading dash** on ids). A normal project also returns its git worktrees' sessions (a worktree's sessions belong to its main repository); a worktree project returns only its own. A non-existent project returns empty, no error.
- `--workspace ID` — sessions of projects in that workspace, each member project's git worktrees included. Mutually exclusive with `--project`.
- `--provider claude_code|codex` — an ordinary column filter, unrelated to state.
- `--state <VALUE>` — keep one `process.state` bucket. **Repeatable, OR-combined** (a session holds a single state): `--state assistant_turn --state awaiting_user_input` = "busy or blocked"; naming all five = the unfiltered listing. `dead` is accepted: "no TwiCC-managed process".
- `--active` — every state but `dead`, `user_turn` included (the agent is loaded and idle, not gone). Mutually exclusive with `--state`.
- The state filter runs **before** the page is cut: `total` and `has_more` count what matches.
- `--include-archived` — include archived sessions (excluded by default).
- `--include-hidden` — include hidden sessions (excluded by default, unless a filiation scope lifts it).
- `--only-hidden` — only hidden sessions. Mutually exclusive with `--include-hidden`.
- Filiation scopes — each **implies `--include-hidden`**; the four are mutually exclusive:
  - `--spawned-by <ID|self|parent>` — direct children of the session. `self` = my children; `parent` = my siblings, myself included.
  - `--spawn-tree <ID|self>` — every session of the spawn tree that contains the id. Any id in the tree works (root, middle, leaf): the CLI resolves the tree it belongs to. A standalone session (no children, never spawned) queried by its own id is a single-node tree. `self` = the tree containing the current session.
  - `--descendants <ID|self|parent>` — proper descendants (every session transitively spawned by it, target excluded). `self` = the current session; `parent` = its spawner (my siblings, their subtrees, my own subtree). Use it for "everything under X" without X: `--spawn-tree` gives the whole tree containing X (root included), `--spawned-by` only direct children.
  - `--siblings <ID|self>` — the *other* sessions spawned by the same parent, **target always excluded**. `self` = my peers. `parent` is **not** supported (siblings are relative to a node's own parent). The direct way to list your peers; `--spawned-by parent` selects the same set but includes yourself.
- `--annotation KEY[OP]VALUE` — match the `annotations` object. Repeatable, AND-combined. Does **not** imply `--include-hidden` (orthogonal). Operators:
  - `KEY=VALUE` — equals VALUE.
  - `KEY!=VALUE` — differs from VALUE, or key absent.
  - `KEY:exists` / `KEY:not-exists` — key present (any value) / absent.
  - `KEY:in:V1,V2,...` — equals one of the values; escape a literal comma with `\,`.
  - Values are typed: `true`/`false` → boolean, `null` → null, integers and floats numeric, anything else → string (same rules as `create-session --annotation`).

### Projection: `--slim` / `--full`

- `--slim` — the reduced row: every field except the payloads you fetch per session (`tasks`, `plan_paths`, `goals`, `layout` — the flags `has_tasks`, `has_goals`, `has_plan`, `has_artifacts`, `has_workflows` say what there is to fetch), the redundant timestamps (`mtime`, `last_started_at`, `last_updated_at`, `last_stopped_at`, `last_viewed_at`), the cost breakdown (`self_cost`, `subagents_cost`), `slug`, `browser_url` and `compute_version_up_to_date`. Its `process` block is `{state}`.
- `--full` — the full payload, the one `session <ID> --full` returns.
- **Default: `--full` before 2026-10-01, `--slim` from that date** (`--slim` then becomes an accepted no-op; `--full` keeps working on both sides). Until then a call with neither flag prints a one-line notice on stderr (RPC envelope: `warnings` key; never on MCP), next to the `--paginated` one.
- Mutually exclusive (exit `2`). `sessions get` takes them too, placeholders included.

### Pagination

- `--limit N` — max results (default 20). `--offset N` — skip the first N (default 0).
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total`, `has_more`: you know whether another page follows instead of guessing from the page size. Without an explicit `--limit` the page size is **20**.
- **Before 2026-10-01 `--paginated` opts in and a call without it keeps its bare-array shape (its rows gain new keys and values now — see the fields below); from that date the envelope is the only shape and the flag an accepted no-op.** Passing it works on both sides.

## Output format

The reduced projection (`--slim` before 2026-10-01, the default from that date):

```json
[
  {
    "id": "abc123-def456", "project_id": "-home-twidi-dev-myproject", "provider": "claude_code",
    "title": "Implement user authentication", "annotations": {"role": "reviewer"},
    "parent_session_id": null, "spawned_by": null, "spawn_root": null,
    "created_at": "2025-03-10T14:30:00+00:00", "last_new_content_at": "2025-03-10T15:45:00+00:00",
    "context_usage": 85000, "context_max": 200000, "total_cost": 1.801, "user_message_count": 12,
    "model": {"raw": "claude-opus-4-20250514", "family": "opus", "version": "4"},
    "git_branch": "feature/auth", "archived": false, "hidden": false, "pinned": null, "stale": false,
    "unavailable_reason": null, "mute_on_user_turn": false,
    "has_artifacts": false, "has_plan": true, "has_workflows": false, "has_tasks": true, "has_goals": false,
    "last_line": 245, "cwd": "/home/twidi/dev/myproject", "git_directory": "/home/twidi/dev/myproject",
    "project_directory": "/home/twidi/dev/myproject",
    "artifacts_dir": "/home/twidi/.twicc/artifacts/abc123-def456",
    "scratch_dir": "/home/twidi/.twicc/scratch/abc123-def456",
    "orchestration_scratch_dir": null, "compacted": false, "hybrid": false,
    "permission_mode": "default", "selected_model": "opus", "effort": "high", "thinking_enabled": true,
    "claude_in_chrome": false, "fast_mode": false, "question_widget": true,
    "process": {"state": "user_turn"}
  }
]
```

With `--full` (the default before 2026-10-01), each row is the full payload (`session <ID> --full`): the fields above plus `mtime`, `last_started_at` / `last_updated_at` / `last_stopped_at` / `last_viewed_at`, `slug`, `compute_version_up_to_date`, `self_cost` / `subagents_cost`, `layout`, `browser_url`, `tasks`, `plan_paths`, `goals` — and a five-field `process` block.

### The `process` block

- Every row carries it: `{state}` in the reduced projection; plus `id`, `started_at`, `last_state_change_at`, `pid` with `--full`.
- `state` — `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn` or `dead`. Note `awaiting_user_input`, the non-obvious stop: the agent is blocked on a human, not working.
- It answers **what TwiCC is running**. `dead` = no TwiCC-managed process: also the answer for the many sessions TwiCC indexed but never started (a `claude` run straight from a terminal keeps generating and still reads `dead`), and for a TwiCC not running at all (an agent does not outlive its backend).
- `null` for one case only: a subagent, which runs inside its parent's process; `parent_session_id` identifies those.

### Key fields

- `provider` — `"claude_code"` or `"codex"`; sets the item schema and the supported settings.
- `slug` (`--full` only) — provider short id (e.g. a Codex subagent nickname), or `null`.
- `parent_session_id` — `null` for regular sessions, set for subagents.
- `model` — `{"raw", "family", "version"}`.
- `context_max` / `context_usage` — context window and current usage, in tokens.
- `compacted` — compacted at least once.
- `project_directory` — the project's directory; `null` for a project with none.
- `artifacts_dir` — **always** the session's artifacts folder, the place to write, even while empty. `has_artifacts` says whether it holds anything over MCP / RPC; from a terminal it is always `false`, so check the folder.
- `scratch_dir` — the session's own scratch folder. `orchestration_scratch_dir` — the orchestration tree's shared one (the `scratch_dir` annotation), `null` outside one.
- Agent settings (`permission_mode`, `selected_model`, `effort`, `thinking_enabled`, `claude_in_chrome`, `fast_mode`, `context_max`, `question_widget`) — **effective**: stored, else the current default (`question_widget`: `true` when not chosen). A subagent row keeps its stored values, mostly `null`.
- `last_new_content_at` — when the last item was appended.
- `last_viewed_at` (`--full` only) — the user's last visit in TwiCC.
- `hidden` — hidden from all listings and broadcasts.
- `spawned_by` — the session that spawned it, or `null`.
- `spawn_root` — root of the spawned-session tree; `null` before the session joins one.
- `annotations` — free-form JSON object set at creation.

## Examples

```bash
$TWICC sessions
$TWICC sessions --project .
$TWICC sessions --project /home/twidi/dev/myproj
$TWICC sessions --workspace backend
$TWICC sessions --include-archived
$TWICC sessions --state assistant_turn --state awaiting_user_input
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
```

## Related commands

- `$TWICC sessions get <ID>...` — the same rows for known ids. File: `get.md`.
- `$TWICC session <session_id|self|parent>` — one session's row, same `--slim` / `--full` (exit 1 when no row has that id, or `self` / `parent` cannot be resolved). Skill: `twicc-session`.
- `$TWICC topology <ID|self>` — map a spawned-session tree. Skill: `twicc-topology`.

## How to present results

1. Show session title, date, and message count.
2. If `--paginated` reports `has_more: true`, offer the next page with `--offset`.
3. Include cost and model only if explicitly asked.
