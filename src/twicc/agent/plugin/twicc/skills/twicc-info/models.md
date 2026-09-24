# `info models` — supported models

Per provider, every model with its identifier, alias, status and capability flags. MCP tool: `mcp__twicc__info`. Read `SKILL.md` first: it resolves `$TWICC` and holds the options and always-present keys.

## Usage

```bash
$TWICC info models [--provider <key>] [--include-disabled-providers]
```

## Output format

```json
"models": {
  "claude_code": [
    {"identifier": "opus-5.5", "alias": "opus", "family": "opus", "version": "5.5",
     "latest": true, "enabled": true, "retirement_date": null,
     "extra": {"supports_1m": true, "supports_effort_xhigh": true, "supports_effort_max": true,
               "supports_fast": true, "supports_permission_auto": true,
               "supports_highres_images": true, "supports_thinking_disabled": false}}
  ]
}
```

- `identifier` — the canonical `family-version` form. **Always valid** as a `--model` value (`create-session` / `update-session settings`).
- `alias` — the bare family name (`"opus"`, `"sonnet"`, `"gpt"`, …) on the latest entry of each family; `null` otherwise. The alias always resolves to the family's current latest: use it for auto-upgrade on a new release; pin to `identifier` for a specific version.
- `enabled` — `false` when the model was taken out of service: it is **not** a valid `--model` value (dropped from the `model` values of `agent-settings`), and anything still set to it resolves to its fallback. `true` for normal models.
- `disable_reason` — short human-readable explanation, present **only** when `enabled` is `false`.
- `retirement_date` — ISO date when the model is retired, or `null` for evergreen / latest entries.
- `extra` — provider-specific capability flags, every flag listed (including `false`). Cross-references the `restricted_to` lists in `agent-settings`.
- The SDK-side `full_name` and pricing are intentionally omitted.

## Examples

```bash
$TWICC info models
$TWICC info models --provider codex
```

## Related commands

- `$TWICC info agent-settings` — which values each model accepts (`restricted_to`). File: `agent-settings.md`.
- `$TWICC create-session --model <value>` / `$TWICC update-session <session_id> settings --model <value>`. Skills: `twicc-create-session`, `twicc-update-session`.

## How to present results

1. Group by `family`; show the latest entry first with both its `identifier` and `alias`.
2. Surface `retirement_date` on non-latest entries.
3. When relevant, translate the `extra` flags into a short capability list ("1M context", "effort=max", …).
