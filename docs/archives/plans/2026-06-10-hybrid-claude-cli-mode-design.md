# Hybrid Claude CLI Mode — Design

**Date:** 2026-06-10
**Status:** Design approved by user — implementation plan to follow
**Scope:** Claude Code provider only (V1)

## 1. Motivation

Some TwiCC users are nostalgic for the Claude Code CLI experience. The hybrid mode
gives them the real interactive CLI (TUI) inside TwiCC, without losing TwiCC's rich
session reading experience:

- Claude Code runs as the **interactive CLI inside a dedicated tmux session**,
  rendered in an xterm.js terminal embedded in the message composer block.
- TwiCC keeps reading the session **JSONL** exactly as today: all items (messages,
  tools, costs, compact summaries…) keep rendering in the normal session view.
- The composer textarea, presets, snippets, attachments, and agent settings UI stay.
  A "send" action transfers the composed text into the TUI instead of going through
  the SDK.

Foundational fact (verified): the Python SDK does nothing magical — it spawns the
**same bundled CLI binary** with CLI flags (`claude_agent_sdk/_internal/transport/subprocess_cli.py`).
Everything TwiCC configures through `ClaudeAgentOptions` has a CLI equivalent, so a
session is configured identically in both modes.

All empirical findings below were validated against the bundled CLI **2.1.170**
(`claude_agent_sdk/_bundled/claude`, resolved via
`src/twicc/providers/claude_code/bin.py::resolve_bundled_binary()`).

## 2. Product decisions (settled)

### 2.1 Mode activation

- A session is either **normal (SDK)** or **hybrid (CLI)**. New column on `Session`.
- **One-way switch:** a session can start hybrid, or start normal and be switched to
  hybrid — but can NEVER go back. Reason (user-verified behavior): a session resumed
  by the CLI is no longer usable through the SDK (the SDK-driven claude does not see
  the CLI-era messages), while SDK→CLI works fine.
- Switching is **manual and UI-only**: displayed near the reset/send buttons (exact
  placement, incl. mobile, decided at implementation). It is NEVER exposed to agents
  (no CLI flag, no skill — same policy as project trust). Hidden/orchestrated
  sessions cannot use it. Background/headless hybrid is explicitly out of scope.
- The project trust security floor applies to hybrid launches exactly as it applies
  to SDK builds: `clamp_permission_mode_for_untrusted` clamps the launch flags.

### 2.2 Terminal placement & UX

- The terminal is part of the **composer block, directly above the textarea**.
- It is a **fully dedicated tmux session** (one per hybrid session, max one). It is
  NOT a tab of the existing Terminal panel — though it reuses the same building
  blocks (`useTerminal.js`, PTY/tmux backend in `src/twicc/terminal.py`).
- The terminal block is **collapsible and expandable to fullscreen**, mirroring the
  existing collapsed composer / pending-request block patterns. Heavy in-terminal
  reading is not the expected usage: the user keeps reading the session in the
  normal web view.
- The Send button **keeps its meaning** ("send this text to Claude"); in hybrid mode
  it performs the tmux transfer. No separate "transfer" button.
- No TwiCC-side message queue: the TUI input stays available at all times, so a send
  can happen at any moment (including mid-turn, where it steers Claude — the TUI
  handles its own queueing/steering semantics).

### 2.3 Process lifecycle

- **Lazy launch:** nothing is started until the user actually sends a message. The
  first send launches claude in tmux and pastes the message.
- **No auto-restart:** if claude exits (`/exit`, Ctrl+D, crash), TwiCC does nothing
  except detect and reflect the dead state. The next send relaunches
  (`claude --resume <session_id>` + same flags) and pastes the message — the exact
  same flow as today's dead-SDK-agent resume.
- **Idle timeout:** identical policy to SDK agents (claude processes hold hundreds
  of MB of RAM). Hook-driven states make the same idle detection possible. An
  idle-killed hybrid process is relaunched transparently on next send.
- **TwiCC restart resilience:** the tmux server (`-L twicc` socket) survives TwiCC
  restarts. At boot, TwiCC scans for surviving hybrid tmux sessions and reconciles
  `ProcessRun` rows (analogous to the existing SDK crash recovery).

