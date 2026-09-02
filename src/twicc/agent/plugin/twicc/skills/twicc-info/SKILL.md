---
name: twicc-info
description: Read-only inspection of TwiCC — version, providers (and enabled state), slash/dollar commands, supported models, agent-settings choices/constraints, and presets — any subset composed into one JSON. Use when you or the user need a machine-readable picture of what's available before scripting a session, picking a preset/model, or filtering commands.
argument-hint: '[presets|commands|models|agent-settings|all...]'
---

# TwiCC Info

A single read-only command that always reports TwiCC's running version and provider list, and on demand composes any subset of four sections: `presets`, `commands`, `models`, `agent-settings`. Pass zero or more section names as positional arguments — the output keys appear in canonical order regardless of input order, duplicates collapse. Everything is JSON on stdout. Nothing here writes to disk or to the live TwiCC.

## When to use

- You want to know the TwiCC version, the registered providers, and which of them the user has disabled.
- You're about to script a `create-session` / `update-session settings` call and need the effective default agent settings, the valid model identifiers / aliases, the allowed values for each setting, or a stored preset to start from.
- You want to find a slash / dollar command by substring of its literal or its description.
- You want to check, for an agent-setting value (e.g. `effort=max`), which models actually support it.
- You want several of the above in one call instead of issuing several CLI runs.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Command shape

```bash
$TWICC info [SECTION...] [OPTIONS]
```

`SECTION...` is any combination (0+) of `presets`, `commands`, `models`, `agent-settings`, plus the shortcut `all` (expands to the four others). Order does not matter and duplicates are ignored.

### Options

- `--provider <key>` — narrow every requested section to a single provider (`claude_code`, `codex`, …). Naming a provider also **bypasses** the disabled-provider filter for that one — its data is returned even when disabled. The top-level `providers` dict always lists every registered provider regardless of this flag.
- `--project <PROJECT>` — directory path or project id (**drop the leading dash** on ids). **Only consumed when `commands` is requested.** Adds the project-scoped commands on top of the globals — i.e. exactly what a session inside that project would see at runtime. Passing it without `commands` is an error.
- `--filter "TOKENS"` — case-insensitive whitespace-tokenised substring search for `commands`: every token must appear in either the `command` literal or the `description`. **Only consumed when `commands` is requested.**
- `--include-disabled-providers` — flips the disabled-provider filter on the section payloads, so disabled providers are listed alongside the enabled ones. Does not change the top-level `providers` dict (which is always exhaustive).

## Always-present keys

Every invocation returns at minimum:

```json
{
  "twicc_version": "1.7.0",
  "twicc_executable": "uvx twicc",
  "providers": {
    "claude_code": {"identifier": "claude_code", "name": "Claude Code", "disabled": false, "default": true, "orchestration": true},
    "codex":       {"identifier": "codex",       "name": "Codex",       "disabled": true,  "default": false, "orchestration": false}
  },
  "available_info_arguments": {}
}
```

- `twicc_version` — the installed TwiCC version.
- `twicc_executable` — shell command that re-invokes the same TwiCC distribution (e.g. `uvx twicc`, `twicc`, `uv run --directory <dir> run.py`, an absolute path, …). Suffix it with subcommand args to reach this TwiCC instance from anywhere.
- `providers` — dict keyed by provider identifier. Each entry carries:
  - `identifier` — same as the dict key.
  - `name` — human-readable label.
  - `disabled` — `true` when the user has disabled the provider; `create-session` and similar calls will refuse it.
  - `default` — `true` for the user's default provider; `false` otherwise.
  - `orchestration` — `true` when the provider is fair game for agents picking providers **on their own** while orchestrating sessions (see `twicc-orchestration`). Soft preference: an explicit user request for an `orchestration: false` provider still works as long as it is not `disabled`. Always `false` for disabled providers.
- `available_info_arguments` — discovery dict for the positional sections this command itself accepts (`presets`, `commands`, `models`, `agent-settings`, `all`). The reserved `__description` key explains the dict; every other key is an accepted argument paired with a one-line summary of what it adds to the payload.

## Section payloads

Each section adds one top-level key with the exact name of the section (`presets`, `commands`, `models`, `agent-settings`). Within a section, the data is keyed by provider identifier.

### `presets`

```bash
$TWICC info presets [--provider <key>] [--include-disabled-providers]
```

Each provider's list starts with the synthetic `__defaults__` entry (always first), then the user-defined presets in their on-disk order.

