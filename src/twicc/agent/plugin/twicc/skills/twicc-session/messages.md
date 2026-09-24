# `session <SESSION_ID> messages` — uniform transcript

User and assistant messages only, one shape across providers: no tool calls, no system noise. The command to read a session's answer. MCP tool: `mcp__twicc__session_messages`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC session <SESSION_ID> messages [OPTIONS]
```

- `--range N` / `--range N-M` — messages whose JSONL `line_num` is in the range (same numbering as `content`), not the Nth message.
- `--role user|assistant` — one side only.
- `--contains TEXT` — messages whose `text` contains the substring. Repeatable, **AND-combined**, **case-insensitive**. Matches the extracted text, not the raw JSONL (unlike `content --contains`).
- `--is-final true|false|null` — messages whose `is_final` has one of these values. Repeatable, **OR-combined**. Without it, nothing is filtered: `null` is dropped only when you ask for a set without it, and all three values filter nothing.
- `--limit N` / `--offset N` — a window. **Until 2026-10-01 a call without `--limit` returns every message; from that date it pages at 20**: pass `--limit` or `--tail` when you need more, and read `has_more`.
- `--tail N` — the last N messages. Mutually exclusive with `--limit`/`--offset`.
- `--paginated` — wraps the result in `{items, pagination}` (`limit`, `offset`, `total`, `has_more`) and pages at **20** without `--limit`. **Opt-in until 2026-10-01, then the only behaviour.** Under `--tail N`, the window reported is the one it covers, and `has_more` means messages remain **before** it.

`--contains` and `--is-final` apply before the window. Without them (or with `--is-final` listing all three values), `total` counts raw items, some of which hold no text: `has_more` can be a rare false positive, never a false negative.

Exit 1 with `session not found` while a new session has no user message yet: nothing to read yet, not an error to report.

## Output format

```json
{"items": [
  {"line_num": 3, "text": "Hello, can you help me?", "role": "user", "timestamp": "2025-03-10T14:30:00+00:00", "is_final": null},
  {"line_num": 4, "text": "Sure — what do you need?", "role": "assistant", "timestamp": "2025-03-10T14:30:02+00:00", "is_final": true}
 ], "pagination": {"limit": 20, "offset": 0, "total": 2, "has_more": false}}
```

Without `--paginated` (until 2026-10-01): the bare `items` array.

`is_final` — `true`: the assistant message that **closes a turn**. `false`: an intermediate one, between tool calls. `null`: unknown — always on a user message, and on an assistant message with no marker, or one TwiCC does not recognise.

**Trust `true`; read `null` as "probably final, no proof".** An assistant `null` comes from:

- a line TwiCC built from a slash-command's output (a `/goal` or `/plugin` ack);
- a message Claude Code split over several lines, marker on the last one only — **the one intermediate case**, frequent on subagent transcripts, rare elsewhere;
- a message interrupted by the user or cut by an API error (both end the turn);
- an old transcript format without markers.

## Reading one session's answer

Take the last message, with **no `--role` and no `--is-final`**:

```bash
$TWICC session <ID> messages --tail 1
```

| What comes back | Reading |
|---|---|
| `assistant`, `is_final: true` | The answer. |
| `assistant`, `is_final: false` | Still working, or stopped mid-turn. Retry; do not use the text. |
| `assistant`, `is_final: null` | Use it; it does not prove the turn ended. |
| `user` | Nothing readable since: the agent has not started, stopped before its first token, or its closing message was empty. `process.state` (`$TWICC session <ID>`, or `sessions get <ID>` right after a spawn) says whether it still runs. |
| empty `items` | The last item has no text (an empty answer, or a Codex inter-agent record). **Re-read with `--tail 2` and apply this table again.** Never widen further. |

Why no filter: `--is-final true` spans the **whole session**, so mid-turn it returns a previous turn's answer, which looks valid. `--role assistant` hides a trailing user message, with the same effect. A window wider than 2 reaches the previous turn too.

## Examples

```bash
$TWICC session parent messages --tail 1
$TWICC session abc123 messages --role assistant --is-final true --tail 5   # the answers, without the commentary (from 2026-10-01 a bare call pages at the 20 OLDEST)
$TWICC session abc123 messages --role assistant --is-final false --tail 5  # the intermediate messages only
$TWICC session abc123 messages --range 120-160                             # a window found with `search`
$TWICC session abc123 messages --contains auth
$TWICC session abc123 messages --role assistant --contains auth --tail 1
```

## Related commands

- `$TWICC session <ID> content` — every raw item, tool calls included. File: `content.md`.
- `$TWICC session <ID> wait-reply` — block until the session answers, instead of polling. File: `wait-reply.md`.
- `$TWICC search "<query>"` — find the session and the lines for `--range`. Skill: `twicc-search`.

## How to present results

1. Render the transcript in order, prefixing each entry with its role.
