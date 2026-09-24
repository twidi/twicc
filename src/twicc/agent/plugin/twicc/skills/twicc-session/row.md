# `session <SESSION_ID>` — the session row

Metadata, agent settings and live process state of one session. MCP tool: `mcp__twicc__session`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC session <SESSION_ID|self|parent> [--slim|--full]
```

- Works on any session row: regular session, subagent, or a session with no user message yet.
- For several ids, use `sessions get <ID>...`: same row, plus a `known` flag.
- `--slim` / `--full` choose the projection. They go before or after the id, and apply to the row alone: with a sub-command, before or after the id, they are refused (exit 2) — write `session <ID> agents --full`. Mutually exclusive (exit 2).
- **Default: `--full` until 2026-10-01, `--slim` from that date** (`--slim` then becomes a no-op; `--full` keeps working on both sides). Until then a call with neither flag prints a one-line notice on stderr (RPC: `warnings` key; never on MCP).
- Exit 1 (a `validation_error` on stdout) when no session has that id, or `self` / `parent` cannot be resolved.

## Output format

`--full`:

```json
{
  "id": "abc123-def456", "project_id": "-home-twidi-dev-myproject", "provider": "claude_code",
  "parent_session_id": null, "last_line": 150, "mtime": 1741654800.0,
  "created_at": "2025-03-10T14:30:00+00:00", "last_started_at": "2025-03-10T14:30:00+00:00",
  "last_updated_at": "2025-03-10T15:45:00+00:00", "last_stopped_at": "2025-03-10T15:50:00+00:00",
  "last_new_content_at": "2025-03-10T15:45:00+00:00", "last_viewed_at": "2025-03-10T16:00:00+00:00",
  "stale": false, "title": "Implement user authentication", "slug": null, "user_message_count": 12,
  "compute_version_up_to_date": true, "context_usage": 85000,
  "self_cost": 1.234, "subagents_cost": 0.567, "total_cost": 1.801,
  "cwd": "/home/twidi/dev/myproject", "git_branch": "feature/auth", "git_directory": "/home/twidi/dev/myproject",
  "model": {"raw": "claude-opus-4-20250514", "family": "opus", "version": "4"},
  "archived": false, "pinned": null,
  "permission_mode": "default", "selected_model": "opus", "effort": "high", "thinking_enabled": true,
  "claude_in_chrome": false, "fast_mode": false, "context_max": 200000, "question_widget": true,
  "compacted": false, "hidden": false, "spawned_by": null, "spawn_root": null,
  "annotations": {"role": "reviewer"},
  "project_directory": "/home/twidi/dev/myproject",
  "artifacts_dir": "/home/twidi/.twicc/artifacts/abc123-def456",
  "scratch_dir": "/home/twidi/.twicc/scratch/abc123-def456",
  "orchestration_scratch_dir": null,
  "process": {"id": 5847, "state": "assistant_turn", "started_at": "2026-09-20T10:43:57.327194+00:00",
              "last_state_change_at": "2026-09-20T10:44:45.240981+00:00", "pid": 3501299}
}
```

`--slim` returns the row `sessions` returns (example in `list.md` of the `twicc-sessions` skill), with `process: {state}`.

### Key fields

- `last_line` — item count; the upper bound of `content` ranges.
- `provider` — `claude_code` or `codex`; sets the item schema of `content`.
- `parent_session_id` — set on a subagent, `null` otherwise.
- `slug` (`--full` only) — provider short id (e.g. a Codex subagent nickname), or `null`.
- `context_max` / `context_usage` — context window and current usage, in tokens.
- `compacted` — compacted at least once.
- `last_viewed_at` (`--full` only) — the user's last visit in TwiCC.
- `hidden` — hidden from all listings and broadcasts.
- `last_new_content_at` — when the last item was appended.
- `spawned_by` — the session that spawned it (through `create-session`), or `null` when no session did. Set at creation, never updated.
- `spawn_root` — root of the spawned-session tree; `null` until the session joins one.
- `annotations` — free-form JSON object set at creation.
- `project_directory` — the project's directory; `null` for a project with none.
- `artifacts_dir` — **always** the folder to write artifacts to, even while empty. `has_artifacts` says whether it holds anything over MCP / RPC; from a terminal it is always `false`, so check the folder.
- `scratch_dir` — the session's own scratch folder. `orchestration_scratch_dir` — the orchestration tree's shared one, `null` outside an orchestration.
- Agent settings (`permission_mode`, `selected_model`, `effort`, `thinking_enabled`, `claude_in_chrome`, `fast_mode`, `context_max`, `question_widget`) — effective values: stored, else the current default (`question_widget`: `true` when never chosen). On a subagent: stored values, mostly `null`.
- `process.state` — `starting`, `assistant_turn`, `awaiting_user_input` (blocked on a human), `user_turn` or `dead`. `dead` = TwiCC runs no process for it — the usual state, since most indexed sessions were never started from TwiCC — also when stopped, or when no backend runs. `process` is `null` on a subagent.

## Examples

```bash
$TWICC session abc123-def456
$TWICC session abc123-def456 --full
$TWICC session self --slim
```

## Related commands

- `$TWICC sessions get <ID>...` — the same row for several ids. Skill: `twicc-sessions`.
- `$TWICC session <ID> messages` / `content` — read the transcript. Files: `messages.md`, `content.md`.
- `$TWICC topology <ID|self>` — the spawned-session tree around it. Skill: `twicc-topology`.
- `$TWICC project <project_id>` — project details. Skill: `twicc-project`.

## How to present results

1. Summarize title, date, model, branch.
2. Include cost only if asked.
