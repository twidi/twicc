# Hybrid mode V2 — GUI answering of approvals & questions (design)

Date: 2026-06-12. Status: designed, awaiting implementation go.
Builds on `2026-06-10-hybrid-claude-cli-mode-design.md` (§2.4, §3.5, §7.2).

## 1. Motivation & scope

In hybrid V1, permission prompts, `AskUserQuestion` widgets and plan
approvals must be answered inside the TUI; TwiCC only shows an
"Answer in the terminal" badge (with the rich hook payload behind it).
This V2 restores full pending-request parity with the SDK mode: the real
`PendingRequestForm` widget appears in TwiCC and the user can answer
**either** in the GUI **or** in the terminal — first responder wins.

In scope: tool approvals, `AskUserQuestion`, `ExitPlanMode` (plan
approval), including "always allow"-style `permission_suggestions`.
Out of scope (unchanged): back-sync TUI→TwiCC, crons on hybrid sessions.

## 2. Verified behavior (empirical probes, 2026-06-12, CLI 2.1.x)

All probes ran a throwaway interactive CLI with an injected
`PermissionRequest` hook that drops its stdin payload then **waits for a
response file** and prints it on stdout — the exact pattern this design
deploys. Findings:

1. **Payload parity with the SDK's `can_use_tool`.** The hook stdin
   carries `session_id`, `transcript_path`, `cwd`, `permission_mode`,
   `effort`, `hook_event_name`, `tool_name`, full `tool_input`, and
   `permission_suggestions` (e.g. `{"type":"setMode","mode":"acceptEdits",
   "destination":"session"}`). For `AskUserQuestion`: full questions
   (text, header, options with descriptions, `multiSelect`). For
   `ExitPlanMode`: the full plan markdown in `tool_input.plan` +
   `planFilePath` — but **no** `permission_suggestions`.
2. **Dual-surface, first responder wins.** The CLI shows the TUI dialog
   AND runs the hook **in parallel** (it does not wait for the hook
   before displaying). A hook stdout decision dismisses the displayed
   dialog ("Allowed/Denied by PermissionRequest hook"). A TUI answer
   resolves immediately; the still-running hook is left alive (its late
   output is ignored — verified with a late deny: no effect, no
   pollution of later requests). On TUI **Esc** the hook is killed.
3. **Decisions via hook stdout JSON** (all verified):
   - allow: `{"hookSpecificOutput":{"hookEventName":"PermissionRequest",
     "decision":{"behavior":"allow"}}}` → tool runs, no dialog left.
   - deny + message: `{"behavior":"deny","message":"…"}` → the message
     reaches Claude as the tool error.
   - `updatedInput` on allow → answers `AskUserQuestion` (same
     `{questions, answers:{"<question text>":"<answer>"}}` shape the SDK
     path sends today).
   - `updatedPermissions` on allow → applying the `setMode acceptEdits`
     suggestion switched the session mode (statusline updated, no
     further edit prompts) — full "always allow" parity.
   - `ExitPlanMode` + plain allow = "manually approve": plan accepted,
     mode exits `plan`, subsequent edits prompt again.
4. **Sequential approvals.** Two parallel tool calls produce one dialog
   (and one hook fire) at a time; answering the first via the hook
   triggers the second hook ~400 ms later. Per-invocation response
   files (keyed by nonce) work.
5. **Hook timeout.** Per-hook `"timeout"` (seconds) is honored (15 and
   300 tested); at expiry the CLI kills the hook silently — the TUI
   dialog stays displayed and fully functional, later approvals fire
   hooks normally. Default is 600 s.

## 3. Product decisions

- **Both surfaces at once.** The full `PendingRequestForm` widget shows
  in TwiCC while the TUI dialog is up. Whichever side answers first
  wins; the other side clears automatically (GUI → hook output dismisses
  the dialog; TUI → the JSONL `tool_result` clears the widget through
  the existing bridge).
- **The badge stays** on the terminal block, as the indicator tying the
  pending prompt to the embedded terminal (and as the only surface after
  GUI-channel expiry).
