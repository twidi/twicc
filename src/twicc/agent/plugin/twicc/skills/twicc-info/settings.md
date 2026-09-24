# `info settings` — synced-settings schema

The schema of every **synced** setting: TwiCC's own configuration, not a session's. MCP tool: `mcp__twicc__info`. Read `SKILL.md` first: it resolves `$TWICC` and holds the options and always-present keys.

## Usage

```bash
$TWICC info settings
```

Instance-wide: `--provider` and `--include-disabled-providers` have no effect on it.

## Output format

The `settings` key carries `__description` + `groups`. Entries are grouped by owner under `settings.groups`; each carries `key`, `type`, `default`, `owner` and a `hint` naming the command that sets it:

- `generic` — `$TWICC settings set <key> <value>`.
- `provider` — per-provider defaults; `$TWICC settings provider <provider>`.
- `notifications` — `$TWICC settings notifications`.
- `excluded` — UI-only, no CLI path. Listed so you can tell "not settable here" from "does not exist".

Do not confuse it with `agent-settings`, which describes what a **session** accepts; this one describes the instance. Read it before `$TWICC settings set` to learn a key's type and owner.

## Examples

```bash
$TWICC info settings
$TWICC info settings agent-settings   # the instance settings and what a session accepts, in one call
```

## Related commands

- `$TWICC settings` — read and write the synced settings this schema describes (`set`, `provider`, `notifications`).
- `$TWICC info agent-settings` — what a session accepts. File: `agent-settings.md`.

## How to present results

1. Group by owner (`generic`, `provider`, `notifications`, `excluded`).
2. Per key, give `key`, `type` and `default`, and name the command its `hint` points at.
3. Say plainly that `excluded` is UI-only.
