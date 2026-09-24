# `session <SESSION_ID> plan` — the session's tracked plan documents

Read the plans, specs and handoffs a session wrote. MCP tool: `mcp__twicc__session_plan`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC session <SESSION_ID> plan
$TWICC session <SESSION_ID> plan <PATH>
$TWICC session <SESSION_ID> plan --list
```

The documents the session or its subagents wrote that look like plans: the native Claude plan (*plan mode*) and detected plans, specs, handoffs, notes. **Both providers.**

- No argument — the **most recently updated** document (not necessarily the native plan), as `{path, abs_path, content}`. Exit 1 when the session tracks none: check first with `--list`, or `plan_paths` in `session <ID> --full`.
- `PATH` — that document. Only a tracked entry: its `path` as `--list` shows it (project-relative under the project, absolute otherwise), or its absolute path. Exit 1 on an unknown path or a missing file.
- `--list` — every tracked document, newest first:

```json
{"plan_paths": [{"path": "docs/plans/feature-plan.md", "exists": true,
                 "created_at": "...", "updated_at": "...", "source": "detected",
                 "abs_path": "/abs/path/to/docs/plans/feature-plan.md"}, ...]}
```

`source` is `detected`, `subagent` (written by a subagent) or `claude_plan` (the native plan). `abs_path` is resolved, worktree-aware. `plan_paths` in `session <ID> --full` has the same entries, without `abs_path`.

## Examples

```bash
$TWICC session abc123 plan
$TWICC session abc123 plan docs/plans/feature-plan.md
$TWICC session abc123 plan --list
```

## Related commands

- `$TWICC session <ID> --full` — `plan_paths` carries the same entries. File: `row.md`.

## How to present results

1. Render the `content` markdown as-is.
