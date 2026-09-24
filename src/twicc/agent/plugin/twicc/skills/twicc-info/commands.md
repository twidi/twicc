# `info commands` — slash / dollar commands

Per provider, the slash / dollar commands available, global or project-scoped. MCP tool: `mcp__twicc__info`. Read `SKILL.md` first: it resolves `$TWICC` and holds the options and always-present keys.

## Usage

```bash
$TWICC info commands [--provider <key>] [--project <PROJECT>] [--filter "TOKENS"] [--include-disabled-providers]
```

- `--project <PROJECT>` — directory path or project id (**drop the leading dash** on ids). Adds the project-scoped commands on top of the globals: exactly what a session inside that project sees at runtime. Without it, only globals (`project=NULL`). Only consumed with `commands`: passing it without `commands` is an error.
- `--filter "TOKENS"` — case-insensitive, whitespace-tokenised substring search: every token must appear in either the `command` literal or the `description`. Only consumed with `commands`.

## Output format

Each provider's list is ordered by `command`.

```json
"commands": {
  "claude_code": [
    {"command": "/commit", "plugin_name": "commit-commands", "description": "Create a git commit",
     "argument_hint": null, "is_builtin": false, "is_workflow": false, "scope": "global"}
  ]
}
```

- `command` — the literal invocation: `activation_char + name` (e.g. `/commit`, `$skill-name`).
- `plugin_name` — `null` for commands not shipped by a plugin.
- `is_workflow` — `true` for saved workflows (`.claude/workflows/*.js`); Claude Code only.
- `scope` — `"global"` or `"project:<id>"`.

## Examples

```bash
$TWICC info commands --filter "git commit"
$TWICC info commands --project /home/twidi/dev/myproj
```

## Related commands

- `$TWICC projects` — browse projects, to feed `--project`. Skill: `twicc-projects`.

## How to present results

1. Group by provider; within a provider, list in `command` order, optionally split by scope (globals first, then project-scoped).
2. For a `--filter` query, lead with the count of matches.
