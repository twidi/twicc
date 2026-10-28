# `update-sessions detach-tree` / `attach-tree` — detach a batch from its spawner in the sidebar tree

Show every targeted session detached from its spawner in the sidebar tree, or restore its nesting. MCP tools: `mcp__twicc__update_sessions_detach_tree`, `mcp__twicc__update_sessions_attach_tree`. Read `SKILL.md` first: it resolves `$TWICC` and holds the session selection, output and exit codes.

## Usage

```bash
$TWICC update-sessions detach-tree [SESSION_ID...] [--spawned-by X|--descendants X]
$TWICC update-sessions attach-tree [SESSION_ID...] [--spawned-by X|--descendants X]
```

- `detach-tree` fits sessions with no topical link with their spawner (a coordinator spawning independent work in other projects or on other subjects). It also detaches existing children later.
- `attach-tree` undoes it and restores each session's nesting under its spawner.
- Only the sidebar tree display changes; filiation, orchestration and reporting are unchanged.

## Examples

```bash
$TWICC update-sessions detach-tree --spawned-by self
$TWICC update-sessions attach-tree abc123 def456
```

## Related commands

- `$TWICC update-session <id> detach-tree` / `attach-tree` — one session. Skill: `twicc-update-session`.
