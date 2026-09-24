# `info agent-settings` — accepted session-setting values

Per provider, the values, aliases and model restrictions of each session setting. MCP tool: `mcp__twicc__info`. Read `SKILL.md` first: it resolves `$TWICC` and holds the options and always-present keys.

The canonical "what can I set, and when does it apply" reference before `update-session settings` or building a preset.

## Usage

```bash
$TWICC info agent-settings [--provider <key>] [--include-disabled-providers]
```

## Output format

```json
"agent-settings": {
  "claude_code": {
    "model": {
      "values": [{"value": "fable", "latest": true}, {"value": "fable-5", "latest": false},
                 {"value": "opus", "latest": true}, {"value": "opus-5", "latest": false},
                 {"value": "sonnet", "latest": true}, {"value": "opus-4.8", "latest": false}],
      "aliases": {"max": "fable", "strongest": "fable", "medium": "opus", "balanced": "opus", "min": "sonnet", "fastest": "sonnet", "cheapest": "sonnet"}
    },
    "effort": {
      "values": [{"value": "low", "restricted_to": null}, {"value": "medium", "restricted_to": null},
                 {"value": "high", "restricted_to": null},
                 {"value": "xhigh", "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5.5", "opus", "opus-5", "opus-4.8", ...]},
                 {"value": "max", "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5.5", "opus", "opus-5", "opus-4.8", "opus-4.7", ...]}],
      "aliases": {"min": "low", "max": "max"}
    },
    "permission_mode": {
      "values": [{"value": "default", "restricted_to": null, "description": "Prompts for permission on first use of each tool"},
                 {"value": "auto", "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5.5", "opus", "opus-5", ...], "description": "Auto-approves tools, with safety checks blocking risky actions"}],
      "aliases": {"min": "dontAsk", "strict": "dontAsk", "safe": "dontAsk", "max": "bypassPermissions", "open": "bypassPermissions", "full": "bypassPermissions"}
    },
    "permission_mode_if_untrusted": {
      "values": [{"value": "default", "restricted_to": null, "description": "Prompts for permission on first use of each tool"},
                 {"value": "acceptEdits", "restricted_to": null, "description": "Auto-accepts file edit permissions"}],
      "aliases": {"min": "dontAsk", "safe": "dontAsk", "max": "acceptEdits"}
    },
    "context_max": {
      "values": [{"value": 200000, "context_max_alias": "200k", "restricted_to": null},
                 {"value": 1000000, "context_max_alias": "1m", "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5.5", "opus", "opus-5", ...]}],
      "aliases": {"min": "200k", "max": "1m"}
    }
  }
}
```

Field keys match the CLI flags / presets: the model is `model` (not the wire name `selected_model`), extended thinking is `thinking` (not `thinking_enabled`); the others share the same name.

- `model.values` — each accepted `--model` value, with `latest` (the current flagship of its family). Richer per-model metadata (family, version, capabilities, retirement) lives in `models`.
- `restricted_to: null` — the value is available on every model of this provider.
- `restricted_to: [...]` — the value applies **only** to those model identifiers / aliases (same vocabulary as `models.identifier` / `models.alias`).
- `description` — present only on values with a documented note (today: every `permission_mode` value across providers, plus `fast_mode=true` where supported). Absent otherwise.
- `context_max_alias` — only on `context_max` entries: compact form of `value` (`200000` → `"200k"`, `1000000` → `"1m"`, `272000` → `"272k"`). The raw integer `value` stays canonical; the alias is for display and matches the syntax `--context-max` accepts in `create-session` / `update-session settings`.
- `aliases` — alias → concrete-value map for the field (`{"max": "fable", ...}`). Pass the alias to the matching `--<field>` flag of `create-session` / `update-session settings` / `update-sessions settings`: it resolves to the value for the session's provider.
- `permission_mode_if_untrusted` — the restricted permission-mode subset for an **untrusted** (or unknown-trust) project: `bypassPermissions` / `yolo` are dropped, and its `max` alias lands on the most permissive *allowed* mode (Claude Code `acceptEdits`, Codex `auto_review`). You never pass this field: `create-session` / `update-session` resolve and clamp `permission_mode` against it automatically (with a note) when the target project is untrusted. Project trust is a human-only decision.

## Examples

```bash
$TWICC info agent-settings --provider codex
```

## Related commands

- `$TWICC info models` — per-model metadata behind `restricted_to`. File: `models.md`.
- `$TWICC info presets` — stored combinations of these values. File: `presets.md`.
- `$TWICC create-session` / `$TWICC update-session <session_id> settings` / `$TWICC update-sessions settings` — take these values and aliases. Skills: `twicc-create-session`, `twicc-update-session`, `twicc-update-sessions`.

## How to present results

1. For each field, list the values briefly.
2. For a value with a `restricted_to` list, summarise the requirement ("only on opus 4.6+"); do not dump the list.
3. Always surface `description` verbatim when present.
