---
name: twicc-send-messages
description: Send the SAME message (and optional attachments) to several TwiCC sessions, selected by id and/or --spawned-by/--descendants/--siblings/--annotation. Use to broadcast a steering instruction, status request, or correction to a batch (e.g. every worker in an orchestration), or for a worker to message peers with --siblings self.
argument-hint: '[SESSION_ID...] [--message <text>] [--spawned-by X|--descendants X|--siblings X] [--annotation ...] [--attach PATH...]'
---

# TwiCC Send Messages

Batch sibling of `send-message`: delivers the SAME message to every targeted session in one call, with the same selection model as `update-sessions`. For a single recipient (or to reply to your `parent`), use `send-message` (skill: `twicc-send-message`).

**Each send starts or resumes an agent** — real work and token spend. A batch can cold-start many stopped sessions at once. And like the singular command it is async: a per-id `sent` means "handed to the agent", not "the agent finished" — chain with `processes wait` (see Following up).

## When to use

- Broadcast a steering correction to a set of children: "the spec changed, the API base is now /v2 — re-check your work."
- Ask every worker for status: "what's your progress and ETA?"
- Graceful wrap-up (vs the hard kill of `processes stop`): "finish your current step, write your report, and reply DONE."
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
$TWICC send-messages [SESSION_ID...] [--message <TEXT>] [--attach PATH...] [--spawned-by X|--descendants X|--siblings X] [--annotation ...]
```

Selection is identical to `update-sessions` (skill: `twicc-update-sessions`): a positional `SESSION_ID...` list merged (union, explicit first) with the scope filters. `self` means the current session.

- `SESSION_ID...` — recipients; optional if a filiation scope is given.
- `--message TEXT` — message text, or a path to a UTF-8 file whose content is the message. **Required unless at least one `--attach` is given**: a message made only of attachments is valid. Same text for every recipient (each delivery gets the sender header on top — see *Sender header*). Over `--remote` the file is read locally; prefix an absolute path with `remote:` to read it on the remote server instead. `@@/abs/path` (or `@@{/path with spaces}`) include markers are replaced by that file's content — see `--no-expand`.
- `--no-expand` — disable `@@` include expansion. By default an `@@/abs/path`, `@@~/path` or `@@{/path with spaces}` marker in the message (or in the file it is read from) is replaced by that file's UTF-8 content, recursively (5 levels max). Inside a file, `@@./path` and `@@../path` resolve against that file's own directory (never the cwd), so only the entry point needs an absolute path; in inline text they are an error. A missing file expands to nothing — a marker alone on its line takes the whole line with it, so includes are optional; a directory, unreadable or non-UTF-8 file is an error; `@@@@` escapes a literal `@@`; the final text is capped at 500 KB. Over `--remote`, markers resolve on the client; use `@@remote:/abs/path` for a file on the remote server.
- `--attach PATH` (repeatable) — attach a file to every message. **Validated per session against its provider** (Claude Code: PNG/JPEG/GIF/WebP/PDF/text up to 5 MB; Codex: images only), so a file one provider rejects yields a per-id `validation_error` while the others still receive it. Local path or a `data:<mime>;base64,...` URI for remote/API callers. Over `--remote`, prefix an absolute path with `remote:` to read it on the remote server instead.
- `--spawned-by <ID|self>` / `--descendants <ID|self>` — also target children / proper descendants. `parent` is **not** supported (use `send-message parent`). Mutually exclusive.
- `--siblings <ID|self>` — also target the siblings of the given session: the *other* sessions spawned by the same parent, **reference always excluded**. `self` broadcasts to your peers (the canonical worker → worker channel). `parent` is **not** supported. Mutually exclusive with `--spawned-by` / `--descendants`. Note `--spawned-by parent` (the same set but including yourself) is **not** available here, so `--siblings self` is the way to reach your peers from this command.
- `--annotation KEY[OP]VALUE` — narrow the filiation scope by annotation; repeatable, AND-combined; requires a filiation scope; does not filter explicit ids. Same syntax as `twicc sessions --annotation` (skill: `twicc-sessions`).
- `--timeout SECONDS` — wall-clock budget for the whole batch (default 30; drops run in parallel server-side).

If neither ids nor a filiation scope is given, the command errors (exit 1). An empty resolved set is not an error: `results` is `{}` and the command exits 0.

## Sender header

When the caller is itself a TwiCC session, each recipient receives the text under the same sender header as `send-message` (a single `:: message from <relation> session <id> ("**<title>**")` line, then the text), computed **per recipient** — the relation wording (`your spawned session` / `your parent session` / `a sibling session` / `another session`) depends on each target's spawn-tree link to you. Never write it yourself; a self-send in the batch gets no header. See the *Sender header* section of `twicc-send-message`.

## Errors

Argument-level problems fail the whole command (exit 1, plain-text on stderr): empty/unreadable `--message`, neither `--message` nor `--attach`, bad `--timeout`, two of `--spawned-by`/`--descendants`/`--siblings` together, `parent` scope (on `--spawned-by`/`--descendants`, or any value on `--siblings`), `--annotation` without a filiation scope, neither ids nor scope.

Per-session problems never fail the batch — reported in `results[<id>]` with `status` `validation_error` (local lookup: `session_not_found`, `is_subagent`, `session_stale`, `project_no_directory`; or an attachment its provider rejects) or `rejected` (server: `awaiting_user_input` — the session has a pending UI dialog a CLI message can't unblock; `manager_busy` — transient, retry; `provider_disabled`). Same vocabulary as `twicc-send-message`.

## Output format

A single object keyed by session_id, plus a summary:

```json
{
  "summary": {"total": 3, "succeeded": 2, "failed": 1, "all_succeeded": false},
  "results": {
    "abc123": {"status": "sent", "session_id": "abc123", "provider": "claude_code", "project_id": "...", "request_uuid": "..."},
    "def456": {"status": "rejected", "errors": [{"field": "...", "code": "awaiting_user_input", "message": "..."}], "request_uuid": "..."},
    "typo":   {"status": "validation_error", "errors": [{"field": "SESSION_ID", "code": "session_not_found", "message": "..."}]}
  }
}
```

Per-id `status`: `sent`, `rejected`, `failed`, `timeout`, or `validation_error`. `succeeded` counts `sent`; `failed` is everything else.

### Exit codes

- `0` — batch ran and at least one message was sent (or the resolved set was empty)
- `1` — local argument error
- `2` — TwiCC server not running
- `6` — resolved set was non-empty but no message was sent
- `64` — bad CLI usage

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

A per-id `sent` means the message was handed to the agent — not that it finished. Await completion, then read results:

- `$TWICC processes wait --spawned-by self user_turn dead --transition --timeout <N>` — block until every recipient finishes the turn this message triggers (`--transition` avoids matching the idle `user_turn` they were already in). Skill: `twicc-processes`.
- `$TWICC session <SESSION_ID> messages --tail 1` — read each reply. Skill: `twicc-session`.

This closes the orchestration loop: `create-session` → … → `send-messages` → `processes wait` → `session messages`.

## Related commands

- `$TWICC send-message <id|parent> <text>` — message one session, or reply to your parent. Skill: `twicc-send-message`.
- `$TWICC processes wait --spawned-by self <STATE>...` — await the batch you just messaged. Skill: `twicc-processes`.
- `$TWICC update-sessions settings --spawned-by self ...` — change settings of the same batch (e.g. before re-prompting). Skill: `twicc-update-sessions`.
- `$TWICC topology self` — discover the ids in your spawn tree. Skill: `twicc-topology`.

## How to present results

1. Bucket by per-id `status` and show counts (e.g. "5 sent, 1 rejected").
2. Surface `rejected` / `validation_error` entries with their `code` (esp. `awaiting_user_input` — that session needs a UI click first).
3. Remind that `sent` ≠ done — point to `processes wait` to await the work.
4. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
