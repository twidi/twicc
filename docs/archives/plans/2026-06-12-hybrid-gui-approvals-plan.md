# Hybrid GUI Approvals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user answer hybrid-session permission prompts, `AskUserQuestion`
widgets and plan approvals from the TwiCC UI (the real `PendingRequestForm`),
while the TUI dialog stays answerable too — first responder wins.

**Architecture:** The injected `PermissionRequest` hook (which already drops its
payload into `hybrid-hooks/`) now also **polls for a `.status.json` answer next
to its drop file** and prints it on stdout — the CLI applies it and dismisses
the displayed TUI dialog. The hybrid agent registers real, answerable
`PendingRequest`s (request_id = the hook's nonce) instead of the synthetic
`hybrid_terminal` marker, and overrides `resolve_pending_request` to convert
the SDK-typed answer built by `ws.py` into the hook's stdout JSON, written as
the status file. Suggestion processing and the ExitPlanMode plan-file write are
extracted from the SDK agent into modules shared by both modes. `ws.py`, the
manager routing and the whole frontend answer pipeline are reused untouched.

**Spec:** `docs/plans/2026-06-12-hybrid-gui-approvals-design.md` — read it
first (all "verified" claims were validated by the 2026-06-12 probe campaign).
Background: `docs/plans/2026-06-10-hybrid-claude-cli-mode-design.md` §2.4/§3.5.

**Project rules that apply (from CLAUDE.md):**
- No tests, no linting — verification is manual, via probes and the running app.
- Everything written (code, comments, UI strings, docs) in English.
- In the worktree, always `cd` into it and set `TWICC_DATA_DIR=$PWD` for any
  manual Python; never touch the user's main instance at `~/.twicc`.
- Commit precisely (`git add <files>`, never `-A`).

## Execution preamble — autonomous run

This plan is meant to be executed END-TO-END WITHOUT STOPPING for user input.

- **Probe technique:** drive throwaway interactive CLIs with tmux — read
  `docs/tmux-probe-recipe.md` FIRST (env purge by prefix is mandatory; a
  leaked `CLAUDE_CODE_CHILD_SESSION` silently disables transcript writes).
  The 2026-06-12 probes used `/tmp/twicc-probe-approvals/` with a hook script
  dropping payloads and polling a response file — reuse that recipe.
- **Test sessions cost real API tokens — keep them cheap:** `--model sonnet`
  (never the bare `haiku` alias — silently ignored) and low effort.
- **UI checks** via Chrome MCP against the worktree frontend (ports from
  `uv run ./devctl.py start`, run from the worktree).
- **`rg` trap:** never glue `-r` into combined short flags (`rg -rln` parses
  as `--replace=ln`). Write flags separately.
- **Judgment points** (exact shell quoting, grace delays, log wording):
  decide alone, verify with a probe, record in the commit message.

---

## File map

**Created:**

| File | Responsibility |
|---|---|
| `src/twicc/providers/claude_code/agent/permissions.py` | Shared inbound suggestion processing + ExitPlanMode plan-file write (extracted from the SDK agent) |
| `src/twicc/providers/claude_code/agent/hybrid/responses.py` | `PermissionResultAllow/Deny` → hook stdout JSON conversion + status-file write/cleanup helpers |

**Modified:**

| File | Change |
|---|---|
| `src/twicc/providers/claude_code/agent/agent.py` | Delegate suggestion processing + plan write to the shared modules (keep the SDK-only `setMode` mode-picker injection in place) |
| `src/twicc/providers/claude_code/agent/hybrid/launch.py` | Hook command: drop + poll `.status.json` + print; `"timeout"` at the probed ceiling; shared timeout constant |
| `src/twicc/providers/claude_code/agent/hybrid/hooks_watcher.py` | Ignore `.status.json`; pass the nonce through; defer drop deletion for handled `PermissionRequest`; boot sweep of orphan status files |
| `src/twicc/providers/claude_code/agent/manager.py` | `handle_hybrid_hook(..., nonce)` signature + passthrough |
| `src/twicc/providers/claude_code/agent/hybrid/agent.py` | Real answerable pendings (nonce-keyed), `resolve_pending_request` override, stale-drop guard, clearing janitor (files + orphan-hook reap), expiry timer |
| `frontend/src/components/session/detail/SessionItemsList.vue` | Render `PendingRequestForm` on hybrid sessions for answerable requests |
| `frontend/src/components/message/HybridTerminalBlock.vue` | Badge matches any pending request (not only `hybrid_terminal`) |
| `CHANGELOG.md` | Feature entry |

---

### Task 1: Extract the shared bricks from the SDK agent

**Files:**
- Create: `src/twicc/providers/claude_code/agent/permissions.py`
- Modify: `src/twicc/providers/claude_code/agent/agent.py`
  (`get_permission_suggestions` ~line 336, the ExitPlanMode post-resolve block
  ~lines 622–632, `_update_plan` ~line 1669)

Pure refactor — zero behavior change on the SDK path.

- [x] **Step 1: `normalize_permission_suggestions(suggestions, cwd) -> list[dict] | None`**

Move from `get_permission_suggestions`: the `PermissionUpdate.to_dict()` /
plain-dict normalization, the `addDirectories`/`removeDirectories` cwd
filtering (drop the suggestion if its directories list becomes empty), and the
field-order normalization (`type, rules, behavior, mode, directories,
destination` + trailing `_`-prefixed private fields). Takes plain data, no
agent state.

**Stays in the SDK agent:** the injected `setMode` mode-picker suggestion
(`_modeOptions`/`_currentMode` block, trust-clamped, `auto`-filtered) — it is
SDK-only by design decision; `get_permission_suggestions` becomes a thin
wrapper: shared normalize → SDK-only injection → shared field-order pass (or
inject before the field-order pass, whichever keeps the output byte-identical).

- [x] **Step 2: `maybe_update_plan_file(tool_input, updated_input, *, slug_getter=None, session_id=None) -> None` (async)**

Move `_update_plan` + the modified-plan detection. Behavior:
- No-op unless `updated_input` is set and `updated_input.get("plan") !=
  tool_input.get("plan")`.
- Target path: `tool_input.get("planFilePath")` (CLI-injected since ~2.1.91,
  present in both the SDK `can_use_tool` input and the hook payload). Fallback
  when absent: the legacy slug lookup (`slug_getter(session_id)` →
  `~/.claude/plans/{slug}.md`) — only the SDK caller passes it.
- Keep the existing warnings/logging semantics (missing file → warn + skip).

- [x] **Step 3: Rewire the SDK agent**

`agent.py` imports both helpers; `_handle_pending_request`'s post-resolve
ExitPlanMode block calls `maybe_update_plan_file(input_data,
response.updated_input, slug_getter=self._get_session_slug,
session_id=self.session_id)`. Delete the now-dead private code.

- [x] **Step 4: Verify (SDK smoke)**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/claude-hybrid
TWICC_DATA_DIR=$PWD uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings')
django.setup()
from twicc.providers.claude_code.agent.permissions import normalize_permission_suggestions
print(normalize_permission_suggestions(
    [{'type': 'addDirectories', 'directories': ['/tmp/x', '/cwd'], 'destination': 'session'}], '/cwd'))
"
```
Expected: one suggestion with `/cwd` filtered out, ordered fields. Then a live
SDK approval check happens in Task 6's sweep (suggestions + mode picker still
render identically).

- [x] **Step 5: Commit** — `refactor(claude): extract shared permission/plan bricks from the SDK agent`

---

### Task 2: Hook command — status polling + timeout ceiling

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/hybrid/launch.py`
  (`build_hooks_settings`, ~line 24)

- [x] **Step 1: Probe the timeout ceiling (impl-time verification §6.1)**

Throwaway CLI (env-purged, `/tmp/twicc-probe-timeout/`) with a
`PermissionRequest` hook set to a huge `"timeout"` (e.g. `31536000` = 1 year;
also try `2147483647`). Trigger one approval, wait > 650 s (past the 600 s
default) with the hook still polling, confirm the hook process is still alive
→ the value is honored. If the CLI rejects/clamps (hook dies at 600 s), bisect
to the highest honored value. **Record the result in the commit message** and
set `HYBRID_HOOK_TIMEOUT_SECONDS` accordingly. (One real-time wait of ~11
minutes; run it in background while doing Task 3.)

- [x] **Step 2: Extend the hook command**

In `build_hooks_settings`, build the nonce once and derive both paths:

```python
hooks_dir = shlex.quote(str(get_hybrid_hooks_dir()))
name = f"{session_id}__PermissionRequest__$$-$(date +%s%N)"
command = (
    f'n={hooks_dir}/{name}; cat > "$n.json.tmp" && mv "$n.json.tmp" "$n.json" || exit 0; '
    f'while :; do if [ -f "$n.status.json" ]; then cat "$n.status.json"; rm -f "$n.status.json"; exit 0; fi; sleep 0.2; done'
)
```

Notes: the drop keeps its `.tmp`→`mv` atomicity; on drop failure exit early
(no point polling); the poll loop is unbounded — the CLI's own `"timeout"` is
the reaper, and the clearing janitor (Task 4) reaps orphans sooner. The hook
entry becomes `{"type": "command", "command": command, "timeout":
HYBRID_HOOK_TIMEOUT_SECONDS}` with the constant defined in `launch.py`
(imported by the agent for the expiry timer).

- [x] **Step 3: Probe the new command end-to-end**

Throwaway CLI launched with the EXACT settings JSON `build_hooks_settings`
produces (print it from a Python one-liner, feed it via `--settings`):
approval fires → drop appears → write a valid allow status file → hook prints
it → CLI dismisses the dialog and runs the tool → status file gone.

- [x] **Step 4: Commit** — `feat(hybrid): hook now polls a .status.json answer next to its drop`

---

### Task 3: Hooks watcher — status exclusion, nonce, deferred deletion

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/hybrid/hooks_watcher.py`
- Modify: `src/twicc/providers/claude_code/agent/manager.py`
  (`handle_hybrid_hook`, ~line 454)

- [x] **Step 1: Suffix exclusion**

`STATUS_SUFFIX = ".status.json"`; `_is_event_file` returns `False` for it (and
keeps the `.tmp` exclusion). **Load-bearing:** without this, a status file
parses as a 3-part event name and gets routed then deleted.

- [x] **Step 2: Nonce passthrough**

`_process_file` already splits `session_id, event, nonce` — pass `nonce` to
`handle_hybrid_hook(session_id, event, payload, nonce)`; update the manager
signature and forward to `agent.on_permission_request(payload, nonce)`.

- [x] **Step 3: Deferred drop deletion (hybrid-hooks only)**

When `handle_hybrid_hook` returns `True` for a `PermissionRequest`, do **not**
unlink the drop — the agent owns it now (deleted at resolution/clear/death).
Unhandled, malformed, and non-PermissionRequest events keep today's
delete-after-processing. Make `handle_hybrid_hook` return enough to
distinguish ("handled-and-owned" vs "handled" vs `False`) — e.g. a small enum
or `True`/`"owned"`/`False`; keep it simple.

Re-feed idempotence: the boot scan may re-process a kept drop after a restart
— the agent's registration must be idempotent per nonce (Task 4) and the
in-flight name guard already prevents double-processing within a run.

- [x] **Step 4: Boot sweep of orphan status files**

In `_scan_existing`, before processing drops: delete any `*.status.json`
without a matching drop file (mirrors
`DropRequestsWatcher._cleanup_orphan_status_files`).

- [x] **Step 5: Probe**

With the worktree backend running and a live hybrid test session: trigger an
approval → drop file PERSISTS in `hybrid-hooks/` while the pending is up (vs
V1 immediate deletion); craft an orphan `foo__PermissionRequest__1.status.json`
→ restart backend → swept at boot; confirm a status file written next to a
live drop is NOT consumed by the watcher.

- [x] **Step 6: Commit** — `feat(hybrid): hooks watcher passes the nonce and defers drop deletion`

---

### Task 4: Hybrid agent — answerable pendings, resolve override, janitor

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/hybrid/agent.py`
  (pending block ~lines 359–420, death paths ~lines 436–483)
- Create: `src/twicc/providers/claude_code/agent/hybrid/responses.py`

- [x] **Step 1: Registration (replaces `_mark_pending_in_terminal`)**

`on_permission_request(payload, nonce)`:
- Idempotent per nonce (re-feed after restart): if `nonce` already registered,
  no-op.
- **Stale-drop guard (boot re-feed):** parse the nonce's nanosecond timestamp
  (`<pid>-<ns>` → ns part). If the session has a `tool_result` or
  `turn_duration` SessionItem whose JSONL-embedded `timestamp` is later than
  the nonce time, the prompt was answered while TwiCC was down → delete the
  drop file, don't register. (JSONL message timestamps, NOT ingest times —
  ingest happens at boot, after the nonce, for exactly these lines.)
