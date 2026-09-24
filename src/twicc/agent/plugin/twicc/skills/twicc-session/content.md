# `session <SESSION_ID> content` — raw items

Every raw JSONL item of a session, tool calls and results included — unlike `messages` and `search`, which see user/assistant text only. MCP tool: `mcp__twicc__session_content`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC session <SESSION_ID> content [LINE_OR_RANGE] [--contains TEXT ...] [--limit N] [--offset N] [--tail N] [--paginated]
```

**At least one selector is required** (`LINE_OR_RANGE`, `--contains`, `--limit`/`--offset`, `--tail`, `--paginated`): a bare call would return every raw item, hundreds of megabytes on a long session.

- `LINE_OR_RANGE` — `5`, or `10-20` (inclusive). An absolute address in the JSONL; `last_line` from the session row is the upper bound.
- `--contains TEXT` — items whose raw content contains the text. Repeatable, **AND-combined**. **Case-insensitive**, matched on the **raw JSONL line**: it also matches JSON keys (`"role"`, `"type"`), and a newline is stored as `\n`, so a query spanning a line break never matches. With a range, it searches inside the range.
- `--limit N` / `--offset N` — a window over what the range and `--contains` kept, applied **last**. To page through a `--contains` search, use them, not the range.
- `--tail N` — the last N **matches**. The way to reach the end of a filtered result without a first call to learn `total`. Mutually exclusive with `--limit`/`--offset`.
- `--paginated` — wraps the result in `{items, pagination}` and pages at **20** without `--limit`. Also a valid selector on its own. **Opt-in until 2026-10-01, then the only behaviour.** Under `--tail N`, the window reported is the one it covers (`offset = total - N`), and `has_more` means matches remain **before** it.

## Output format

A JSON array of `{line_num, content}`; empty when nothing matches (not an error). `content` is the raw JSONL object, in the provider's schema:

- `claude_code` — Claude API objects (user/assistant messages, `tool_use`, `tool_result`, …).
- `codex` — Codex schema (user/assistant messages, `function_call`, `function_call_output`, …).

```json
[{"line_num": 5, "content": {"type": "user", "timestamp": "2025-03-10T14:30:00.000Z",
  "message": {"role": "user", "content": [{"type": "text", "text": "Hello"}]}}}]
```

## Examples

```bash
$TWICC session abc123 content 5
$TWICC session abc123 content 10-20
$TWICC session abc123 content --contains "TypeError"
$TWICC session abc123 content --contains TypeError --contains "auth.py"
$TWICC session abc123 content 10-200 --contains "TypeError"
$TWICC session abc123 content --contains TypeError --tail 10
```

## Related commands

- `$TWICC session <ID> messages` — user/assistant text only, one shape across providers. File: `messages.md`.
- `$TWICC session <ID>` — `last_line`, the upper bound of a range. File: `row.md`.
- `$TWICC search "<query>"` — results carry `session_id` + `line_num` to feed `content`. Skill: `twicc-search`.

## How to present results

1. Show items in readable form, distinguishing user, assistant and tool.
