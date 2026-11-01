# Codex Approvals PR3 — Frontend Refactor + Rich Rendering + Strict Mode

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the Codex approval UX up to Claude's quality (per-tool rich rendering, split-button Approve menus, tooltips) AND fix the factorisation mistake from PR2b Task 5 (shared shell + per-provider body), AND add the 5th `strict` preset mode.

**Architecture:** Three layers of change.

1. **Backend** (Python, small): add the `strict` preset to `_PRESET_MAP`; align refusal reason strings to Claude's subject-first style ("User …") so `ToolResultLink.error` reads consistently across providers.

2. **Frontend constants** (small): add `STRICT: 'strict'` to the Codex constants and the matching entry in `AGENT_SETTINGS_CHOICES` (picker label + description). 5 modes after this change.

3. **Frontend components** (the bulk): factor `PendingRequestForm.vue` into a **shell** (header, count badge, expand toggle, single body slot, dispatch on `'submit'`) and two **bodies** (`claude_code/PendingRequestBody.vue` extracted from the inline Claude render; `codex/PendingRequestBody.vue` refondu with per-`tool_name` rich rendering + split-button Approve menus + Deny/Cancel-turn tooltips). Body → shell contract is a single Vue `emit('submit', payload)` with the provider-shaped payload — no `defineExpose`, no shared mutable refs.

**Tech Stack:** Vue 3 SFC (Composition API + `<script setup>`), Web Awesome 3 (`wa-button`, `wa-dropdown`, `wa-dropdown-item`, `wa-badge`, `wa-icon`, `wa-divider`), Pinia (`useDataStore`), Python 3.13.

---

## Reference spec sections

- **Memo carryover** — `docs/superpowers/plans/2026-05-14-codex-approvals-pr3-pr4-carryover-notes.md` — sections "PR3 revisée — Scope" (items 1-7).
- **Spec design** — `docs/superpowers/specs/2026-05-14-codex-approvals-design.md`:
  - §4 Étape 5.2 — Provider-aware rendering (shell/body split)
  - §4 Étape 5.3 — Affichage des informations per tool_name
  - §4 Étape 7 — `strict` mode preset table
  - §7-Q3 — Decisions visibles dans l'UI (3 boutons + split menu sur Approve)
  - §7-Q5 — Permissions UI

---

## File Structure

### Backend changes (Python)

| File | Action | Responsibility |
|------|--------|----------------|
| `src/twicc/providers/codex/permission_modes.py` | Modify | Add `"strict"` to `_PRESET_MAP`; update docstring table |
| `src/twicc/providers/codex/agent/agent.py` | Modify (`_record_decision_outcome`) | Refactor 3 refusal reason strings to "User …" subject-first style |

### Frontend constants (JS)

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/src/providers/codex/constants.js` | Modify | Add `STRICT: 'strict'` to `PERMISSION_MODE` |
| `frontend/src/providers/codex/helpers.js` | Modify (`AGENT_SETTINGS_CHOICES`) | Add `STRICT` entry between `READ_ONLY` and `AUTO` |

### Frontend components (Vue)

| File | Action | Responsibility |
|------|--------|----------------|
| `frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue` | **Create** | Pure Claude body: tool_input + permission suggestions + edit mode + ask_user_question variant + buttons row. Emits `'submit'` with `{request_type, decision, …}` payload. |
| `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` | **Rewrite** (replace PR2b stub) | Per-`tool_name` rich rendering (commandExecution / fileChange / permissions) + split-button Approve menus + tooltips. Emits `'submit'` with `{tool_name, decision, …}` payload. |
| `frontend/src/components/message/PendingRequestForm.vue` | Refactor (shell only) | Card wrapper, header (icon + title + count badge + expand toggle), routes to a body sub-component based on `session.provider`, listens on `@submit`, manages `isResponding`, dispatches via `respondToPendingRequest`. No tool-specific rendering inside. |
| `frontend/src/main.js` | Modify if needed | Import `wa-dropdown` + `wa-dropdown-item` if not already (verify first) |

### Body → Shell communication contract

**Single event** `'submit'` carrying the FULL provider-shaped payload (what `respondToPendingRequest` expects as its last argument). The shell does not interpret the payload — it just forwards it to the WS dispatcher.

```javascript
// In a body (Claude example for an Allow):
emit('submit', {
    request_type: 'tool_approval',
    decision: 'allow',
    updated_input: toolInputValue,
    updated_permissions: checkedPermissions,
})

// In a body (Codex example for commandExecution Approve once):
emit('submit', {
    tool_name: 'commandExecution',
    decision: 'accept',
})

// In a body (Codex example for permissions For-session):
emit('submit', {
    tool_name: 'permissions',
    permissions: granted,
    scope: 'session',
})

// In the shell:
function onBodySubmit(payload) {
    if (isResponding.value) return
    isResponding.value = true
    respondToPendingRequest(provider.value, sessionId, requestId, payload)
}
```

**Why a single event** : Vue's parent-via-emit is the cleanest decoupling pattern. Multiple events (`approve`/`deny`/`cancel`) duplicate the handler boilerplate in the shell and would force the shell to know provider-specific button vocabulary. The body already builds the right payload — emit it.

---

## Tasks

### Task 1: Add `strict` preset (backend)

**Files:**
- Modify: `src/twicc/providers/codex/permission_modes.py`

- [ ] **Step 1: Add `"strict"` entry to `_PRESET_MAP`**

Replace the dict at `permission_modes.py:43-48` with:

```python
_PRESET_MAP: dict[str, tuple[SandboxMode, AskForApproval]] = {
    "read_only":  (SandboxMode.read_only,           AskForApproval("on-request")),
    "strict":     (SandboxMode.read_only,           AskForApproval("never")),
    "auto":       (SandboxMode.workspace_write,     AskForApproval("on-request")),
    "autonomous": (SandboxMode.workspace_write,     AskForApproval("never")),
    "yolo":       (SandboxMode.danger_full_access,  AskForApproval("never")),
}
```

- [ ] **Step 2: Update the docstring table at lines 10-19 to 5 rows**

Replace the existing 4-row table with the 5-row version (insert `strict` between `read_only` and `auto`, same order as `_PRESET_MAP`):

```
+-------------+-------------------+--------------------+-----------+----------------+
| Mode (wire) | sandbox_mode      | approval_policy    | Prompts?  | Can write?     |
+=============+===================+====================+===========+================+
| read_only   | read-only         | on-request         | yes       | no             |
| strict      | read-only         | never              | no        | no             |
| auto        | workspace-write   | on-request         | yes       | workspace only |
| autonomous  | workspace-write   | never              | no        | workspace only |
| yolo        | danger-full-access| never              | no        | anywhere       |
+-------------+-------------------+--------------------+-----------+----------------+
```

Also drop the line "The 5th mode ``strict`` is added in a later PR." from the module docstring (line 7-8) — it's now misleading.

- [ ] **Step 3: Verify nothing else hardcodes the 4 modes**

Run:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -rn 'read_only.*auto.*autonomous.*yolo\|PRESET_MAP\|MODES\[' src/twicc/providers/codex/
```

