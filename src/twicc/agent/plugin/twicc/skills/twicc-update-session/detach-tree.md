# `update-session <SESSION_ID> detach-tree` / `attach-tree` — detach a session from its spawner in the sidebar tree

Show a spawned session detached from its spawner in the sidebar tree, or restore its nesting. MCP tools: `mcp__twicc__update_session_detach_tree`, `mcp__twicc__update_session_attach_tree`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC update-session '<SESSION_ID>' detach-tree
$TWICC update-session '<SESSION_ID>' attach-tree
```

- `detach-tree` fits a session with no topical link with its spawner (a coordinator spawning independent work in other projects or on other subjects). It also detaches an existing child later.
- `attach-tree` undoes it and restores the nesting under the spawner.
- Only the sidebar tree display changes; filiation, orchestration and reporting are unchanged.

## Examples

```bash
$TWICC update-session 4a8352fb-... detach-tree
$TWICC update-session 4a8352fb-... attach-tree
```

## Related commands

- `$TWICC update-sessions detach-tree` / `attach-tree` — several sessions at once. Skill: `twicc-update-sessions`.
- `$TWICC create-session --tree-detached` — create a session already detached. Skill: `twicc-create-session`.
