---
name: twicc-share
description: Create and manage public read-only links for session transcripts or bookmarked artifacts. Use when you or the user want to create or manage a link, including a new link for a peer message.
---

# Sharing sessions and artifacts

A share is a public, read-only capability URL. Two global settings gate this
surface, per kind: `allowAgentSessionShares` and `allowAgentArtifactShares`,
both OFF by default. **Never enable these settings yourself** (you could,
via `twicc settings set` — that is a property of the trust model, not an
invitation): only the user flips them, in Settings → Sharing.

## When to use

- You or the user want a public read-only link to a session transcript.
- You or the user want a public read-only link to a bookmarked artifact.
- You or the user want to list, inspect, update, revoke, restore, delete, or propagate share links.
- You need a new share URL to send through the peer system.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### Scope

You may share **your own session or any session in your spawn subtree** — sessions you spawned directly or through intermediaries, at any depth — and **an artifact whose bookmark belongs to such a session**. You may manage (update / unrevoke / delete / propagate) only shares **created by agents in your own spawn subtree**. You may **revoke** any share of an enabled kind, whatever created it — un-publishing is always safe.

### List

```bash
$TWICC share [--kind session|artifact] [--session ID|self|parent] [--project PROJECT] [--include-revoked] [--limit N] [--offset N] [--paginated]
```

