---
name: twicc-create-session
description: Create a new TwiCC session with a prompt and optional agent settings (provider, model, effort, permission mode, etc.). Use when you or the user want to spawn a fresh Claude Code/Codex session, kick off a sub-task in another project, or scaffold from a script.
argument-hint: <prompt>
---

# TwiCC Create Session

Spawn a new agent session. The session appears in TwiCC exactly as if started from the UI.

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

### Arguments

- `PROMPT` — the first user message. Inline text or a path to a UTF-8 file. For available slash/dollar commands, use `$TWICC info commands` (skill: `twicc-info`). Over `--remote` the file is read locally; prefix an absolute path with `remote:` to read it on the remote server instead. `@@/abs/path` (or `@@{/path with spaces}`) include markers are replaced by that file's content — see `--no-expand`.

### Options

- `--project PATH-OR-ID` — directory path or project id (**drop the leading dash** on ids). Non-existent directories are auto-created as projects. Defaults to the current working directory.
- `--provider claude_code|codex` — falls back to the target project's `default_provider` (inherited through parent projects / git worktree main repo), then the user's global default. Use `$TWICC info` to check available providers, which is the default, and which are disabled (skill: `twicc-info`). When orchestrating and choosing the provider **yourself**, prefer providers flagged `orchestration: true` there; an explicit user choice prevails for any enabled provider (see `twicc-orchestration`).
- `--preset NAME` — saved agent-settings preset. Per-flag options override preset values. `__defaults__` forces the user-configured defaults explicitly. Use `$TWICC info presets` to list available presets (skill: `twicc-info`).
- `--title TEXT` — **always pass this.** A concise 5–7 word title derived from the prompt. Don't rely on the auto-derived title.
- `--annotation KEY=VALUE` — add a free-form session annotation; repeatable.
- `--annotations-file PATH` — load session annotations from a JSON object file.
- `--no-expand` — disable `@@` include expansion. By default an `@@/abs/path`, `@@~/path` or `@@{/path with spaces}` marker in the prompt (or in the file it is read from) is replaced by that file's UTF-8 content, recursively (5 levels max). Inside a file, `@@./path` and `@@../path` resolve against that file's own directory (never the cwd), so only the entry point needs an absolute path; in inline text they are an error. A missing file expands to nothing — a marker alone on its line takes the whole line with it, so includes are optional; a directory, unreadable or non-UTF-8 file is an error; `@@@@` escapes a literal `@@`; the final text is capped at 500 KB. Over `--remote`, markers resolve on the client; use `@@remote:/abs/path` for a file on the remote server.
- `--timeout SECONDS` — seconds to wait for the server's response (default 30). If the CLI times out, the session may still get created.

### Worktree

Land the session in a git worktree of `--project`, in one command — the CLI counterpart of the UI's "new worktree" button. Either create a new worktree or adopt one that already exists on disk:

- `--worktree-branch BRANCH` — create the session in a NEW git worktree of `--project` on `BRANCH` (an existing local branch is checked out; a new one is created with `-b`). `--project` then names the **source repository**; the session lands in the worktree, which is registered as its own project linked back to the source (`worktree_of`) and inherits the source's agent defaults and trust. Requires `--worktree-path`.
- `--worktree-path PATH` — absolute path of the git worktree's directory. **With `--worktree-branch`**: where the NEW worktree is created (git rejects a non-empty target). **Without `--worktree-branch`**: an EXISTING worktree of `--project` to **adopt** — registered as its own project linked via `worktree_of` (no `git worktree add`), so the session opens in it. Adoption is idempotent (an already-registered worktree is reused). The path must be a real linked worktree of `--project`, not an arbitrary directory.
- `--worktree-start-from REF` — branch/revision the new branch starts from (only when `--worktree-branch` does not yet exist). Defaults to the source repo's current HEAD. Ignored for an existing-branch checkout. Only valid with `--worktree-branch`.

The settings flags below still resolve against `--project` (the source) — the worktree inherits from it, so the effective values match a UI draft opened in the worktree.

### Agent settings