Expected: only the modified `permission_modes.py` mentions the full list. The factory in `manager.py` calls `resolve_codex_policy` — no hardcoding. The agent reads `agent_settings.permission_mode` via `resolve_codex_turn_overrides` — no hardcoding. ✅

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/permission_modes.py
git commit -m "$(cat <<'EOF'
feat(codex): add strict preset mode (read-only + never-ask)

5th preset. Like read_only in sandbox but with silent rejection (no
prompt) — the functional equivalent of Claude's "Don't ask" mode for
sessions where you want read-only exploration without any UI noise.

Wire value: "strict". Picker label + description land in PR3 frontend
constants. Behaviour-wise: ``approval_policy="never"`` means the SDK
never emits ``commandExecution`` / ``fileChange`` / ``permissions``
server requests — the bridge stays dormant, the agent silently refuses
writes per the sandbox.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Align refusal reason strings ("User …" subject-first)

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py` (4 string occurrences in `_record_decision_outcome` + 1 in the cancel-siblings branch)

Current state — inconsistent vocabulary:
- Claude (`ws.py:219`): `"User denied this action"` ← target style (subject-first, active voice)
- Codex Deny: `"Denied by user"` (passive)
- Codex Cancel: `"Turn cancelled by user"` (passive)
- Codex Permissions: `"Permissions denied by user"` (passive)

After this task: subject-first for both providers.

- [ ] **Step 1: Update the 3 Codex reason strings**

In `src/twicc/providers/codex/agent/agent.py`, function `_record_decision_outcome` (around lines 820-870):

```python
# permissions branch:
self._denied_tool_ids[item_id] = "Permissions denied by user"
# becomes:
self._denied_tool_ids[item_id] = "User refused permissions"

# decline branch:
self._denied_tool_ids[item_id] = "Denied by user"
# becomes:
self._denied_tool_ids[item_id] = "User denied this action"

# cancel branch — current itemId:
self._denied_tool_ids[item_id] = "Turn cancelled by user"
# becomes:
self._denied_tool_ids[item_id] = "User cancelled this turn"

# cancel branch — siblings loop:
self._denied_tool_ids[other_id] = "Turn cancelled by user"
# becomes:
self._denied_tool_ids[other_id] = "User cancelled this turn"
```

Also update the log strings that quote the reason to match (the 4 `logger.debug(...)` calls in the same function).

- [ ] **Step 2: Update related code comments referencing the old strings**

Search for the old strings in comments:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -n '"Denied by user"\|"Turn cancelled by user"\|"Permissions denied by user"' src/
```

Expected hits: only the strings being changed above (no surrounding comments reference them directly today). If any comment hits, update it.

- [ ] **Step 3: Visual verification (no test infra)**

Verify the file compiles via a Python syntax check:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "from twicc.providers.codex.agent import agent; print(agent.CodexAgent._CANCELLABLE_ITEM_TYPES)"
```

Expected: `frozenset({'commandExecution', 'fileChange'})` (or with reverse order; the set print is non-deterministic).

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/agent.py
git commit -m "$(cat <<'EOF'
refactor(codex): unify refusal reason strings to subject-first style

Aligns Codex with Claude's vocabulary ("User denied this action"
at ``claude_code/ws.py:219``). Before: 3 passive strings ("Denied by
user", "Turn cancelled by user", "Permissions denied by user"). After:
3 subject-first strings ("User denied this action", "User cancelled
this turn", "User refused permissions").

User-facing impact: ``ToolResultLink.error`` reads consistently across
providers. No protocol change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Add `strict` mode frontend (constants + helpers)

**Files:**
- Modify: `frontend/src/providers/codex/constants.js`
- Modify: `frontend/src/providers/codex/helpers.js`

- [ ] **Step 1: Add `STRICT: 'strict'` to `PERMISSION_MODE`**

In `frontend/src/providers/codex/constants.js`, replace the `PERMISSION_MODE` block:

```javascript
export const PERMISSION_MODE = {
    READ_ONLY: 'read_only',
    STRICT: 'strict',
    AUTO: 'auto',
    AUTONOMOUS: 'autonomous',
    YOLO: 'yolo',
}
```

(Keep the existing docblock comment above the const; just update the values.)

- [ ] **Step 2: Add `STRICT` entry to `AGENT_SETTINGS_CHOICES.permission_mode`**

In `frontend/src/providers/codex/helpers.js`, insert the new entry between `READ_ONLY` and `AUTO` in `AGENT_SETTINGS_CHOICES.permission_mode` (around lines 66-87):

```javascript
permission_mode: [
    {
        value: PERMISSION_MODE.READ_ONLY,
        label: 'Read-only',
        description: 'Read-only. Any write requires confirmation.',
    },
    {
        value: PERMISSION_MODE.STRICT,
        label: 'Strict',
        description: 'Read-only. Writes are refused silently (no prompt).',
    },
    {
        value: PERMISSION_MODE.AUTO,
        label: 'Auto',
        description: 'Writes freely in the workspace; asks to step outside.',
    },
    {
        value: PERMISSION_MODE.AUTONOMOUS,
        label: 'Autonomous',
        description: 'Like Auto but uninterrupted (sandbox protects).',
    },
    {
        value: PERMISSION_MODE.YOLO,
        label: 'YOLO',
        description: 'No restrictions.',
    },
],
```

- [ ] **Step 3: Visual verification**

The frontend dev server is HMR-enabled. With backend running, open the Codex session-settings picker and confirm 5 modes appear. (Don't restart anything — Vite HMR will reload on file save.)

If you want to verify without UI: search for `PERMISSION_MODE.STRICT` usage:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -rn 'PERMISSION_MODE\.' frontend/src/providers/codex/
```