- Register `PendingRequest(request_id=nonce, request_type="ask_user_question"
  if tool_name == "AskUserQuestion" else "tool_approval", tool_name,
  tool_input, created_at=now, permission_suggestions=
  normalize_permission_suggestions(payload.get("permission_suggestions"),
  self.cwd))` — hook-native suggestions only, through the shared filter; NO
  setMode mode-picker injection, NO synthesized suggestions (design §3/§4.4).
- Schedule the expiry timer (Step 4). Keep `last_activity` + broadcast as
  today. Multiple nonces can coexist in `_pending_requests` (sequential in
  practice).

- [x] **Step 2: `responses.py` — wire conversion + file helpers**

- `to_hook_output(response) -> dict`:
  `PermissionResultAllow` → `{"hookSpecificOutput": {"hookEventName":
  "PermissionRequest", "decision": {"behavior": "allow"}}}` plus, when set,
  `updatedInput` (= `updated_input` verbatim) and `updatedPermissions`
  (= `updated_permissions`, serializing `PermissionUpdate` objects back to
  dicts via `.to_dict()` — `ws.py` reconstructs them as dataclasses).
  `PermissionResultDeny` → `{"behavior": "deny", "message": response.message}`.
- `status_path_for(session_id, nonce) -> Path` and
  `drop_path_for(session_id, nonce) -> Path` (single place deriving the
  `hybrid-hooks/<sid>__PermissionRequest__<nonce>[.status].json` names).
