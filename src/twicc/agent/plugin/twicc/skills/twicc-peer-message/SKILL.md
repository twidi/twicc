---
name: twicc-peer-message
description: Re-check the status of a message sent to a peer TwiCC instance with peer-send — still pending the remote user's approval, delivered, done, refused, or failed.
argument-hint: <message_id>
---

# TwiCC Peer Message

Check the current status of one outbound peer message. Delivery always goes through the remote user's approval: a message stays `pending` until they deliver it to one of their sessions (`delivered`), deal with it themselves without any agent (`done`), or turn it down (`refused`).

## When to use

- You sent a message with `peer-send` and want to know whether the remote user has acted on it.
- The user asks "did David's instance get my message?".

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

```bash
$TWICC peer-message <MESSAGE_ID>
```

### Arguments

- `MESSAGE_ID` — the `message_id` returned by `peer-send` (`pm_...`).

## Output format

```json
{"id": 12, "message_id": "pm_1a2b3c4d5e6f7a8b", "peer_id": "peer_a1b2c3d4", "direction": "out",
 "thread_id": "pm_parent000000001", "reply_to": "pm_parent000000001",
 "reply_to_ref": {"message_id": "pm_parent000000001", "title": "Original API question", "direction": "in", "status": "delivered"},
 "reply_target": "session-receiver", "title": "API changes recap", "status": "pending", "error": "",
 "text_preview": "Here is the recap...",
 "attachments_meta": [{"kind": "image", "media_type": "image/png", "bytes": 48211}],
 "origin": {"sent_at": "2026-07-24T12:00:00+00:00"},
 "recipient_note": "", "origin_session_id": "abc123", "delivered_to_session_id": null,
 "origin_session": {"id": "abc123", "title": "Front revamp", "project_id": "-home-me-app"},
 "delivered_to_session": null,
 "created_at": "...", "resolved_at": null, "purged": false}
```

- `status` — `pending` (awaiting the remote user), `delivered` (handed to one of their agents), `done` (the remote USER dealt with it themselves — no agent received it), `refused`, or `failed`. For `failed`, the sender received no confirmed acceptance; the peer may still have stored the message. See `error` for the local failure detail. The status records the remote user's FIRST decision; they may change it later without this side being told.
- `thread_id` — the local thread root id. Its complete local key includes `peer_id`; it never crosses the wire.
- `reply_to` — the answered message id, or `""` for a root message.
- `reply_to_ref` — summary of the resolved parent (`id`, `message_id`, `title`, `direction`, `status`, `author`), or `null`.
- `latest_reply_author` — `human` or `agent` when the message received replies (the most recent one decides), else `null`. For your outbound message, that is who answered on the peer's side.
- `reply_target` — id of the parent's local-end session, or `null`; it is not a delivery action or eligibility promise.
- `title` — the required subject the send carried.
- `origin_session` / `delivered_to_session` — the local session at each end (`null` when there is none), with its title read live. The peer receives neither.
- **Wire boundary** — the peer-message wire carries `message_id`, `title`, `reply_to`, `origin.sent_at`, and `payload`. A root message carries `reply_to` as `""`. `thread_id`, `reply_to_ref`, and `reply_target` are local serialization values, not wire fields.

### Exit codes

- `0` — Found
- `1` — Unknown message_id
- `64` — Bad CLI usage

## Examples

```bash
$TWICC peer-message pm_1a2b3c4d5e6f7a8b
# → {"message_id":"pm_1a2b3c4d5e6f7a8b","status":"delivered",...}
```

## Related commands

- `$TWICC peer-send <peer> '<title>' '<text>'` — send a titled message to a peer instance. Skill: `twicc-peer-send`.
- `$TWICC peers` — list peer instances. Skill: `twicc-peers`.

## How to present results

1. Translate the status for the user: `pending` = "their user hasn't reviewed it yet"; `delivered` = "handed to one of their agents — something may follow"; `done` = "their user dealt with it themselves, no agent received it — nothing more will come through this status; if they answered, the answer arrived as a peer message in your user's inbox"; `refused` = their decision; `failed` = "this sender received no confirmed acceptance, but the peer may still have stored it".
2. There is no push on resolution — re-run this command when the user asks again.
