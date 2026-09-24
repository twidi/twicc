# `sessions get <SESSION_ID>...` — batch lookup by id

Fetch the rows of known session ids in one call. MCP tool: `mcp__twicc__sessions_get`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC sessions get <SESSION_ID>... [--slim | --full] [--paginated]
```

- One entry per id, in input order (duplicates collapsed).
- No filter flags: every session you name is returned — subagents, archived and hidden included.
- `--slim` / `--full` — same projection as the listing, same default, same 2026-10-01 date (file: `list.md`). Placeholders take them too, so a batch lookup keeps one shape.
- `--paginated` — wrap the result in `{"items": [...]}`, **no `pagination`**: one entry per id asked, nothing to page. **Before 2026-10-01 the result is a bare array and the flag opts in; from that date `{"items": [...]}` is the only shape and the flag an accepted no-op.** Until then a call without it prints a one-line notice on stderr (RPC envelope: `warnings`; never on MCP) — a flagless call prints two: this one, then the slim one.

## Output format

Each entry is a listing row (fields: `list.md`) plus a `known` boolean. When `known: false`, the session fields are `null` (`artifacts_dir` included: no session owns a folder) — but not `process`: a live process can exist before its session is indexed, so a live block on an unknown id is a session that just started.

Before 2026-10-01 (without `--paginated`), a bare array:

```json
[
  {"id": "abc123-def456", "title": "Implement user authentication", ..., "known": true},
  {"id": "typo-or-unknown", "title": null, ..., "known": false}
]
```

From 2026-10-01, or with `--paginated` now, the same entries under `items` (no `pagination`):

```json
{"items": [
  {"id": "abc123-def456", "title": "Implement user authentication", ..., "known": true},
  {"id": "typo-or-unknown", "title": null, ..., "known": false}
]}
```

## Examples

```bash
$TWICC sessions get abc123-def456
$TWICC sessions get abc123 def456 ghi789
```

## Related commands

- `$TWICC sessions` — list and filter sessions. File: `list.md`.
- `$TWICC session <ID>` — one session's row. Skill: `twicc-session`.

## How to present results

1. Show session title, date, and message count.
2. Flag `known: false` entries as unknown (typo or already cleaned up).
3. Include cost and model only if explicitly asked.
