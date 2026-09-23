---
name: twicc-session
description: Inspect, wait on, unblock, or stop a single session — view metadata, read raw item content by line number, read user/assistant messages, list subagents, read its plan, list/inspect its workflows, see what it is waiting on and answer its question, or stop its live agent. Use when you or the user want to examine a session, read conversation content, explore subagent activity, block until it answers, unblock a session waiting on a human, or stop its agent.
argument-hint: <session_id> [content|messages|agents|plan|wait-reply|pending-requests|answer-questions|cancel-questions|stop|workflows|workflow]
---

# TwiCC Session

Inspect, wait on, unblock, or stop a single session. Twelve sub-commands:

- Default — full session metadata, `process` block included (the live state, `null` on a subagent).
- `content [LINE_OR_RANGE] [--contains TEXT ...] [--limit N] [--offset N] [--tail N] [--paginated]` — raw JSONL items by line number and/or content substring(s) (provider-specific schema).
- `messages [--contains TEXT ...]` — user/assistant messages only, uniform shape across providers.
- `wait-reply [--from N]` — block until this session concludes past the cursor. Use it on a session **you did not just message**: one spawned earlier, steered from the UI, or messaged by someone else. `--from` is the `line_num` or `since_line_num` a previous wait returned, so a wait that timed out can be resumed exactly where it stopped; omitted, it is the session's current last line. `--since` names that same cursor as an ISO 8601 instant instead, for when you have a time and not a line number. An idle session concludes with `ended` rather than hanging, but not instantly: the loop waits out a ~5 s flush window before it can tell a finished turn from one about to speak, so a `--wait-timeout` below that always reports `timeout`. Two endings, and no flag chooses between them: an answer — the message closing a turn — or a **pending request**, which only a human can clear. An answer arriving in the same poll wins. Exactly what `--wait-reply` does on the commands that send, which is why it carries the same name. Also takes `--wait-timeout` (default 300 s) and `--no-reply-text`. **Exit code:** `0` answered or blocked, `5` neither came, `2` TwiCC stopped, `1` a local refusal — a bad `--from` or `--since`, the two cursors passed together, a non-positive `--wait-timeout`, an unknown session, or the wait itself breaking. So `$TWICC session <ID> wait-reply && …` chains, and a `1` is worth reading before retrying.
- `pending-requests [--raw]` — what this session's live agent is waiting on, answerable or not.
- `answer-questions [--request-id ID] [--choice 'ID=VALUE']` — answer the question it is waiting on.
- `cancel-questions [--request-id ID]` — decline it.
- `stop` — stop this session's live agent (`--timeout`, `--force` for a SIGKILL without the grace window). Idempotent: stopping an already-stopped session still reports `stopped`. Same operation as `process <ID> stop`, which it is meant to replace.
- `agents` — list subagents spawned by this session. Rows use the reduced projection or the full payload like `sessions` (see the `twicc-sessions` skill): **before 2026-10-01 the full payload is the default and `--slim` opts in; from that date the reduced one is the default, `--slim` is a no-op, and `--full` brings the full payload back**. Until then a call with neither flag prints a one-line notice on stderr (in the RPC `warnings` key; never on MCP). Rows carry the same `process` block as `sessions`, always `null` here: a subagent runs inside its parent's process and never has one of its own.
- `plan [PATH] [--list]` — the session's tracked plan documents (both providers): most recently updated one by default, a specific one by path, `--list` to enumerate.
- `workflows [--limit N] [--offset N] [--paginated] [--result] [--full]` — list this session's workflows (Claude Code only).
- `workflow <ID>` — show one (Claude Code only).

## When to use

- You or the user want details about a specific session.
- You want to read conversation content (raw items or clean messages).
- You want to see which subagents were spawned by a session.
- You want to read the session's plan (e.g. inspect what a worker session planned).
- You want to list a session's workflows, or inspect one.
- A session is blocked on a human and you want to see what it asks, or answer it.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Usage

### Default — session metadata

```bash
$TWICC session <SESSION_ID>
```