Expected: 5 occurrences in `helpers.js` (one per AGENT_SETTINGS_CHOICES entry).

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/providers/codex/constants.js frontend/src/providers/codex/helpers.js
git commit -m "$(cat <<'EOF'
feat(codex/frontend): expose the strict preset in the session picker

5th mode after the backend addition. Same sandbox as read_only
(read-only) but with ``approval_policy=never`` — writes are refused
silently rather than triggering an approval prompt. The functional
equivalent of Claude's "Don't ask" for sessions where you want
exploration without any UI interruption.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Extract Claude `PendingRequestBody.vue` (new file)

**Files:**
- Create: `frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue`

This task creates the file **without** yet wiring it into the shell (that's Task 5). After this task, the new body file exists but is unreferenced — confirms isolation.

The new body owns:
- Local state: `denyReason`, `showDenyReason`, `isEditing`, `editedToolInput`, `checkedSuggestions`, `editedSuggestions`, `questionSelections`, `otherTexts`, `otherActive`
- All `tool_approval` rendering (tool badge, JsonHumanView, permission suggestions, edit mode, deny reason input, action buttons)
- All `ask_user_question` rendering (questions list, option cards, "Other" input, submit button)
- All button click handlers — but each handler `emit('submit', payload)` instead of calling `respondToPendingRequest` directly

The new body does NOT own:
- The card outer wrapper (`<wa-divider>`, container div)
- The shared header (icon + title + count badge + expand toggle)
- `isResponding` (lives in the shell)
- The dispatch via `respondToPendingRequest`

- [ ] **Step 1: Create the new file with the full Claude body content**

Path: `frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue`

Template structure:
```html
<template>
    <template v-if="requestType === 'tool_approval'">
        <!-- tool name badge + JsonHumanView + permission suggestions + edit mode + deny reason + buttons -->
    </template>
    <template v-else-if="requestType === 'ask_user_question'">
        <!-- questions list + options + "Other" + submit -->
    </template>
</template>
```

Props:
```javascript
const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])
```

Move ALL the following from the current `PendingRequestForm.vue` (lines 16-235 + 313-597) into the new file:
- The `TOOL_OVERRIDES_BASE`, `BASE_SUGGESTION_OVERRIDES`, `TOOL_PATH_KEYS` constants
- The `suggestionOverrides` helper function
- The `denyReason`, `showDenyReason`, `denyReasonInputRef`, `isEditing`, `editedToolInput`, `hasEditableContent`, `checkedSuggestions`, `editedSuggestions` refs + computed
- The `questionSelections`, `otherTexts`, `otherActive`, `otherInputRefs` refs
- The `requestType`, `toolName`, `toolNameDisplay`, `toolInput`, `toolOverrides`, `permissionSuggestions`, `hasPermissionSuggestions`, `permissionSummaryLabel` computed
- `editedSuggestion`, `onSuggestionUpdate`, `togglePermissionSuggestion`, `getCheckedPermissionSuggestions`
- `questions`, `canSubmitQuestions` computed
- `buildApprovalResponse`, `handleApprove`, `handleDeny`, `cancelDeny`, `onDenyReasonKeydown`, `handleStartEdit`, `cancelEdit`, `handleApproveWithChanges`, `onToolInputUpdate`
- `selectOption`, `isOptionSelected`, `toggleOther`, `onOtherInput`, `getQuestionAnswer`, `handleSubmitQuestions`
- The `watch(() => props.pendingRequest?.request_id, ...)` reset block
- All matching template branches + the Tool approval styles + Ask User Question styles

Modify the handlers to emit instead of dispatch:

```javascript
// OLD (in handleApprove):
respondToPendingRequest(
    provider.value,
    props.sessionId,
    props.pendingRequest.request_id,
    buildApprovalResponse(toolInput.value),
)

// NEW:
emit('submit', buildApprovalResponse(toolInput.value))
```

Same shape for `handleDeny`, `handleApproveWithChanges`, `handleSubmitQuestions`. Drop all `isResponding.value = true` / `if (isResponding.value) return` — these now live in the shell and are exposed via the `isResponding` prop.

- [ ] **Step 2: Move the related CSS**

Move the following CSS rule sets from `PendingRequestForm.vue` to the new file (keep `<style scoped>`):
- `.pending-request-details`, `.tool-name-badge`
- `.permission-suggestions-details`, `.permission-suggestions-list`, `.permission-suggestion-card`, `.permission-suggestion-card.selected`, `.permission-suggestion-card-body`, `.permission-suggestion-card-footer`
- `.pending-request-actions`, `.deny-reason-row`, `.deny-reason-input`
- `.questions-container`, `.question-block`, `.question-header`, `.question-text`, `.question-select-hint`, `.question-options`, `.option-card`, `.option-card.selected`, `.option-card.disabled`, `.option-card-content`, `.option-label`, `.option-description`, `.other-section`, `.other-toggle-link`, `.other-input-row`, `.other-input`

These do NOT move (they stay in the shell):
- `wa-divider`
- `.pending-request-form` (the outer container)
- `.pending-request-header`, `.pending-request-title`, `.pending-count-badge`, `.expand-toggle-btn`, `.question-icon`

- [ ] **Step 3: Visual verification**

The shell still inlines the Claude render at this point (Task 5 wires the new body). Vite HMR should not warn about anything, since the new file is not imported by anything. Confirm via:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
ls -la frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue
```

Expected: file exists, ~400+ lines (most of the original Claude rendering).

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue
git commit -m "$(cat <<'EOF'
refactor(claude_code/frontend): extract PendingRequestBody from form

Pure copy-paste of the Claude tool_approval + ask_user_question
rendering and matching local state out of ``PendingRequestForm.vue``
into a new sub-component, in preparation for the shell/body split
that PR3 brings in line with the Codex side.

Not yet wired — the shell still inlines the same rendering. Wiring
lands in the next commit. Done as a separate commit so the diff
shows JUST the extraction (every byte in the new file matches a byte
deleted from the form in the next commit).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Refactor `PendingRequestForm.vue` as a thin shell

**Files:**
- Modify: `frontend/src/components/message/PendingRequestForm.vue`

After this task the shell contains ONLY:
- Card wrapper + outer `.pending-request-form` div + `<wa-divider>`
- Shared header (icon "shield-halved" for tool, "circle-question" for question — passed from body via a slot or a prop) + title + count badge + expand toggle
- A single `<component :is="bodyComponent" />` slot routing to Claude or Codex body
- `isResponding` state + the `respondToPendingRequest` dispatch on `@submit`
- The `watch(() => props.pendingRequest?.request_id, …)` that resets `isResponding` (the body owns its own internal reset)

- [ ] **Step 1: Trim down imports in the script**

In `PendingRequestForm.vue`:

```javascript
// Replace the current imports with:
import { ref, computed, watch, useId } from 'vue'
import { getProviderLabel, respondToPendingRequest } from '../../providers'
import { useDataStore } from '../../stores/data'
import AppTooltip from '../ui/AppTooltip.vue'
import ClaudePendingRequestBody from '../session/detail/items/claude_code/PendingRequestBody.vue'
import CodexPendingRequestBody from '../session/detail/items/codex/PendingRequestBody.vue'
```

Drop: `reactive`, `nextTick`, `JsonHumanView`, `getLanguageFromPath`.

- [ ] **Step 2: Trim down state**

In the script, keep only:
- `props` definition (unchanged)
- `extraPendingCount`, `providerLabel`, `provider` computed (unchanged)
- `isResponding` ref
- `isExpanded` ref + `toggleExpanded` function
- `toolExpandToggleId` + `questionExpandToggleId` from `useId()`
- `requestType` computed (used to switch header icon/title)

Delete every other ref / reactive / computed / function from this script — they all moved to the body files.

- [ ] **Step 3: Add `bodyComponent` and `onBodySubmit`**

```javascript
const bodyComponent = computed(() => {
    if (provider.value === 'codex') return CodexPendingRequestBody
    if (provider.value === 'claude_code') return ClaudePendingRequestBody
    return null
})

function onBodySubmit(payload) {
    if (isResponding.value) return
    isResponding.value = true
    respondToPendingRequest(
        provider.value,
        props.sessionId,
        props.pendingRequest.request_id,
        payload,
    )
}
```

- [ ] **Step 4: Trim the `watch` reset**

Keep the watch but only reset `isResponding`:

```javascript
watch(() => props.pendingRequest?.request_id, () => {
    isResponding.value = false
})
```

The body's own watch handles its local state reset.

- [ ] **Step 5: Rewrite the template**

```html
<template>
    <wa-divider></wa-divider>
    <div class="pending-request-form" :class="{ expanded: isExpanded }">
        <!-- Shared header. Title + icon vary on requestType. -->
        <div class="pending-request-header">
            <wa-icon
                :name="requestType === 'ask_user_question' ? 'circle-question' : 'shield-halved'"
                class="pending-request-icon"
                :class="{ 'question-icon': requestType === 'ask_user_question' }"
            ></wa-icon>
            <span class="pending-request-title">
                {{ requestType === 'ask_user_question'
                    ? `${providerLabel} needs your input`
                    : 'Tool approval requested' }}
            </span>
            <span
                v-if="extraPendingCount > 0"
                class="pending-count-badge"
                :id="`pending-count-${sessionId}`"
                role="status"
            >+{{ extraPendingCount }} pending</span>
            <AppTooltip
                v-if="extraPendingCount > 0"
                :for="`pending-count-${sessionId}`"
            >{{ extraPendingCount }} more request{{ extraPendingCount > 1 ? 's' : '' }} waiting after this one</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="expand-toggle-btn"
                :id="toolExpandToggleId"
                @click="toggleExpanded"
            >
                <wa-icon :name="isExpanded ? 'compress' : 'expand'" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="toolExpandToggleId">{{ isExpanded ? 'Collapse' : 'Expand' }}</AppTooltip>
        </div>

        <!-- Provider-routed body -->
        <component
            :is="bodyComponent"
            v-if="bodyComponent"
            :session-id="sessionId"
            :pending-request="pendingRequest"
            :is-responding="isResponding"
            @submit="onBodySubmit"
        />
    </div>
</template>
```

The Claude body sees `:pending-request` for the data and `:is-responding` to disable its buttons while a response is in flight. The Codex body sees the same shape. `@submit` is the single channel; the shell dispatches.

- [ ] **Step 6: Trim the CSS**

Delete from the `<style scoped>` block every rule that moved to the body files (Task 4 Step 2 listed them). Keep ONLY:
- `wa-divider`
- `.pending-request-form` (incl. `.expanded`)
- `.pending-request-header`, `.pending-request-title`, `.pending-count-badge`, `.expand-toggle-btn`
- `.question-icon`
- `.pending-request-icon` (move from per-template — it's already on the icon)

- [ ] **Step 7: Visual verification**

Open the session that triggered the Claude pending earlier and confirm:
- The shell renders (header is visible)
- The Claude body sub-component renders inside (tool name badge, JsonHumanView)
- Approve / Deny / Approve with changes all work
- The Codex body still renders (PR2b stub for now — Task 6 refondu)

If the Claude body's `emit('submit', payload)` is wrong shape, `respondToPendingRequest` in `claude_code/ws.py:_handle_pending_request_response` will fail to parse — check backend logs.

- [ ] **Step 8: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/message/PendingRequestForm.vue
git commit -m "$(cat <<'EOF'
refactor(frontend): turn PendingRequestForm into a thin shell

After this commit the form owns only:
- the card wrapper + ``wa-divider``
- the shared header (icon + title + count badge + expand toggle)
- the per-provider body routing (``<component :is="bodyComponent" />``)
- the ``isResponding`` guard + the provider-agnostic ``respondToPendingRequest``
  dispatch (triggered by the body's ``@submit`` event)

The Claude body extracted in the previous commit is now wired in.
The Codex body (still the PR2b stub) renders identically; PR3's
specialised rendering replaces it in a follow-up commit.

Closes the factorisation gap from PR2b Task 5: both providers now
share the same shell with matching visual layout, and any future
shell-level change is done once.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Codex `PendingRequestBody.vue` — full refondu

**Files:**
- Modify (replace contents): `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue`

Refondu = total rewrite. The stub from PR2b is dropped. The new body:

- Adapts its rendering to `tool_name` (`commandExecution`, `fileChange`, `permissions`)
- For each tool_name, renders a per-tool body with the rich data from `tool_input` (see Step list)
- Emits `'submit'` with the provider-shaped payload (no internal `respondToPendingRequest` call)
- Bottom action row is shared across tool_names: 3 buttons (Approve / Deny / Cancel turn) for `commandExecution` and `fileChange`; (Approve / Deny) for `permissions` (no Cancel-turn variant per spec §1.1.c)
- The Approve button is a **plain `wa-button` for now** — split-button menu lands in Task 7

This task focuses on the **rendering** and the **3-button action row** (no menus, no tooltips, no amendments yet). The split-button menus, tooltips, and amendments are Tasks 7 and 8.

- [ ] **Step 1: Setup props + emits + computed**

Replace the script block in `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` with:

```javascript
<script setup>
import { computed } from 'vue'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

// Codex tool_name: 'commandExecution' | 'fileChange' | 'permissions'.
const toolName = computed(() => props.pendingRequest.tool_name || 'unknown')

// Wire params (as injected by make_pending_request).
const toolInput = computed(() => props.pendingRequest.tool_input || {})

// commandExecution-specific fields
const command = computed(() => toolInput.value.command)
const cwd = computed(() => toolInput.value.cwd)
const reason = computed(() => toolInput.value.reason)
const commandActions = computed(() => toolInput.value.commandActions || [])
const networkApprovalContext = computed(() => toolInput.value.networkApprovalContext)
const proposedExecpolicyAmendment = computed(() => toolInput.value.proposedExecpolicyAmendment)
const proposedNetworkPolicyAmendments = computed(() => toolInput.value.proposedNetworkPolicyAmendments)

// fileChange-specific fields
const fileChanges = computed(() => {
    const payload = toolInput.value._item_payload
    if (!payload) return []
    const changes = payload.changes || []
    if (Array.isArray(changes)) return changes
    // ``changes`` may be a dict { path: change } depending on the SDK shape;
    // normalise to an array of {path, kind, …}.
    if (typeof changes === 'object') {
        return Object.entries(changes).map(([path, change]) => ({
            path,
            ...(change || {}),
        }))
    }
    return []
})

// permissions-specific fields
const requestedPermissions = computed(() => toolInput.value.permissions || {})

// Whether the "Cancel turn" button is available for this tool_name.
// Permissions don't have a "cancel" wire variant per spec §1.1.c.
const supportsCancelTurn = computed(
    () => toolName.value === 'commandExecution' || toolName.value === 'fileChange',
)

function handleApprove() {
    if (toolName.value === 'permissions') {
        emit('submit', {
            tool_name: 'permissions',
            permissions: requestedPermissions.value,
            scope: 'turn',
        })
    } else {
        emit('submit', { tool_name: toolName.value, decision: 'accept' })
    }
}

function handleDeny() {
    if (toolName.value === 'permissions') {
        emit('submit', {
            tool_name: 'permissions',
            permissions: {},
            scope: 'turn',
        })
    } else {
        emit('submit', { tool_name: toolName.value, decision: 'decline' })
    }
}

function handleCancelTurn() {
    emit('submit', { tool_name: toolName.value, decision: 'cancel' })
}
</script>
```

- [ ] **Step 2: Template — outer structure + commandExecution body**

```html
<template>
    <div class="codex-pending-body">
        <!-- commandExecution rich body -->
        <template v-if="toolName === 'commandExecution'">
            <div class="codex-pending-section">
                <div class="codex-pending-summary">
                    <span class="codex-summary-label">Command</span>
                    <code class="codex-summary-code">{{ command }}</code>
                </div>
                <div v-if="cwd" class="codex-pending-summary">
                    <span class="codex-summary-label">cwd</span>
                    <code class="codex-summary-code">{{ cwd }}</code>
                </div>
                <div v-if="reason" class="codex-pending-reason">
                    <wa-icon name="comment" variant="classic"></wa-icon>
                    <span>{{ reason }}</span>
                </div>
                <div v-if="commandActions.length" class="codex-action-chips">
                    <wa-badge
                        v-for="(action, idx) in commandActions"
                        :key="idx"
                        variant="neutral"
                    >{{ action.kind || 'action' }}{{ action.target ? `: ${action.target}` : '' }}</wa-badge>
                </div>
                <div v-if="networkApprovalContext" class="codex-pending-network">
                    <wa-icon name="globe" variant="classic"></wa-icon>
                    <span>
                        Wants network access to
                        <code>{{ networkApprovalContext.host }}</code>
                        via {{ networkApprovalContext.protocol || 'unknown' }}
                    </span>
                </div>
            </div>
        </template>
        <!-- fileChange / permissions rendered in following steps. Placeholder for now. -->
        <template v-else-if="toolName === 'fileChange'">
            <div class="codex-pending-section"><em>fileChange body — implemented in Step 3</em></div>
        </template>
        <template v-else-if="toolName === 'permissions'">
            <div class="codex-pending-section"><em>permissions body — implemented in Step 4</em></div>
        </template>
        <template v-else>
            <div class="codex-pending-section"><em>Unknown tool_name: {{ toolName }}</em></div>
        </template>

        <!-- Shared action row. Approve is a plain button for now; menus in Task 7. -->
        <div class="codex-pending-actions">
            <wa-button
                variant="danger"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="handleDeny"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Deny
            </wa-button>
            <wa-button
                v-if="supportsCancelTurn"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="handleCancelTurn"
            >
                <wa-icon slot="start" name="stop" variant="classic"></wa-icon>
                Cancel turn
            </wa-button>
            <wa-button
                variant="brand"
                size="small"
                :disabled="isResponding"
                @click="handleApprove"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Approve
            </wa-button>
        </div>
    </div>
</template>
```

- [ ] **Step 3: fileChange body**

Replace the `fileChange` template branch:

```html
<template v-else-if="toolName === 'fileChange'">
    <div class="codex-pending-section">
        <div class="codex-pending-summary">
            <span class="codex-summary-label">
                Wants to modify {{ fileChanges.length }} file{{ fileChanges.length === 1 ? '' : 's' }}
            </span>
        </div>
        <ul v-if="fileChanges.length" class="codex-file-list">
            <li v-for="(change, idx) in fileChanges" :key="idx" class="codex-file-row">
                <wa-badge
                    :variant="change.kind === 'delete' ? 'danger'
                        : change.kind === 'add' ? 'success' : 'neutral'"
                >{{ change.kind || 'update' }}</wa-badge>
                <code class="codex-file-path">{{ change.path }}</code>
            </li>
        </ul>
        <div v-if="reason" class="codex-pending-reason">
            <wa-icon name="comment" variant="classic"></wa-icon>
            <span>{{ reason }}</span>
        </div>
    </div>
</template>
```

Notes:
- The diff itself is already rendered higher in the timeline as a regular `ApplyPatch` item; the banner only summarises. See spec §7-Q10 + memo PR3 item 2.
- `change.path` formatting: in PR3 we just display the raw absolute path. A later PR can wire up `formatRelativePath` if desired.

- [ ] **Step 4: permissions body**

Replace the `permissions` template branch:

```html
<template v-else-if="toolName === 'permissions'">
    <div class="codex-pending-section">
        <div class="codex-pending-summary">
            <span class="codex-summary-label">Requests additional permissions</span>
        </div>
        <ul class="codex-permission-list">
            <li v-for="(value, key) in requestedPermissions" :key="key" class="codex-permission-row">
                <code class="codex-permission-key">{{ key }}</code>
                <span class="codex-permission-value">{{ JSON.stringify(value) }}</span>
            </li>
        </ul>
        <div v-if="reason" class="codex-pending-reason">
            <wa-icon name="comment" variant="classic"></wa-icon>
            <span>{{ reason }}</span>
        </div>
    </div>
</template>
```

`requestedPermissions` is the raw `RequestPermissionProfile` dict (see spec §1.1.c). PR3 displays it as flat key → value; future PRs can specialise per known key (network y/n, fileSystem entries…) if needed.

- [ ] **Step 5: Replace the styles**

Replace the `<style scoped>` block with:

```css
<style scoped>
.codex-pending-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.codex-pending-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
}

