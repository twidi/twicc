# Codex Approvals — PR3 + PR4 Carryover Notes

> **Not a plan.** This is a memo written before context compaction (2026-05-14, post-PR2c).
> Captures decisions, scope, and reviewer-flagged carry-over items that aren't yet in committed plans.
> PR3 and PR4 plans will be written when work starts on each, using this memo as the source of truth.

---

## Status

- PR1 ✅ — `BaseAgent` refactor (shared pending plumbing)
- PR2a ✅ — Backend pipeline + permission_modes wired (`DEFAULT_MODE = "yolo"`, behavioural no-op)
- PR2b ✅ — `DEFAULT_MODE` flipped to `"auto"` + frontend stub + dispatcher
- PR2c ✅ — Spinner orphan fix + live `permission_mode` update (just landed)
- **PR3** ❌ — plan to write, scope below
- **PR4** ❌ — plan to write, scope below

---

## Decision: "Option groupée 3 PRs"

Made during the PR2b smoke-test debrief when 3 issues surfaced:
1. **Spinner orphelin** on Deny / Cancel turn (function-call tool cards stayed spinning forever)
2. **Live `permission_mode`** had no effect mid-session
3. **UI factorisation** was wrong in PR2b Task 5 (`CodexPendingRequestBody.vue` contains too much; the shell isn't shared)

User picked the **grouped option** (out of 3: fine / grouped / mono-bloc):
- **PR2c**: backend bugs (issues 1 + 2) — DONE
- **PR3 revisée**: frontend factorisation correctly done + rich render + 5e mode `strict` + tooltips
- **PR4**: tests + docs

---

## Spec §0.2 deviation — confirmed by user

Spec §0.2 originally said:
> Pas de mode runtime style Claude (changement live ... en cours de session). On utilise bien le champ `Session.permission_mode` côté DB, mais comme un **préset déterminé au démarrage** ... Pas d'override mid-session via WS.

User confirmed in PR2b smoke test that this exclusion was a **design mistake at brainstorm time**. The agent_settings "closed bundle" contract (CLAUDE.md frontend section) says all settings should be re-applied at every turn. `effort` already obeys this; PR2c brought `permission_mode` in line.

Spec file is NOT modified (per "no historical doc edits" memory). Deviation is documented in:
- Plan PR2c "Reference spec" section
- Commit `14ed2e4c` body
- Code comment in `_run_turn` (`agent.py:271-279`)

**Implication for future spec audits**: §0.2 has 2 more exclusions that should be reviewed when PR3 starts to confirm they're still intentional:
- "Pas de `ask_user_question` côté Codex" — probably still correct (no Codex equivalent today), but worth confirming.
- "Pas d'auto-approval / guardian" — probably still correct.

---

## PR3 revisée — Scope

**Goal**: bring the Codex approval UX up to Claude's quality, AND fix the factorisation mistake from PR2b Task 5.

### 1. Factorisation correcte (PR2b Task 5 bug to fix)

**What went wrong in PR2b Task 5**: `CodexPendingRequestBody.vue` contains the entire card (tool badge header, payload area, buttons). Each provider re-implements the shell. The pre-existing Claude render is still inline in `PendingRequestForm.vue` template branches. Result:
- Codex banner looks completely different from Claude's (red buttons too big, different layout, no shared "Tool approval requested" header).
- Any future shell change has to be done 2× (once per provider).

**What should be done in PR3** (per spec §4 Étape 5.2):

- **`PendingRequestForm.vue`** = SHELL ONLY. Owns:
  - Card wrapper (`<wa-divider>`, container div, expand toggle)
  - Header: icon "shield-halved", title "Tool approval requested", count badge for parallel pending, expand toggle
  - Bottom buttons row — SAME visual size and style for both providers (Claude currently has Deny / Approve with changes / Approve; Codex has Deny / Cancel turn / Approve)
  - `isResponding` state guard
  - The provider-agnostic `respondToPendingRequest` dispatch (already in place from PR2b Task 4)
  - Provider-aware routing: pick the right body sub-component based on `dataStore.getSession(sessionId)?.provider`

- **`claude_code/PendingRequestBody.vue`** (NEW, extracted from current inline Claude render in `PendingRequestForm.vue`). Contains JUST:
  - Tool name display
  - JsonHumanView with `TOOL_OVERRIDES_BASE`
  - Permission suggestions section (with destination override, edit suggestions, etc.)
  - Edit mode (toggle, edited tool_input, save edits)
  - Branch on `requestType` between `tool_approval` and `ask_user_question`
  - **Builds** the response payload but DOES NOT send (the shell does the dispatch)

- **`codex/PendingRequestBody.vue`** (REFONDU, replaces the PR2b stub):
  - Specialised content per `tool_name` (commandExecution / fileChange / permissions)
  - Builds response payload, doesn't send

**Body → Shell communication contract** (design choice for PR3 plan):
- Recommended: Vue emit events (`'approve'`, `'deny'`, `'cancel'`, `'submit'`) with the response payload as the event detail. The shell listens, dispatches via the provider-agnostic dispatcher, manages `isResponding`. Clean separation.
- Alternative: `defineExpose()` surfacing payload-builder functions to the parent. More coupling.

### 2. Rich rendering per `tool_name` (spec §4 Étape 5.3 + §7-Q3 / Q5)

- **`commandExecution`** card:
  - Header: command (1-line ellipsed) + cwd + reason if present
  - Body: if `commandActions` present, show parsed intent (read / list / search / unknown); if `networkApprovalContext` present, show "wants network access to {host} via {protocol}"; show `proposedExecpolicyAmendment` and `proposedNetworkPolicyAmendments` as add-rule chips
- **`fileChange`** card:
  - Header: "Wants to modify N file(s)"
  - Body: list of paths from `_item_payload.changes`, each with its `kind` badge (add/update/delete). Reason if present.
  - The actual diff is already rendered higher in the timeline as an `ApplyPatch` item — the banner just summarises.
- **`permissions`** card:
  - Header: "Requests additional permissions"
  - Body: list of requested permissions (network y/n, fileSystem entries) + reason

Source data already in `pendingRequest.tool_input`:
- `commandExecution`: params verbatim (command, cwd, reason, commandActions, proposedExecpolicyAmendment, proposedNetworkPolicyAmendments, networkApprovalContext)
- `fileChange`: params + `_item_payload` (injected by `_enrich_params_with_item_payload`, PR2a Task 4)
- `permissions`: `params.permissions` (= `RequestPermissionProfile`)

### 3. Split-button Approve menus (spec §7-Q3, §7-Q5)

- **commandExecution** Approve dropdown:
  - Once → `{decision: "accept"}`
  - For session → `{decision: "acceptForSession"}`
  - +add allow rule → `{decision: {acceptWithExecpolicyAmendment: {...}}}` or `{decision: {applyNetworkPolicyAmendment: {...}}}` — **only visible if `proposedExecpolicyAmendment` or `proposedNetworkPolicyAmendments` is non-null in params**
- **fileChange** Approve dropdown:
  - Once → `{decision: "accept"}`
  - For session → `{decision: "acceptForSession"}`
- **permissions** Approve dropdown:
  - For this turn → `{permissions: <granted>, scope: "turn"}`
  - For this session → `{permissions: <granted>, scope: "session"}`

Wa-Awesome 3: `wa-dropdown` + `wa-dropdown-item` (verify imports in `main.js` — likely not yet imported).

### 4. 5e mode `strict` (spec §4 Étape 7)

- **Frontend**:
  - Add `STRICT: 'strict'` to `PERMISSION_MODE` in `frontend/src/providers/codex/constants.js`
  - Add the entry in `AGENT_SETTINGS_CHOICES` of `frontend/src/providers/codex/helpers.js` with label + help text
- **Backend**:
  - Add `"strict": (SandboxMode.read_only, AskForApproval("never"))` to `_PRESET_MAP` in `permission_modes.py`
  - Update the wire/preset table in the module docstring (now 5 rows)
  - `strict` behaves like `read_only` in sandbox but with silent rejection (no prompt) — the "Don't ask" equivalent of Claude

### 5. Tooltips Deny vs Cancel turn (spec §7-Q3)

- **Deny** tooltip: "Refuse this action. Codex may try another approach."
- **Cancel turn** tooltip: "End this turn. Codex returns control to you. Different from Stop (which kills the agent)."

### 6. Reason strings unification (carryover from PR2c Task 3 code review, Minor M1)

Current state — inconsistent vocabulary:
- Claude (`ws.py:219`): `"User denied this action"` (subject-first, active voice)
- Codex `agent.py:_record_decision_outcome` (PR2c):
  - `"Denied by user"` (passive)
  - `"Turn cancelled by user"` (passive)
  - `"Permissions denied by user"` (passive)

Decision needed in PR3: pick a style and align both providers. Suggestion: subject-first ("User …") for consistency with Claude:
- Deny: "User denied this action"
- Cancel: "User cancelled this turn"
- Permissions: "User refused permissions"

### 7. Retry nested template structure

PR2b Task 5 ended up with flat-chain `v-if / v-else-if / v-else-if` because nested `<template v-else-if>` failed in the Vue SFC compiler. After the shell/body split is done in PR3, retest whether the structure can nest cleanly (`<template v-if="provider === 'codex'"><CodexBody/></template>` vs `<template v-else-if="provider === 'claude_code'"><ClaudeBody/></template>`). If it works, simpler structure. If not, keep the explicit `provider === 'claude_code' && requestType === ...` gates added in PR2b commit `4831f2c6`.

---

## PR4 — Scope

### 1. Tests unitaires (spec §8 "Tests")

Pure functions to unit-test (all extracted by PR2a / PR2c so easy to test in isolation):

**`permission_modes.py`**:
- `resolve_codex_policy(mode)` — 5 modes (after PR3 adds `strict`) + None + unknown → expected `(SandboxMode, AskForApproval)`
- `resolve_codex_turn_overrides(mode)` — same, but with `(SandboxPolicy, AskForApproval)`
- `_to_sandbox_policy(sandbox_mode)` — 3 SandboxMode values → 3 SandboxPolicy variants, ValueError on unsupported

**`approvals.py`**:
- `is_approval_method(method)` — true for 3 known methods, false otherwise
- `derive_request_id(params)` — approvalId > itemId > UUID4 fallback; None / empty / non-string defensive cases
- `make_pending_request(method, params)` — 3 valid methods → correct `PendingRequest` shape; ValueError on unknown method
- `default_response_for(method)` — 3 methods → correct wire fallback; ValueError on unknown; defensive copy (mutation doesn't leak)

**`codex/ws.py`**:
- `_build_codex_response(tool_name, content)` — happy paths + invalid
- `_build_command_response(decision)` — strings, dict variants, inner-payload validation (acceptWithExecpolicyAmendment requires `{execpolicy_amendment: list}`, applyNetworkPolicyAmendment requires `{network_policy_amendment: dict}`)
- `_build_file_response(decision)` — strings only, no dict variants
- `_build_permissions_response(content)` — scope validation, permissions dict type, optional strictAutoReview boolean
- `_safe_default_for(tool_name)` — 3 tool_names + fallback

**Note**: don't test `_record_decision_outcome` or `_denied_tool_reason` directly — they're tightly coupled to `CodexAgent` / agent registry state. Cover them via integration tests or skip.

### 2. Background re-compute verification (carryover from PR2c Task 4 review, Minor M2)

The `_denied_tool_ids` side-table is in-memory only. After a backend restart, a background re-compute on a session's JSONL has no agent to consult; `_denied_tool_reason` returns None; the 3 exit-code helpers return None for "aborted by user" / "Rejected by user" trailers (no exit code). So a re-compute's `ToolResultInfo` has `is_error=False`.

**PR4 verifications needed**:
1. Does `BaseSessionCompute.create_tool_result_link_live` (`compute_base.py:1155-1188`) OVERWRITE `ToolResultLink.error` if a re-compute produces `error_text=None`? Read the `get_or_create` + update semantics.
2. If yes: design a fix. Two options:
   - **Option A**: Persist the refusal reason in the DB at WS-resolve time (e.g. a column on `SessionItem` or a side-table), so the deterministic compute path can recover the reason without an agent. More surface area.
   - **Option B**: In `create_tool_result_link_live`, when an existing row already has `error` set and the new `error_text` is None, skip the overwrite. Smaller change.

### 3. Debug log cancel siblings (carryover from PR2c Task 3 review, Minor M3)

In `CodexAgent._record_decision_outcome`'s cancel-turn branch (`agent.py:838-848`), add a debug log inside the iteration:

```python
logger.debug(
    "Codex cancel: marking sibling item_id=%r type=%r",
    other_id, payload.get("type"),
)
```

Helps debug if the sibling marking doesn't fire when expected during multi-tool turns (rare but possible).

### 4. Mémoires + docs (spec §8)

**New memory** at `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/project_codex_approvals.md`:
- Architecture overview of Codex approvals
- Bridge sync/async (monkey-patch SDK approval handler, `run_coroutine_threadsafe`, `CancelledError` + `concurrent.futures.CancelledError` handling — the gotcha from PR2a)
- Permission_mode preset table (5 modes after PR3)
- Side-tables on `CodexAgent`: `_items_by_id` (for fileChange diff retrieval, PR2a Task 3) and `_denied_tool_ids` (for spinner fix, PR2c Task 3)
- Live per-turn updates: agent_settings re-read in `_run_turn`, `thread.turn(approval_policy=, sandbox_policy=)` per-turn overrides
- Pointer to spec §0.2 deviation: live updates ARE supported (contrary to original spec text)

**Update existing memory** `reference_codex_sdk_update_procedure.md`:
- Add check that `SandboxPolicy` variant classes (`ReadOnlySandboxPolicy`, `WorkspaceWriteSandboxPolicy`, `DangerFullAccessSandboxPolicy`) still exist at `codex_app_server.generated.v2_all`
- Add check that their `type` Literal values are still `"readOnly"`, `"workspaceWrite"`, `"dangerFullAccess"` (the discriminator could change)
- Add check that `AsyncThread.turn(approval_policy=..., sandbox_policy=...)` signature is unchanged

**CHANGELOG.md**: entry in `[Unreleased]`:
- Feature: Codex approvals are now interactive (vs. always-allow before)
- 5 modes: read_only / strict / auto / autonomous / yolo
- Approve / Deny / Cancel turn buttons with per-tool rich rendering (cmd / file / permissions)
- Live updates: changing the mode picker mid-session affects the next turn

---

## Notes / surprises / lessons from PR2a → PR2c

- **`JsonHumanView` is a magic fallback** — PR2b's user test revealed that even without a Codex-specific render component, the generic Vue layer surfaces the entire `tool_input` as a structured tree. This is what kept PR2b functional despite the factorisation mistake. PR3's specialised rendering replaces this fallback per `tool_name`, BUT the fallback stays as a safety net.

- **PR1 + PR2a BaseAgent refactor pays off** — `_cancel_all_pending_futures`, `_await_pending_request`, `resolve_pending_request` are all base class methods now. Codex's only Codex-specific code is the wire/SDK bridge + the 3 approval methods. PR2c added 2 small additions (`_denied_tool_ids` map + `_to_sandbox_policy`) that fit cleanly in the existing structure.

- **`asyncio.CancelledError` vs `concurrent.futures.CancelledError`** — caught by review in PR2a (commit `57e4ede1`). Since Python 3.8 these are DIFFERENT classes. `asyncio.run_coroutine_threadsafe(coro, loop).result()` re-raises as the latter. The bridge needs to catch both: `except (asyncio.CancelledError, concurrent.futures.CancelledError):`. **Don't forget when reading any new code that bridges async to sync via `run_coroutine_threadsafe`**.

- **Spec §0.2 was wrong about live mode**. Other exclusions in §0.2 should be sanity-checked when PR3 starts (`ask_user_question` for Codex, auto-approval/guardian) — they may also need revisiting.

- **Reviewer-flagged items grow per PR** — keep this memo alive between PRs. The 3 Minor flags from PR2c are now in PR3/PR4 scope. If PR3 surfaces new ones, append.

---

## Open considerations (not yet decided)

- Should the "Cancel turn" button be hidden for `permissions` requests (PR2b decision) or shown but mapped to the same payload as Deny (cancel doesn't exist in the wire format for permissions per spec §1.1.c)? PR2b hid it. PR3 may reconsider for visual consistency.
- Reason strings vocabulary: subject-first ("User …") vs passive ("… by user")? Decide in PR3.
- Body → Shell communication: events vs `defineExpose`. Probably events for cleanness.
- Background re-compute overwrite: option A (persist refusal in DB) vs option B (skip overwrite when new value is None). Probably B for simplicity.

---

End of memo. Next step: smoke-test PR2c, then proceed to PR3 plan.
