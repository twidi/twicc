---
name: twicc-topology
description: Show the spawned-session tree around a session, rooted at its top-level ancestor, with session metadata, process state, and aggregate child/cost data.
argument-hint: <session_id|self> [--no-processes] [--slim|--full] [--siblings]
---

# TwiCC Topology

Show the spawned-session tree containing a session. The tree follows `spawned_by` links only — provider-internal subagents (`parent_session_id`) are intentionally out of scope.

## When to use

- You or the user want to see the agent hierarchy around a session.
- You need to discover siblings, children, or descendants before sending direct messages.
- You want process state and aggregate child/cost data for every session in an orchestration tree.

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
$TWICC topology <SESSION_ID|self> [OPTIONS]
```

### Arguments

- `SESSION_ID` — any regular session in the spawned tree.
- `self` — resolve the current TwiCC session (from PID ancestry, or from the connection over MCP).

### Options

- `--processes / --no-processes` — include compact live process state when a TwiCC backend is running. Defaults to `--processes`; if no backend is running, topology is still returned with process data marked unavailable.
- `--full` — emit the full serializer payload for every node — agent settings as stored, `artifacts_dir` as the serializer reports it (set only once the backend has seen an artifact, so always `null` from a terminal), none of the CLI-added keys (`project_directory`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget`) — minus its `process` block, which topology carries per node as `nodes[].process` — and the full five-field `process` block. Off by default: each `nodes[].session` block carries only the slim subset listed below. Use this only when you actually need extra fields for every node; otherwise call `$TWICC session <ID> --full` for the few nodes you care about. `--full-sessions` is a deprecated alias of `--full`.
- `--slim` — reduce each node's `process` block to `{state}`. **Before 2026-10-01 the block keeps its five fields by default and `--slim` opts in; from that date `{state}` is the default and `--slim` is an accepted no-op.** Until then a call with neither flag (and with processes requested) prints a one-line notice on stderr (in the RPC `warnings` key; never on MCP). Mutually exclusive with `--full` / `--full-sessions` (exit `2`).
- `--annotation KEY[OP]VALUE` — annotate every node with a `matches_annotations` boolean indicating whether that node's `annotations` match the expression. The full tree is always preserved (no pruning). Repeatable; multiple flags are AND-combined. Five operators:
  - `KEY=VALUE` — annotation key equals VALUE.
  - `KEY!=VALUE` — annotation key differs from VALUE (or key absent).
  - `KEY:exists` — annotation key is present (any value).
  - `KEY:not-exists` — annotation key is absent.
  - `KEY:in:V1,V2,...` — annotation key equals one of the listed values; escape a literal comma with `\,`.
  - Values are inferred as typed: `true`/`false` → boolean, `null` → null, integers and floats parsed numerically, everything else → string (same rules as `create-session --annotation`).
  - `matches_annotations` is absent from each `nodes[]` entry when no `--annotation` is passed.
  - Example: `$TWICC topology self --annotation role=implementer`
- `--siblings` — annotate every node with a `matches_siblings` boolean: `true` for the **anchor's** siblings (the other sessions spawned by the anchor's parent, anchor excluded). Like `--annotation`, this **marks, it does not prune** — the full tree is always returned. A rootless anchor (no parent) has no siblings, so every flag is `false`. Combinable with `--annotation` (the two flags are independent dimensions). `matches_siblings` is absent from each `nodes[]` entry when `--siblings` is not passed.
  - Example: `$TWICC topology self --siblings`

## Output format

```json
{
  "seed_session_id": "B",
  "root_session_id": "A",
  "path_to_seed": ["A", "B"],
  "cycle_detected": false,
  "total_cost": 2.34,
  "node_count": 2,
  "processes": {"requested": true, "available": true, "reason": null},
  "tree": {"id": "A", "children": [{"id": "B", "children": []}]},
  "nodes": [
    {
      "id": "A",
      "session": {
        "id": "A",
        "project_id": "-home-twidi-dev-myproject",
        "provider": "codex",
        "title": "Root orchestrator",
        "annotations": {"role": "coordinator"},
        "spawned_by": null,
        "spawn_root": "A",
        "created_at": "2026-06-01T10:00:00+00:00",
        "last_new_content_at": "2026-06-01T10:05:12+00:00",
        "context_usage": 18432,
        "context_max": 200000,
        "total_cost": 1.23,
        "directory": "/home/twidi/dev/myproject"
      },
      "process": {"id": 42, "state": "user_turn", "started_at": "...", "last_state_change_at": "...", "pid": 12345},
      "direct_child_count": 1,
      "descendant_count": 1,
      "subtree_total_cost": 2.34,
      "matches_annotations": true
    },
    {
      "id": "B",
      "session": {
        "id": "B",
        "project_id": "-home-twidi-dev-myproject",
        "provider": "codex",
        "title": "Implementation worker",
        "annotations": {"role": "worker"},
        "spawned_by": "A",
        "spawn_root": "A",
        "created_at": "2026-06-01T10:01:30+00:00",
        "last_new_content_at": "2026-06-01T10:04:55+00:00",
        "context_usage": 9210,
        "context_max": 200000,
        "total_cost": 1.11,
        "directory": "/home/twidi/dev/myproject"
      },
      "process": {"id": null, "state": "dead", "started_at": null, "last_state_change_at": null, "pid": null},
      "direct_child_count": 0,
      "descendant_count": 0,
      "subtree_total_cost": 1.11,
      "matches_annotations": false
    }
  ]
}
```

