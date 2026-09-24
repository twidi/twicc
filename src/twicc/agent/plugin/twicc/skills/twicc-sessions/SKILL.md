---
name: twicc-sessions
description: List sessions tracked by TwiCC with each one's live process state, batch-look them up by id, wait until several of them conclude, or stop the agents behind them. Use when you or the user want to browse sessions, find a session ID, filter by project, see which are still running, block until several of them conclude, or batch-stop them.
---

# TwiCC Sessions

List sessions, batch-look them up by id, wait on several, or stop several. This file is the index; each sub-command has its own file next to it.

## When to use

- You or the user want to list or browse sessions.
- You need to find a session ID.
- You have known session_ids and want to batch-fetch their metadata — `sessions get` (returns subagents, archived and hidden too, since you named them).
- You want to block until several sessions conclude — one wall-clock wait instead of one per session.
- You want to stop the agents behind several sessions (e.g. every child of an orchestration).

## How to invoke

**Prefer the `mcp__twicc__*` tools — inside a TwiCC session you normally have all of them.** One per command below (the command with `/` and `-` turned into `_`, e.g. `mcp__twicc__create_session`, `mcp__twicc__update_session_settings`). Use them instead of the `$TWICC` CLI: same arguments, same JSON result, no shell, and your session identity travels with the call so `self`/`parent` resolve on their own. **Most of them are deferred, so a tool missing from your visible tool list is not a missing tool** — search your full tool list for the one you need (`ToolSearch` on Claude Code, `ALL_TOOLS` on Codex), and fall back to the `$TWICC` CLI below only when the search finds nothing (outside a session, or when scripting from a terminal).

TwiCC's executable varies by launch mode (uvx, dev, installed tool). ALWAYS USE THIS TO RESOLVE $TWICC AT THE START OF EACH BASH INVOCATION:

```bash
TWICC=${TWICC_BIN:-$(command -v twicc 2>/dev/null)}
[ -n "$TWICC" ] || { echo "TwiCC executable not found in this context" >&2; exit 1; }
```

Then run `$TWICC <args>` — **never quote `$TWICC`** (use `$TWICC args`, never `"$TWICC" args`): it may expand to multiple words, which quoting would break.

## Common to all sub-commands

- The listing, `wait-reply` and `stop` select sessions with the same filters (`--project`, `--spawned-by`, `--annotation`...). Their full description is in `list.md`, section "Filters"; `wait-reply.md` and `stop.md` give which ones each accepts and what each refuses or forces.
- You are in TwiCC — link to a session: `[link text](/project/{project_id}/session/{session_id})`.

## Sub-commands

**ALWAYS READ THE SUB-COMMAND'S FILE BEFORE YOU CALL IT.** It holds the options, the output shape, the pitfalls, and any result presentation beyond the common rules above. The files sit next to this `SKILL.md`.

| Sub-command | Purpose | File |
|---|---|---|
| *(none)* | The listing: filters, projection, live process state, pagination. | `list.md` |
| `get` | Batch lookup by id, any session type, `known` flag per entry. | `get.md` |
| `wait-reply` | Block until several sessions conclude, one budget for the batch. | `wait-reply.md` |
| `stop` | Stop the agents behind several sessions (never the calling one). | `stop.md` |

## Related commands

- `$TWICC session <session_id|self|parent>` — one session: its row, transcript, wait, stop. Skill: `twicc-session`.
- `$TWICC topology <ID|self>` — map a spawned-session tree. Skill: `twicc-topology`.
- `$TWICC project <PROJECT>` / `$TWICC projects` — project details or listing. Skill: `twicc-project` / `twicc-projects`.
- `$TWICC search "<query>"` — full-text search across sessions. Skill: `twicc-search`.