### 2.4 Process state (JSONL-first, a single hook)

Decision (2026-06-11, after a full empirical probe on 2.1.170): **everything
derivable from the JSONL is read by the watcher** — it is internal to the TwiCC
process, so it broadcasts to the frontend exactly like the SDK path does. Hooks
are reduced to the ONE signal the JSONL cannot provide.

| Signal | Source |
|---|---|
| → `ASSISTANT_TURN` | JSONL: the user message line appears at submission time (verified) |
| → `USER_TURN` | JSONL: `system`/`turn_duration` line written at turn end (verified) |
| Liveness / `DEAD` | tmux pane polling — `pane_pid`/`pane_dead` (§3.4) |
| Pending approval ON | **hook `PermissionRequest`** → drop file (the only injected hook). Fires exactly when the prompt shows; payload carries `tool_name`, the full `tool_input`, `permission_mode`, `permission_suggestions` (verified) → rich badge |
| Pending approval OFF | JSONL: the `tool_result` for the pending `tool_use` appears (approve → result, deny → error result) — no hook needed (verified) |
| `AskUserQuestion` | JSONL: the assistant `tool_use` line (full questions) is present while the widget is up, before any answer (verified); `PermissionRequest` also fires for it |

Notes:
- Approvals are **invisible in the JSONL while pending** (verified) — that is the
  whole reason the single hook exists.
- The watcher must know a session is hybrid to enable these extra treatments
  (line types otherwise ignored); the `Session.hybrid` flag provides that.
- User-defined hooks coexist with injected ones (verified: GitKraken + plugin
  hooks ran alongside). Hook events available in 2.1.170 (verified in binary):
  `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Stop`, `SubagentStop`,
  `Notification`, `PermissionRequest`, `PreCompact`, `PreToolUse`, `PostToolUse`,
  `PostToolUseFailure` — kept available for the V2 ideas (§7), unused in V1
  beyond `PermissionRequest`.
- The pending-requests widget is replaced in hybrid mode by a **"answer in the
  terminal" badge** (V1); the user responds inside the TUI (permission prompts,
  AskUserQuestion, plan approval, trust dialog…). See §7 for the V2 idea that
  could bring GUI answering back.
- The existing stop affordances (Stop button, menu entry, triple-Escape shortcut)
  currently stop the SDK process; in hybrid mode they are plugged to kill the
  tmux session and claude with it — nothing to do inside the terminal.

### 2.5 Agent settings

Launch flags cover the full bundle (see §3.2). Mid-session changes follow the same
**live / idle / startup** machinery as the SDK
(`manager.py` settings-application flow), but hybrid mode gets **its own category
classification**, distinct from `AGENT_SETTINGS_CATEGORIES` in
`src/twicc/providers/claude_code/constants.py`:

| Setting | SDK category | Hybrid category | Hybrid application |
|---|---|---|---|
| `permission_mode` | live | **startup** | relaunch (Shift+Tab cycling is stateful/unstable; no slash command sets it directly — `/permissions` is unrelated) |
| `selected_model` | idle | idle | paste `/model <name>` (verified: applies instantly, no menu) |
| `context_max` | idle | idle | with model: `/model <name>[1m]` (to verify at implementation) |
| `effort` | startup | idle | paste `/effort <level>` (verified 2026-06-11: argument form applies instantly; bare `/effort` opens a dialog) |
| `thinking_enabled` | startup | startup (maybe live via Tab toggle — to verify) | relaunch |
| `claude_in_chrome` | startup | startup | relaunch |
| `fast_mode` | startup | idle | paste `/fast on` / `/fast off` (verified 2026-06-11: argument form applies instantly, no global side effect; bare `/fast` opens a dialog) |
| `question_widget` | startup | startup | relaunch (`--disallowedTools AskUserQuestion`) |

Startup changes = kill claude + relaunch with new flags + re-paste pending text,
mirroring the existing SDK restart flow.