- `--kind session|artifact` — filter by share kind.
- `--session ID|self|parent` — filter both session and artifact shares by their owning session.
- `--project PROJECT` — filter both kinds by project, with worktree-aware scope.
- `--include-revoked` — include revoked rows.
- `--limit N` — maximum rows, default 50.
- `--offset N` — rows to skip, default 0.
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`, so you know whether another page follows instead of guessing from the page size. Without an explicit `--limit` the page size becomes **50**. **Before 2026-09-15 the flag is opt-in and a call without it is unchanged; from that date the envelope is the only shape and the flag is an accepted no-op.** Passing it works on both sides.

### Show

```bash
$TWICC share show <SHARE_ID>
```

Returns one share, including its `url` when the setting for its kind is enabled.

### Create a session share

```bash
$TWICC share create session <SESSION_ID|self|parent> [--label L] [--password P] [--expires ISO] [--live|--frozen] [--max-display MODE] [--include-subagents|--no-subagents] [--title T] [--show-title|--no-title] [--timeout N]
```

- With no `--live`/`--frozen` flag your share is a **frozen snapshot** at the
  current line; pass `--live` explicitly for a live-following link. Use
  `share propagate <SHARE_ID>` to re-freeze a snapshot at the newest content.
- `--max-display` accepts `conversation`, `simplified`, `normal`.
- `parent` resolves, then fails the scope test (`out_of_scope`): you cannot
  share the session that spawned you.
- `--label L` — owner-only label; use the peer convention below when applicable.
- `--password P` — set an initial viewer password.
- `--expires ISO` — set an ISO 8601 expiry.
- `--title T` — override the public title when title display is enabled.
- `--show-title` / `--no-title` — show a title or a generic viewer label.
- `--timeout N` — seconds to wait for the server, default 30.

### Create an artifact share

An artifact share requires a bookmark first (see the twicc-artifacts skill):

```bash
$TWICC artifacts bookmark <SESSION_ID|self|parent> <PATH> --name "..."
$TWICC share create artifact <BOOKMARK_ID> [--label L] [--password P] [--expires ISO] [--title T] [--show-title|--no-title] [--timeout N]
```

- `BOOKMARK_ID` — id returned by `artifacts bookmark` or listed by `artifacts`.
- `--label L` — owner-only label; use the peer convention below when applicable.
- `--password P` — set an initial viewer password.
- `--expires ISO` — set an ISO 8601 expiry.
- `--title T` — override the bookmark name shown to viewers.
- `--show-title` / `--no-title` — show a title or a generic viewer label.
- `--timeout N` — seconds to wait for the server, default 30.

### Get the URL

`share create …` returns `{"status": "created", "share_id": "shr_…"}` — no
token, no URL. Then:

```bash
$TWICC share show <SHARE_ID>
```

Read the `url` field from the show result.

### Share with a peer

When you create a share in order to send it through the peer system, set
`--label "peer <PEER_NAME>"` — the peer's local name from `twicc peers`, or
its `peer_…` id when it has no name — so the user sees from their share list
which link went to which peer. Then send the URL with `twicc peer-send`.

### Passwords

You can set or replace a share password (`--password` on create or update),
never clear one — clearing is the user's (owner UI or their own CLI).
Replacing a password invalidates every existing viewer grant: new page loads
and new live connections need the new password. A viewer already streaming a
live share is NOT cut off by a password change — use `revoke` for an
immediate cutoff.

### Update

```bash
$TWICC share update <SHARE_ID> [--label L] [--password P] [--expires ISO] [--timeout N]
```

- `--label L` — replace the owner-only label.
- `--password P` — set or replace the viewer password; agents cannot clear it.
- `--expires ISO` — set an ISO 8601 expiry; the CLI normalizes an empty value to a clear.
- `--timeout N` — seconds to wait for the server, default 30.

### Revoke

```bash
$TWICC share revoke <SHARE_ID> [--timeout N]
```

Revoke any share of an enabled kind. `--timeout N` defaults to 30 seconds.

### Unrevoke

```bash
$TWICC share unrevoke <SHARE_ID> [--timeout N]
```

Restore an in-scope agent-created share. `--timeout N` defaults to 30 seconds.

### Delete

```bash
$TWICC share delete <SHARE_ID> [--timeout N]
```

Delete an in-scope agent-created share and its snapshot directory. `--timeout N` defaults to 30 seconds.

### Propagate

```bash
$TWICC share propagate <SHARE_ID> [--timeout N]
```

Advance an in-scope frozen share to current content. `--timeout N` defaults to 30 seconds.

## Errors

### Local (exit 1)

- `session_context_not_found` — `self` or `parent` has no current TwiCC session context.
- `parent_not_found` — `parent` was used from a root session.

### Server (exit 3)

- `agent_sharing_disabled` — the kind's setting is off. **Relay to the user**
  (only they can enable it in Settings → Sharing); never retry, never enable
  the setting yourself.
- `share_host_unset` — no share host is configured. **Relay to the user**
  (Settings → Sharing); never retry.
- `out_of_scope` — on create: the target session is outside your spawn
  subtree. On update/unrevoke/delete/propagate: the share was created outside
  your subtree (or by the user).
- `field_forbidden` — the payload carried a key, type or value reserved to
  human surfaces (for example clearing a password).
- `display_mode_forbidden` — the requested display ceiling is not agent-available.
- `invalid` — an expiry is not a valid ISO 8601 datetime.
- `not_found` — the requested target session, bookmark, or share does not exist.
- `snapshot_failed` — the artifact snapshot could not be created or refreshed.
- `not_snapshot` — `propagate` was used on a live session share.

## Output format

### List

```json
[
  {
    "id": "shr_abc123",
    "kind": "session",
    "label": "peer workstation",
    "status": "active",
    "session_id": "session-1",
    "token": "…",
    "url_path": "/share/…/",
    "url": "https://share.example.com/share/…/",
    "options": {"mode": "snapshot", "frozen_at_line": 42},
    "view_count": 0,
    "created_by": {"kind": "agent", "session": {"id": "session-1", "title": "Builder", "project_id": "-project"}}
  }
]
```

### Show

The show command returns one object with the same fields as a list row.

- With a kind's setting off, `share` (list) and `share show` still answer but
  with `token`/`url`/`url_path` null and `"redacted": true` — tell the user a
  share exists and which setting unlocks it.

### Mutations

```json
{"status": "created", "share_id": "shr_abc123", "request_uuid": "…"}
{"status": "updated", "share_id": "shr_abc123", "request_uuid": "…"}
{"status": "deleted", "share_id": "shr_abc123", "request_uuid": "…"}
```

Creation returns a `share_id`, not a token or URL. Use `share show` next.

On rejection:

```json
{"status": "rejected", "errors": [{"field": "settings", "code": "agent_sharing_disabled", "message": "…"}], "request_uuid": "…"}
```

### Exit codes

- `0` — Success
- `1` — Local validation error
- `2` — TwiCC server not running or remote misuse
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC share --kind session --limit 20 --offset 0
$TWICC share --session self
$TWICC share show shr_abc123
$TWICC share create session self --label "peer workstation" --frozen --timeout 30
$TWICC artifacts bookmark self report.md --name "Report"
$TWICC share create artifact 12 --label "peer workstation" --timeout 30
$TWICC share update shr_abc123 --password "new secret" --timeout 30
$TWICC share revoke shr_abc123 --timeout 30
$TWICC share unrevoke shr_abc123 --timeout 30
$TWICC share propagate shr_abc123 --timeout 30
$TWICC share delete shr_abc123 --timeout 30
```

## Related commands

- `$TWICC artifacts [OPTIONS]` — list bookmarks or create the artifact prerequisite. Skill: `twicc-artifacts`.
- `$TWICC peers` — list peers and find the peer label name. Skill: `twicc-peers`.
- `$TWICC peer-send <PEER> <TITLE> <PROMPT>` — send the resolved share URL to a peer. Skill: `twicc-peer-send`.
- `$TWICC session <SESSION_ID>` — inspect a target session. Skill: `twicc-session`.

## How to present results

1. State the share kind, target, status, and frozen/live mode.
2. For creation, show the `share_id`, then call `share show` and show its `url`.
3. If a row is redacted, state that it exists and name the disabled kind setting.
4. Relay `agent_sharing_disabled` and `share_host_unset` to the user without retrying or changing settings.