- `write_status(path, data)`: orjson dump, `.tmp` → `os.replace`, chmod 600
  (same recipe as `DropRequestsWatcher._write_status`).

- [x] **Step 3: `resolve_pending_request` override**

Async-safe override on the hybrid agent (base signature
`resolve_pending_request(request_id, response) -> bool`, called by the base
manager — check whether the base calls it sync; if so, do the file I/O via
`asyncio.ensure_future` on a small async helper, popping the pending
synchronously first):
- Unknown `request_id` → log + `False` (already cleared).
- If `tool_name == "ExitPlanMode"` and allow: `await
  maybe_update_plan_file(pending.tool_input, response.updated_input)` (no slug
  fallback in hybrid — `planFilePath` is in the payload) **before** writing
  the status file.
- Write the status file (`to_hook_output`), delete the drop file, pop the
  pending, stamp `last_pending_resolved_at` when none left, broadcast.
- The frontend's `isResponding` spinner resolves when the pending disappears
  from the next `process_state` broadcast — same as SDK.

- [x] **Step 4: Clearing janitor + expiry timer**

- `on_jsonl_progress` / `on_jsonl_turn_end` (and the death paths that call
  `_clear_pending_marker` today): for EVERY registered pending — pop it, then
  **reap the orphan hook**: write a dummy status file (a deny with message
  `"Already answered in the terminal"` — the CLI provably ignores late hook
  output) so the still-polling hook consumes it and exits; delete the drop
  file; schedule a delayed (~5 s) `unlink(missing_ok=True)` of the status file
  for the case where the hook is already dead (TUI Esc kills it) and nobody
  consumes the dummy. Boot sweep (Task 3) is the final backstop.