Accepted side effect: `/model X` and `/effort X` in the TUI also save X as the
user's global default for new CLI sessions ("saved as your default for new
sessions"). Non-issue: TwiCC passes both explicitly at every session launch, so
TwiCC sessions always honor the user's per-session choice. `/fast` has no such
side effect (verified).

**No TUI→TwiCC back-sync in V1.** If the user changes model/permission mode inside
the TUI, TwiCC's stored settings drift. We add a small note advising users to change
settings through TwiCC. (The JSONL does record `mode`, `permission-mode`, `ai-title`
lines, so back-sync is possible later.)

### 2.6 Titles

- TwiCC's title generation stays authoritative (it runs in parallel, takes seconds,
  and only starts once the first message is sent).
- At launch, pass `-n "<temporary title>"` where the temporary title is the classic
  fallback: the **first ~100 characters of the user's prompt** (same approach used
  elsewhere in TwiCC and by old Claude Code versions).
- A custom title **permanently suppresses the CLI's own AI-title generation**
  (verified: guard `if (!customTitle) generate…` in the bundle; empirically, a
  session launched with `-n` produced `custom-title` JSONL lines and no `ai-title`
  line after a full turn). Since the temp title exists from launch, there is no race.
- When TwiCC's generated title is ready, paste `/rename <title>` (alias `/name`).
  Verified: takes the title as argument, applies instantly, no dialog, no API call,
  writes `custom-title` lines to the JSONL (so the watcher sees it).

### 2.7 Attachments (images & documents)

Ctrl+V-style clipboard paste is impossible (the CLI reads the **backend machine's**
system clipboard, unreachable from the browser through xterm.js/tmux). Path-based
delivery replaces it — verified to give the model the same visual content:

- TwiCC saves each attachment into a **per-session attachments directory** with a
  **randomized filename** (e.g. `att_<random>.png`) so the model judges content,
  not filenames (verified pitfall: a model can guess from a revealing filename even
  when reading fails).
- The launch command includes `--add-dir <attachments_dir>` → zero permission
  prompts, even in `dontAsk` (verified; without it, reads outside cwd trigger a TUI
  permission prompt — except under `/tmp`, which is oddly lax).
- The transferred text embeds `@<path>` mentions. Verified end-to-end in the
  interactive TUI via tmux paste: the image reaches the model (described correctly),
  either embedded client-side as an `attachment` JSONL line (base64) or via an
  auto-approved `Read` returning an image block — both equivalent to a paste.
- If the user also changes the SDK path to pass scratch/artifacts dirs via
  `--add-dir` / `add_dirs`, both modes stay symmetrical.

## 3. Technical design

### 3.1 Launch command

claude runs as the **direct pane command** of a dedicated tmux session (no wrapping
shell), with `exec` so the pane PID *is* the claude PID:

```
tmux -L twicc new-session -d -s twicc-hybrid-<session_id> -c <cwd> \
  "exec env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT ... <bundled_claude> <flags…>"
tmux -L twicc set-option -t twicc-hybrid-<session_id> remain-on-exit on
```

- Same `-L twicc` socket and env-purging (`purge_env_vars`) as the existing terminal
  infra; same tmux config resolution (`resolve_tmux_config_path`, forced
  `mouse off`).
- Session naming: dedicated prefix (e.g. `twicc-hybrid-<sanitized_session_id>`),
  disjoint from the Terminal panel's `twicc-<session_id>` namespace.
- `remain-on-exit on` keeps the dead pane inspectable and makes `pane_dead`
  detection reliable.

### 3.2 Flag mapping (all verified on 2.1.170)

