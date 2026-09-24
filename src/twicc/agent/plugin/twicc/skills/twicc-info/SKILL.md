---
name: twicc-info
description: Read-only inspection of TwiCC — version, providers (and enabled state), slash/dollar commands, supported models, agent-settings choices/constraints, presets, and the synced-settings schema — any subset composed into one JSON. Use when you or the user need a machine-readable picture of what's available before scripting a session, picking a preset/model, or filtering commands.
argument-hint: '[presets|commands|models|agent-settings|settings|all...]'
---

# TwiCC Info

One read-only command: it always reports TwiCC's version and providers, and adds any subset of five sections. This file is the index; each section has its own file next to it.

## When to use

- You want the TwiCC version, the registered providers, and which of them the user has disabled.
- You are about to script a `create-session` / `update-session settings` call and need the effective default agent settings, the valid model identifiers / aliases, the allowed values for each setting, or a stored preset to start from.
- You want to find a slash / dollar command by substring of its literal or its description.
- You want to check which models support an agent-setting value (e.g. `effort=max`).
- You want to know which TwiCC settings exist, the type of each, and which command sets it — before `$TWICC settings set`.
- You want several of the above in one call.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Common to all sections

MCP tool: `mcp__twicc__info` (sections and options as arguments).

```bash
$TWICC info [SECTION...] [OPTIONS]
```

- `SECTION...` — 0+ of `presets`, `commands`, `models`, `agent-settings`, `settings`, plus `all` (the five). Order does not matter: output keys always come in canonical order (`presets`, `commands`, `models`, `agent-settings`, `settings`). Duplicates are ignored.
- Nothing writes to disk or to the live TwiCC.

### Options

- `--provider <key>` — narrow every requested **provider-keyed** section to one provider (`claude_code`, `codex`, …). No effect on `settings` (instance-wide). Naming a provider **bypasses** the disabled-provider filter for that one: its data is returned even when disabled. The top-level `providers` dict always lists every registered provider.
- `--project <PROJECT>` — directory path or project id (**drop the leading dash** on ids). **Only consumed with `commands`**; passing it without `commands` is an error. See `commands.md`.
- `--filter "TOKENS"` — token search for `commands`. **Only consumed with `commands`**. See `commands.md`.
- `--include-disabled-providers` — flips the disabled-provider filter on the provider-keyed section payloads (not `settings`): disabled providers are listed with the enabled ones. Does not change the top-level `providers` dict (always exhaustive).

### Output format

JSON on stdout. The always-present keys come first; each requested section adds one top-level key named exactly as the section. In `presets`, `commands`, `models`, `agent-settings` the data is keyed by provider identifier; `settings` describes the instance and carries `__description` + `groups` instead.

Always-present keys (a call with no section returns only these):

```json
{
  "twicc_version": "1.7.0", "twicc_executable": "uvx twicc",
  "providers": {
    "claude_code": {"identifier": "claude_code", "name": "Claude Code", "disabled": false, "default": true, "orchestration": true},
    "codex":       {"identifier": "codex",       "name": "Codex",       "disabled": true,  "default": false, "orchestration": false}
  },
  "available_info_arguments": {}
}
```

- `twicc_version` — the installed TwiCC version.
- `twicc_executable` — shell command that re-invokes the same TwiCC distribution (e.g. `uvx twicc`, `twicc`, `uv run --directory <dir> run.py`, an absolute path, …). Suffix it with sub-command args to reach this TwiCC instance from anywhere.
- `providers` — dict keyed by provider identifier; each entry:
  - `identifier` — same as the dict key. `name` — human-readable label.
  - `disabled` — `true` when the user disabled the provider; `create-session` and similar calls refuse it.
  - `default` — `true` for the user's default provider.
  - `orchestration` — `true` when agents may pick the provider **on their own** while orchestrating sessions (see `twicc-orchestration`). Soft preference: an explicit user request for an `orchestration: false` provider still works if it is not `disabled`. Always `false` for disabled providers.
- `available_info_arguments` — discovery dict for the positional sections of this command (`presets`, `commands`, `models`, `agent-settings`, `settings`, `all`). The reserved `__description` key explains the dict; every other key is an accepted argument with a one-line summary of what it adds.

## Sections

**ALWAYS READ THE SECTION'S FILE BEFORE YOU USE ITS PAYLOAD.** It holds the section's payload shape, key meanings, examples, and any presentation beyond the common rules of this index. The files sit next to this `SKILL.md`.

| Section | Purpose | File |
|---|---|---|
| `presets` | Effective defaults (`__defaults__`) and user presets, per provider. | `presets.md` |
| `commands` | Slash / dollar commands, per provider, global or project-scoped, filterable. | `commands.md` |
| `models` | Supported models: identifier, alias, latest, enabled, retirement, capability flags. | `models.md` |
| `agent-settings` | Accepted values, aliases and model restrictions of each session setting. | `agent-settings.md` |
| `settings` | Schema of TwiCC's synced (instance) settings and the command that sets each. | `settings.md` |

## Examples

```bash
$TWICC info                        # always-present keys only
$TWICC info models agent-settings
$TWICC info presets models --provider claude_code
$TWICC info presets commands models agent-settings
$TWICC info all                    # the previous line plus `settings`
$TWICC info models --include-disabled-providers
```

## Related commands

- `$TWICC create-session` — `--preset` (a user preset from `info presets`) and individual setting overrides. Skill: `twicc-create-session`.
- `$TWICC update-session <session_id> settings` — same preset / override vocabulary on an existing session. Skill: `twicc-update-session`.
- `$TWICC usage` — current usage quotas and cost estimates per provider. Skill: `twicc-usage`.

## How to present results

1. **No section requested** — restate `twicc_version`, then list the providers and flag any with `disabled: true`.
2. **Several sections** — one short summary per requested section, in canonical order, following each section file; never dump the whole JSON.
3. You are in TwiCC — when discussing a session in the output of another command, link to it: `[link text](/project/{project_id}/session/{session_id})`.