.codex-pending-summary {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.codex-summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.codex-summary-code {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    background: var(--wa-color-neutral-fill-quiet);
    padding: 2px var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    word-break: break-all;
}

.codex-pending-reason {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.codex-pending-network {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.codex-action-chips {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.codex-file-list,
.codex-permission-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.codex-file-row,
.codex-permission-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.codex-file-path,
.codex-permission-key {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    word-break: break-all;
}

.codex-permission-value {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.codex-pending-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}
</style>
```

- [ ] **Step 6: Visual verification — three smoke checks**

With backend running, in read-only mode:

1. **commandExecution**: ask Codex to run a shell command (e.g. "list files in /tmp"). Approval banner appears with: code-styled command, cwd, reason if Codex provided one. Buttons: Deny / Cancel turn / Approve. Click Approve → command runs.

2. **fileChange**: ask Codex to create a file. Banner: "Wants to modify 1 file" + path with `add` badge. Click Deny → spinner stops on the apply_patch card (PR2c fix), reason "User denied this action" (Task 2 alignment).

3. **permissions**: this is rare; can be tested by an MCP server requesting a new permission. If unavailable in your local setup, skip this scenario (verify the wire decoder by inspection: `tool_input.permissions` populates the list).

If any branch shows the placeholder `<em>` text from Step 2, you forgot to replace it in Steps 3 / 4.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/session/detail/items/codex/PendingRequestBody.vue
git commit -m "$(cat <<'EOF'
feat(codex/frontend): rich per-tool rendering in the pending request body

Replaces the PR2b stub (raw JsonHumanView dump) with three specialised
templates routed on ``tool_name``:

- ``commandExecution``: command + cwd + reason + commandActions chips +
  optional networkApprovalContext line
- ``fileChange``: "Wants to modify N file(s)" + paths with per-change
  kind badges (add/update/delete). The diff itself is rendered higher
  in the timeline as the regular ApplyPatch item — the banner just
  summarises.
- ``permissions``: requested permissions list (raw key → value), reason

Action row stays at 3 buttons (Approve / Deny / Cancel turn for
command + file, Approve / Deny only for permissions per spec §1.1.c).
The split-button Approve menus (Once / For session / +add allow rule)
land in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Split-button Approve menus (Codex)

**Files:**
- Modify: `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue`
- Modify if needed: `frontend/src/main.js` (add `wa-dropdown` + `wa-dropdown-item` imports)

After this task the Approve button becomes a split-button (caret on the right opens a menu of variants).

Variants per tool_name (per spec §7-Q3, §7-Q5):

- `commandExecution`:
  - **Once** → `{decision: 'accept'}`
  - **For session** → `{decision: 'acceptForSession'}`
  - **+ Add allow rule** → `{decision: {acceptWithExecpolicyAmendment: {execpolicy_amendment: amendmentArray}}}` — shown only if `proposedExecpolicyAmendment` is non-null
  - **+ Allow network access** → `{decision: {applyNetworkPolicyAmendment: {network_policy_amendment: amendmentObject}}}` — shown only if `proposedNetworkPolicyAmendments` is non-null
- `fileChange`:
  - **Once** → `{decision: 'accept'}`
  - **For session** → `{decision: 'acceptForSession'}`
- `permissions`:
  - **For this turn** → `{permissions: granted, scope: 'turn'}`
  - **For this session** → `{permissions: granted, scope: 'session'}`

- [ ] **Step 1: Verify the wa-dropdown imports in main.js**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -n 'dropdown' frontend/src/main.js
```

If the imports are missing, add them next to the other Web Awesome component imports:

```javascript
import '@awesome.me/webawesome/dist/components/dropdown/dropdown.js'
import '@awesome.me/webawesome/dist/components/dropdown-item/dropdown-item.js'
```

If they're already there, skip this step.

- [ ] **Step 2: Add the approve-variant emit helpers**

In the Codex body script, add a single dispatcher function and the variant handlers:

```javascript
function emitApprove(variant) {
    // `variant` is one of:
    // - 'once' | 'forSession'      (command / file)
    // - 'addAllowRule'              (command only, requires proposedExecpolicyAmendment)
    // - 'allowNetwork'              (command only, requires proposedNetworkPolicyAmendments)
    // - 'turn' | 'session'          (permissions)
    if (toolName.value === 'permissions') {
        emit('submit', {
            tool_name: 'permissions',
            permissions: requestedPermissions.value,
            scope: variant === 'session' ? 'session' : 'turn',
        })
        return
    }
    if (variant === 'forSession') {
        emit('submit', { tool_name: toolName.value, decision: 'acceptForSession' })
        return
    }
    if (variant === 'addAllowRule') {
        emit('submit', {
            tool_name: toolName.value,
            decision: {
                acceptWithExecpolicyAmendment: {
                    execpolicy_amendment: proposedExecpolicyAmendment.value,
                },
            },
        })
        return
    }
    if (variant === 'allowNetwork') {
        emit('submit', {
            tool_name: toolName.value,
            decision: {
                applyNetworkPolicyAmendment: {
                    network_policy_amendment: proposedNetworkPolicyAmendments.value,
                },
            },
        })
        return
    }
    // 'once' (default)
    emit('submit', { tool_name: toolName.value, decision: 'accept' })
}
```

Drop the previous `handleApprove` function — `emitApprove('once')` now covers the default Approve click.

- [ ] **Step 3: Replace the Approve button with a split-button + dropdown**

In the action row, replace the single `<wa-button variant="brand" @click="handleApprove">` with:

```html
<wa-dropdown placement="top-end">
    <wa-button
        slot="trigger"
        variant="brand"
        size="small"
        :disabled="isResponding"
        caret
    >
        <wa-icon slot="start" name="check" variant="classic"></wa-icon>
        Approve
    </wa-button>

    <!-- command / file menu -->
    <template v-if="toolName === 'commandExecution' || toolName === 'fileChange'">
        <wa-dropdown-item @click="emitApprove('once')">
            <wa-icon slot="icon" name="check" variant="classic"></wa-icon>
            Once
        </wa-dropdown-item>
        <wa-dropdown-item @click="emitApprove('forSession')">
            <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
            For this session
        </wa-dropdown-item>
        <wa-dropdown-item
            v-if="toolName === 'commandExecution' && proposedExecpolicyAmendment"
            @click="emitApprove('addAllowRule')"
        >
            <wa-icon slot="icon" name="plus" variant="classic"></wa-icon>
            + Add allow rule
        </wa-dropdown-item>
        <wa-dropdown-item
            v-if="toolName === 'commandExecution' && proposedNetworkPolicyAmendments"
            @click="emitApprove('allowNetwork')"
        >
            <wa-icon slot="icon" name="globe" variant="classic"></wa-icon>
            + Allow network access
        </wa-dropdown-item>
    </template>

    <!-- permissions menu -->
    <template v-else-if="toolName === 'permissions'">
        <wa-dropdown-item @click="emitApprove('turn')">
            <wa-icon slot="icon" name="clock" variant="classic"></wa-icon>
            For this turn
        </wa-dropdown-item>
        <wa-dropdown-item @click="emitApprove('session')">
            <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
            For this session
        </wa-dropdown-item>
    </template>
</wa-dropdown>
```

The default click on the trigger now opens the menu (it has `caret` and no `@click` handler). For users who prefer one-click Approve, the "Once" item is always at the top.

Alternatively (if you want a true split-button where the main face is clickable for "Once" and only the caret opens the menu): wrap with `<wa-button-group>` and put a separate small caret button next to it. The simpler approach above is sufficient for PR3 — split-button-group can be a follow-up if user feedback asks for it.

- [ ] **Step 4: Verify the WS backend handlers parse the new variants**

The `accept` and `decline` variants worked since PR2b. Verify `acceptForSession`, `acceptWithExecpolicyAmendment`, `applyNetworkPolicyAmendment` are accepted at `src/twicc/providers/codex/ws.py:_build_command_response`:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -n 'acceptForSession\|acceptWithExecpolicyAmendment\|applyNetworkPolicyAmendment' src/twicc/providers/codex/ws.py
```

Expected: all three appear (they were added in PR2a). If missing, the backend will warn and fall back to `default_response_for`.

- [ ] **Step 5: Visual smoke**

In a Codex session in `auto` mode:
1. Trigger commandExecution → menu shows "Once / For this session" (no amendment items unless Codex provided them — rare in practice)
2. Trigger fileChange → menu shows "Once / For this session" (no other items)
3. Click "For this session" → command runs. Triggering a similar command later does not prompt again (Codex remembers the session-level grant).

If Codex emits a `proposedExecpolicyAmendment` (depends on the command — e.g. running an unknown binary outside common allowed prefixes), the "+ Add allow rule" item appears. Trigger this if convenient; otherwise it's tested via direct payload inspection by viewing the backend log.

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/session/detail/items/codex/PendingRequestBody.vue frontend/src/main.js
git commit -m "$(cat <<'EOF'
feat(codex/frontend): split-button Approve menus per tool_name

Once / For this session for commandExecution + fileChange.
+ Add allow rule (proposedExecpolicyAmendment) and
+ Allow network access (proposedNetworkPolicyAmendments) shown
conditionally for commandExecution when Codex proposes them.
For this turn / For this session for permissions.

The menu is a ``wa-dropdown`` anchored to a caret-styled
``wa-button`` trigger; the first item is always the most common
default ("Once" / "For this turn") so a user who clicks-and-picks
gets the safest variant on top.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Tooltips Deny / Cancel turn

**Files:**
- Modify: `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue`

Per spec §7-Q3, the Deny vs Cancel turn distinction needs to be explicit. Add `AppTooltip` to the two outlined buttons.

- [ ] **Step 1: Import AppTooltip and add tooltip IDs**

In the Codex body script, add the import and the IDs:

```javascript
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { useId } from 'vue'
// ...
const denyButtonId = useId()
const cancelTurnButtonId = useId()
```

- [ ] **Step 2: Wrap the action row buttons with tooltip-bound IDs**

Update the two buttons to carry the IDs and add `AppTooltip` siblings:

```html
<wa-button
    :id="denyButtonId"
    variant="danger"
    appearance="outlined"
    size="small"
    :disabled="isResponding"
    @click="handleDeny"
>
    <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
    Deny
</wa-button>
<AppTooltip :for="denyButtonId">
    Refuse this action. Codex may try another approach.
</AppTooltip>

<wa-button
    v-if="supportsCancelTurn"
    :id="cancelTurnButtonId"
    variant="neutral"
    appearance="outlined"
    size="small"
    :disabled="isResponding"
    @click="handleCancelTurn"
>
    <wa-icon slot="start" name="stop" variant="classic"></wa-icon>
    Cancel turn
</wa-button>
<AppTooltip v-if="supportsCancelTurn" :for="cancelTurnButtonId">
    End this turn. Codex returns control to you. Different from Stop (which kills the agent).
</AppTooltip>
```

- [ ] **Step 3: Smoke**

Hover the two buttons; the tooltip text appears. The Cancel-turn tooltip mentions Stop explicitly to disambiguate (Stop is the unrelated button on the agent toolbar that kills the entire agent process).

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/session/detail/items/codex/PendingRequestBody.vue
git commit -m "$(cat <<'EOF'
feat(codex/frontend): tooltips on Deny / Cancel turn buttons

Distinguishes the two refusal flavours per spec §7-Q3:
- Deny: refuse this action; Codex may try another approach
- Cancel turn: end the turn; control returns to the user (distinct
  from Stop which kills the agent)

Tooltips bind via ``AppTooltip`` + ``useId()`` — same pattern as the
expand toggle in the shared shell header.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Retry nested template structure in the shell

**Files:**
- Modify: `frontend/src/components/message/PendingRequestForm.vue`

After Task 5 the shell uses `<component :is="bodyComponent" v-if="bodyComponent" />`. That bypasses the `<template v-if> / <template v-else-if>` chain that originally caused the Vue SFC compiler to fall back to a flat structure (PR2b commit `4831f2c6`). The dynamic component already cleanly handles unknown providers (returns `null` → block not rendered).

This task is essentially a verification: there's nothing nested left to retry, since the new shell uses `:is="bodyComponent"`. **The carryover memo item is satisfied by the Task 5 design itself.**

- [ ] **Step 1: Verify by inspection**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -n '<template v-' frontend/src/components/message/PendingRequestForm.vue
```

Expected: zero hits (the new shell template has no `<template v-if>` / `<template v-else-if>` constructs because the body routing is done via `:is`).

If a `<template v-if>` reappears (e.g. you added a tool-specific UI inside the shell instead of in a body), refactor it into the body before considering this task done. The shell stays generic.

- [ ] **Step 2: Document the design decision in the shell**

Add a leading comment to the shell template (just inside `<template>`):

```html
<template>
    <!--
        Shell-only component. Per-provider rendering lives in the
        ``bodyComponent`` resolved by ``session.provider`` (Claude vs
        Codex). The dynamic ``:is="bodyComponent"`` avoids the SFC
        compiler limitation that bit PR2b when we tried to nest
        ``<template v-if>`` branches.
    -->
    <wa-divider></wa-divider>
    ...
</template>
```

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/message/PendingRequestForm.vue
git commit -m "$(cat <<'EOF'
docs(frontend): comment the dynamic-component routing choice in the shell

Explicits why the shell uses ``<component :is="bodyComponent" />``
instead of a chain of ``<template v-if="provider === ...">`` branches
— a PR2b regression where the Vue SFC compiler couldn't handle a
nested ``<template v-else-if>`` (commit ``4831f2c6``). The dynamic
component is both cleaner and unaffected by that limitation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: E2E smoke test (user-assisted)

**Files:** none modified — verification only.

This task hands a structured checklist to the user. No commits unless a bug surfaces (in which case a follow-up task is created).

- [ ] **Step 1: Restart back (only needed because of Tasks 1 + 2)**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run ./devctl.py restart back
```

Front HMR picks up Tasks 3-9 without restart.

- [ ] **Step 2: User runs the matrix**

| Mode | Tool | Action | Expected |
|------|------|--------|----------|
| `strict` | exec_command (any) | — | Silent refusal (no banner appears); error in tool result `"User denied this action"`-style or `"refused by sandbox"` (TBD per Codex CLI behaviour) |
| `read_only` | exec_command "ls /tmp" | Approve / Once | Command runs; spinner stops |
| `read_only` | exec_command "ls /tmp" | Approve / For this session | Command runs; trigger same shape again next turn → no banner |
| `read_only` | apply_patch "create test.md" | Deny | Apply-patch card shows error `"User denied this action"`; spinner stops (PR2c fix still holds) |
| `read_only` | apply_patch "create test.md" | Cancel turn | Apply-patch card shows error `"User cancelled this turn"` (Task 2 reason string); spinner stops (PR2c fix on apply_patch single-row); USER_TURN regained |
| `auto` | exec_command (rare amendment-emitting case if available) | Approve / + Add allow rule | Command runs; subsequent similar commands in the session don't prompt |
| `auto` | (any) | Hover Deny | Tooltip "Refuse this action. Codex may try another approach." |
| `auto` | (any) | Hover Cancel turn | Tooltip "End this turn. Codex returns control to you. Different from Stop (which kills the agent)." |
| `auto` | (Claude session, sanity) | Approve / Deny | Claude flow unchanged; new shell renders Claude body identically to before |

- [ ] **Step 3: Verify the log signal chain on a representative case**

For one Deny on exec_command, follow the chain (PR2c logging is still in place):

```bash
tail -F /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log | grep -E 'Codex (approval request|decision recorded|denied-tool lookup hit|compute: marking)'
```

Expected:
- `Codex approval request: session=… method=item/commandExecution/requestApproval itemId=…`
- `Codex decision recorded: session=… itemId=… outcome=decline reason='User denied this action'` ← new wording (Task 2)
- `Codex denied-tool lookup hit: session=… itemId=… reason='User denied this action'` ← new wording
- `Codex compute: marking tool result as denied: … reason='User denied this action' …` ← new wording

- [ ] **Step 4: Wrap-up notes**

If everything passes:
- The 10-commit PR3 is complete on `feature/multi-provider`.
- The `feature/multi-provider` branch now has 23 commits since branching (PR1 / PR2a / PR2b / PR2c / PR3).
- PR4 (tests + docs + memories) is the last piece — see carryover memo, kept up to date.

If a scenario fails:
- Cancel the smoke task in TodoWrite and open a fix task.
- Capture the failing log chain + frontend devtools output before debugging.

---

## Out of scope (PR4 territory)

The following are explicitly OUT of PR3 and tracked in the carryover memo:

1. Unit tests on the pure helpers (`permission_modes`, `approvals`, `_build_*_response`).
2. Background re-compute verification (whether `create_tool_result_link_live` overwrites `ToolResultLink.error` on a re-compute without an agent).
3. Debug log inside the cancel-siblings loop.
4. Memory write-up (architecture of the bridge + side-tables + per-turn live updates).
5. CHANGELOG entry.

Everything else needed for the feature is in PR3.

---

## Acceptance criteria

- [x] All 5 modes (`read_only`, `strict`, `auto`, `autonomous`, `yolo`) appear in the Codex picker with labels + descriptions.
- [x] `strict` mode silently refuses writes (no banner).
- [x] `auto` / `read_only` modes show banners with per-`tool_name` rich rendering.
- [x] Approve is a split-button; Once / For session menu items are present for command + file; amendment items are conditional on Codex's params; turn / session items are present for permissions.
- [x] Deny and Cancel turn buttons have tooltips disambiguating their effects.
- [x] Refusal reason strings unified to "User …" subject-first style across Claude and Codex.
- [x] Same shell drives both providers (header / count badge / expand toggle / body slot / dispatch).
- [x] Claude flow unchanged from a user-facing perspective; the inline rendering moved to a sub-component but the UX is identical.
- [x] No spinner orphans (PR2c regressions guarded).
- [x] No backend-restart required between Task 3 and Task 9 — Vite HMR handles frontend reloads.