- Expiry timer (per pending): at `created_at + HYBRID_HOOK_TIMEOUT_SECONDS`,
  if still registered, replace the entry with a copy whose
  `request_type="hybrid_terminal"` (dataclass is frozen — rebuild), broadcast
  (widget degrades to the badge). Cancel the timer on resolve/clear/death.
- DEAD path: clear all pendings + delete their drop/status files (no dummy
  needed — the tmux kill takes the hooks down with the CLI).

- [x] **Step 5: Probe (backend-only, no UI)**

Live hybrid test session on the worktree backend; drive answers by calling the
WS-equivalent path or directly `manager.resolve_pending_request(...)` from a
`TWICC_DATA_DIR=$PWD` Python shell against the running agent — simpler: use
`websocat`/the UI in Task 6. Minimum here: trigger approval → pending appears
in `process_state` with `request_type="tool_approval"` and real suggestions;
answer in the TUI → pending clears, drop file gone, orphan hook exits (dummy
status consumed) within ~1 s.

- [x] **Step 6: Commit** — `feat(hybrid): answerable pending requests resolved through the hook status file`

---

### Task 5: Frontend — widget on hybrid sessions

**Files:**
- Modify: `frontend/src/components/session/detail/SessionItemsList.vue` (~line 1528)
- Modify: `frontend/src/components/message/HybridTerminalBlock.vue` (~line 110)

