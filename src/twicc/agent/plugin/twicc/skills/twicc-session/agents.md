# `session <SESSION_ID> agents` — list subagents

The provider-internal subagents a session spawned. MCP tool: `mcp__twicc__session_agents`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC session <SESSION_ID> agents [--limit N] [--offset N] [--paginated] [--slim | --full]
```

- The provider-internal subagents, most recently active first. Not the sessions created with `create-session`: see `topology`.
- Fails on a subagent.
- Rows as `sessions` returns them (skill: `twicc-sessions`). **Default: `--full` until 2026-10-01, `--slim` from that date** (`--slim` then becomes a no-op; `--full` keeps working on both sides). Until then a call with neither flag prints a one-line notice on stderr (RPC: `warnings` key; never on MCP).
- `process` is always `null`: a subagent runs inside its parent's process.

## Examples

```bash
$TWICC session abc123 agents
$TWICC session abc123 agents --limit 50
```

## Related commands

- `$TWICC topology <ID|self>` — the sessions spawned with `create-session` (the `spawned_by` tree). Skill: `twicc-topology`.
- `$TWICC session <SUBAGENT_ID> messages` — read one subagent's transcript. File: `messages.md`.

## How to present results

1. List with titles; offer to inspect any specific subagent.