All optional. A field you omit (and the preset doesn't set) takes the target project's `default_agent_settings` for the chosen provider (inherited through parent projects / git worktree main repo — see `twicc-project`), then the user's global default. The resolved values are frozen onto the session at creation, exactly like a session created from the UI. Use `$TWICC info models agent-settings` for authoritative model lists, valid values, and per-value restrictions (skill: `twicc-info`). The lists below are indicative.

- `--model VALUE` — Claude Code: `fable`, `opus`, `sonnet`, `fable-5`, `opus-5`, `opus-4.8`, `opus-4.7`, `opus-4.6`, `opus-4.5`, `sonnet-4.6`, `sonnet-4.5`. Codex: `gpt-astra`, `gpt-sol`, `gpt-terra`, `gpt-luna`, `gpt`, `gpt-sol-5.6`, `gpt-luna-5.6`.
- `--effort VALUE` — Claude Code: `low`, `medium`, `high`, `xhigh`, `max`. Codex: `low`, `medium`, `high`, `xhigh`, `max` (`max` needs a GPT-6 or GPT-5.6 model; silently demoted otherwise).
- `--permission-mode VALUE` — Claude Code: `default`, `auto`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`. Codex: `read_only`, `strict`, `auto`, `autonomous`, `auto_review`, `yolo`.
- `--thinking / --no-thinking` — Claude Code only.
- `--claude-in-chrome / --no-claude-in-chrome` — Claude Code only (Allows to manipulate browser tabs, take screenshots, etc.).
- `--fast-mode / --no-fast-mode` — Supported Claude Code and Codex models; increases credit usage.
- `--context-max VALUE` — Claude Code: `200k` or `1m` (silently capped to 200k on unsupported models). Codex: `272k` (fixed by the model; a divergent value is silently pinned to the model's window).
- `--question-widget / --no-question-widget` — Claude Code and Codex. See below.

### Aliases

Some settings also accept provider-agnostic aliases, resolved to each provider's concrete value — useful for cross-provider scripting without knowing the exact values:

- `--model` — `max`/`strongest` → top family, `medium`/`balanced` → middle family, `min`/`fastest`/`cheapest` → lightest family.
- `--effort`, `--context-max` — `max` → highest/largest, `min` → lowest/smallest.
- `--permission-mode` — `min`/`strict`/`safe` → most-locked (non-interactive), `max`/`open`/`full`/`yolo`/`bypass` → most permissive (non-interactive), `auto` → balanced (interactive).

A flag the chosen provider doesn't support (e.g. `--thinking` on Codex) is silently ignored (no-op), so one command works across a mix of providers.

**Untrusted projects.** In a project whose trust is *untrusted* — or not yet decided (unknown counts as untrusted) — `permission_mode` is restricted to a safe subset: `bypassPermissions` (Claude Code) / `yolo` (Codex) are unavailable. A session created there resolves `--permission-mode` against that subset — `min`/`safe`/`max` still work, with `max` → the most permissive *allowed* mode (Claude Code `acceptEdits`, Codex `auto_review`) — and an out-of-subset value (e.g. `bypass`) is clamped to the project's untrusted default with a note on stderr. When `--permission-mode` is omitted, the session seeds from the project chain's `permission_mode_if_untrusted` default, then the global untrusted default. See `twicc info agent-settings` → `permission_mode_if_untrusted` for the subset + its aliases. Project trust is a human-only decision; agents never set it.

### `--wait-reply` — create, then wait for the answer

```bash
$TWICC create-session "<PROMPT>" --wait-reply [--wait-timeout N] [--no-reply-text]
```

Keeps going after the session is created, until it answers, and adds a `reply` block to the result. One command instead of create, poll, read — and no window in which the answer can slip past you.

```json
{"status": "created", "request_uuid": "…", "session_id": "01a0c4d2-…",
 "provider": "claude_code", "project_id": "…",
 "reply": {"outcome": "replied", "line_num": 19, "is_final": true,
           "since_line_num": 0, "waited_seconds": 4.3, "text": "OK"}}
```

- `--no-reply-text` drops `text` and keeps `line_num` — use it when you only need the go-ahead, not the payload in your context. The key is **absent**, never `null`.
- `--wait-timeout N` caps the wait, whatever ends it. Default **300 s**, which is the ceiling MCP callers are asked to respect — and an MCP client may itself give up on a tool silent that long, so over MCP pass a shorter value and come back rather than riding the default to its end. From a shell, raise it freely for a long first turn. There is no way to disable it, though nothing caps how high you set it.
- A **pending request** ends the wait too — a tool approval or a question, which only a human can clear. It is not a slow turn: no line will ever arrive until someone clicks, so riding the budget out would buy nothing. `outcome` becomes `awaiting_user_input`, and an answer arriving in the same poll wins.
- `--wait-timeout` and `--no-reply-text` require `--wait-reply` — passing them alone is an error, not a no-op.

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

**An agent blocked on a click ends the wait**, with `outcome: awaiting_user_input`. Nothing would arrive before the deadline: it stays blocked until someone clears it. Read it with `$TWICC session <SESSION_ID> pending-requests` (skill: `twicc-session`). An entry whose `kind` is `question` you can answer yourself, with `answer-questions` — the plain read already carries the question ids and options, so **never pass `--raw` to answer**. Anything else is the user's to clear in the UI; `--raw` is how you tell them what it is about, and it returns the whole payload, diff included. Pass `--no-question-widget` to a session you drive yourself — though it does not rule the case out entirely: an MCP server's elicitation reaches that path in every permission mode.

**A timeout is not a failure.** Resume it with `$TWICC session <SESSION_ID> wait-reply --from <CURSOR>` (skill: `twicc-session`), which is the command that takes a cursor: pass the `line_num` when the ending consumed a line (`replied`, `provider_error`), the `since_line_num` otherwise. The agent keeps working and the session is intact — only the waiting stopped. Expect it on a worker whose first turn runs long: raise `--wait-timeout`, or take the `session_id` and come back later. **The exit code never reflects the wait**, only whether the session was created, so a script must read `outcome` rather than `$?`.

The wait reads the **transcript**, not the process state. A session held busy by a Monitor, a live subagent or a pending wake-up has already written its answer long before it goes idle — this reports it at once instead of hours later.

### Session behavior

- `--mute-on-user-turn` — suppress only this session's finished-working notifications (toast, sound, browser notification, and Apprise user-turn event). Choose it when a controlling agent will read and handle the result. Questions and approvals still notify. The option is independent of `--hidden` and does not change global notification settings.

### `--hidden`

Creates the session invisible in every user-facing listings, search, and broadcasts (still counted in cost aggregates). Requires a non-interactive `--permission-mode` (Claude Code: `bypassPermissions`/`dontAsk`; Codex: `yolo`/`strict`; alias `open` for the permissive pair, `strict` for the read-only pair). `--hidden` forces `question_widget=False` automatically — passing `--question-widget` alongside is rejected.

**Restrictive modes (`dontAsk` / `strict`) are heavily sandboxed.** A hidden child running in one of these modes can read files from the project but typically **cannot**:
- write any file (anywhere, including scratch or temp directories),
- access the network,
- run the `twicc` CLI (it writes to its DB, logs, and `uv` cache — all blocked),
- therefore **invoke any TwiCC skill** (every skill goes through the `twicc` CLI),
- therefore **send a message back to its parent** via `twicc-send-message`.

The child's only output channel is the final assistant message of its turn. **The simplest way to collect it is `--wait-reply`** (see above), which creates the session and hands back the answer in one call. Failing that, **the parent is responsible** for fetching it via `$TWICC session <ID> messages --tail 1` (skill: `twicc-session`). Check the entry's `is_final` field before using it: `true` confirms the turn's closing message, `false` means you read an intermediate one and must retry, `null` means unknown — use the entry, but do not treat it as proof the turn ended. Read too early, a `false` is the difference between the child's answer and "I'll start by reading the file". Use these modes for pure "analyst" workers (read code, return a synthesis as text); for anything that needs side effects, pick `bypassPermissions` (Claude Code) or `yolo` (Codex) — alias `open` for both — and accept the broader latitude.

### `--no-question-widget`

By default, questions from the agent surface as an interactive UI widget — the user must click in the TwiCC UI to answer. Each provider has its own: Claude Code `AskUserQuestion`, Codex `request_user_input`. Pass `--no-question-widget` when driving the workflow from a script: the widget tool is removed at start, so questions appear as plain text in the conversation, readable via `messages` and answerable via `send-message`.

Always pass it to a child you drive yourself. A widget answer travels on the UI channel only — neither `send-message` nor any CLI command can answer one, so a scripted child that opens a widget blocks until a human clicks.

### Annotations

- `--annotation KEY=VALUE` supports dotted keys and scalar values: `true`, `false`, `null`, numbers, or strings.
- `--annotations-file PATH` must contain a JSON object and is merged before `--annotation` flags.
- Use `--annotations-file` for list or object values.

### Attachments

- `--attach PATH` (repeatable). Accepted types (sniffed by magic bytes): Claude Code: PNG, JPEG, GIF, WebP, PDF, text/plain; Codex: images only. Per-file cap: 5 MB. Per-batch cap: 100 files, 32 MB. Images are auto-resized to the provider/model's long-edge cap. Over `--remote`, prefix an absolute path with `remote:` to read it on the remote server instead.

## Errors

### Local (exit 1)

- `invalid_choice` — value out of the provider's allowed set (a typo on a supported field; a flag the provider doesn't support is silently ignored instead).
- `hidden_constraint_violation` — `--hidden` used with an interactive permission mode or `--question-widget`.
- `invalid_annotation` — `--annotation` must use `key=value`.
- `invalid_annotation_path` — dotted annotation keys cannot contain empty segments.
- `annotation_path_conflict` — an annotation path conflicts with an existing scalar or object.
- `annotation_non_scalar` — use `--annotations-file` for list or object values.
- `invalid_annotations_file` — file missing, invalid JSON, or root value is not an object.
- `missing_worktree_branch` — `--worktree-start-from` given without `--worktree-branch` (start-from only shapes new-branch creation).
- `missing_worktree_path` — `--worktree-branch` given without `--worktree-path`.
- `invalid_worktree_path` — `--worktree-path` must be an absolute path.

### Server (exit 3)

- `provider_disabled` — enable the provider from the UI.
- `project_not_found` / `project_no_directory` — `--project` didn't resolve.
- `invalid_annotations` — annotations must be a JSON object.
- `manager_busy` — transient; retry.
- `not_git_repo` — a worktree flag was used but `--project` is not a git repository.
- `not_a_worktree` — `--worktree-path` (adoption, no `--worktree-branch`) is not a real linked worktree of `--project`, or its directory is gone.
- `project_already_exists` — a project is already registered for the new `--worktree-path` (creation only; adoption reuses it instead).
- `start_from_not_found` — `--worktree-start-from` is not an existing local branch.
- `git_error` — `git worktree add` failed (branch already checked out, non-empty target, …); the git message is relayed verbatim.

## Output format

```json
{"status":"created","session_id":"...","provider":"...","project_id":"...","request_uuid":"..."}
{"status":"created","session_id":"...","...":"...","reply":{"outcome":"replied","line_num":19,"is_final":true,"since_line_num":0,"waited_seconds":4.3,"text":"..."}}
{"status":"validation_error","errors":[{"field":"--effort","code":"invalid_choice","message":"..."}]}
{"status":"rejected","errors":[{"field":"...","code":"...","message":"..."}],"request_uuid":"..."}
{"status":"failed","error":"...","request_uuid":"..."}
{"status":"timeout","received_seen":true,"message":"...","request_uuid":"..."}
```

### Exit codes

- `0` — Session created
- `1` — Local validation error
- `2` — TwiCC server not running, or bad CLI usage (unknown option, missing argument; the error message tells them apart)
- `3` — Server rejected
- `4` — Server error
- `5` — Timeout

## Examples

```bash
$TWICC create-session 'Run the tests and fix the failing ones'
$TWICC create-session --project /home/twidi/dev/myproj --provider claude_code 'Add a /healthz endpoint'
$TWICC create-session --project /home/twidi/dev/myproj --preset 'deep think' /home/twidi/prompts/audit.md
$TWICC create-session --provider claude_code --preset 'deep think' --effort low 'Quick review of last commit'
$TWICC create-session --provider claude_code --attach /home/twidi/screenshot.png --attach /home/twidi/report.pdf 'What do you think?'
$TWICC create-session --annotation role=reviewer --annotation task.priority=2 'Review this change'
$TWICC create-session --annotations-file /home/twidi/session-annotations.json 'Run the annotated task'
$TWICC create-session --provider claude_code --no-question-widget 'Resize images — ask me before overwriting'
$TWICC create-session --mute-on-user-turn --no-question-widget 'Review the patch and report the risks'
$TWICC create-session --project /home/twidi/dev/myproj --worktree-branch feature/login --worktree-path /home/twidi/dev/myproj.worktrees/feature-login 'Implement the login flow'
$TWICC create-session --provider claude_code 'Hello'
# → {"status":"created","session_id":"...","provider":"claude_code","project_id":"...","request_uuid":"..."}
```

## Following up

A `created` status only means the session started and the prompt was handed to the agent — not that the agent has finished. It keeps working in the background.

**Map spawned work:** `$TWICC topology self` shows the full spawned-session tree rooted at your top-level ancestor, with compact process state for every node (skill: `twicc-topology`).

**Wait for the answer later:** `$TWICC sessions wait-reply <SESSION_ID>...` waits until each child answers or blocks on a pending request (skill: `twicc-sessions`). Each wait starts after the child's last user message, so an answer already given is returned (except while a session's compute is not current — e.g. right after a TwiCC restart: then pass `--since` an instant before the spawn or the send). Exit 0 whatever the outcomes: read `summary.all_replied` and each `outcome`.

**Check state (snapshot):** `process.state` from `$TWICC sessions get <SESSION_ID>` (skill: `twicc-sessions`) — `session <ID>` exits 1 until the watcher writes the new session's row:
- `assistant_turn` → still working.
- `awaiting_user_input` → blocked on a pending request. Do NOT call `send-message`: it is refused. Read what is being asked with `$TWICC session <ID> pending-requests` — **not** `messages`, which does not carry it. A `question` you answer with `answer-questions`; anything else needs the user in the UI.
- `user_turn` → done; fetch the reply with `$TWICC session <ID> messages --tail 1`.
- `starting` → still booting; retry shortly.
- `dead` → no live process: the turn finished, or the agent stopped. Check `messages --tail 1`: if the last message is from the assistant **and its `is_final` is `true`**, the turn completed; `is_final: false` means it stopped mid-turn; `is_final: null` leaves it undecided — the text is probably the answer, but nothing here proves the turn ended; a trailing **user** message means nothing readable came back; an **empty list** means the last item carries no readable text — re-read with `--tail 2` and apply the same checks to what comes back, keeping in mind that "the child's closing message was empty" is a real outcome. Do not widen further: a longer window reaches the previous turn, whose closing message also says `is_final: true`. Read the **last** message and check its field, rather than filtering on `--is-final true`: the filter spans the whole session and would hand you a previous turn's answer.

**Continue the conversation:** once at `user_turn`, post a follow-up with `$TWICC send-message <SESSION_ID> '<text>'` (skill: `twicc-send-message`). To change settings mid-session, use `$TWICC update-session <SESSION_ID> settings ...` (skill: `twicc-update-session`).

**Let the child talk back:** to enable async replies, instruct the spawned session in the prompt to load the `twicc-send-message` skill and use `send-message parent '<text>'` — the `parent` keyword resolves to you via its `spawned_by` link, and the reply lands in your own session prefixed with the child's id. Loading the skill is what gives the child the `$TWICC` resolution and full invocation syntax. **Not available under `--permission-mode dontAsk` / `strict`** (the `twicc` CLI itself is blocked there — see the `--hidden` section above); fetch the child's final message via `$TWICC session <ID> messages --tail 1` instead.

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
3. On exit 3, diagnose from `errors[].code` (see Errors section above).
