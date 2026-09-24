---
name: twicc-send-message
description: Send a message (and optional attachments) to an existing TwiCC session. Use when you or the user want to continue a conversation, drop a follow-up from a script, or attach files.
argument-hint: <session_id|parent> [prompt]
---

# TwiCC Send Message

Send a message to an existing session. The message is delivered as if typed from the UI. To send the same message to several sessions at once (by id or by `--spawned-by` / `--descendants` / `--annotation`), use `$TWICC send-messages` (skill: `twicc-send-messages`).

## When to use

- You or the user want to send a follow-up message to an existing session.
- A script needs to queue work into a running session.
- You want to attach files to an ongoing conversation.

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
$TWICC send-message [OPTIONS] '<SESSION_ID|parent>' ['<PROMPT>']
```

### Arguments

- `SESSION_ID` — id of the session to send to, **or** the keyword `parent` to target the session that spawned the calling agent. When using `parent`, fails with `parent_not_found` if not running inside a TwiCC agent or if the current session has no spawner.
- `PROMPT` — message text, or a path to a UTF-8 file. Optional when at least one `--attach` is given: a message made only of attachments is valid (unlike `create-session`, which always needs text). For available slash/dollar commands, use `$TWICC info commands` (skill: `twicc-info`). Over `--remote` the file is read locally; prefix an absolute path with `remote:` to read it on the remote server instead. `@@/abs/path` (or `@@{/path with spaces}`) include markers are replaced by that file's content — see `--no-expand`.

### Options

- `--attach PATH` (repeatable) — attach a file. Accepted types (sniffed by magic bytes): Claude Code: PNG, JPEG, GIF, WebP, PDF, text/plain; Codex: images only. Per-file cap: 5 MB. Per-batch cap: 100 files, 32 MB. Images are auto-resized to the provider/model's long-edge cap. Over `--remote`, prefix an absolute path with `remote:` to read it on the remote server instead.
- `--no-expand` — disable `@@` include expansion. By default an `@@/abs/path`, `@@~/path` or `@@{/path with spaces}` marker in the message (or in the file it is read from) is replaced by that file's UTF-8 content, recursively (5 levels max). Inside a file, `@@./path` and `@@../path` resolve against that file's own directory (never the cwd), so only the entry point needs an absolute path; in inline text they are an error. A missing file expands to nothing — a marker alone on its line takes the whole line with it, so includes are optional; a directory, unreadable or non-UTF-8 file is an error; `@@@@` escapes a literal `@@`; the final text is capped at 500 KB. Over `--remote`, markers resolve on the client; use `@@remote:/abs/path` for a file on the remote server.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30). If the CLI times out, the message may still get delivered.
- `--wait-reply` — keep going after delivery, until the session answers. See **Following up** below.
- `--wait-timeout N` — caps that wait, whatever ends it. Default **300 s**, which is the ceiling MCP callers are asked to respect — and an MCP client may itself give up on a tool silent that long, so over MCP pass a shorter value and come back rather than riding the default to its end. There is no way to disable it. Requires `--wait-reply`.
- `--no-reply-text` — report that the answer arrived without returning its text. Requires `--wait-reply`.
- A **pending request** ends the wait too — a tool approval or a question, which only a human can clear. It is not a slow turn: no line will ever arrive until someone clicks, so riding the budget out would buy nothing. `outcome` becomes `awaiting_user_input`, and an answer arriving in the same poll wins.

### Target discovery

To message a sibling or descendant whose id you don't know yet, use `$TWICC topology self` first and pick the target node (skill: `twicc-topology`).

## Errors

### Local (exit 1)

- `session_not_found`
- `is_subagent` — subagents cannot be messaged directly; target the parent session.
- `session_stale`
- `project_no_directory`
- `parent_not_found` — `parent` used but no TwiCC session in the ancestry, or the current session has no `spawned_by` link.
- `missing_prompt` — no `PROMPT` and no `--attach`: the message would be empty.
- `requires_wait_reply` — `--wait-timeout` or `--no-reply-text` passed without `--wait-reply`.
- `invalid_value` — `--wait-timeout` is not > 0.

### Server (exit 3)

- `awaiting_user_input` — the session holds a pending request; a CLI message cannot unblock it. Read what is being asked with `$TWICC session <id> pending-requests` — **not** `messages`, which does not carry it. A `question` you answer yourself with `answer-questions`; anything else the user must clear in the UI.
- `is_subagent`
- `provider_disabled`
- `session_not_found`
- `session_stale`
- `project_no_directory`
- `manager_busy` — transient; retry.

## Output format

`last_line` is the transcript cursor at the instant the agent took the message. It ships on every `sent` result, with or without `--wait-reply`.

```json
{"status":"sent","session_id":"...","provider":"...","project_id":"...","request_uuid":"...","last_line":124}
{"status":"validation_error","errors":[{"field":"SESSION_ID","code":"session_stale","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Message sent
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout

## Examples

```bash
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a 'Run the tests now'
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a /home/twidi/prompts/follow-up.md
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a --attach /home/twidi/screenshot.png --attach /home/twidi/report.pdf 'What do you think?'
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a --attach /home/twidi/screenshot.png
# No PROMPT: the attachment alone is the message.
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a 'Hello'
# → {"status":"sent","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"...","last_line":124}
$TWICC send-message parent 'I finished the sub-task you asked for.'
# From inside an agent: targets the session that spawned it;
```

## Delivery timing

The message is delivered immediately. The recipient picks it up based on its current state:

- **`user_turn`** — starts a new turn right away.
- **`assistant_turn`** — delivered immediately; the agent reads it as soon as possible, typically before finishing its current turn (real-time steering, mid-flight redirects, reminders).
- **`dead`** — the session is resumed automatically. A session stopped by timeout, manual kill, or any other reason will come back to life on receiving a message. This means there is no need to check a session's state before sending — `user_turn`, `assistant_turn`, and `dead` all work transparently.
- **`awaiting_user_input`** — the only case that fails (exit 3). A CLI message cannot unblock a pending request, but `session <id> answer-questions` clears a **question**, and the send then goes through. To avoid the case entirely, create orchestration sessions with `--hidden` (which enforces a non-interactive `permission_mode` and disables the question widget), making `awaiting_user_input` impossible.

## Following up

A `sent` status only means the message was handed to the agent — not that the agent has finished processing it.

**To wait for the answer, use `--wait-reply`** — one call, and no window in which the reply can slip past:

```bash
$TWICC send-message <SESSION_ID> '<TEXT>' --wait-reply [--wait-timeout N] [--no-reply-text]
```

It adds a `reply` block: `outcome`, `line_num`, `is_final`, `since_line_num`, `waited_seconds`, the answer's `text` (drop it with `--no-reply-text` when you only need the go-ahead), and `error` on `wait_failed`. `outcome` is `replied` (the message closing the turn), `awaiting_user_input` (a pending request — a tool approval or a question — which only a human can clear), `provider_error` (the provider refused: quota, outage), `ended` (the turn is over and nothing closed it — a crash, an interruption, an empty answer), `timeout`, `backend_gone`, or `wait_failed`.

**Only a line written after your message counts.** `since_line_num` is the transcript cursor, read server-side the instant the agent took it, so the *previous* turn's closing message is not returned in its place — the trap a separate wait started after the send falls into. Chaining `--wait-reply` calls is safe by construction: each one returns only once the answer is indexed, so the next cursor is always past it. A timeout is not a failure: the agent keeps working, and that cursor is what you resume from — with `$TWICC session <SESSION_ID> wait-reply --from <CURSOR>` (skill: `twicc-session`), the command that takes one. Pass the `line_num` when the ending consumed a line (`replied`, `provider_error`), the `since_line_num` otherwise. The exit code only ever says whether the message was sent.

**Known limit (Claude Code), rare and accepted.** A message sent while a Claude session is busy is queued by Claude and recorded as a queued command, not as a user message. Every wait for an answer — `--wait-reply` included, with a cursor or the default — returns the first final message past its cursor, which is then the running turn's closing message. Usually that turn read the queued message and its closing message covers it; in the rare case where the queued message runs as a turn of its own afterwards, the wait returns an answer that does not cover it. In an orchestration, message a session once it has finished, not while it works.

`since_line_num: 0` on a `replied` means no cursor came back (a backend older than the flag). The wait then started from the top of the turn, so check the answer is the one you expected.

An agent that blocks on a click **during** the turn ends the wait, with `outcome: awaiting_user_input`. Read it with `$TWICC session <SESSION_ID> pending-requests` (skill: `twicc-session`). An entry whose `kind` is `question` you can answer yourself, with `answer-questions` — the plain read already carries the question ids and options, so **never pass `--raw` to answer**. Anything else is the user's to clear in the UI; `--raw` is how you tell them what it is about, and it returns the whole payload, diff included. A target already blocked when you send is a different case: it is refused outright (exit 3, above).

Without `--wait-reply`:

- Wait later: `$TWICC session <SESSION_ID> wait-reply --from <LAST_LINE>`, with the `last_line` of the send result as the cursor. **Never follow a send without `--wait-reply` by a wait without a cursor**: the default cursor (after the last user message) could land before your message is indexed and return the previous answer. `--from` and `--since` (an ISO 8601 instant; no offset means UTC; a bare date means its midnight UTC) exist only on `session <ID> wait-reply` (both) and `sessions wait-reply` (`--since` only) — not on this command, whose `--wait-reply` reads the cursor server-side. Exit `0` on `replied` / `awaiting_user_input`, `5` on `timeout` / `ended` / `provider_error`, `2` on `backend_gone`, `1` on a refusal or `wait_failed`. Skill: `twicc-session`.
- Check state (snapshot): `process.state` from `$TWICC session <SESSION_ID>` — still working, blocked, or done? Skill: `twicc-session`.
- Read the reply: `$TWICC session <SESSION_ID> messages --tail 1`.

**Let the child talk back:** if the target is a session you spawned, tell it in the message to load the `twicc-send-message` skill and use `send-message parent '<text>'` to reply async — `parent` resolves to you via its `spawned_by` link. Loading the skill is what gives the child the `$TWICC` resolution and full invocation syntax.

## Related commands

- `$TWICC info commands [--provider <key>] [--project <PROJECT>]` — list slash / dollar commands available in the target session's scope before referencing them in the message. Skill: `twicc-info`.
- `$TWICC send-messages [SESSION_ID...] --message <text>` — send the same message to several sessions at once (same selection model as `update-sessions`). Skill: `twicc-send-messages`.
- `$TWICC create-session` — create a new session instead. Skill: `twicc-create-session`.
- `$TWICC topology self` — discover sibling and descendant session ids. Skill: `twicc-topology`.
- `$TWICC update-session <session_id> settings` — change agent settings before sending. Skill: `twicc-update-session`.
- `$TWICC session <session_id> stop` — stop the live agent. Skill: `twicc-session`.
- `$TWICC sessions --state awaiting_user_input` — find sessions blocked on user input. Skill: `twicc-sessions`.
- `$TWICC session <session_id>` — one session's row (reduced from 2026-10-01; `--full` for every field). Skill: `twicc-session`.
- `$TWICC sessions --project <PROJECT>` — find session ids. Skill: `twicc-sessions`.

## How to present results

1. On success, give a clickable link: `[link text](/project/{project_id}/session/{session_id})`.
2. On validation error, summarize the failing fields with codes.
3. On `awaiting_user_input` (exit 3), read it with `session <id> pending-requests` before reporting: a `question` you can answer yourself, and only then does the send go through. For anything else, tell the user what it is about and that they must clear it in the TwiCC UI.