Works for regular sessions and subagents. The same row `sessions get <SESSION_ID>` returns for one id, `process` block included, minus its `known` flag — reach for that one for several ids, for the reduced projection, or for an id this one refuses: an id with no row at all comes back as a `known: false` placeholder, and a session with no user message — one still being computed, or one that never had a user turn at all — comes back in full with `known: true`; both exit 1 here.

```json
{
  "id": "abc123-def456",
  "project_id": "-home-twidi-dev-myproject",
  "provider": "claude_code",
  "parent_session_id": null,
  "last_line": 150,
  "mtime": 1741654800.0,
  "created_at": "2025-03-10T14:30:00+00:00",
  "last_started_at": "2025-03-10T14:30:00+00:00",
  "last_updated_at": "2025-03-10T15:45:00+00:00",
  "last_stopped_at": "2025-03-10T15:50:00+00:00",
  "last_new_content_at": "2025-03-10T15:45:00+00:00",
  "last_viewed_at": "2025-03-10T16:00:00+00:00",
  "stale": false,
  "title": "Implement user authentication",
  "slug": null,
  "user_message_count": 12,
  "compute_version_up_to_date": true,
  "context_usage": 85000,
  "self_cost": 1.234,
  "subagents_cost": 0.567,
  "total_cost": 1.801,
  "cwd": "/home/twidi/dev/myproject",
  "git_branch": "feature/auth",
  "git_directory": "/home/twidi/dev/myproject",
  "model": {"raw": "claude-opus-4-20250514", "family": "opus", "version": "4"},
  "archived": false,
  "pinned": null,
  "permission_mode": "default",
  "selected_model": null,
  "effort": null,
  "thinking_enabled": null,
  "claude_in_chrome": false,
  "fast_mode": false,
  "context_max": 200000,
  "compacted": false,
  "hidden": false,
  "spawned_by": null,
  "spawn_root": null,
  "annotations": {"role": "reviewer"},
  "process": {"id": 5847, "state": "assistant_turn",
              "started_at": "2026-09-20T10:43:57.327194+00:00",
              "last_state_change_at": "2026-09-20T10:44:45.240981+00:00", "pid": 3501299}
}
```

#### Key fields

- `last_line` — total item count; use as the upper bound for `content` ranges.
- `provider` — `"claude_code"` or `"codex"`. Determines item schema for `content`.
- `slug` — provider short id (e.g. Codex subagent nickname), or `null`.
- `parent_session_id` — `null` for regular sessions, set for subagents.
- `model` — `{"raw": "...", "family": "...", "version": "..."}`.
- `context_max` / `context_usage` — max context window and current usage in tokens.
- `compacted` — whether the session has been compacted at least once.
- `last_new_content_at` — most recent item appended.
- `last_viewed_at` — when the user last opened the session in TwiCC.
- `hidden` — whether the session is hidden from all listings and broadcasts.
- `spawned_by` — session ID that spawned this session, or `null`.
- `spawn_root` — root session ID for the spawned-session tree, or `null` before a session joins one.
- `annotations` — free-form JSON object attached at session creation.
- `process` — the live process, the same block `sessions` puts on every row: `state` is one of `starting`, `assistant_turn`, `awaiting_user_input` (blocked on a user click), `user_turn`, or `dead`. `dead` means TwiCC runs no process for this session — most sessions it indexes it never started — and it is also the answer when no backend is running. `null` on a subagent, which has no process of its own.

### Content — raw items

```bash
$TWICC session <SESSION_ID> content [LINE_OR_RANGE] [--contains TEXT ...] [--limit N] [--offset N] [--tail N] [--paginated]
```

The lowest-level view: **every** raw item, including tool calls and results — unlike `messages` and `search`, which only ever see user/assistant text.

Filter by line/range, by substring, or both:

- Single line: `content 5`
- Range: `content 10-20` (inclusive)
- Substring: `content --contains "some text"` — every item whose raw content contains the text.
- Multiple substrings: `content --contains foo --contains bar` — repeatable, **AND-combined** (an item must contain every term).
- Combined: `content 10-20 --contains "some text"` — the line/range scopes the substring search.
- Windowed: `content --contains foo --limit 20 --offset 20` — `--limit`/`--offset` apply **last**, after the range and `--contains`.
- Last N: `content --contains foo --tail 10` — the last 10 **matches**. Mutually exclusive with `--limit`/`--offset`.

