---
name: twicc-session
description: Inspect, wait on, unblock, or stop a single session — view metadata, read raw item content by line number, read user/assistant messages, list subagents, read its plan, list/inspect its workflows, see what it is waiting on and answer its question, or stop its live agent. Use when you or the user want to examine a session, read conversation content, explore subagent activity, block until it answers, unblock a session waiting on a human, or stop its agent.
argument-hint: <session_id|self|parent> [--slim|--full] [content|messages|agents|plan|wait-reply|pending-requests|answer-questions|cancel-questions|stop|workflows|workflow]
---

# TwiCC Session

Inspect, wait on, unblock, or stop a single session. This file is the index; each sub-command has its own file next to it.

## When to use

- You or the user want details about a specific session.
- You want to read conversation content (raw items or clean messages).
- You want to see which subagents were spawned by a session.
- You want to read the session's plan (e.g. inspect what a worker session planned).
- You want to list a session's workflows, or inspect one.
- A session is blocked on a human and you want to see what it asks, or answer it.
- You want to stop a session's live agent, your own included.

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Common to all sub-commands

- The first argument is `<SESSION_ID>`, and on most sub-commands `self` (your own session) or `parent` (the session that spawned you). Each file gives the exceptions.
- You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.

## Sub-commands

**ALWAYS READ THE SUB-COMMAND'S FILE BEFORE YOU CALL IT.** It holds the options, the output shape, the pitfalls, and any result presentation beyond the common rules above. The files sit next to this `SKILL.md`.

| Sub-command | Purpose | File |
|---|---|---|
| *(none)* | The session row: metadata, agent settings, live process state. | `row.md` |
| `content` | Every raw JSONL item, tool calls included, by line or substring. Provider-specific schema. | `content.md` |
| `messages` | User/assistant text only, one shape across providers. Use it to read an answer. | `messages.md` |
| `wait-reply` | Block until the session answers, or blocks on a human. | `wait-reply.md` |
| `pending-requests` / `answer-questions` / `cancel-questions` | See what the session is waiting on; answer or decline its question. | `questions.md` |
| `stop` | Stop the live agent, your own included. | `stop.md` |
| `agents` | List the provider-internal subagents. | `agents.md` |
| `plan` | Read the session's tracked plan documents. | `plan.md` |
| `workflows` / `workflow` | List the session's workflow runs, or show one (Claude Code only). | `workflows.md` |

## Related commands

- `$TWICC sessions` — find session IDs, or act on several sessions at once. Skill: `twicc-sessions`.
- `$TWICC topology <ID|self>` — map spawned sessions around a node. Skill: `twicc-topology`.
- `$TWICC search "<query>"` — full-text search across sessions. Skill: `twicc-search`.