```json
"presets": {
  "claude_code": [
    {
      "name": "__defaults__",
      "permission_mode": "bypassPermissions",
      "model": "opus",
      "effort": "high",
      "thinking": true,
      "claude_in_chrome": true,
      "fast_mode": false,
      "context_max": 200000
    },
    {
      "name": "Maximal",
      "model": "opus-4.7",
      "context_max": 1000000,
      "effort": "max",
      "thinking": true,
      "permission_mode": "bypassPermissions",
      "claude_in_chrome": true
    }
  ]
}
```

`__defaults__` reports the user defined defaults a brand-new session inherits — user-configured values merged with provider hard-coded fallbacks. The name is reserved; user presets cannot use it.

Fields a provider does not use are absent or `null`.

### `commands`

```bash
$TWICC info commands [--provider <key>] [--project <PROJECT>] [--filter "TOKENS"] [--include-disabled-providers]
```

Each provider's list carries the slash / dollar commands available, ordered by `command`. Without `--project`, only globals (`project=NULL`) are listed; with one, the project-scoped commands are added on top.

```json
"commands": {
  "claude_code": [
    {
      "command": "/commit",
      "plugin_name": "commit-commands",
      "description": "Create a git commit",
      "argument_hint": null,
      "is_builtin": false,
      "is_workflow": false,
      "scope": "global"
    }
  ]
}
```

- `command` — the literal invocation: `activation_char + name` (e.g. `/commit`, `$skill-name`).
- `plugin_name` — `null` for commands that aren't shipped by a plugin.
- `is_workflow` — `true` for saved workflows (`.claude/workflows/*.js`); Claude Code only.
- `scope` — `"global"` or `"project:<id>"`.

### `models`

```bash
$TWICC info models [--provider <key>] [--include-disabled-providers]
```

```json
"models": {
  "claude_code": [
    {
      "identifier": "opus-5",
      "alias": "opus",
      "family": "opus",
      "version": "5",
      "latest": true,
      "enabled": true,
      "retirement_date": null,
      "extra": {
        "supports_1m": true,
        "supports_effort_xhigh": true,
        "supports_effort_max": true,
        "supports_fast": true,
        "supports_permission_auto": true,
        "supports_highres_images": true,
        "supports_thinking_disabled": true
      }
    }
  ]
}
```