- **Timeout philosophy — mirror TwiCC's own rule.** TwiCC's agent
  manager never auto-kills an agent while a pending request is open
  (the user must find their morning approval still there in the
  evening); the hook channel must live just as long. Set the hook `timeout` as high as the CLI accepts
  (target: effectively infinite; trials at implementation time will find
  the accepted ceiling). If the hook still dies (CLI clamp, crash), the
  widget degrades to the V1 badge — answering in the terminal always
  works.
- **No second hook.** `PermissionRequest` remains the single injected
  hook; clearing still comes from the JSONL bridge.

## 4. Technical design

### 4.1 Response channel (filesystem, the drop-requests `.status.json` pattern)

Same directory, same convention as the established CLI drop-requests
system (`drop_requests_watcher.py` + `cli/_drop_request/polling.py`):
the response to a drop file lives **next to it**, named
`<drop stem>.status.json` — here
`hybrid-hooks/<session_id>__PermissionRequest__<nonce>.status.json`.
The backend writes it atomically (`.tmp` → rename, `chmod 600`); the
polling reader (the hook) consumes and deletes it — exactly how the
`twicc` CLI polls its own `.status.json`. The hooks watcher gains the
same status/tmp suffix exclusion the drop-requests watcher has
(critical: a bare `*.json` filter would parse the status file as a
3-part event name and route-then-delete it), plus the matching boot
sweep for orphaned status files.

### 4.2 Hook command (`launch.py` / `build_hooks_settings`)

The injected shell command becomes: drop stdin (unchanged) → poll
`<drop stem>.status.json` in the same directory every ~0.2 s → on hit,
`cat` it to stdout, `rm` it, exit 0. Still pure shell, no HTTP, no
token. The hook entry gains `"timeout": <very high>` (value found by
trial; see §6). The internal poll loop is unbounded — the CLI's own
timeout is the only reaper, so the channel lives exactly as long as the
CLI allows.

### 4.3 Hooks watcher (`hooks_watcher.py`)

- Passes the **nonce** through to the manager
  (`handle_hybrid_hook(session_id, event, payload, nonce)`); it already
  parses it from the filename.
- Ignores `.status.json` / `.tmp` files (suffix exclusion, §4.1).
- **Deferred deletion for handled `PermissionRequest` events** —
  scoped strictly to the `hybrid-hooks/` directory; the general CLI
  `drop-requests/` lifecycle is untouched. Today every hybrid-hook drop
  file is deleted after processing. V2: when the event is handled by a
  live hybrid agent, the drop file is kept; the **agent** deletes it
  when the pending clears (GUI answer, JSONL clearing, agent death).
  Unhandled/malformed drops are deleted as today. Benefit: the
  existing boot scan re-feeds surviving drops after a TwiCC restart, so
  a pending approval — whose hook process is still alive inside the
  surviving tmux, still polling — regains its GUI widget across TwiCC
  restarts for free.
- **Stale-drop guard at re-feed.** A kept drop may describe a prompt
  answered while TwiCC was down (its clearing `tool_result` already in
  the JSONL — no future line will clear it). At registration the agent
  compares the nonce's nanosecond timestamp against the session's last
  JSONL activity (tool_result / turn_duration timestamps): if the JSONL
  moved after the drop, the drop is stale → delete, don't register.

### 4.4 Hybrid agent (`hybrid/agent.py`)

Replaces the single synthetic `hybrid_terminal` marker with **real,
answerable pending requests**:

- `on_permission_request(payload, nonce)` registers a `PendingRequest`
  with `request_id=nonce`, `request_type` = `ask_user_question` when
  `tool_name == "AskUserQuestion"` else `tool_approval`, real
  `tool_name` / `tool_input` / `permission_suggestions`, then
  broadcasts. **Hook-native suggestions only** (decided at review): the
  hybrid path does NOT add TwiCC-invented suggestions — neither the
  SDK path's injected `setMode` mode-picker suggestion
  (`_modeOptions`/`_currentMode`) nor an "auto-accept edits" synthesis
  for `ExitPlanMode` (whose payload carries no suggestions: its widget
  offers plain approve/deny + plan editing). The mode picker disappears
  by construction — the frontend only renders it when a suggestion
  carries `_modeOptions`. Both may come later.
