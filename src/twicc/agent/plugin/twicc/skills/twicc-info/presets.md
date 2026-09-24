# `info presets` — effective defaults and user presets

Per provider, the defaults a new session inherits and the user's stored presets. MCP tool: `mcp__twicc__info`. Read `SKILL.md` first: it resolves `$TWICC` and holds the options and always-present keys.

## Usage

```bash
$TWICC info presets [--provider <key>] [--include-disabled-providers]
```

## Output format

Each provider's list starts with the synthetic `__defaults__` entry (always first), then the user presets in their on-disk order.

```json
"presets": {
  "claude_code": [
    {"name": "__defaults__", "permission_mode": "bypassPermissions", "model": "opus", "effort": "high",
     "thinking": true, "claude_in_chrome": true, "fast_mode": false, "context_max": 200000},
    {"name": "Maximal", "model": "opus-4.7", "context_max": 1000000, "effort": "max", "thinking": true,
     "permission_mode": "bypassPermissions", "claude_in_chrome": true}
  ]
}
```

- `__defaults__` — the user-defined defaults a brand-new session inherits: user-configured values merged with provider hard-coded fallbacks. The name is reserved; user presets cannot use it.
- Fields a provider does not use are absent or `null`.

## Examples

```bash
$TWICC info presets --provider claude_code
```

## Related commands

- `$TWICC create-session --preset <name>` — start from a user preset, with individual overrides. Skill: `twicc-create-session`.
- `$TWICC update-session <session_id> settings` — same preset / override vocabulary on an existing session. Skill: `twicc-update-session`.
- `$TWICC info agent-settings` — the allowed values of each field. File: `agent-settings.md`.

## How to present results

1. Show `__defaults__` first, labelled "effective defaults", then the user presets by name.
2. Name the provider scope explicitly when the output holds more than one provider.