- `identifier` — the canonical `family-version` form. **Always valid** as a `--model` value (accepted by `create-session` / `update-session settings`).
- `alias` — the bare family name (`"opus"`, `"sonnet"`, `"gpt"`, …) for the latest entry of each family; `null` otherwise. Passing the alias means "always resolve to the family's current latest". Pin to `identifier` for a specific version; use `alias` for auto-upgrade on a new release.
- `enabled` — `false` when the model has been taken out of service: it is **not** a valid `--model` value (it is dropped from `agent-settings`' `selected_model` choices) and anything still set to it resolves to its fallback. `true` for normal models.
- `disable_reason` — short human-readable explanation, present **only** when `enabled` is `false`; absent otherwise.
- `retirement_date` — ISO date when the model is retired, or `null` for evergreen / latest entries.
- `extra` — provider-specific capability flags (every flag listed, including `false` values). Cross-references the `restricted_to` lists in `agent-settings`.

The SDK-side `full_name` and pricing are intentionally omitted.

### `agent-settings`

```bash
$TWICC info agent-settings [--provider <key>] [--include-disabled-providers]
```

```json
"agent-settings": {
  "claude_code": {
    "model": {
      "values": [
        {"value": "fable",    "latest": true},
        {"value": "fable-5",  "latest": false},
        {"value": "opus",     "latest": true},
        {"value": "sonnet",   "latest": true},
        {"value": "opus-4.8", "latest": false}
      ],
      "aliases": {"max": "fable", "strongest": "fable", "medium": "opus", "balanced": "opus", "min": "sonnet", "fastest": "sonnet", "cheapest": "sonnet"}
    },
    "effort": {
      "values": [
        {"value": "low",    "restricted_to": null},
        {"value": "medium", "restricted_to": null},
        {"value": "high",   "restricted_to": null},
        {"value": "xhigh",  "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5", "opus", "opus-4.8", "opus-4.7", ...]},
        {"value": "max",    "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5", "opus", "opus-4.8", "opus-4.7", "opus-4.6", ...]}
      ],
      "aliases": {"min": "low", "max": "max"}
    },
    "permission_mode": {
      "values": [
        {"value": "default", "restricted_to": null, "description": "Prompts for permission on first use of each tool"},
        {"value": "auto",    "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5", "opus", ...], "description": "Auto-approves tools, with safety checks blocking risky actions"}
      ],
      "aliases": {"min": "dontAsk", "strict": "dontAsk", "safe": "dontAsk", "max": "bypassPermissions", "open": "bypassPermissions", "full": "bypassPermissions"}
    },
    "permission_mode_if_untrusted": {
      "values": [
        {"value": "default",     "restricted_to": null, "description": "Prompts for permission on first use of each tool"},
        {"value": "acceptEdits", "restricted_to": null, "description": "Auto-accepts file edit permissions"}
      ],
      "aliases": {"min": "dontAsk", "safe": "dontAsk", "max": "acceptEdits"}
    },
    "context_max": {
      "values": [
        {"value": 200000,  "context_max_alias": "200k", "restricted_to": null},
        {"value": 1000000, "context_max_alias": "1m",   "restricted_to": ["fable-5.1", "fable", "fable-5", "opus-5", "opus", ...]}
      ],
      "aliases": {"min": "200k", "max": "1m"}
    }
  }
}
```

Field keys match the CLI flags / presets: the model is `model` (not the wire name `selected_model`) and extended thinking is `thinking` (not `thinking_enabled`); the rest share the same name.

- `restricted_to: null` — value is available on every model of this provider.
- `restricted_to: [...]` — value applies **only** to those model identifiers / aliases (same vocabulary as `models.identifier` / `models.alias`).
- `description` — present only for values that have a documented note (today: every `permission_mode` value across providers, plus `fast_mode=true` where supported). Silently absent otherwise.
- `context_max_alias` — only on entries of the `context_max` field: compact human-friendly form of `value` (`200000` → `"200k"`, `1000000` → `"1m"`, `272000` → `"272k"`). The `value` (raw integer) remains the canonical form; the alias is a convenience for display and matches the syntax accepted by `--context-max` in `create-session` / `update-session settings`.
- `aliases` — alias → concrete-value map for the field (`{"max": "fable", ...}`): pass the alias to the matching `--<field>` flag in `create-session` / `update-session settings` / `update-sessions settings` and it resolves to the value for the session's provider.
- `permission_mode_if_untrusted` — the restricted permission-mode subset that applies in an **untrusted** (or unknown-trust) project: `bypassPermissions` / `yolo` are dropped, and its `max` alias lands on the most permissive *allowed* mode (Claude Code `acceptEdits`, Codex `auto_review`). You never pass this field directly — `create-session` / `update-session` resolve and clamp `permission_mode` against it automatically (with a note) when the target project is untrusted. Project trust is a human-only decision.
- `model.values` — each accepted `--model` value with a `latest` flag (the current flagship of its family); richer per-model metadata (family, version, capabilities, retirement) lives in the `models` section.

This is the canonical "what can I set, and when does it apply" reference before calling `update-session settings` or building a preset.

## Examples

```bash
# Always-on header only
$TWICC info

# A single section
$TWICC info models
$TWICC info presets --provider claude_code
$TWICC info commands --filter "git commit"
$TWICC info commands --project /home/twidi/dev/myproj
$TWICC info agent-settings --provider codex

# Several sections composed in one shot
$TWICC info models agent-settings
$TWICC info presets models --provider claude_code
$TWICC info presets commands models agent-settings
$TWICC info all                    # same as the previous line

# Include disabled providers in the section payloads
$TWICC info models --include-disabled-providers
```

## Related commands

- `$TWICC create-session` — accepts a `--preset` name (one of the user presets listed by `info presets`) and individual setting overrides. Skill: `twicc-create-session`.
- `$TWICC update-session <session_id> settings` — same preset / override vocabulary on an existing session. Skill: `twicc-update-session`.
- `$TWICC projects` — browse projects (needed to feed `info commands --project`). Skill: `twicc-projects`.
- `$TWICC usage` — current usage quotas and cost estimates per provider. Skill: `twicc-usage`.

## How to present results

1. **No section requested** — restate `twicc_version`, then list the providers and flag any with `disabled: true`.
2. **`presets`** — show `__defaults__` first labelled as "effective defaults", then the user presets by name. Mention the provider scope explicitly when more than one provider is in the output.
3. **`commands`** — group by provider; within a provider, list in `command` order and optionally split by scope (globals first, then project-scoped). For `--filter` queries, lead with the count of matches.
4. **`models`** — group by `family`; show the latest entry first with both its `identifier` and `alias`. Surface `retirement_date` on non-latest entries. Translate the `extra` flags into a short capability bullet list when relevant ("1M context", "effort=max", …).
5. **`agent-settings`** — for each field, list values briefly. For values with a `restricted_to`, summarise the requirement ("only on opus 4.6+") rather than dumping the full list. Always surface `description` verbatim when present.
6. **Multi-section payloads** — give each requested section its own short summary in canonical order (`presets`, `commands`, `models`, `agent-settings`) rather than dumping the whole JSON.
7. You are in TwiCC — when discussing a session in the output of another command, link to it: `[link text](/project/{project_id}/session/{session_id})`.