- **Shared inbound conversion.** The SDK agent's suggestion processing
  (`get_permission_suggestions` in `agent/agent.py`: `PermissionUpdate`
  → plain dicts, `addDirectories`/`removeDirectories` cwd filtering,
  field-order normalization) is extracted into a module shared by both
  modes; the SDK-only enrichment (the injected `setMode` mode-picker
  suggestion) stays in the SDK agent, layered on top of the shared
  passthrough. The hybrid path feeds the hook's already-plain dicts
  through the same shared filter so both modes present identical
  shapes to the frontend. The outbound conversion (front answer →
  `PermissionResultAllow/Deny`) is already shared: it lives in `ws.py`,
  which is mode-agnostic.
- **`resolve_pending_request(request_id, response)` override** (the SDK
  path resolves an asyncio Future; ws.py and the manager stay
  untouched): converts the SDK-typed response built by `ws.py`
  (`PermissionResultAllow{updated_input, updated_permissions}` /
  `PermissionResultDeny{message}`) into the hook stdout JSON of §2.3,
  writes the `.status.json` next to the kept drop file (§4.1), pops
  the pending request, deletes the kept drop file, broadcasts. Returns
  `False` when the request_id is unknown (already cleared).
  `ws.py`'s existing `setMode` persistence (`Session.permission_mode`)
  applies unchanged — the next hybrid restart's argv picks it up.
- **ExitPlanMode plan editing — shared brick.** In SDK mode the widget
  lets the user edit the plan; the CLI/SDK ignores the plan carried in
  `updated_input`, so the SDK agent rewrites the plan file itself
  (`_update_plan` in `agent/agent.py`: slug lookup → 
  `~/.claude/plans/{slug}.md`). That logic (modified-plan detection +
  plan-file write) is **extracted into a module shared by both modes**
  — per the standing directive to reuse the SDK bricks, reorganizing
  code where needed. Both modes now have the target path directly:
  `tool_input.planFilePath` is CLI-injected into the ExitPlanMode tool
  input itself (verified in SDK-session transcripts since CLI ~2.1.91),
  so the hook payload AND the SDK's `can_use_tool` input both carry it;
  the shared helper uses it and keeps the slug lookup only as a legacy
  fallback. The file is written **before** the `.status.json` so the
  CLI proceeds with the edited plan already in place.
- **Clearing (existing JSONL bridge, now also a janitor).** The
  unconditional clear-on-`tool_result` / clear-on-turn-end logic stays;
  on clear the agent additionally deletes the kept drop file and any
  orphaned status file for that nonce. With the timeout pushed very
  high, a TUI-answered prompt would otherwise leave its hook polling
  for as long as the CLI lives — so the janitor also **reaps the orphan
  hook** by writing a dummy status file (the CLI provably ignores late
  hook output): the hook consumes it and exits; a delayed unlink covers
  the hook-already-dead case (covers: answered in TUI before
  the GUI, Esc — where the CLI kills the hook so a just-written response
  would never be consumed —, and hook death).
- **GUI-channel expiry timer.** Each registered pending schedules a
  timer at `created_at + hook_timeout`: on fire, the pending's
  `request_type` is swapped to `hybrid_terminal` (widget disappears,
  badge remains) and state is re-broadcast. With the timeout pushed to
  its ceiling this should never fire in practice; it is the safety net
  for a CLI clamp.
- **Death cleanup**: on DEAD, clear all pendings and delete their drop
  and status files (no futures to cancel — hybrid pendings are not
  awaited server-side).

### 4.5 Frontend

- `SessionItemsList.vue`: `PendingRequestForm` is no longer gated on
  `!isHybridSession` — it renders whenever the head pending request is
  answerable (`tool_approval` / `ask_user_question`); a
  `hybrid_terminal`-typed pending (expired channel) keeps the V1
  badge-only behavior. The form stacks above the composer (which hosts
  the terminal block), so both surfaces are visible together.