| TwiCC setting / need | CLI flag |
|---|---|
| session id (pre-minted UUID) | `--session-id <uuid>` (works interactively; appears in status bar; JSONL filename matches) |
| resume | `--resume <uuid>` |
| model (+ 1M context) | `--model <value>` — aliases `sonnet`/`opus`/`fable` OK; full names OK; `[1m]` suffix passed as today (verify interactively); ⚠️ alias `haiku` is **silently ignored** (falls back to default) — non-issue, not offered in UI |
| effort | `--effort low\|medium\|high\|xhigh\|max` |
| thinking | `--thinking enabled\|adaptive\|disabled` (hidden flag) + `--thinking-display` |
| permission mode | `--permission-mode …` + `--allow-dangerously-skip-permissions` (trusted only) |
| untrusted setting sources | `--setting-sources user` (trusted: `user,project,local`) |
| fast mode | `--settings '{"fastMode":true|false}'` |
| chrome | `--chrome` / `--no-chrome` |
| system prompt addendum | `--append-system-prompt-file <file>` (hidden flag, verified) — addendum already persisted in `Session.system_prompt_addendum`, written to a temp file at launch; avoids shell-quoting a multi-KB argument |
| TwiCC plugin (skills) | `--plugin-dir <plugin_path>` |
| question widget off | `--disallowedTools AskUserQuestion` |
| temp title | `-n "<first ~100 chars of prompt>"` |
| attachments dir | `--add-dir <attachments_dir>` |
| hooks | merged into the `--settings` JSON (see §3.5) |

### 3.3 Text transfer (composer → TUI)

```
printf '%s' "<text>" | tmux -L twicc load-buffer -
tmux -L twicc paste-buffer -p -t <session>     # -p = bracketed paste
tmux -L twicc send-keys -t <session> Enter
```

Verified end-to-end: multiline text pastes without premature submission, `@`
mentions don't trigger the file picker, slash commands submitted this way are
interpreted as commands (`/model`, `/rename` tested; user confirms all slash
commands work in the CLI). Text is sent **raw** — no `<twicc:context>` prefix
(would break leading-slash command detection, and the per-turn context channel does
not exist in hybrid mode anyway).

### 3.4 Liveness detection

Three complementary layers (all verified):

1. `tmux list-panes -F '#{pane_pid}'` → claude's actual PID (thanks to `exec`);
   `kill -0` checks liveness; usable for memory tracking (`memory_rss`) too.
2. `#{pane_dead}` + `#{pane_dead_status}` after exit (with `remain-on-exit on`).
3. `SessionStart` / `SessionEnd` hooks (push).

### 3.5 Hybrid signals — JSONL state bridge, single hook channel, original files

**JSONL state bridge (no hooks).** The watcher, when ingesting lines of a hybrid
session, derives: user message line → `ASSISTANT_TURN`; `turn_duration` system
line → `USER_TURN`; `tool_result` matching a pending approval's `tool_use` →
pending cleared. Being in-process, it updates the hybrid agent and broadcasts
`process_state` exactly like SDK agents (`broadcast_process_state`,
`awaiting_user_input`).

**Single hook → file-drop channel.** The injected `--settings` JSON defines ONE
command hook, on `PermissionRequest` (pure shell: `cat` + `mv`), writing one
event file into `<data_dir>/hybrid-hooks/` — filename
`<session_id>__<event>__<nonce>.json`, content = the hook's stdin JSON payload
(tool_name + full tool_input + suggestions), atomic `.tmp`→rename writes (same
convention as the drop-requests watcher).
- No HTTP and no secret: authentication is filesystem write access (same user).
  Works identically when TwiCC is password-protected — nothing is exempted from
  the password middleware — survives restarts (stable path), and routes per data
  dir (each worktree instance only sees its own events). Kept separate from the
  agent-facing `drop-requests/` directory so the namespaces don't mix, and no
  `twicc` CLI command is involved.
- A small dedicated watcher (modeled on the drop-requests watcher: watchfiles +
  boot scan + delete-after-processing) feeds the event to the hybrid agent
  (pending marker ON).
- Hooks `--settings` JSON shape **validated empirically on 2.1.170**
  (2026-06-11 probe: all events fired from a settings file).
- Frequency is one fire per approval prompt → the per-invocation cost debate is
  moot; pure shell keeps it at ~0 ms anyway.

