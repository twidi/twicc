# `session <SESSION_ID> wait-reply` — block until the session concludes

Wait until a session answers, or blocks on a human. MCP tool: `mcp__twicc__session_wait_reply`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC session '<SESSION_ID>' wait-reply [--from N] [--since INSTANT] [--wait-timeout N] [--no-reply-text]
```

The wait that `--wait-reply` runs on the commands that send, for a session **you did not just message**: spawned earlier, steered from the UI, messaged by someone else, or messaged by you without `--wait-reply`.

**When you send the message yourself, use `send-message --wait-reply`**: it reads the cursor server-side. A send without it is **never** followed by a wait without a cursor: pass `--from` the `last_line` the send returned.

### The cursor

Only a line strictly past the cursor counts; an answer already past it is returned at once. **Choosing it right is the whole of using this command.**

| Situation | `--from` |
|---|---|
| First call | Omit it: the wait starts after the session's last user message, so an answer already given is returned. Exception: while the session's compute is not current (e.g. right after a TwiCC restart), it starts at the current last line — pass `--since` an instant before the spawn or the send. |
| Previous wait ended `replied` or `provider_error` | Its **`line_num`**. Its `since_line_num` points before that line and would return it again. |
| Previous wait ended `timeout`, `ended`, `backend_gone`, `wait_failed` or `awaiting_user_input` | Its **`since_line_num`**: nothing was consumed. |

`--since INSTANT` — the same cursor as an ISO 8601 instant, when you have a time and not a line, or one moment for several sessions (a line number means a different place in each): the cursor goes just before the first line stamped strictly after it, so that line counts. A line with no timestamp is not a boundary, and such a line that sits before the boundary is not re-scanned, even if it was written after the instant. A timestamp going backwards can only move the cursor earlier, never past a line. Mutually exclusive with `--from`. Accepts `2026-09-19T05:38:20+00:00` (the format `messages` returns), `2026-09-19 05:38:20`, or a bare date (its midnight). **No offset means UTC.** An instant older than the session is not an error: the cursor goes just before its first stamped line, which counts.

### Other options and limits

- `--wait-timeout` (default 300 s) — caps the wait. An idle session with no answer past the cursor ends with `ended`, but only after a ~5 s flush window: below that, the wait can only report `timeout`.
- `--no-reply-text` — drop the answer's `text` from the result.
- A session just spawned, with no indexed transcript yet, is waited on from line 0 (or `--from`); `--since` does not apply to it.
- `session self wait-reply` is refused (exit 1): your own answer cannot come while you wait.
- `--since` exists here and on `sessions wait-reply`; `--from` only here. The sending commands read their cursor server-side.

## Output format

`{"session_id": ..., "reply": {...}}`. The `reply` block is the one `--wait-reply` returns: `outcome`, `line_num`, `is_final`, `since_line_num`, `waited_seconds`, `text` (unless `--no-reply-text`), `error` (on `wait_failed`).

`outcome`:

- `replied` — the message closing the turn arrived.
- `awaiting_user_input` — a pending request: read it with `pending-requests`, answer it with `answer-questions` if it is a question (file: `questions.md`).
- `ended` — the turn is over and nothing closed it: a crash, an interruption, an empty answer.
- `timeout`, `provider_error` (quota, outage), `backend_gone`, `wait_failed`.

No flag chooses between an answer and a pending request, and a block is an `outcome`, never a separate field. Both in the same poll: the answer wins.

### Exit codes

- `0` — answered or blocked. So `$TWICC session <ID> wait-reply && …` chains.
- `5` — neither came: `timeout`, `ended` or `provider_error`.
- `2` — TwiCC stopped.
- `1` — a local refusal: bad `--from` or `--since`, both passed, `--wait-timeout` not > 0, no session and no live process for the id, or the wait itself broke. Read it before retrying.

## Known limit (Claude Code)

A message sent while a Claude session is busy is queued and recorded as a queued command, not a user message. Every wait for an answer — a send's own `--wait-reply` included, with `--from` or the default cursor — then returns the running turn's closing message. It usually covers the queued message; rarely, the queued message runs as a turn of its own afterwards, and the wait returned an answer that does not cover it. In an orchestration, message a session once it has finished.

## Examples

```bash
$TWICC session 4a8352fb-... wait-reply --wait-timeout 120
# → {"session_id":"4a8352fb-...","reply":{"outcome":"replied","line_num":42,...,"text":"done"}}
$TWICC session 4a8352fb-... wait-reply --from 42 --wait-timeout 60
# Resumes past the answer just read: only a later line counts.
$TWICC session 4a8352fb-... wait-reply --since 2026-09-19T05:38:20+00:00
# Anything said after that moment, without knowing a line number.
```

## Related commands

- `$TWICC session <ID> messages` — read the answer by hand. File: `messages.md`.
- `$TWICC session <ID> pending-requests` / `answer-questions` — the `awaiting_user_input` outcome. File: `questions.md`.
- `$TWICC sessions wait-reply` — several sessions, one budget for the batch. Skill: `twicc-sessions`.
- `$TWICC send-message <ID> --wait-reply` — send and wait in one call. Skill: `twicc-send-message`.

## How to present results

1. `replied` — give the answer's `text`, or its summary.
2. `awaiting_user_input` — say the session is blocked on a human, then read what it asks (`questions.md`).
3. `timeout` — say the session is still working; keep the cursor to resume from.
4. Any other outcome — name it and what it means.