At least one selector (`LINE_OR_RANGE`, `--contains`, `--limit`/`--offset`, `--tail`, `--paginated`) is required: a bare call would return every raw item, **the heaviest payload the CLI can produce** — hundreds of megabytes on a long session.

The range and the window answer different questions and stack. The range is an absolute address in the JSONL (a `line_num` span); the window is a rank in what the filters kept. They only coincide when nothing else filters. To page through a substring search, use `--limit`/`--offset`, not the range.

**To reach the end of a filtered result, use `--tail`.** A filtered result has no line address, so without it you would need a first call to learn `total`, and the session can grow in between. Under `--tail N` the reported window is the range it covers (`offset = total - N`) and `has_more` means matches remain **before** it.

`--paginated` adds the `{items, pagination}` envelope and caps the page at **50** when no `--limit` is given, so `total` tells you how many items match before you pull them all. **Before 2026-10-01 that capping is opt-in; from that date it is the only behaviour**, so a call with no `--limit` returns a page rather than every item in the session. It also counts as a selector on its own — `content --paginated` is a valid browse entry point, since a bounded page cannot dump the session.

`--contains` is **case-insensitive** and matches the **raw JSONL string** (the verbatim line as stored). Consequences: it also matches JSON keys (e.g. `"role"`, `"type"`), and embedded newlines are escaped (`\n`), so a query spanning a line break won't match. This is the only way to substring-search across all raw items (tool_use/tool_result included).

Returns a JSON array — empty when nothing matches, which is not an error. Each entry is `{line_num, content}`, where `content` is the raw JSONL object. Schema of `content` depends on provider:
- `claude_code` — Claude API objects (user/assistant messages, tool_use, tool_result, …).
- `codex` — Codex schema (user/assistant messages, function_call, function_call_output, …).

```json
[
  {
    "line_num": 5,
    "content": {
      "type": "user",
      "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
      "timestamp": "2025-03-10T14:30:00.000Z"
    }
  }
]
```

Check `last_line` from the default sub-command first to know the valid range.

### Messages — uniform transcript

```bash
$TWICC session <SESSION_ID> messages [OPTIONS]
```

User + assistant messages only, uniform shape across providers. No tool calls, no system noise.

- `--range N` or `--range N-M` — filter by JSONL line number (same numbering as `content`). Only user/assistant messages whose `line_num` falls within the range are returned — not the Nth message in the list.
- `--role user|assistant` — keep only one side.
- `--contains TEXT` — keep only messages whose text contains the substring. Repeatable and **AND-combined** (a message must contain every term). **Case-insensitive.** Unlike `content`'s `--contains` (which matches the raw JSONL), this matches the extracted `text` shown below — no JSON keys, no tool noise. Applied **before** `--tail`/`--limit`/`--offset`, so paging windows the matching messages.
- `--is-final true|false|null` — keep only messages whose `is_final` field (see below) has one of these values. Repeatable and **OR-combined** — the opposite of `--contains`, because a message carries a single value, so an AND would always be empty. Omit it and nothing is filtered: **`null` is only ever dropped when you ask for a set without it.** Passing all three is the identity — exactly the no-flag answer. Applied **before** `--tail`/`--limit`/`--offset`.
- `--limit N` — cap results. **Before 2026-10-01 a call without it returns every message; from that date it pages at 50**, so pass it (or `--tail`) whenever you need more, and read `has_more`.
- `--offset N` — skip first N messages (default: 0).
- `--tail N` — return the last N messages. Mutually exclusive with `--limit`/`--offset`.
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`. Without an explicit `--limit` the page size becomes **50** instead of "everything". **Before 2026-10-01 that is opt-in; from that date it is the only behaviour**, so an unfiltered call returns a page rather than the whole session. With `--tail N` the reported window is the range it covers, and `has_more` means messages remain **before** it. When nothing filters after extraction — no `--contains`, and no `--is-final` (or one listing all three values, which filters nothing) — `total` counts raw items, a few of which extract to nothing and are dropped, so `has_more` can be a rare false positive, never a false negative.

```json
{"items": [
  {"line_num": 3, "text": "Hello, can you help me?", "role": "user", "timestamp": "2025-03-10T14:30:00+00:00", "is_final": null},
  {"line_num": 4, "text": "Sure — what do you need?", "role": "assistant", "timestamp": "2025-03-10T14:30:02+00:00", "is_final": true}
 ], "pagination": {"limit": 50, "offset": 0, "total": 2, "has_more": false}}