**Original-file capture via Claude's own file-history (no hooks).** The launch
`--settings` also forces `"fileCheckpointingEnabled": true` (off by default in
the SDK, possibly disabled by the user — forcing restores it; the env purge
already drops an inherited `CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING`). The CLI
then maintains its checkpoint store: `~/.claude/file-history/<session_id>/<hash>@v<n>`
holds the FULL pre-edit content, and `file-history-snapshot` JSONL lines map
`<cwd-relative path> → {backupFileName, version, backupTime}` (verified
end-to-end: backup content matched the pre-edit file exactly). When the watcher
processes an Edit/Write `tool_result` of a hybrid session, it resolves the
original from that store and persists it in TwiCC's own storage at ingest time
(same place the SDK-captured `originalFile` goes today — claude owns the store
and its retention, so copy, don't reference).

> Implementation note (2026-06-11): the store-side capture turned out to be
> unnecessary — with `fileCheckpointingEnabled` forced on, the interactive CLI
> embeds `toolUseResult.originalFile` (full pre-edit content) directly in the
> Edit/Write tool_result JSONL lines (verified on 2.1.170 on two sessions), so
> the existing pipeline reads it natively with zero hybrid-specific code. The
> snapshot/store mapping above stays documented for reference.

### 3.6 Backend architecture sketch

- **`Session.hybrid` flag** (one-way; new migration). Cleared never.
- **Hybrid agent manager** (within the Claude Code provider): a sibling of the SDK
  agent path implementing the same `BaseAgent`/`AgentInfo` contract — states,
  `ProcessRun` persistence, broadcasts, idle timeout — but driven by hooks + tmux
  polling instead of an SDK message loop. Its operations:
  - `launch(text)` — build flags, write addendum file, create tmux session, paste.
  - `send(text, attachments)` — save attachments, build `@path` mentions, paste.
  - `apply_settings(changes)` — hybrid live/idle/startup logic (§2.5).
  - `rename(title)` — paste `/rename <title>`.
  - `interrupt()` / `kill()` — send-keys Escape / tmux kill-session.
  - boot-time reconciliation of surviving tmux sessions.
- **JSONL pipeline unchanged.** The watcher already ingests CLI sessions (initial
  sync + live watcher treat them identically; `Session` rows are auto-created).
  Costs, context usage, compact detection (`isCompactSummary`) all come from JSONL
  already. Turn-state is NOT derived from JSONL (hooks own it).
- **Compute check:** CLI-only JSONL line types must render properly:
  `attachment` (incl. base64 images — display them), `mode`, `permission-mode`,
  `last-prompt`, `custom-title`, `ai-title`, `queued-command`. Audit
  `display_level`/`kind` classification for each.
- Existing helpers reused: `resolve_bundled_binary()`, `purge_env_vars()`,
  `resolve_tmux_config_path()`, tmux subprocess patterns from `terminal.py`,
  title-suggestion pipeline (unchanged — it is an independent Haiku call).

### 3.7 Frontend architecture sketch

- **Composer terminal block** in `MessageInput.vue`'s zone: a new component
  embedding a terminal instance (reusing `useTerminal.js` against a new WS terminal
  context for the hybrid tmux session), with collapse/fullscreen controls patterned
  after the pending-request block.
- **Send path:** in hybrid mode, the send action posts the text (and attachment
  references) to the hybrid manager over the existing WS instead of the SDK
  `send_message` path. UI stays identical (button, presets, snippets, drafts).
- **Mode toggle** near reset/send; once enabled, irreversible (confirm dialog).
- **Pending-request badge** replacing the interactive widget for hybrid sessions.
- **Settings popover**: unchanged UI; backend applies via hybrid categories; small
  note "change settings here, not in the TUI".

## 4. Known losses & accepted limitations (V1)

- Pending requests (permissions, AskUserQuestion, plan approval, trust dialog) are
  answered **in the TUI** — TwiCC only badges their existence.
- No clipboard image paste; path-based attachments instead (§2.7).
- No per-turn `<twicc:context>` reconciliation channel (addendum stays frozen at
  creation, as today).
- TUI-side changes (model, permission mode, rename) are not synced back to TwiCC
  settings in V1.
- The user can type directly in the TUI, bypassing the composer entirely — by
  design (JSONL remains the display source of truth either way).
- SDK logs (`logs/sdk/`) don't apply to hybrid sessions.
- Codex hybrid mode: out of scope.

## 5. Remaining verifications (implementation phase, high confidence)

1. ~~`--settings` hooks JSON schema accepted by 2.1.170~~ — **DONE 2026-06-11**
   (probe: all events fired from a settings file; `PermissionRequest` payload
   carries tool_name/tool_input/suggestions; approvals confirmed absent from the
   JSONL while pending; `fileCheckpointingEnabled` forcing + file-history
   mapping + backup content all verified).
2. `/model <name>[1m]` (context_max) applied interactively.
3. ~~`/effort <level>` / `/fast` as live alternatives to startup-relaunch for
   `effort` / `fast_mode`~~ — **DONE 2026-06-11**: both apply instantly in
   their argument form and are now IDLE (pasted by `apply_live_settings`);
   the bare `/effort` / `/fast` forms open interactive dialogs and must
   never be pasted. `/effort` shares `/model`'s save-as-global-default side
   effect; `/fast` has none. The Tab thinking toggle remains unverified —
   `thinking_enabled` stays STARTUP.
4. Compute classification audit for CLI-only JSONL line types (§3.6), now
   including `file-history-snapshot` handling and the tool_use↔snapshot-version
   association for original-file capture (§3.5).
5. Trust dialog UX on first hybrid launch in a never-trusted directory (user
   handles it in the TUI; just confirm the flow feels right).

### 5.bis Inherited-env regression found at implementation time (2026-06-11)

**Initial diagnosis (night run — WRONG):** bundled CLI 2.1.172
(claude-agent-sdk 0.2.96) was believed not to persist interactive transcripts
at all, and the worktree temporarily pinned `claude-agent-sdk==0.2.95`
(CLI 2.1.170).

**Actual root cause (same-day re-investigation):** 2.1.172 persists
interactive transcripts live and correctly — when its environment is clean.
The night probes ran from inside a Claude Code session and only unset three
hard-coded variables; the inherited **`CLAUDE_CODE_CHILD_SESSION`** marker
alone makes the CLI treat the new session as a child session and skip
transcript persistence entirely: nothing is written live, nothing is flushed
at a graceful `/exit`, `--resume` answers "No conversation found", and only
the separately-written `ai-title` line lands — which also fooled the original
bisect (its "OK" criterion was file existence). This is a regression of the
upstream 2.1.170 fix: "Fixed sessions not saving transcripts (and not
appearing in --resume) when launched from the VS Code integrated terminal or
any shell that inherited Claude Code environment variables."

