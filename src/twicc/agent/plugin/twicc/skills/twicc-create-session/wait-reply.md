# `create-session --wait-reply` — wait for the answer, then follow up

Create the session and wait for its answer in one call, or track and read it later. MCP tool: `mcp__twicc__create_session`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC create-session "<PROMPT>" --wait-reply [--wait-timeout N] [--no-reply-text]
```

Keeps going after the session is created, until it answers, and adds a `reply` block to the result. One command instead of create, poll, read — and no window in which the answer can slip past you.

- `--no-reply-text` — drops `text`, keeps `line_num`: when you only need the go-ahead, not the payload in your context. The key is **absent**, never `null`.
- `--wait-timeout N` — caps the wait, whatever ends it. Default **300 s**, the ceiling MCP callers are asked to respect; an MCP client may itself give up on a tool silent that long, so over MCP pass a shorter value and come back. From a shell, raise it freely for a long first turn. It cannot be disabled; nothing caps how high you set it.
- `--wait-timeout` and `--no-reply-text` require `--wait-reply`: passing them alone is an error, not a no-op.
- A **pending request** (a tool approval or a question, which only a human can clear) ends the wait too, with `outcome: awaiting_user_input`: no line arrives until someone clicks. An answer arriving in the same poll wins.
- The wait reads the **transcript**, not the process state. A session held busy by a Monitor, a live subagent or a pending wake-up has written its answer long before it goes idle; this reports it at once instead of hours later.

## Output format

```json
{"status": "created", "request_uuid": "…", "session_id": "01a0c4d2-…",
 "provider": "claude_code", "project_id": "…",
 "reply": {"outcome": "replied", "line_num": 19, "is_final": true,
           "since_line_num": 0, "waited_seconds": 4.3, "text": "OK"}}
```

`outcome` says what ended the wait:

| `outcome` | Meaning | What `reply` carries |
|---|---|---|
| `replied` | the message closing the turn | that message |
| `awaiting_user_input` | a pending request — a tool approval, or a question you can answer yourself | read it with `session <ID> pending-requests`, then `answer-questions` if it is a question |
| `provider_error` | the provider refused the turn (quota, outage) — your request was not the problem and retrying now fails the same way | the error line |
| `ended` | the turn is over and nothing closed it — a crash, an interruption, an answer whose text was empty, or one whose provider marker is missing (`is_final: null`) | the last thing it said, or `line_num: null` |
| `timeout` | the deadline passed | the last thing it said, if any; resume from `since_line_num` |
| `backend_gone` | TwiCC stopped or restarted mid-wait | nothing — the session may well be fine, you just cannot see it from here |
| `wait_failed` | the wait itself broke (a locked DB, a Ctrl-C) — the session is unaffected | nothing, plus an `error` string |

**The exit code never reflects the wait**, only whether the session was created: a script must read `outcome`, not `$?`.

### `awaiting_user_input`

The agent stays blocked until someone clears it. Read it with `$TWICC session <SESSION_ID> pending-requests` (skill: `twicc-session`).

- An entry whose `kind` is `question`: answer it yourself with `answer-questions`. The plain read already carries the question ids and options, so **never pass `--raw` to answer**.
- Anything else is the user's to clear in the UI. `--raw` is how you tell them what it is about: it returns the whole payload, diff included.
- Pass `--no-question-widget` to a session you drive yourself (file: `session-behavior.md`). It does not rule the case out entirely: an MCP server's elicitation reaches that path in every permission mode.

### `timeout`

**Not a failure**: the agent keeps working and the session is intact — only the waiting stopped. Expect it on a worker whose first turn runs long: raise `--wait-timeout`, or take the `session_id` and come back later.

Resume with `$TWICC session <SESSION_ID> wait-reply --from <CURSOR>` (skill: `twicc-session`), the command that takes a cursor. Pass the `line_num` when the ending consumed a line (`replied`, `provider_error`), the `since_line_num` otherwise.

## Following up

A `created` status only means the session started and the prompt was handed to the agent; it keeps working in the background.

- **Map spawned work:** `$TWICC topology self` — the full spawned-session tree rooted at your top-level ancestor, with compact process state for every node (skill: `twicc-topology`).
- **Wait for the answer later:** `$TWICC sessions wait-reply <SESSION_ID>...` waits until each child answers or blocks on a pending request (skill: `twicc-sessions`). Each wait starts after the child's last user message, so an answer already given is returned — except while a session's compute is not current (e.g. right after a TwiCC restart): then pass `--since` an instant before the spawn or the send. Exit 0 whatever the outcomes: read `summary.all_replied` and each `outcome`.
- **Continue the conversation:** once at `user_turn`, `$TWICC send-message <SESSION_ID> '<text>'` (skill: `twicc-send-message`). Change settings mid-session with `$TWICC update-session <SESSION_ID> settings ...` (skill: `twicc-update-session`).
- **Let the child talk back:** tell the spawned session, in the prompt, to load the `twicc-send-message` skill and use `send-message parent '<text>'`. `parent` resolves to you via its `spawned_by` link; the reply lands in your own session. Under `--permission-mode dontAsk` / `strict` the child cannot run the `$TWICC` CLI: tell it to use the `mcp__twicc__send_message` tool instead (file: `session-behavior.md`). With the TwiCC MCP server disabled, fetch the child's final message via `$TWICC session <ID> messages --tail 1` instead.

### Check state (snapshot)

Read `process.state` from `$TWICC sessions get <SESSION_ID>` (skill: `twicc-sessions`). `session <ID>` exits 1 until the watcher writes the new session's row.

- `starting` → still booting; retry shortly.
- `assistant_turn` → still working.
- `awaiting_user_input` → blocked on a pending request. Do NOT call `send-message`: it is refused. Read what is asked with `$TWICC session <ID> pending-requests` — **not** `messages`, which does not carry it. A `question` you answer with `answer-questions`; anything else needs the user in the UI.
- `user_turn` → done; fetch the reply with `$TWICC session <ID> messages --tail 1`.
- `dead` → no live process: the turn finished, or the agent stopped. Check `messages --tail 1`:
  - last message from the assistant **with `is_final: true`** → the turn completed; `is_final: false` → it stopped mid-turn; `is_final: null` → undecided: the text is probably the answer, but nothing here proves the turn ended.
  - a trailing **user** message → nothing readable came back.
  - an **empty list** → the last item carries no readable text. Re-read with `--tail 2` and apply the same checks, keeping in mind that "the child's closing message was empty" is a real outcome. Do not widen further: a longer window reaches the previous turn, whose closing message also says `is_final: true`.
  - Read the **last** message and check its field rather than filtering on `--is-final true`: the filter spans the whole session and would hand you a previous turn's answer.

## Examples

```bash
$TWICC create-session --title 'Review last commit' --wait-reply --wait-timeout 120 'Review the last commit and report the risks'
$TWICC session 01a0c4d2-... wait-reply --from 19 --wait-timeout 60
# Resumes past the answer read at line 19: only a later line counts.
```

## Related commands

- `$TWICC session <ID> wait-reply` / `pending-requests` / `answer-questions` / `messages` — one session. Skill: `twicc-session`.
- `$TWICC sessions wait-reply` / `sessions get` — several sessions. Skill: `twicc-sessions`.
- `$TWICC send-message <ID> --wait-reply` — send a follow-up and wait. Skill: `twicc-send-message`.

