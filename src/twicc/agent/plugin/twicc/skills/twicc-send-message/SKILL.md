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

### Target discovery

To message a sibling or descendant whose id you don't know yet, use `$TWICC topology self` first and pick the target node (skill: `twicc-topology`).

## Sender header

When the caller is itself a TwiCC session (the CLI run from inside an agent, or the `mcp__twicc__send_message` tool), the recipient receives the text under a sender header:

```
:: message from your spawned session `<caller_id>` ("**<caller title>**")

<your text>
```

The header is a single line; nothing wraps your text, which stays plain markdown below it. The relation wording depends on the spawn-tree link between caller and recipient: `your spawned session` (the recipient spawned you), `your parent session` (you spawned the recipient), `a sibling session` (same spawner), `another session` (no relation). The `("<title>")` part is omitted when the caller has no title yet. No header when a human runs the command from a plain shell, or on a self-send. Never write this header yourself — it is added automatically.

Symmetrically: an incoming message that opens with this header comes from another session, not from the user.

## Errors

### Local (exit 1)

- `session_not_found`
- `is_subagent` — subagents cannot be messaged directly; target the parent session.
- `session_stale`
- `project_no_directory`
- `parent_not_found` — `parent` used but no TwiCC session in the ancestry, or the current session has no `spawned_by` link.
- `missing_prompt` — no `PROMPT` and no `--attach`: the message would be empty.

### Server (exit 3)

- `awaiting_user_input` — the session has a pending dialog in the UI; a CLI message cannot unblock it. The user must click in the UI first. Fetch what's being asked with `$TWICC session <id> messages --tail 1`.
- `is_subagent`
- `provider_disabled`
- `session_not_found`
- `session_stale`
- `project_no_directory`
- `manager_busy` — transient; retry.

## Output format

```json
{"status":"sent","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"validation_error","errors":[{"field":"SESSION_ID","code":"session_stale","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Message sent
- `1` — Local validation error
- `2` — TwiCC server not running
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout
- `64` — Bad CLI usage

## Examples

```bash
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a 'Run the tests now'
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a /home/twidi/prompts/follow-up.md
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a --attach /home/twidi/screenshot.png --attach /home/twidi/report.pdf 'What do you think?'
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a --attach /home/twidi/screenshot.png
# No PROMPT: the attachment alone is the message.
$TWICC send-message 4a8352fb-1674-41c0-8a85-0a5a3e4e623a 'Hello'
# → {"status":"sent","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
$TWICC send-message parent 'I finished the sub-task you asked for.'
# From inside an agent: targets the session that spawned it;
```

## Delivery timing

The message is delivered immediately. The recipient picks it up based on its current state:

- **`user_turn`** — starts a new turn right away.
- **`assistant_turn`** — delivered immediately; the agent reads it as soon as possible, typically before finishing its current turn (real-time steering, mid-flight redirects, reminders).
- **`dead`** — the session is resumed automatically. A session stopped by timeout, manual kill, or any other reason will come back to life on receiving a message. This means there is no need to check a session's state before sending — `user_turn`, `assistant_turn`, and `dead` all work transparently.
- **`awaiting_user_input`** — the only case that fails (exit 3). A CLI message cannot unblock a pending UI dialog. To avoid this entirely, create orchestration sessions with `--hidden` (which enforces a non-interactive `permission_mode` and disables the question widget), making `awaiting_user_input` impossible.

## Following up

A `sent` status only means the message was handed to the agent — not that the agent has finished processing it.

- Wait for a state: `$TWICC process <SESSION_ID> wait <STATE>... --timeout <N>` blocks until the agent reaches one of the listed states (`starting`, `assistant_turn`, `awaiting_user_input`, `user_turn`, `dead`). To wait for the end of the turn this message triggers, add `--transition` so it doesn't match the idle `user_turn` the session was already in before the message: `wait user_turn --transition --timeout <N>`. Skill: `twicc-process`.
- Check state (snapshot): `$TWICC process <SESSION_ID>` — still working, blocked, or done? Skill: `twicc-process`.
- Read the reply: `$TWICC session <SESSION_ID> messages --tail 1`. Skill: `twicc-session`.

**Let the child talk back:** if the target is a session you spawned, tell it in the message to load the `twicc-send-message` skill and use `send-message parent '<text>'` to reply async — `parent` resolves to you via its `spawned_by` link. Loading the skill is what gives the child the `$TWICC` resolution and full invocation syntax.

## Related commands

- `$TWICC info commands [--provider <key>] [--project <PROJECT>]` — list slash / dollar commands available in the target session's scope before referencing them in the message. Skill: `twicc-info`.
- `$TWICC send-messages [SESSION_ID...] --message <text>` — send the same message to several sessions at once (same selection model as `update-sessions`). Skill: `twicc-send-messages`.
- `$TWICC create-session` — create a new session instead. Skill: `twicc-create-session`.
- `$TWICC topology self` — discover sibling and descendant session ids. Skill: `twicc-topology`.
- `$TWICC update-session <session_id> settings` — change agent settings before sending. Skill: `twicc-update-session`.
- `$TWICC process <session_id> stop` — stop the live agent. Skill: `twicc-process`.
- `$TWICC processes --state awaiting_user_input` — find sessions blocked on user input. Skill: `twicc-processes`.
- `$TWICC session <session_id>` — full session metadata. Skill: `twicc-session`.
- `$TWICC sessions --project <PROJECT>` — find session ids. Skill: `twicc-sessions`.

## How to present results

1. On success, give a clickable link: `[link text](/project/{project_id}/session/{session_id})`.
2. On validation error, summarize the failing fields with codes.
3. On `awaiting_user_input` (exit 3), tell the user they must click in the TwiCC UI first — a CLI message cannot unblock the pending dialog.
