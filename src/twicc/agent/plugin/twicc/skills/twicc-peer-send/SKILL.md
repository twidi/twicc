---
name: twicc-peer-send
description: Send a titled message (subject + text + optional attachments) to a peer TwiCC instance — another user's installation. Delivery requires the REMOTE user's approval; the message must be fully self-contained.
argument-hint: <peer> <title> <prompt>
---

# TwiCC Peer Send

Send a message to a peer instance — another user's TwiCC installation, paired by the two humans beforehand. No confirmation is needed on this side, but nothing reaches the remote agent directly: the REMOTE user reads the message first and delivers it to an agent, deals with it themselves, or refuses it. The receiving side shares **no memory or context** with you — write the message fully self-contained (who you are, what project, what you need).

## When to use

- The user asks to send something to a colleague's instance ("send David's instance a recap of the API changes").
- You need to hand off information, a screenshot, or a document to an agent working on another user's machine.
- When a peer message needs a newly created share link, use the `twicc-share` skill and label the new link `peer <PEER_NAME>`; forwarding an existing share URL needs no relabelling.

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
$TWICC peer-send [OPTIONS] '<PEER>' '<TITLE>' '<PROMPT>'
```

### Arguments

- `PEER` — the peer's id (`peer_...`) or its exact local name — resolve with `$TWICC peers`.
- `TITLE` — required subject, shown prominently to the remote user (inbox, notification, delivery envelope). Write it like an email subject: short, specific, self-contained — aim for ~60 characters. Hard cap 100 characters (longer is rejected, not truncated). Always inline text, never a file path; newlines are flattened.
- `PROMPT` — message text, or a path to a UTF-8 file. Only the title, text and attachments travel — the remote agent has none of your context.

### Options

- `--reply-to MESSAGE_ID` — answer a message of this peer; copy the id from the header of the delivered peer message. The id is case-sensitive and can name an inbound or outbound message in any status.
- `--attach PATH` (repeatable) — attach a file: PNG, JPEG, GIF, WebP, PDF, text/plain; 5 MB per file, 100 files / 32 MB per batch. Local path or base64 data URI.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30).

## Errors

### Local (exit 1)

- Unknown peer — no peer matches by id or exact name.
- Peer `broken` — the peer revoked the relationship or is unreachable; only the user can fix it in Settings › Peers.
- Peer still pending — the pairing has not been accepted yet.
- Invalid title — empty, or over 100 characters (`empty_title` / `title_too_long`); rewrite it shorter, it is never truncated for you.
- `invalid_reply_to` — the reply id does not match the peer-message identifier grammar.
- `unknown_reply_to` — no message with this id exists for the selected peer.

### Server (exit 3)

- `not_found` / `peer_broken` / `not_active` — same conditions, re-checked server-side.
- `invalid_reply_to` / `unknown_reply_to` — the reply id is malformed or does not exist for this peer, re-checked server-side.
- `unreachable` — the peer instance could not be reached over the network.
- `send_failed` — the peer answered with an error.

Every server-side failure surfaces as `rejected` (exit 3); the distinction is in the error `code`.

## Output format

```json
{"status": "sent", "message_id": "pm_1a2b3c4d5e6f7a8b", "peer_id": "peer_a1b2c3d4", "peer_status": "pending", "request_uuid": "..."}
{"status": "rejected", "errors": [{"field": "peer", "code": "peer_broken", "message": "..."}], "request_uuid": "..."}
```

`peer_status` is the remote delivery state: it stays `pending` until the remote user delivers the message to an agent (`delivered`), deals with it themselves (`done`), or refuses it (`refused`).

### Exit codes

- `0` — Sent (stored on the peer, awaiting their user's review)
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC peer-send David 'TwiCC: /peer API landed' 'Recap from Stephane'\''s instance, project TwiCC: the /peer API landed today; endpoints are documented in docs/plans/. Nothing needed on your side yet.'
$TWICC peer-send peer_a1b2c3d4 --attach /tmp/front-screenshot.png 'Layout bug screenshot' 'Screenshot of the layout bug we discussed — top bar overlaps at <1200px.'
$TWICC peer-send David --reply-to pm_1a2b3c4d5e6f7a8b 'Follow-up on the API recap' 'One correction to the recap: the endpoint now returns 202.'
# → {"status":"sent","message_id":"pm_...","peer_id":"peer_a1b2c3d4","peer_status":"pending",...}
```

## Following up

`sent` only means the message is stored on the peer, pending THEIR user's review — there is no push on resolution.

- Re-check later: `$TWICC peer-message <MESSAGE_ID>` (skill: `twicc-peer-message`) — `pending`, `delivered`, or `refused`.

## Related commands

- `$TWICC peers` — resolve the peer's id or exact name first. Skill: `twicc-peers`.
- `$TWICC peer-message <message_id>` — re-check an outbound message's status. Skill: `twicc-peer-message`.

## How to present results

1. On success, tell the user the message awaits the remote user's approval — delivery is not immediate.
2. On `peer_broken`, tell the user to check the relationship in Settings › Peers — agents cannot manage peer relationships.