- `seed_session_id` — session id passed as input, after resolving `self`.
- `root_session_id` — top-level ancestor id for this spawned-session tree.
- `path_to_seed` — root-to-seed chain.
- `total_cost` — sum of `session.total_cost` for every node, or `null` when none has cost.
- `node_count` — total number of nodes in the topology.
- `tree` — nested id-only tree for traversal.
- `nodes` — node data in tree pre-order; the root is first.
- `nodes[].id` — same value as `nodes[].session.id`, exposed for direct indexing.
- `nodes[].session` — slim session payload by default (fields shown above); pass `--full` to get the full serializer payload for every node — agent settings as stored, `artifacts_dir` as the serializer reports it (always `null` from a terminal), none of the CLI-added keys (`project_directory`, `scratch_dir`, `orchestration_scratch_dir`, `question_widget`) — minus its `process` block, which lives at `nodes[].process` here. With `--full`, the synthetic `directory` field is **not** added: use `git_directory` / `cwd` directly.
- `nodes[].session.directory` — resolved working directory: `git_directory` when known, else `cwd`. Slim payload only.
- `nodes[].direct_child_count` — immediate spawned children.
- `nodes[].descendant_count` — spawned descendants across all levels.
- `nodes[].subtree_total_cost` — sum of `session.total_cost` for this node and all descendants, or `null` when none has cost.
- `nodes[].matches_annotations` — `true` if this node's annotations match all `--annotation` predicates; `false` otherwise. Absent when no `--annotation` is passed. The tree is never pruned: all nodes are present regardless.
- `nodes[].matches_siblings` — `true` if this node is a sibling of the anchor (shares the anchor's parent, anchor itself excluded); `false` otherwise. Absent when `--siblings` is not passed. The tree is never pruned.
- `process.state` — `starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, or `dead`; `process` is `null` when process data is unavailable or not requested. The example shows the five-field block, the default until 2026-10-01; from that date the default block is `{"state": …}` alone, and `--full` keeps the five fields.
- `cycle_detected` — defensive flag for corrupt `spawned_by` data.

### Exit codes

- `0` — Success
- `1` — Session not found, `self` could not resolve, or the target is a provider-internal subagent
- `2` — Bad CLI usage: `--slim` with `--full` / `--full-sessions`, an invalid `--annotation`, an unknown option or a missing argument

## Examples

```bash
$TWICC topology self
$TWICC topology 4a8352fb-1674-41c0-8a85-0a5a3e4e623a
$TWICC topology self --no-processes
$TWICC topology self --full
$TWICC topology self --annotation role=implementer
$TWICC topology self --annotation status:exists --annotation priority:in:high,critical
$TWICC topology self --siblings
```

## Related commands

- `$TWICC whoami` — identify the current session. Skill: `twicc-whoami`.
- `$TWICC sessions --spawned-by <ID|self>` — list direct children. Skill: `twicc-sessions`.
- `$TWICC sessions --spawned-by <ID|self> --active` — list children with a live process. Skill: `twicc-sessions`.
- `$TWICC send-message <SESSION_ID>` — message a discovered session. Skill: `twicc-send-message`.
- `$TWICC session <SESSION_ID>` — inspect one node's session row (reduced from 2026-10-01; `--full` for every field). Skill: `twicc-session`.

## How to present results

1. Lead with the root title/id, the target path, and the number of nodes.
2. Render the tree by title when available, falling back to session id.
3. Call out `awaiting_user_input` and long-running `assistant_turn` nodes first.
4. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
