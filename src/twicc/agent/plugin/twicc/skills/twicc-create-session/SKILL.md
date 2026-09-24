---
name: twicc-create-session
description: Create a new TwiCC session with a prompt and optional agent settings (provider, model, effort, permission mode, etc.). Use when you or the user want to spawn a fresh Claude Code/Codex session, kick off a sub-task in another project, or scaffold from a script.
argument-hint: <prompt>
---

# TwiCC Create Session

Spawn a new agent session; it appears in TwiCC exactly as if started from the UI. This file covers the base command and indexes the option topics; each topic has its own file next to it.

## When to use

- You or the user want to start a new session in a project.
- You want to kick off work on a sub-task or separate project.
- A script needs to queue work into TwiCC programmatically.

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
$TWICC create-session [OPTIONS] '<PROMPT>'
```

- `PROMPT` — the first user message: inline text or a path to a UTF-8 file. Over `--remote` the file is read locally; prefix an absolute path with `remote:` to read it on the remote server instead. `@@` include markers are expanded (see `--no-expand`). Available slash/dollar commands: `$TWICC info commands` (skill: `twicc-info`).
- `--project PATH-OR-ID` — directory path or project id (**drop the leading dash** on ids). A non-existent directory is auto-created as a project. Default: the current working directory.
- `--provider claude_code|codex` — falls back to the target project's `default_provider` (inherited through parent projects / git worktree main repo), then the user's global default. `$TWICC info` shows the available providers, the default, and the disabled ones (skill: `twicc-info`). When orchestrating and choosing the provider **yourself**, prefer providers flagged `orchestration: true` there; an explicit user choice prevails for any enabled provider (see `twicc-orchestration`).
- `--preset NAME` — saved agent-settings preset; per-flag options override its values. `__defaults__` forces the user-configured defaults explicitly. List them with `$TWICC info presets` (skill: `twicc-info`).
- `--title TEXT` — **always pass this**: a concise 5–7 word title derived from the prompt. Do not rely on the auto-derived title.
- `--no-expand` — disable `@@` include expansion. By default an `@@/abs/path`, `@@~/path` or `@@{/path with spaces}` marker in the prompt (or in the file it is read from) is replaced by that file's UTF-8 content, recursively (5 levels max).
  - Inside a file, `@@./path` and `@@../path` resolve against that file's own directory (never the cwd), so only the entry point needs an absolute path; in inline text they are an error.
  - A missing file expands to nothing — a marker alone on its line takes the whole line with it, so includes are optional. A directory, unreadable or non-UTF-8 file is an error.
  - `@@@@` escapes a literal `@@`. The final text is capped at 500 KB.
  - Over `--remote`, markers resolve on the client; use `@@remote:/abs/path` for a file on the remote server.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30). If the CLI times out, the session may still get created.

## Output format

```json
{"status":"created","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"created","session_id":"...","...":"...","reply":{"outcome":"replied","line_num":19,"is_final":true,"since_line_num":0,"waited_seconds":4.3,"text":"..."}}
{"status":"validation_error","errors":[{"field":"--effort","code":"invalid_choice","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

The `reply` block comes only with `--wait-reply` (file: `wait-reply.md`). A `created` status only means the session started and the prompt was handed to the agent — not that the agent has finished; it keeps working in the background.

### Exit codes

- `0` — session created
- `1` — local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — server rejected
- `4` — server error
- `5` — timeout

## Errors

- Local (exit 1): `invalid_choice` — value out of the provider's allowed set (a typo on a supported field; a flag the provider does not support is silently ignored instead).
- Server (exit 3):
  - `provider_disabled` — enable the provider from the UI.
  - `project_not_found` / `project_no_directory` — `--project` did not resolve.
  - `manager_busy` — transient; retry.

Codes specific to a topic (annotations, `--hidden`, worktree) are in its file.

## Examples

```bash
$TWICC create-session 'Run the tests and fix the failing ones'
$TWICC create-session --project /home/twidi/dev/myproj --provider claude_code 'Add a /healthz endpoint'
$TWICC create-session --project /home/twidi/dev/myproj --preset 'deep think' /home/twidi/prompts/audit.md
$TWICC create-session --provider claude_code 'Hello'
# → {"status":"created","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## Topics

**ALWAYS READ THE TOPIC'S FILE BEFORE YOU USE ITS OPTIONS.** It holds the topic's options, their constraints, errors and examples; this file holds the base options, the general errors and basic examples, plus a short preview of some topic items. The files sit next to this `SKILL.md`. MCP tool for all: `mcp__twicc__create_session`.

| Topic | Purpose | File |
|---|---|---|
| Agent settings | `--model`, `--effort`, `--permission-mode`, `--thinking`, `--claude-in-chrome`, `--fast-mode`, `--context-max`: values, aliases, defaults, untrusted projects. | `agent-settings.md` |
| Wait for the answer, follow up | `--wait-reply`, `--wait-timeout`, `--no-reply-text`, the `outcome` table; then how to track, read, and continue the child. | `wait-reply.md` |
| Worktree | `--worktree-branch`, `--worktree-path`, `--worktree-start-from`: land the session in a new or existing git worktree. | `worktree.md` |
| Session behavior | `--hidden`, `--no-question-widget`, `--mute-on-user-turn`, annotations, attachments. | `session-behavior.md` |

## Related commands

- `$TWICC info [models|agent-settings|presets|commands]` — discover providers, models, agent-settings values, presets, and slash / dollar commands before crafting a session. Skill: `twicc-info`.
- `$TWICC send-message <session_id>` — send a follow-up. Skill: `twicc-send-message`.
- `$TWICC sessions --spawned-by self --active` — track sessions you spawned (`sessions get <ID>` for one spawned seconds ago). Skill: `twicc-sessions`.
- `$TWICC topology self` — map the spawned-session tree around you. Skill: `twicc-topology`.
- `$TWICC update-session <session_id> settings` — change agent settings. Skill: `twicc-update-session`.
- `$TWICC session <session_id>` — one session's row (reduced from 2026-10-01; `--full` for every field). Skill: `twicc-session`.
- `$TWICC sessions --project <PROJECT>` — browse sessions in the project. Skill: `twicc-sessions`.

## How to present results

1. On success, give the title, session id, and a clickable link: `[link text](/project/{project_id}/session/{session_id})`.
2. On validation error, summarize the failing fields with expected values.
3. On exit 3, diagnose from `errors[].code` (Errors above, or the topic's file).
