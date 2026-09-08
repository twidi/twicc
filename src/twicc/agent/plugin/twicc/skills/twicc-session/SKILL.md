---
name: twicc-session
description: Inspect a single session — view metadata, read raw item content by line number, read user/assistant messages, list subagents, read its plan, or list/inspect its workflows. Use when you or the user want to examine a session, read conversation content, or explore subagent activity.
argument-hint: <session_id> [content|messages|agents|plan|workflows|workflow]
---

# TwiCC Session

Inspect a single session. Seven sub-commands:

- Default — full session metadata.
- `content [LINE_OR_RANGE] [--contains TEXT ...] [--limit N] [--offset N] [--tail N] [--paginated]` — raw JSONL items by line number and/or content substring(s) (provider-specific schema).
- `messages [--contains TEXT ...]` — user/assistant messages only, uniform shape across providers.
- `agents` — list subagents spawned by this session.
- `plan [PATH] [--list]` — the session's tracked plan documents (both providers): most recently updated one by default, a specific one by path, `--list` to enumerate.
- `workflows [--limit N] [--offset N] [--paginated]` — list this session's workflows (Claude Code only).
- `workflow <ID>` — show one (Claude Code only).

## When to use

- You or the user want details about a specific session.
- You want to read conversation content (raw items or clean messages).
- You want to see which subagents were spawned by a session.
- You want to read the session's plan (e.g. inspect what a worker session planned).
- You want to list a session's workflows, or inspect one.

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

Works for regular sessions and subagents.

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
  "annotations": {"role": "reviewer"}
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

`--paginated` adds the `{items, pagination}` envelope and caps the page at **50** when no `--limit` is given, so `total` tells you how many items match before you pull them all. **From 2026-09-15 that is the only behaviour**: a call with no `--limit` returns a page, not every item in the session. It also counts as a selector on its own — `content --paginated` is a valid browse entry point, since a bounded page cannot dump the session.

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
- `--limit N` — cap results (default: no cap; 50 with `--paginated`).
- `--offset N` — skip first N messages (default: 0).
- `--tail N` — return the last N messages. Mutually exclusive with `--limit`/`--offset`.
- `--paginated` — wrap the result in `{items, pagination}` with `limit`, `offset`, `total` and `has_more`. Without an explicit `--limit` the page size becomes **50** instead of "everything" — and **from 2026-09-15 that is the only behaviour**, so an unfiltered call returns a page rather than the whole session. With `--tail N` the reported window is the range it covers, and `has_more` means messages remain **before** it. Without `--contains`, `total` counts raw items — a few extract to nothing and are dropped — so `has_more` can be a rare false positive, never a false negative.

```json
[
  {"line_num": 3, "text": "Hello, can you help me?", "role": "user", "timestamp": "2025-03-10T14:30:00+00:00"},
  {"line_num": 4, "text": "Sure — what do you need?", "role": "assistant", "timestamp": "2025-03-10T14:30:02+00:00"}
]
```

Common patterns:
- Last agent reply: `messages --role assistant --tail 1`
- Last N exchanges: `messages --tail N`
- Focused window from search: `messages --range A-B`
- Messages mentioning a term: `messages --contains "auth"`
- Last reply mentioning a term: `messages --role assistant --contains "auth" --tail 1`

### Agents — list subagents

```bash
$TWICC session <SESSION_ID> agents [--limit N] [--offset N] [--paginated]
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
$TWICC session <SESSION_ID> workflows [--limit N] [--offset N] [--paginated]
```

This session's workflows, newest first (**Claude Code** only). The default view's `has_workflows` boolean says whether any exist.

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
$TWICC session abc123 workflows
$TWICC session abc123 workflows --limit 5
$TWICC session abc123 workflow wf_cd590ff1
```

## Related commands

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
6. You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.
6. Only include cost information if explicitly asked.