Verified empirically on 2.1.172 (env bisect with `/proc/<pid>/environ`
checks, a 12-case flag/model matrix, and a production-faithful counter-probe):
keeping only `CLAUDE_CODE_CHILD_SESSION` reproduces the failure; a full purge
— or keeping any other marker (`CLAUDE_CODE_SESSION_ID`,
`CLAUDE_CODE_EXECPATH`, `CLAUDE_AGENT_SDK_VERSION`, `CLAUDE_EFFORT`,
`AI_AGENT`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`) — writes live. The full
`build_argv` command line under the production prefix purge writes live and
resumes correctly: hybrid mode is safe on 0.2.96 / 2.1.172.

Consequences: the SDK pin was lifted (back to `claude-agent-sdk==0.2.96`);
`hybrid/tmux.py` additionally unsets a static list of known marker names so a
marker living only in the tmux server's global environment (inherited from
whoever first started the server) never reaches claude; the probe recipe now
mandates prefix-based purging (`docs/tmux-probe-recipe.md`).

Additional findings from the night run (still valid): the trust dialog
swallows a paste entirely (composer left empty after the dialog is answered)
— the hybrid agent waits for the dialog to clear before its first paste; the
PermissionRequest drop hook and the file-history capture both worked on
2.1.172 as on 2.1.170.

## 6. Decision log (chronological summary)

1. Dedicated tmux session per hybrid session, terminal embedded in composer above
   the textarea — not a Terminal-panel tab.
2. Full state parity via CLI hooks. Initially a local HTTP endpoint; revised
   2026-06-11 to a dedicated file-drop channel — a password-protected instance
   must not expose an auth-exempt URL, and a command-line token would leak via
   `ps`; plain shell file drops have neither problem.
3. One-way normal→hybrid switch, manual, UI-only, human-only.
4. Same idle timeouts as SDK (RAM reclamation); lazy launch; no auto-restart.
5. Send button semantics unchanged; no TwiCC queue; steering anytime.
6. No TUI→TwiCC back-sync in V1 (+ advisory note).
7. Titles: temp title from prompt prefix via `-n`, then `/rename` with TwiCC's
   generated title; CLI ai-title generation thereby suppressed.
8. Attachments: randomized filenames in per-session dir + `--add-dir` + `@path`.
9. Hybrid-specific live/idle/startup classification; `permission_mode` → startup.
10. `/model` global-default side effect accepted (model passed at every launch).
11. 2026-06-11 — states become JSONL-first (user line / `turn_duration` /
    `tool_result` clearing, all verified) with tmux liveness; hooks reduced to a
    single `PermissionRequest` drop. Dissolves the channel-cost debate
    (drop-files vs dedicated CLI command vs UDS were compared; one rare
    fire-and-forget event makes pure-shell drops the obvious fit).
12. 2026-06-11 — original files captured from Claude's own file-history store,
    forced on via `fileCheckpointingEnabled: true` (off by default in SDK,
    user-disablable → always forced at hybrid launch). Verified end-to-end.
13. 2026-06-11 — two V2 ideas noted, NOT in V1 (see §7): live "Claude is
    editing…" synthetic indicators via tool hooks; bidirectional hooks
    (drop + status-file wait) to answer approvals/AskUserQuestion from the
    TwiCC UI.

## 7. Noted for V2 (explicitly NOT in V1)

1. ~~**Live tool indicators ("Claude is editing…").**~~ **CANCELLED for good
   (user decision, 2026-06-12) — do not revisit.** The hook→drop-file channel
   is too slow relative to fast tools, and in hybrid mode the user has the
   terminal in front of them: if they want to watch tool activity live, the
   TUI is the surface. A probe that day also invalidated the premise below:
   the interactive CLI writes the assistant `tool_use` line to the transcript
   WHILE the tool is still running (~3-4 s after the actual tool start,
   irregular flush; the `tool_result` is flushed in ~200 ms), and the
   frontend's generic running-spinner (a `tool_use` ingested without its
   `tool_result`) already renders on hybrid sessions — so near-live
   indicators exist via the JSONL alone, with no hook.
   <br>Original note (historical): The SDK path shows synthetic
   assistant items for in-flight tools. Pure JSONL can't: tool activity appears
   only once written. The `PreToolUse`/`PostToolUse` hooks (available, verified)
   could feed live indicators. To think through later.
2. **GUI answering of approvals/AskUserQuestion (bidirectional hooks).**
   **→ Verified and designed on 2026-06-12 — see
   `2026-06-12-hybrid-gui-approvals-design.md`.** The "to verify" below held
   up (decisions DO flow back through the hook's stdout JSON), with one
   correction: the CLI does not block the dialog on the hook — it shows the
   TUI prompt and runs the hook in parallel, first responder wins.
   <br>Original note (historical): Claude
   BLOCKS while a hook runs, and the `PermissionRequest` payload carries the
   exact data the SDK's `can_use_tool` receives. V2 idea: the hook drops the
   request, then WAITS for a status file (same drop→status pattern as the CLI
   drop-requests watcher); TwiCC shows the real pending-request widget; the
   user answers in the UI; the status file carries the answer; the hook returns
   it to Claude. **To verify when V2 starts:** the `PermissionRequest` hook can
   actually return allow/deny decisions (and AskUserQuestion answers) through
   its stdout JSON — strongly suspected, not yet tested. Would restore full
   pending-request parity with the SDK mode. In V1, approvals and questions are
   answered in the terminal; the badge (with rich payload) is the V1 surface.