```

`is_final` separates the assistant message that **closes a turn** from the ones it emits between tool calls (a long turn usually has several: talk, run a tool, talk, run a tool, then answer). `true` = the closing one, `false` = an intermediate one, `null` = unknown. A user message is always `null`; an assistant message is `null` when the provider left no marker on that line or wrote one TwiCC does not recognise.

**`true` is reliable; `null` is not "false".** Several things produce a `null`, and only one of them means "an intermediate message":

- a line TwiCC built itself from a slash-command's output (a `/goal` or `/plugin` ack) — the model never wrote it, so there is no marker to find;
- a message Claude Code split across several JSONL lines, writing the marker on the last one only — common on **subagent** transcripts, rare on a session's own. **This is the intermediate case**;
- a message the user interrupted, or one cut short by an API error — both *do* end their turn;
- an older transcript format that carried no marker at all.

Expect `null` to be uncommon on a recent session and frequent on a subagent transcript. So: trust a `true`, and read a `null` as "probably, no proof".

Common patterns:

**Reading one session's answer — take the last message, plain, then look at it. No `--role`, no `--is-final`.**

```bash
$TWICC session <ID> messages --tail 1
```

| What comes back | Reading |
|---|---|
| `role: assistant`, `is_final: true` | That is the answer. |
| `role: assistant`, `is_final: false` | Still working, or stopped mid-turn. Retry; do not use the text. |
| `role: assistant`, `is_final: null` | Use it, but it is no proof the turn ended. |
| `role: user` | Nothing readable has been said since — the agent has not started, it stopped before its first token, or (if you landed here from the row below) its closing message was empty. `$TWICC process <ID>` tells you whether it is still running (skill: `twicc-process`); the transcript cannot. |
| **empty `items` list** | The last item carries no readable text — an answer whose text was empty, or a Codex inter-agent dispatch record. **Re-read with `--tail 2` and apply this same table to what comes back**, which may well be the `role: user` row: "the closing message was empty" is a real, terminal outcome, not a glitch to dig past. |

**Widen by one, never more.** A longer window walks back into the *previous* turn, and its closing message carries `is_final: true` — the staleness this table exists to prevent. One trailing unreadable item is all that was ever observed, so `--tail 2` is enough.

(`messages` exits 1 with `session not found` while a freshly created session still has no user message — a third non-answer, not an error to report.)

**Why neither filter here.** `--is-final true` spans the **whole session**, not the current turn, so mid-turn it returns the closing message of a *previous* turn — an answer to an older question, which reads as perfectly valid. Measured live: last user message at line 956, agent still writing at line 1228, and `--role assistant --is-final true --tail 1` returned line **953**. And `--role assistant` alone hides a trailing user message, bringing the same staleness back in the window before the agent's first line.

Other patterns:
- The session's answers, without the commentary: `messages --role assistant --is-final true --tail N` (spanning the session is what you want here — but a bare call pages at **50 oldest**, so ask for the end explicitly, or page with `--limit`/`--offset` and read `has_more`)
- Only the intermediate chatter, for debugging: `messages --role assistant --is-final false --tail N` (same 50-page cap as above)
- Last N exchanges: `messages --tail N`
- Focused window from search: `messages --range A-B`
- Messages mentioning a term: `messages --contains "auth"`
- Last reply mentioning a term: `messages --role assistant --contains "auth" --tail 1`

### Wait-reply — block until the session concludes

```bash
$TWICC session '<SESSION_ID>' wait-reply [--from N] [--since INSTANT] [--wait-timeout N] [--no-reply-text]
```

The same wait `--wait-reply` runs on the commands that send — an answer or a pending request, whichever comes first — on a session **you did not just message**: one spawned earlier, steered from the UI, or messaged by someone else. When you sent the message yourself, use `send-message --wait-reply` instead — it reads its own cursor server-side and needs nothing from you.

**`--from` is the cursor**, and choosing it right is the whole of using this command. Only a line strictly past it counts.

| The previous wait ended with | Resume from |
|---|---|
| `replied` | its **`line_num`** — its `since_line_num` still points below the answer and would return the same one again |
| `provider_error` | its **`line_num`** too: the error is a line in the transcript, so resuming below it re-matches the same one forever |
| `timeout`, `ended`, `backend_gone`, `wait_failed`, `awaiting_user_input` | its **`since_line_num`** — nothing was consumed |
| nothing yet (first call) | omit it: the cursor becomes the session's current last line, "tell me the next thing it says" |

**`--since` is that same cursor as an instant**, mutually exclusive with `--from`: the wait starts just below the first line stamped strictly after that moment. A timestamp that goes backwards — they are not monotonic — can only push the cursor lower, never past a line; a line carrying no timestamp is not a boundary, and one below the boundary is not re-scanned. Use it when you have a time and not a `line_num` — "did it say anything since I left" — or when the same moment must address several sessions, which a line number cannot: line 42 is a different place in every transcript. ISO 8601: `2026-09-19T05:38:20+00:00` — exactly what `session messages` returns, so a timestamp goes straight back in — and also `2026-09-19 05:38:20` or a bare `2026-09-19` for its midnight. **No offset means UTC**, which is what this CLI stores and prints, so a timestamp pasted back from any of its output lands exactly where it came from. An instant older than the session starts the wait above the first stamped line rather than failing.

Returns `{"session_id": ..., "reply": {...}}`, the `reply` block being the one `--wait-reply` returns: `outcome`, `line_num`, `is_final`, `since_line_num`, `waited_seconds`, the answer's `text` (dropped by `--no-reply-text`), `error` on `wait_failed`. A block is the `outcome`, never a field beside it: there is one place to read it.

`outcome` is `replied` (the message closing the turn), `awaiting_user_input` (a pending request — read it with `pending-requests`, and answer it with `answer-questions` when it is a question), `ended` (the turn is over and nothing closed it — a crash, an interruption, an empty answer), `timeout`, `provider_error` (quota, outage), `backend_gone`, or `wait_failed`.

```bash
$TWICC session 4a8352fb-... wait-reply --wait-timeout 120
# → {"session_id":"4a8352fb-...","reply":{"outcome":"replied","line_num":42,...,"text":"done"}}
$TWICC session 4a8352fb-... wait-reply --from 42 --wait-timeout 60
# Resumes above the answer just read.
$TWICC session 4a8352fb-... wait-reply --since 2026-09-19T05:38:20+00:00
# Anything said after that moment, without knowing a line number.
```

An idle session ends rather than hanging, but only after a ~5 s flush window: below that, `--wait-timeout` can only report `timeout`.

### Pending request — what the session is waiting on

```bash
$TWICC session <SESSION_ID> pending-requests [--raw] [--timeout N]
```

Lists **every** pending request, not only the ones you can answer. A session
frozen on a tool approval is waiting even though nothing here can unblock it,
and an empty list would say the opposite.

```json
{
  "session_id": "...",
  "agent_state": "awaiting_user_input",
  "pending_requests": [
    {
      "request_id": "...",
      "created_at": "2026-09-18T15:04:11.123456+00:00",
      "age_seconds": 42.1,
      "kind": "question",
      "tool_name": "AskUserQuestion",
      "questions": [
        {"id": "1", "header": "Database", "question": "Which database should we use?",
         "multi_select": false, "allows_free_text": true, "secret": false,
         "options": [{"label": "PostgreSQL", "description": "Reliable, feature-rich"},
                     {"label": "SQLite", "description": "Lightweight, file-based"}]}
      ],
      "actions": [
        {"action": "answer-questions", "label": "Answer the questions", "accepts": ["--choice"]},
        {"action": "cancel-questions", "label": "Decline to answer"}
      ]
    }
  ]
}
```

`kind` is `question` or `out_of_scope`. An out-of-scope entry stops at
`request_id`, `created_at`, `kind`, `reason` and `tool_name`, with an empty
`actions` — it exists to say *something is waiting and it is not yours*.
`reason` is one of `tool_approval`, `mcp_tool_approval`, `elicitation`,
`terminal_only`, `choice`.

`--raw` adds each request's untouched `tool_input`, on **every** entry. Ask for
it on a session blocked on a large patch and you get the patch.

**You never need `--raw` to answer.** The plain read already carries the
question ids, options and flags — everything `answer-questions` takes. `--raw`
is for the other half of the job: telling a human what a request you *cannot*
answer is about. So read plain first, and add `--raw` only once you know the
entry is out of scope and you mean to describe it.

`actions[].action` names the sub-command that performs it.

**Read `actions` before answering.** A question whose only action is `cancel-questions` is
structurally unanswerable here. Four cases: no questions at all, a secret one,
one carrying no id, or two sharing the same id. The last two are the same
problem — `--choice` names a question by its id, so a set you cannot name one
by one can never be answered in full, and every attempt earns
`missing_answers`. Cancel it, or answer it in the web UI.

Nothing pending is exit 0 with an empty list; no agent at all is exit 0 with
`agent_state: "dead"`. Neither is an error.

### Answer — unblock a waiting session

```bash
$TWICC session <SESSION_ID> answer-questions --choice '1=PostgreSQL'
$TWICC session <SESSION_ID> cancel-questions
```

Answers only a **question**. A tool approval or an MCP elicitation is refused
(`not_a_question`) and stays the web UI's business.

- **The id comes from `pending-requests`**, and it is not the same thing on both
  providers: a 1-based index on Claude Code, which has no question ids, and the
  native id on Codex. Read first, then answer.
- `--choice` splits on the **first** `=`, so a value may contain more. Repeat it
  once per question, or several times on one id for a multi-select question.
- A value matching no option is free text, accepted when `allows_free_text` says
  so, and exclusive of the options — do not mix the two on one question.
- `--request-id` is optional when one question is pending, required when several
  are. **Pass it anyway from a script:** a request that cleared between your read
  and your answer then surfaces as `request_gone` instead of a wrong answer.
- Answer every question and it submits. Answer some and the agent gets a partial
  answer, on Claude Code only. Answer none and it is refused — `cancel-questions`
  is how you decline.
- **A session cannot answer its own question.** Answering for the human is the
  one thing this command must not let an agent do.

Exit 3 carries the reason: `no_pending_question`, `ambiguous_request`,
`not_a_question`, `request_gone`, `agent_dead`, `missing_answers`,
`unknown_question_id`, `free_text_not_allowed`, `free_text_exclusive`,
`multi_select_unsupported`, `secret_answer_unsupported`, `option_not_accepted`,
`self_answer_refused`, `provider_disabled`.

### Agents — list subagents

```bash
$TWICC session <SESSION_ID> agents [--limit N] [--offset N] [--paginated] [--slim | --full]
```

Only valid on parent sessions (errors on subagents). Returns provider-internal subagents, not sessions created via `create-session`; use `$TWICC topology <ID|self>` for the `spawned_by` tree (skill: `twicc-topology`). Ordered by most recently active.

### Plan — the session's tracked plan documents

```bash
$TWICC session <SESSION_ID> plan
$TWICC session <SESSION_ID> plan <PATH>
$TWICC session <SESSION_ID> plan --list
```

The session's plan-like documents: the native Claude plan (what *plan mode* writes) plus detected plans/specs/handoffs/notes... written by the session or its subagents — **both providers**.

Without argument: the content of the **most recently updated** tracked document (not necessarily the native plan), as `{path, abs_path, content}`. Errors (exit 1) when the session tracks none — the default view's `plan_paths` field tells you up front.

With a `PATH` argument: the content of that document. Matched against the tracked entries only (never an arbitrary filesystem path): give the stored `path` exactly as shown by `--list` (project-relative when the doc lives under the project, absolute otherwise) or its resolved absolute path. Errors (exit 1) on unknown path or missing file.

`--list`: every tracked document, newest first:

```json
{"plan_paths": [{"path": "docs/plans/feature-plan.md", "exists": true,
                 "created_at": "...", "updated_at": "...", "source": "detected",
                 "abs_path": "/abs/path/to/docs/plans/feature-plan.md"}, ...]}
