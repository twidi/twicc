---
name: twicc-send-messages
description: Send the SAME message (and optional attachments) to several TwiCC sessions, selected by id and/or --spawned-by/--descendants/--siblings/--annotation. Use to broadcast a steering instruction, status request, or correction to a batch (e.g. every worker in an orchestration), or for a worker to message peers with --siblings self.
argument-hint: '[SESSION_ID...] [--message <text>] [--spawned-by X|--descendants X|--siblings X] [--annotation ...] [--attach PATH...]'
---

# TwiCC Send Messages

Batch sibling of `send-message`: delivers the SAME message to every targeted session in one call, with the same selection model as `update-sessions`. For a single recipient (or to reply to your `parent`), use `send-message` (skill: `twicc-send-message`).

## When to use

- Broadcast a steering correction to a set of children: "the spec changed, the API base is now /v2 — re-check your work."
- Ask every worker for status: "what's your progress and ETA?"
- Graceful wrap-up (vs the hard kill of `sessions stop`): "finish your current step, write your report, and reply DONE."
- Push a uniform follow-up to a fan-out group after delegating the same task.
- **Talk to your peers** (worker → worker): `--siblings self` broadcasts to the other sessions your parent spawned, you excluded — e.g. "I finished the auth module, you can wire against `/v2/login` now." This is the direct peer channel; you do not have to route everything back through the manager.

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
$TWICC send-messages [SESSION_ID...] [--message <TEXT>] [--attach PATH...] [--spawned-by X|--descendants X|--siblings X] [--annotation ...] [--wait-reply [--wait-first] [--wait-timeout N] [--no-reply-text]]
```

Selection is identical to `update-sessions` (skill: `twicc-update-sessions`): a positional `SESSION_ID...` list merged (union, explicit first) with the scope filters. `self` means the current session.

- `SESSION_ID...` — recipients; optional if a filiation scope is given.
- `--message TEXT` — message text, or a path to a UTF-8 file whose content is the message. **Required unless at least one `--attach` is given**: a message made only of attachments is valid. Same text for every recipient. Over `--remote` the file is read locally; prefix an absolute path with `remote:` to read it on the remote server instead. `@@/abs/path` (or `@@{/path with spaces}`) include markers are replaced by that file's content — see `--no-expand`.
- `--no-expand` — disable `@@` include expansion. By default an `@@/abs/path`, `@@~/path` or `@@{/path with spaces}` marker in the message (or in the file it is read from) is replaced by that file's UTF-8 content, recursively (5 levels max). Inside a file, `@@./path` and `@@../path` resolve against that file's own directory (never the cwd), so only the entry point needs an absolute path; in inline text they are an error. A missing file expands to nothing — a marker alone on its line takes the whole line with it, so includes are optional; a directory, unreadable or non-UTF-8 file is an error; `@@@@` escapes a literal `@@`; the final text is capped at 500 KB. Over `--remote`, markers resolve on the client; use `@@remote:/abs/path` for a file on the remote server.
- `--attach PATH` (repeatable) — attach a file to every message. **Validated per session against its provider** (Claude Code: PNG/JPEG/GIF/WebP/PDF/text up to 5 MB; Codex: images only), so a file one provider rejects yields a per-id `validation_error` while the others still receive it. Local path or a `data:<mime>;base64,...` URI for remote/API callers. Over `--remote`, prefix an absolute path with `remote:` to read it on the remote server instead.
- `--spawned-by <ID|self>` / `--descendants <ID|self>` — also target children / proper descendants. `parent` is **not** supported (use `send-message parent`). Mutually exclusive.
- `--siblings <ID|self>` — also target the siblings of the given session: the *other* sessions spawned by the same parent, **reference always excluded**. `self` broadcasts to your peers (the canonical worker → worker channel). `parent` is **not** supported. Mutually exclusive with `--spawned-by` / `--descendants`. Note `--spawned-by parent` (the same set but including yourself) is **not** available here, so `--siblings self` is the way to reach your peers from this command.
- `--annotation KEY[OP]VALUE` — narrow the filiation scope by annotation; repeatable, AND-combined; requires a filiation scope; does not filter explicit ids. Same syntax as `twicc sessions --annotation` (skill: `twicc-sessions`).
- `--timeout SECONDS` — wall-clock budget for the whole batch (default 30; the sends run in parallel).
- `--wait-reply` — keep going until the recipients answer. Adds a `reply` block per entry, the same shape `send-message --wait-reply` returns, and `replied` / `all_replied` to the summary. Only entries that reached `sent` are waited on: a rejected send has no turn to answer it.
- `--wait-first` / `--wait-all` — `--wait-all` (default) waits until EVERY recipient answers; `--wait-first` stops at the first one to answer or block, leaving the rest `outcome: pending`. A recipient whose turn crashed or was refused never ends a `--wait-first` batch: the others may still answer, and an answer is what was asked for. Requires `--wait-reply`.
- A recipient hitting a **pending request** — a tool approval or a question — ends its own wait with `outcome: awaiting_user_input`. There is nothing to wait for: it stays blocked until someone clears it, and an answer arriving in the same poll wins. Read it with `session <id> pending-requests`; a `question` you can answer yourself with `answer-questions`.
- `--wait-timeout N` — caps the wait, whatever ends it. Default **300 s**, a wall-clock budget for the whole batch (they are waited on together, not one after another), which is also the ceiling MCP callers are asked to respect. Requires `--wait-reply`.
- `--no-reply-text` — report that the answers arrived without returning their text; each `line_num` is still there to fetch one. Requires `--wait-reply`.

If neither ids nor a filiation scope is given, the command errors (exit 1). An empty resolved set is not an error: `results` is `{}` and the command exits 0.

### Delivery timing

Each recipient picks the message up based on its current state, as with `send-message` (skill: `twicc-send-message`):

- **`user_turn`** — starts a new turn right away.
- **`assistant_turn`** — the agent reads it as soon as possible, typically before finishing its current turn.
- **`dead`** — the session is resumed automatically, so one batch can restart many stopped sessions at once.
- **`awaiting_user_input`** — that recipient alone is `rejected` with code `awaiting_user_input`; the others still receive the message.

So there is no need to check the recipients' states before sending.

## Errors

Argument-level problems fail the whole command (exit 1, plain-text on stderr): empty/unreadable `--message`, neither `--message` nor `--attach`, bad `--timeout`, two of `--spawned-by`/`--descendants`/`--siblings` together, `parent` scope (on `--spawned-by`/`--descendants`, or any value on `--siblings`), `--annotation` without a filiation scope, neither ids nor scope.

Local (exit 1), before anything is sent: `requires_wait_reply` (a wait modifier without `--wait-reply`) and `invalid_value` (`--wait-timeout` not > 0).

Per-session problems never fail the batch — reported in `results[<id>]` with `status` `validation_error` (local lookup: `session_not_found`, `is_subagent`, `session_stale`, `project_no_directory`; or an attachment its provider rejects) or `rejected` (server: `awaiting_user_input` — the session has a pending UI dialog a CLI message can't unblock; `manager_busy` — transient, retry; `provider_disabled`). Same vocabulary as `twicc-send-message`.

## Output format

A single object keyed by session_id, plus a summary:

```json
{
  "summary": {"total": 3, "succeeded": 2, "failed": 1, "all_succeeded": false},
  "results": {
    "abc123": {"status": "sent", "session_id": "abc123", "provider": "claude_code", "project_id": "...", "request_uuid": "...", "last_line": 124},
    "def456": {"status": "rejected", "errors": [{"field": "...", "code": "awaiting_user_input", "message": "..."}], "request_uuid": "..."},
    "typo":   {"status": "validation_error", "errors": [{"field": "SESSION_ID", "code": "session_not_found", "message": "..."}]}
  }
}
```

Per-id `status`: `sent`, `rejected`, `failed`, `timeout`, or `validation_error`. `succeeded` counts `sent`; `failed` is everything else.

### Exit codes

- `0` — batch ran and at least one message was sent (or the resolved set was empty)
- `1` — local argument error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `6` — resolved set was non-empty but no message was sent

## Examples

```bash
$TWICC send-messages abc123 def456 --message 'The spec changed: API base is now /v2. Re-check your work.'
$TWICC send-messages --spawned-by self --message "What's your status and ETA?"
$TWICC send-messages --siblings self --message 'Auth module done — wire against /v2/login.'  # worker → peers
$TWICC send-messages --descendants self --annotation status=working --message 'Wrap up, write your report, reply DONE.'
$TWICC send-messages --spawned-by self --message 'Review this mockup.' --attach /home/twidi/mockup.png
$TWICC send-messages --spawned-by self --attach /home/twidi/mockup.png  # attachment alone, no --message
$TWICC send-messages --spawned-by self --message /home/twidi/prompts/broadcast.md
```

## Following up

A per-id `sent` means the message was handed to the agent — not that it finished.

**To wait for the answers, use `--wait-reply`** — one call, and each recipient's cursor is read server-side the instant its agent takes the message, so the previous turn's closing message cannot answer for this one:

```bash
$TWICC send-messages --spawned-by self --message '<TEXT>' --wait-reply
```

Each entry gains a `reply` block (`outcome`, `line_num`, `is_final`, `since_line_num`, `waited_seconds`, the answer's `text`, `error` on `wait_failed`), and the summary gains `replied` / `all_replied`. `replied` counts `outcome: replied` and nothing else: a recipient that ended on `awaiting_user_input` concluded, but is not counted, so `all_replied` can be `false` with every recipient done. `outcome` is `replied`, `awaiting_user_input` (a pending request), `provider_error`, `ended`, `timeout`, `backend_gone`, `wait_failed`, or `pending` (cut short by `--wait-first`).

**A timeout is not a failure** and nothing is lost: the agents keep working, and each entry carries its own cursor to resume from — `line_num` when its ending consumed a line (`replied`, `provider_error`), `since_line_num` otherwise. Resume each one with `$TWICC session <SESSION_ID> wait-reply --from <CURSOR>` (skill: `twicc-session`): a batch needs one cursor per recipient, so the per-session resume is the exact one. For one wait over all of them instead, `$TWICC sessions wait-reply <SESSION_ID>... --since <the instant the batch started>` (skill: `twicc-sessions`). Re-running resumes, except for a session whose compute is not current; `--since <the instant the batch started>` resumes in every case. The exit code never reflects the wait, only whether the sends went out.

**Known limit (Claude Code), rare and accepted.** A message sent while a Claude session is busy is queued by Claude and recorded as a queued command, not as a user message. Every wait for an answer — `--wait-reply` included, with a cursor or the default — returns the first final message past its cursor, which is then the running turn's closing message. Usually that turn read the queued message and its closing message covers it; in the rare case where the queued message runs as a turn of its own afterwards, the wait returns an answer that does not cover it. In an orchestration, message a session once it has finished, not while it works.

Without `--wait-reply`, **never follow the send with a wait without a cursor** — the default cursor could land before a message not indexed yet and return the previous answer. Pass `sessions wait-reply <SESSION_ID>...` `--since` an **instant taken before this command** (an ISO 8601 instant, e.g. from `date -u +%Y-%m-%dT%H:%M:%SZ`; no offset means UTC; a bare date means its midnight UTC), or pass each `session <SESSION_ID> wait-reply` `--from` the `last_line` of its entry. `--since` / `--from` exist only on those two wait commands, not on this one, whose `--wait-reply` reads the cursors server-side.

- `$TWICC sessions get <SESSION_ID>...` — whether each recipient still runs (`process.state`). Skill: `twicc-sessions`.
- `$TWICC session <SESSION_ID> messages --tail 1` — read a reply by hand. Skill: `twicc-session`.

This closes the orchestration loop: `create-session` → … → `send-messages --wait-reply`.

## Related commands

- `$TWICC send-message <id|parent> <text>` — message one session, or reply to your parent. Skill: `twicc-send-message`.
- `$TWICC sessions wait-reply <SESSION_ID>... --since <INSTANT>` — await a batch messaged without `--wait-reply`, with an instant taken before the send. Skill: `twicc-sessions`.
- `$TWICC update-sessions settings --spawned-by self ...` — change settings of the same batch (e.g. before re-prompting). Skill: `twicc-update-sessions`.
- `$TWICC topology self` — discover the ids in your spawn tree. Skill: `twicc-topology`.

## How to present results

1. Bucket by per-id `status` and show counts (e.g. "5 sent, 1 rejected").
2. Surface `rejected` / `validation_error` entries with their `code`. For `awaiting_user_input`, read that session with `session <id> pending-requests` before reporting: a `question` you can answer yourself with `answer-questions`, and the send then goes through; anything else the user must clear in the UI.
3. Remind that `sent` ≠ done — point to `--wait-reply` to await the answers.
4. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