- `HybridTerminalBlock.vue`: the badge matches any pending request (not
  just `hybrid_terminal`); label unchanged ("Answer in the terminal
  (`tool`)") — with the widget visible it reads as the pointer to the
  second surface.
- `respondToPendingRequest()` and the whole answer pipeline are reused
  verbatim (`request_id` is now the nonce). `PendingRequestBody`
  (claude_code) needs zero changes: payload shapes are identical to the
  SDK path.

### 4.6 Lifecycle summary

| Scenario | Resolution | Cleanup |
|---|---|---|
| GUI answer | agent writes the `.status.json`; hook prints it; CLI dismisses dialog | hook deletes the status file; agent pops pending + deletes drop |
| TUI answer | CLI resolves; `tool_result` lands in JSONL | bridge clears pending; agent deletes drop (+ orphan status file if any); orphaned hook's late output ignored |
| TUI Esc | rejection `tool_result` in JSONL; CLI kills hook | same as TUI answer |
| Hook timeout (CLI clamp) | nothing resolves; TUI dialog still up | expiry timer downgrades widget→badge; TUI remains the surface |
| TwiCC restart mid-pending | hook still polling in surviving tmux | boot scan re-feeds kept drop → widget restored; stale-drop guard skips answered ones |
| Session stop/kill | tmux dies, CLI + hook die | DEAD cleanup deletes files, clears pendings |

### 4.7 Races (all benign, verified)

- GUI and TUI answer simultaneously: the CLI takes the first, ignores
  the hook's late output; the JSONL result reflects the winner; pending
  cleared either way.
- Response written just after Esc killed the hook: file is orphaned;
  deleted by the clear-janitor (or boot sweep).
- Two parallel tool calls: hooks fire sequentially (§2.4); per-nonce
  files keep them independent; the widget shows the head request with
  the existing "+N pending" counter.

## 5. Known limitations (accepted)

- A GUI answer needs the hook process alive: prompts that predate the
  hybrid launch design change (old sessions without the polling hook)
  or that outlive the CLI-accepted timeout fall back to terminal-only.
- The `permission_suggestions` offered are exactly what the CLI sends;
  no TwiCC-invented suggestions in hybrid for now — in particular no
  mode-picker (`_modeOptions`) injection and no ExitPlanMode
  "auto-accept edits" synthesis (its widget is approve/deny + plan
  editing only).
- If the user answers in the TUI, the orphaned hook keeps polling until
  the CLI timeout (one `sleep 0.2` shell loop — negligible).

## 6. To verify at implementation time

1. **Timeout ceiling**: find the highest accepted `"timeout"` value
   (target ≥ weeks). Probe: huge value → does the hook survive past
   600 s? If clamped, pick the max accepted and rely on the expiry
   timer for the degradation.
2. **Stale-drop guard**: confirm nonce-timestamp vs JSONL-timestamp
   comparison is reliable on a real restart-mid-pending scenario.
3. **Hook survival across TwiCC restart**: end-to-end check of §4.6
   row 5 (probe strongly suggests it; the hook is a child of the CLI in
   the tmux, not of TwiCC).
4. `permission_suggestions` round-trip for non-setMode types (e.g.
   `addRules`) through the hook's `updatedPermissions` — the SDK path
   supports them; verify the CLI applies them when returned by the hook.

## 7. Decision log

- Dual-surface (widget + TUI, first wins) over hiding either: the probe
  showed the CLI displays the dialog regardless; embracing it gives the
  best hybrid UX (answer from the phone OR the terminal).
- Response channel = polled file, not HTTP: same rationale as the drop
  channel (no auth exemption, no token in `ps`, per-data-dir routing),
  and it survives TwiCC restarts.
- Response = `<drop stem>.status.json` in the same watched directory,
  reusing the drop-requests convention (suffix-excluded in the watcher),
  rather than a separate response directory.
- ExitPlanMode plan editing reuses the SDK brick (`_update_plan`),
  extracted to a shared module; both modes use `tool_input.planFilePath`
  (CLI-injected, verified in transcripts since ~2.1.91), slug lookup
  demoted to legacy fallback.
- Inbound suggestion processing (normalize + filter) extracted from the
  SDK agent into a shared module; TwiCC's enrichments (the injected
  `setMode` mode-picker suggestion, any synthesized suggestion) stay
  SDK-only for now — hybrid presents hook-native suggestions verbatim.
- Drop files now deleted at **resolution** (not ingestion) to get
  restart resilience from the existing boot scan.
- Hook timeout pushed to the CLI's ceiling instead of a re-arming
  scheme: there is nothing to re-arm — the hook fires once per dialog.
- request_id = the hook's own nonce: no new identifier, the filename is
  the contract.