```

`source` is `detected`, `subagent` (written by a subagent), or `claude_plan` (the native plan); `abs_path` is always resolved (worktree-aware). The default view's `plan_paths` field carries the same entries (without `abs_path`), so you rarely need `--list` after fetching the session.

### Workflows — list runs

```bash
$TWICC session <SESSION_ID> workflows [--limit N] [--offset N] [--paginated] [--result] [--full]
```

This session's workflows, newest first (**Claude Code** only). The session's `has_workflows` boolean says whether any exist.

The listing answers *which runs are there*: `id`, `workflowName`, `summary`, `status`, `statusKind`, `startTime`, `durationMs`, `agentCount`, `totalTokens`, `totalToolCalls`, `phases`, `phaseCompletion`, `scriptPath`, `defaultModel`.

- `--result` — add each run's `result`, what it produced. Use it when you are reading conclusions, not choosing a run.
- `--full` — the envelope verbatim, execution trace included (`workflowProgress`, `script`, `logs`, `args`, `result`). **Can be megabytes**: `workflowProgress` carries a prompt and a result preview per agent, and a long run has hundreds. For one run, `session workflow <ID>` gives the same thing without listing the others.

### Workflow — one run

```bash
$TWICC session <SESSION_ID> workflow <ID>
```

One workflow, by the `id` from `workflows`. Errors (exit 1) when unknown.

## Examples

```bash
$TWICC session abc123-def456
$TWICC session abc123 content 5
$TWICC session abc123 content 10-20
$TWICC session abc123 content --contains "TypeError"
$TWICC session abc123 content --contains TypeError --contains "auth.py"
$TWICC session abc123 content 10-200 --contains "TypeError"
$TWICC session abc123 messages --tail 1
$TWICC session abc123 messages --role user
$TWICC session abc123 messages --contains auth
$TWICC session abc123 messages --role assistant --contains auth --tail 1
$TWICC session abc123 agents
$TWICC session abc123 agents --limit 50
$TWICC session abc123 plan
$TWICC session abc123 plan docs/plans/feature-plan.md
$TWICC session abc123 plan --list
$TWICC session abc123 pending-requests
$TWICC session abc123 pending-requests --raw
$TWICC session abc123 answer-questions --choice '1=PostgreSQL' --choice '2=Redis'
$TWICC session abc123 cancel-questions --request-id req-9
$TWICC session abc123 workflows
$TWICC session abc123 workflows --limit 5
$TWICC session abc123 workflow wf_cd590ff1
```

## Related commands

- `$TWICC sessions wait-reply` — the plural of `wait-reply`: several at once, one budget for the batch. Skill: `twicc-sessions`.
- `$TWICC sessions` — find session IDs. Skill: `twicc-sessions`.
- `$TWICC process <session_id>` — live process state and PID. Skill: `twicc-process`.
- `$TWICC topology <ID|self>` — map spawned sessions around this node. Skill: `twicc-topology`.
- `$TWICC search "<query>"` — results include `session_id` + `line_num` for use with `content`. Skill: `twicc-search`.
- `$TWICC project <project_id>` — project details. Skill: `twicc-project`.

## How to present results

1. Default: summarize title, date, model, branch.
2. Content: show items in readable form, distinguishing user vs assistant vs tool.
3. Messages: render transcript in order, prefixing each entry with its role.
4. Agents: list with titles; offer to inspect any specific subagent.
5. Plan: render the `content` markdown as-is.
6. Pending request: say what is waiting and whether you can answer it; quote the question and its options.
7. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
8. Only include cost information if explicitly asked.