- [x] **Step 1: Gating**

`PendingRequestForm` renders when `hasPendingRequest` AND the head request is
answerable: on hybrid sessions that means
`pendingRequest.request_type !== 'hybrid_terminal'`; non-hybrid behavior
unchanged. Keep the comment explaining the hybrid dual-surface. The form
already routes to `ClaudePendingRequestBody` by provider — no change there,
and without `_modeOptions` suggestions the mode picker simply doesn't render.

- [x] **Step 2: Badge**

`hybridPending` matches the head pending request regardless of
`request_type` (any pending means "a prompt is up in the terminal"); label
logic unchanged.

- [x] **Step 3: Build check**

`cd frontend && npx vite build` — then **delete `src/twicc/static/frontend/`**
(stale-release trap). Visual checks land in Task 6.

- [x] **Step 4: Commit** — `feat(hybrid): show the pending-request widget on hybrid sessions`

---

### Task 6: E2E sweep + CHANGELOG

**Files:**
- Modify: `CHANGELOG.md` ([Unreleased] → Added)

End-to-end on the worktree instance (servers via `uv run ./devctl.py start`
from the worktree; hybrid test session in `/tmp/twicc-hybrid-test`, sonnet,
low effort), via Chrome MCP:

- [x] **Sweep — all through the real UI:**
  1. Tool approval (Write in default mode): widget appears with suggestions →
     **Allow** in GUI → TUI dialog dismisses, tool runs, widget clears.
  2. Same → **Deny with message** → message visible in the TUI error and the
     session flow.
  3. "Always allow" (`setMode acceptEdits` suggestion) → subsequent edit runs
     without a prompt; `Session.permission_mode` updated in DB (ws.py
     persistence).
  4. `AskUserQuestion` → widget shows options → answer in GUI → TUI widget
     dismisses, answer visible ("→ X" line) and used by Claude.
  5. `ExitPlanMode` (plan-mode session): widget shows the plan → **edit the
     plan text** in the widget → approve → plan file on disk contains the
     edited text; CLI proceeds.
  6. Race: trigger approval, answer in the **TUI** → widget clears by itself
     (JSONL bridge), no stray files in `hybrid-hooks/` after ~5 s, no lingering
     hook process (`pgrep` on the hook script must use a pattern that does not
     self-match).
  7. TUI **Esc** → widget clears, files cleaned.
  8. **TwiCC restart mid-pending** (worktree backend only): trigger approval,
     `devctl.py stop back` → `start back` (anti-zombie sequence from memory)
     → widget reappears (re-fed drop), GUI answer still works (hook survived).
  9. SDK session regression: a normal (non-hybrid) approval still renders with
     the mode picker and resolves (Task 1 refactor sanity).
- [x] **CHANGELOG** under `### Added`: hybrid sessions now show the real
  pending-request widget — approvals, questions and plan reviews can be
  answered from the UI or the terminal, whichever comes first.
- [x] **Commit** — `feat(hybrid): GUI answering E2E + changelog`

---

## Out of scope — do not implement

- TwiCC-added suggestions on hybrid (the injected `setMode` mode-picker
  `_modeOptions`, any "auto-accept edits" synthesis for ExitPlanMode) — may
  come later; the SDK path keeps them.
- Back-sync TUI→TwiCC, crons on hybrid sessions (still V2-parked).
- Any change to the general `drop-requests/` CLI system — its lifecycle is
  untouched; only `hybrid-hooks/` drops get deferred deletion.
- Live tool indicators — cancelled for good (design doc 2026-06-10 §7.1).
