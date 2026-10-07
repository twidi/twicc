# `create-session --hidden/--no-question-widget/...` — session behavior

Hide the session, turn off question widgets, mute its notifications, annotate it, attach files. MCP tool: `mcp__twicc__create_session`. Read `SKILL.md` first: it resolves `$TWICC`.

## Usage

```bash
$TWICC create-session [--hidden] [--question-widget|--no-question-widget] [--mute-on-user-turn] [--annotation KEY=VALUE]... [--annotations-file PATH] [--attach VALUE]... '<PROMPT>'
```

### `--mute-on-user-turn`

Suppresses only this session's finished-working notifications (toast, sound, browser notification, and Apprise user-turn event). Choose it when a controlling agent will read and handle the result. Questions and approvals still notify. Independent of `--hidden`; does not change global notification settings.

### `--no-question-widget`

By default, questions from the agent surface as an interactive UI widget: the user must click in the TwiCC UI to answer. Each provider has its own: Claude Code `AskUserQuestion`, Codex `request_user_input`.

- `--no-question-widget` removes the widget tool at start: questions appear as plain text in the conversation, readable via `messages` and answerable via `send-message`. Use it when driving the workflow from a script.
- **Always pass it to a child you drive yourself.** A widget answer travels on the UI channel only — neither `send-message` nor any CLI command can answer one — so a scripted child that opens a widget blocks until a human clicks.

### `--hidden`

Creates the session invisible in every user-facing listing, search, and broadcast (still counted in cost aggregates).

- Requires a non-interactive `--permission-mode`: Claude Code `bypassPermissions`/`dontAsk`; Codex `yolo`/`strict`. Alias `open` for the permissive pair, `strict` for the read-only pair.
- Forces `question_widget=False` automatically: passing `--question-widget` alongside is rejected.

**Restrictive modes (`dontAsk` / `strict`) are heavily sandboxed.** A hidden child in one of them can read project files but typically **cannot**:

- write any file (anywhere, including scratch or temp directories),
- access the network,
- run any shell command, so it **cannot use the `$TWICC` CLI**.

It can still use the `mcp__twicc__*` tools, which work in every mode — including `mcp__twicc__send_message` with `parent` to push a message back to you. Only when the TwiCC MCP server is disabled is the child's only output channel the final assistant message of its turn:

- **Simplest: `--wait-reply`**, which creates the session and hands back the answer in one call (file: `wait-reply.md`).
- Failing that, **the parent is responsible** for fetching it via `$TWICC session <ID> messages --tail 1` (skill: `twicc-session`). Check the entry's `is_final`: `true` confirms the turn's closing message; `false` means you read an intermediate one and must retry; `null` means unknown — use the entry, but do not treat it as proof the turn ended. Read too early, a `false` is the difference between the child's answer and "I'll start by reading the file".

Use these modes for pure "analyst" workers (read code, return a synthesis as text). For anything that needs side effects, pick `bypassPermissions` (Claude Code) or `yolo` (Codex) — alias `open` for both — and accept the broader latitude.

### Annotations

- `--annotation KEY=VALUE` — add a free-form session annotation; repeatable. Dotted keys; scalar values: `true`, `false`, `null`, numbers, or strings.
- `--annotations-file PATH` — a JSON object file, merged before the `--annotation` flags. Use it for list or object values.

### Attachments

- `--attach VALUE` (repeatable) — a file of any type: a local path, a base64 data URI `data:<mime>;name=<percent-encoded file name>;base64,<data>` (`name=` optional), or, over `--remote`, `remote:<absolute path>` for a file on the remote server. TwiCC decides per file how the agent gets it: natively (images; PDF and text on Claude Code) or as a file in the session's `attachments/` artifacts folder. Inline data (data URIs, and local files sent over `--remote`) is limited to 50 MB in total per command; a file read from disk has no limit. On a local command line, Linux caps one argument at 128 KiB, so pass a larger local file by its path, not as a data URI. For a larger file, put it on a file storage service and pass its URL in the prompt, or pass a path the server reads: `remote:<absolute path>` over `--remote`, an absolute server path over MCP.

## Errors

Local (exit 1):

- `hidden_constraint_violation` — `--hidden` used with an interactive permission mode or `--question-widget`.
- `invalid_annotation` — `--annotation` must use `key=value`.
- `invalid_annotation_path` — dotted annotation keys cannot contain empty segments.
- `annotation_path_conflict` — an annotation path conflicts with an existing scalar or object.
- `annotation_non_scalar` — use `--annotations-file` for list or object values.
- `invalid_annotations_file` — file missing, invalid JSON, or root value is not an object.
- `not_a_file` / `relative_path` / `remote_requires_remote` / `invalid_data_uri` — a bad `--attach` value.
- `attachments_too_large` — more than 50 MB of inline data; the message says what to do instead.
- `attachment_stage_failed` — the copy of a file failed (disk full, permission).

Server (exit 3):

- `invalid_annotations` — annotations must be a JSON object.
- `attachment_missing` / `attachment_not_ready` / `attachment_commit_failed` — a file could not be delivered; create the session again.
- `attachments_with_command` — files cannot ride a Codex built-in command.
- `invalid_attachments` — the request came from an older `twicc` CLI; update it.

## Examples

```bash
$TWICC create-session --provider claude_code --no-question-widget 'Resize images — ask me before overwriting'
$TWICC create-session --mute-on-user-turn --no-question-widget 'Review the patch and report the risks'
$TWICC create-session --hidden --permission-mode strict --wait-reply 'Summarize the architecture of src/'
$TWICC create-session --annotation role=reviewer --annotation task.priority=2 'Review this change'
$TWICC create-session --annotations-file /home/twidi/session-annotations.json 'Run the annotated task'
$TWICC create-session --provider claude_code --attach /home/twidi/screenshot.png --attach /home/twidi/report.pdf 'What do you think?'
```

## Related commands

- `$TWICC update-session <session_id> annotations|hide|unhide|mute|notify` — change these after creation. Skill: `twicc-update-session`.
- `$TWICC session <ID> messages --tail 1` — read a hidden child's answer. Skill: `twicc-session`.
