# Codex Approvals — PR2b — Frontend stub + default-mode flip

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Codex approvals user-visible end-to-end. Flip `DEFAULT_MODE` from `"yolo"` to `"auto"` so fresh Codex sessions actually emit approvals, and add the frontend stub (provider-agnostic dispatcher + Codex `respondToPendingRequest` sender + minimal Codex body component with 3 buttons). PR3 later adds the rich rendering (split-button menus, type-specific cards, `strict` mode).

**Architecture:** Backend change is one literal flip plus comment updates — PR2a already wired the whole approval pipeline. Frontend gets a small dispatcher in `providers/index.js` that routes `respondToPendingRequest(provider, sessionId, requestId, payload)` to the right provider-specific sender. A new `respondToPendingRequest` lands in `providers/codex/ws.js` mirroring the Claude one. `PendingRequestForm.vue` switches from the hardcoded Claude import to the dispatcher (Claude behaviour identical) and gains a top-level `v-if` branch that renders a new minimal `CodexPendingRequestBody.vue` stub for Codex sessions. The Codex body shows the wire payload in human-readable form and exposes Approve / Deny / Cancel turn buttons, with payloads matching the spec wire format (`{decision: ...}` for command/file, `{permissions, scope}` for permissions).

**Tech Stack:** Python ≥ 3.13 (backend), Vue 3 Composition API + Pinia (frontend), Web Awesome 3 components. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-05-14-codex-approvals-design.md` — §3.3 (frontend status today), §4 Étape 5 (frontend factorisation), §4 Étape 6 (WS protocol), §4 Étape 7 (permission_mode mapping), §7-Q3 (button set), §7-Q5 (permissions buttons), §7-Q6 (bottom-banner UX), §7-Q13 PR2b criteria, §9 (wire formats).

**PR2b acceptance criteria** (spec §7-Q13):
- Backend: a fresh Codex session started without explicit `permission_mode` falls on `DEFAULT_MODE = "auto"` (= `workspace-write` + `on-request`).
- Frontend: shell exec / file change / permissions request shows a bottom-banner `PendingRequest` with 3 buttons (Approve / Deny / Cancel turn for command/file; Approve / Deny for permissions in this stub). Clicking resolves the future, the agent resumes.
- Claude sessions: unchanged behaviour. The dispatcher is purely additive — Claude messages still route to `claude_code:pending_request_response`.
- Cancel-mid-approval still kills cleanly (already covered by PR2a's `_cancel_all_pending_futures` ordering).

**What PR2b does NOT do** (deferred to PR3):
- Rich rendering per `tool_name` (specialised commandExecution / fileChange / permissions cards).
- Split-button Approve menu (Once / For session / + add allow rule).
- 5th `strict` mode.
- Tooltips clarifying Deny vs Cancel turn.
- Extracting Claude's `PendingRequestBody` into its own sub-component (the spec calls this "peut-être en symétrie" — we defer).

---

## File Structure

### Files modified

| File | Why |
|------|-----|
| `src/twicc/providers/codex/permission_modes.py` | Flip `DEFAULT_MODE` from `"yolo"` to `"auto"`. Trim the PR2a/PR2b transition comment. |
| `frontend/src/providers/codex/ws.js` | Add an outbound `respondToPendingRequest(sessionId, requestId, responseData)` mirroring the Claude sender. |
| `frontend/src/providers/index.js` | Add a top-level dispatcher `respondToPendingRequest(provider, sessionId, requestId, responseData)` that routes to the per-provider sender. |
| `frontend/src/components/message/PendingRequestForm.vue` | Replace the hardcoded Claude import with the dispatcher. Add a `provider` computed from the store. Wrap the existing Claude render in `v-if="provider === 'claude_code'"` and add `v-else-if="provider === 'codex'"` mounting the new Codex body. |

### Files created

| File | Why |
|------|-----|
| `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` | New minimal stub: renders `tool_name`-aware text view of `tool_input` + 3 buttons. Calls the dispatcher with `provider="codex"`. |

### Files NOT touched

- `frontend/src/components/session/detail/items/claude_code/**` — Claude's body lives inline in `PendingRequestForm.vue` today; PR3 may extract it for symmetry, not PR2b.
- Anything else in the backend — PR2a delivered the full pipeline. The single flip in `permission_modes.py` is all that's left.
- `Session.permission_mode` rows in the DB — existing sessions keep whatever value they have (mostly `NULL`). After the flip, a `NULL` value resolves to `"auto"` automatically via `resolve_codex_policy`.

---

## How to run / verify each step

This refactor has no automated tests (project policy: "no tests and no linting"). Per-step verification is by `ast.parse` for Python, Vite dev-server reload for the frontend (HMR), and a user-assisted smoke test at the end.

For Python syntax:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('PATH').read()); print('OK')"
```

For frontend: the Vite dev server auto-reloads on save. Browser DevTools console flags syntax errors immediately. We don't have a JS linter in CI per project policy.

For E2E (Task 6): user restarts backend, opens a fresh Codex session, sends a message that triggers a shell exec, clicks one of the three buttons.

---

## Task 1: Flip `DEFAULT_MODE` to `"auto"`

**Files:**
- Modify: `src/twicc/providers/codex/permission_modes.py`

Minimal backend change — one literal flip plus a comment update. After this commit, every Codex session WITHOUT an explicit `permission_mode` in the DB becomes a `workspace-write` + `on-request` session and will emit approvals.

- [ ] **Step 1.1: Change `DEFAULT_MODE` value**

Open `src/twicc/providers/codex/permission_modes.py`. Find the constant declaration (currently around line 48):

```python
# PR2a ships this as ``"yolo"`` so existing Codex sessions keep behaving like
# the current bypass. PR2b flips it to ``"auto"`` (workspace-write +
# on-request).
DEFAULT_MODE = "yolo"
```

Replace with:

```python
# ``"auto"`` is the canonical default since PR2b: ``workspace-write`` sandbox
# + ``on-request`` approval policy. Existing sessions with ``permission_mode``
# already stored in the DB keep their stored value; sessions where the field
# is NULL fall on this default. To recover the pre-PR2a unrestricted
# behaviour, pick ``"yolo"`` in the session picker.
DEFAULT_MODE = "auto"
```

- [ ] **Step 1.2: Update the module docstring**

The module docstring (lines 1-25 or so) currently says:

```
``DEFAULT_MODE`` is the value used when ``Session.permission_mode`` is unset
(``None``) or unknown. In PR2a we ship ``"yolo"`` to preserve the current
behaviour: every Codex session that doesn't have an explicit mode keeps
running with the bypass. PR2b flips this to ``"auto"`` once the frontend
banner is wired.
```

Replace with:

```
``DEFAULT_MODE`` is the value used when ``Session.permission_mode`` is unset
(``None``) or unknown. Since PR2b it is ``"auto"`` — ``workspace-write`` +
``on-request``. Users can opt into a more permissive mode (``"autonomous"``
to skip prompts, ``"yolo"`` for full unrestricted access) or a stricter one
(``"read_only"``) via the session settings picker.
```

- [ ] **Step 1.3: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/permission_modes.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 1.4: Sanity-check the behavioural flip**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.permission_modes import DEFAULT_MODE, resolve_codex_policy
assert DEFAULT_MODE == 'auto', DEFAULT_MODE
sandbox, policy = resolve_codex_policy(None)
assert sandbox.value == 'workspace-write', sandbox
# Verify all 4 modes still resolve correctly.
for mode, expected_sandbox in [
    ('read_only', 'read-only'),
    ('auto', 'workspace-write'),
    ('autonomous', 'workspace-write'),
    ('yolo', 'danger-full-access'),
]:
    s, _ = resolve_codex_policy(mode)
    assert s.value == expected_sandbox, (mode, s.value)
print('OK', DEFAULT_MODE)
"
```

Expected: `OK auto`.

- [ ] **Step 1.5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/permission_modes.py
git commit -m "$(cat <<'EOF'
feat(codex): flip DEFAULT_MODE from "yolo" to "auto"

PR2a installed the approval pipeline behind a behavioural no-op
(DEFAULT_MODE = "yolo" preserved the pre-existing bypass). This commit
flips the default to "auto" — workspace-write sandbox + on-request
approval policy. Fresh Codex sessions and existing sessions with
permission_mode=NULL will now actually emit approvals.

The frontend stub (3-button Codex body component) lands in subsequent
commits in this PR. Users who want the pre-PR2a unrestricted behaviour
can pick "yolo" in the session settings.

Module docstring trimmed to drop the PR2a/PR2b transition framing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `respondToPendingRequest` sender to `providers/codex/ws.js`

**Files:**
- Modify: `frontend/src/providers/codex/ws.js`

Mirror the Claude sender exactly. Codex's wire shape is slightly different (the message type prefix is `codex:` and we need to pass `tool_name` along), but the sender is otherwise structurally identical.

- [ ] **Step 2.1: Read the current `codex/ws.js` shape**

Re-confirm the file exists and what it exports today. The Codex WS module is the natural twin of `claude_code/ws.js`.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
cat frontend/src/providers/codex/ws.js | head -30
```

You should see imports (`sendWsMessage`, statuspage helpers, store) and an existing `codexWsHandler` export at the bottom. There is **no `respondToPendingRequest` export yet** — Task 2 adds it.

- [ ] **Step 2.2: Add the sender export**

Locate the "Outbound senders" section in `frontend/src/providers/codex/ws.js`. If the file has a sibling outbound function (e.g. `sendCheckAuth`), the new export goes right next to it. Otherwise add a new "Outbound senders" comment-block heading at the top of the exports.

Insert this export (the exact placement is alongside any existing outbound `sendCheckAuth` or near the top of the module, before the inbound `codexWsHandler` definition):

```js
/**
 * Respond to a pending Codex tool-approval request raised by the SDK
 * via the sync ↔ async bridge in CodexAgent. The ``responseData`` shape
 * must already match the Codex wire format (see backend spec §9.3/§9.5):
 *
 *   commandExecution / fileChange:
 *     { tool_name: 'commandExecution' | 'fileChange',
 *       decision: 'accept' | 'acceptForSession' | 'decline' | 'cancel'
 *                | { acceptWithExecpolicyAmendment: {...} }
 *                | { applyNetworkPolicyAmendment: {...} } }
 *
 *   permissions:
 *     { tool_name: 'permissions',
 *       permissions: {...}, scope: 'turn' | 'session',
 *       strictAutoReview?: boolean }
 *
 * The backend ``CodexWSHandler._build_codex_response`` validates this
 * strictly and falls back to a safe default on any malformed payload, so
 * the SDK is never left waiting.
 *
 * @returns {boolean} True if the message was sent.
 */
export function respondToPendingRequest(sessionId, requestId, responseData) {
    return sendWsMessage({
        type: 'codex:pending_request_response',
        session_id: sessionId,
        request_id: requestId,
        ...responseData,
    })
}
```

If `sendWsMessage` is not yet imported in this file, add the import at the top:

```js
import { sendWsMessage } from '../../composables/useWebSocket'
```

(It is almost certainly already imported — verify with the file's existing senders. The Claude module imports it from the same path.)

- [ ] **Step 2.3: Verify nothing else broke**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
node --check frontend/src/providers/codex/ws.js
```

Expected: no output (success). If `node --check` complains about ESM syntax (it may, depending on flags), an alternative is to start (or already-running) Vite dev server and watch for HMR errors in `logs/frontend.log`:

```bash
tail -20 /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/frontend.log
```

If the Vite log shows no compile error for this file, the syntax is fine.

- [ ] **Step 2.4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/providers/codex/ws.js
git commit -m "$(cat <<'EOF'
feat(codex/frontend): add respondToPendingRequest sender

Mirror of the Claude sender but emits ``codex:pending_request_response``.
Caller is responsible for shaping ``responseData`` to the Codex wire
format (validated strictly server-side by CodexWSHandler._build_codex_response).

No callers yet — the dispatcher and PendingRequestForm wiring land in
the next commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add the provider-agnostic dispatcher to `providers/index.js`

**Files:**
- Modify: `frontend/src/providers/index.js`

The dispatcher hides the per-provider mapping behind one function call. Components call `respondToPendingRequest(provider, sessionId, requestId, payload)` and the dispatcher picks the right sender.

- [ ] **Step 3.1: Add imports + dispatcher**

Open `frontend/src/providers/index.js`. The existing imports list looks like:

```js
import { claudeCodeWsHandler } from './claude_code/ws'
import { codexWsHandler } from './codex/ws'
```

Right next to those, add the two sender imports (rename-on-import to avoid name collision):

```js
import { respondToPendingRequest as respondToClaudeCodePendingRequest } from './claude_code/ws'
import { respondToPendingRequest as respondToCodexPendingRequest } from './codex/ws'
```

Then, at the end of the file (after the existing exported functions like `getProviderLabel`, `getProviderOptions`), add the new export:

```js
const PENDING_REQUEST_SENDERS = {
    [ClaudeCodeHelpers.provider]: respondToClaudeCodePendingRequest,
    [CodexHelpers.provider]: respondToCodexPendingRequest,
}

/**
 * Provider-agnostic dispatcher for resolving a pending tool-approval /
 * ask-user-question request. Routes to the matching provider's outbound
 * sender. Throws if ``provider`` is unknown — callers should always
 * provide a registered provider (typically from ``session.provider``).
 *
 * ``responseData`` is provider-specific:
 * - claude_code: { request_type: 'tool_approval' | 'ask_user_question',
 *                  decision?: 'allow' | 'deny', updated_input?, updated_permissions?,
 *                  message?, answers? }
 * - codex:       { tool_name: 'commandExecution' | 'fileChange' | 'permissions',
 *                  decision?: <string|dict>, permissions?, scope?, strictAutoReview? }
 *
 * @returns {boolean} True if the message was sent.
 */
export function respondToPendingRequest(provider, sessionId, requestId, responseData) {
    const sender = PENDING_REQUEST_SENDERS[provider]
    if (!sender) {
        throw new Error(`respondToPendingRequest: unknown provider ${provider!r}`)
    }
    return sender(sessionId, requestId, responseData)
}
```

(Note the `{provider!r}` is Python-flavour syntax — JS doesn't have it. Use JS template literal `${provider}` instead. The implementer should write the correct JS:)

```js
        throw new Error(`respondToPendingRequest: unknown provider ${provider}`)
```

- [ ] **Step 3.2: Verify the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
node --check frontend/src/providers/index.js
```

Or watch HMR (Vite will reload).

- [ ] **Step 3.3: Quick browser-side test (optional but useful)**

If the frontend dev server is running, open DevTools and run:

```js
window.__test_dispatcher__ = (await import('./providers/index.js')).respondToPendingRequest
// (won't be available in production builds; this is dev-only sanity)
```

Actually skip this — the real verification is when `PendingRequestForm.vue` uses it in Task 4.

- [ ] **Step 3.4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/providers/index.js
git commit -m "$(cat <<'EOF'
feat(frontend): add provider-agnostic respondToPendingRequest dispatcher

The dispatcher routes a pending-request response to the right
per-provider sender (claude_code/ws.js or codex/ws.js).

Callers (e.g. PendingRequestForm.vue) now do
``respondToPendingRequest(session.provider, sessionId, requestId, payload)``
instead of importing a specific provider's sender directly.

No callers yet — the swap in PendingRequestForm lands next.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Switch `PendingRequestForm.vue` to the dispatcher (Claude paths)

**Files:**
- Modify: `frontend/src/components/message/PendingRequestForm.vue`

Decouple the form from the hardcoded Claude sender. Claude render behaviour stays identical — only the way the WS message is sent changes (from direct sender to dispatcher). The dispatcher routes Claude back to the same Claude sender, so wire output is byte-identical.

This task does NOT add Codex routing yet. Task 5 does that with the new sub-component.

- [ ] **Step 4.1: Replace the import**

In `frontend/src/components/message/PendingRequestForm.vue` find line 9:

```js
import { respondToPendingRequest as respondToClaudeCodePendingRequest } from '../../providers/claude_code/ws'
```

Replace with:

```js
import { respondToPendingRequest } from '../../providers'
```

(`getProviderLabel` is already imported from `'../../providers'` at line 11 — make sure not to duplicate the import statement. The cleanest form merges the two:

```js
import { getProviderLabel, respondToPendingRequest } from '../../providers'
```

…and the existing line 11 `import { getProviderLabel } from '../../providers'` is removed.)

- [ ] **Step 4.2: Add a `provider` computed**

Just below the existing `providerLabel` computed (around line 102), add:

```js
// Wire-key provider for the current session, used to route responses
// through the provider-agnostic dispatcher.
const provider = computed(() => dataStore.getSession(props.sessionId)?.provider)
```

The `dataStore` is already in scope at line 101.

- [ ] **Step 4.3: Update every call site of the old Claude sender**

Search the file for `respondToClaudeCodePendingRequest`. The plan inspected: 4 distinct call sites at approximate lines 354, 380, 439, 587 (in `handleApprove`, `handleDeny`, `handleApproveWithChanges`, `handleSubmitQuestions`).

For each call site like:

```js
respondToClaudeCodePendingRequest(
    props.sessionId,
    props.pendingRequest.request_id,
    payload,
)
```

Replace with:

```js
respondToPendingRequest(
    provider.value,
    props.sessionId,
    props.pendingRequest.request_id,
    payload,
)
```

The order: `provider.value` first (the new positional arg the dispatcher requires), then `sessionId`, `requestId`, `payload`.

Verify after the edit that **no occurrence of `respondToClaudeCodePendingRequest` remains in the file** (other than maybe a docstring comment which is fine to leave). Grep:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -n "respondToClaudeCodePendingRequest" frontend/src/components/message/PendingRequestForm.vue
```

Expected: 0 hits in code (the function name should not be referenced anywhere anymore). If a docstring mentions it, decide whether to update for accuracy or leave.

- [ ] **Step 4.4: HMR / browser smoke check**

If dev servers are running, save the file. Vite HMR should reload `PendingRequestForm.vue` without a full page reload (per the project's "no circular imports" rule). Check the browser console for errors.

Trigger a Claude approval to confirm Claude paths still work:
- Open a Claude session.
- Issue a command requiring approval (e.g. a non-allowlisted Bash command).
- Click Approve.
- Confirm the request resolves and the session continues normally.
- Repeat for Deny.

If anything is broken, the dispatcher routing for Claude is the most likely culprit — re-check Task 3's `PENDING_REQUEST_SENDERS` registry.

(This step is optional during implementation; the Task 6 smoke test will exercise Claude paths comprehensively.)

- [ ] **Step 4.5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/message/PendingRequestForm.vue
git commit -m "$(cat <<'EOF'
refactor(frontend): route PendingRequestForm through the provider dispatcher

Replace the hardcoded ``respondToClaudeCodePendingRequest`` import with
the provider-agnostic ``respondToPendingRequest`` from providers/index.js.
Add a ``provider`` computed (read from the session's wire-key) and pass
it as the first positional arg to every call site (4 in
handleApprove / handleDeny / handleApproveWithChanges / handleSubmitQuestions).

Claude behaviour is byte-identical: the dispatcher routes
``provider === 'claude_code'`` straight back to the same Claude sender.
Codex routing is wired in the next commit (provider branching + body
component).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Create `CodexPendingRequestBody.vue` stub + wire provider branching

**Files:**
- Create: `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue`
- Modify: `frontend/src/components/message/PendingRequestForm.vue`

The minimal Codex body. Shows the wire payload in a human-readable form (we don't try to be fancy — `JsonHumanView` is reused if convenient; otherwise a simple `<dl>` is fine) and exposes 3 buttons (Approve / Deny / Cancel turn for command/file; Approve / Deny for permissions). Clicking calls the dispatcher with the right Codex wire shape.

PR3 will replace this stub with rich per-`tool_name` rendering and split-button menus.

- [ ] **Step 5.1: Create the new directory**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
mkdir -p frontend/src/components/session/detail/items/codex
```

- [ ] **Step 5.2: Write the Codex body component**

Create `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` with the following content. The pattern mirrors the existing Claude render (header + body + buttons) but minimal:

```vue
<script setup>
// PendingRequestBody.vue (Codex) — minimal stub.
//
// Renders the wire payload from the backend Codex approval bridge plus
// 3 action buttons (Approve / Deny / Cancel turn). PR2b is intentionally
// rough on the rendering side — PR3 will specialise the layout per
// ``tool_name`` (commandExecution / fileChange / permissions) and add the
// split-button Approve menu (Once / For session / + add allow rule).

import { ref, computed } from 'vue'
import { respondToPendingRequest } from '../../../../../providers'
import JsonHumanView from '../../../../json/JsonHumanView.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    pendingRequest: { type: Object, required: true },
})

// Whether a response has been sent and we're waiting for the store to clear it
const isResponding = ref(false)

// Codex tool_name: 'commandExecution' | 'fileChange' | 'permissions'.
// Unknown tool_names fall through with a generic JSON view.
const toolName = computed(() => props.pendingRequest.tool_name || 'unknown')

// The wire params (as injected by the backend's make_pending_request).
const toolInput = computed(() => props.pendingRequest.tool_input || {})

// Whether the request type supports a "Cancel turn" decision. Permissions
// have no ``cancel`` wire variant per spec §1.1.c, so we hide the third
// button for them.
const supportsCancelTurn = computed(
    () => toolName.value === 'commandExecution' || toolName.value === 'fileChange',
)

/**
 * Send a decision through the dispatcher. For commandExecution / fileChange
 * the payload is ``{tool_name, decision: <string>}``. For permissions the
 * Approve payload is ``{tool_name, permissions: <granted>, scope: 'turn'}``,
 * Deny is ``{tool_name, permissions: {}, scope: 'turn'}``.
 *
 * @param {'accept' | 'decline' | 'cancel'} action - The user's choice.
 */
function send(action) {
    if (isResponding.value) return
    isResponding.value = true

    const payload = { tool_name: toolName.value }
    if (toolName.value === 'permissions') {
        // Approve = grant exactly what was requested. Deny / cancel = empty.
        const granted = action === 'accept' ? (toolInput.value.permissions || {}) : {}
        payload.permissions = granted
        payload.scope = 'turn'
    } else {
        // commandExecution / fileChange / unknown — use the wire string.
        payload.decision = action
    }

    respondToPendingRequest('codex', props.sessionId, props.pendingRequest.request_id, payload)
}

function handleApprove() { send('accept') }
function handleDeny() { send('decline') }
function handleCancelTurn() { send('cancel') }
</script>

<template>
    <div class="codex-pending-body">
        <div class="codex-pending-header">
            <span class="codex-pending-tool-badge">{{ toolName }}</span>
        </div>

        <div class="codex-pending-payload">
            <JsonHumanView :value="toolInput" />
        </div>

        <div class="codex-pending-actions">
            <wa-button variant="danger" :disabled="isResponding" @click="handleDeny">
                <wa-icon slot="start" name="xmark"></wa-icon>
                Deny
            </wa-button>
            <wa-button
                v-if="supportsCancelTurn"
                variant="neutral"
                :disabled="isResponding"
                @click="handleCancelTurn"
            >
                <wa-icon slot="start" name="stop"></wa-icon>
                Cancel turn
            </wa-button>
            <wa-button variant="success" :disabled="isResponding" @click="handleApprove">
                <wa-icon slot="start" name="check"></wa-icon>
                Approve
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.codex-pending-body {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.codex-pending-header {
    display: flex;
    align-items: center;
}

.codex-pending-tool-badge {
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.25rem 0.5rem;
    background: var(--wa-color-neutral-90);
    border-radius: 4px;
    color: var(--wa-color-neutral-30);
}

.codex-pending-payload {
    max-height: 400px;
    overflow: auto;
    border: 1px solid var(--wa-color-neutral-90);
    border-radius: 6px;
    padding: 0.5rem;
    background: var(--wa-color-neutral-95);
}

.codex-pending-actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
    flex-wrap: wrap;
}
</style>
```

The paths are relative — `../../../../..` walks from `components/session/detail/items/codex/` up to `frontend/src/`. Verify the depth count is correct.

Verify the import paths in particular:
- `../../../../../providers` should land on `frontend/src/providers/index.js`. Count the segments: `codex/PendingRequestBody.vue` → `..` items/ → `..` detail/ → `..` session/ → `..` components/ → `..` src/ → `providers/index.js`. That's 5 `..`s — confirm.
- `../../../../json/JsonHumanView.vue` should land on `frontend/src/components/json/JsonHumanView.vue`. Count: 4 `..`s (codex → items → detail → session → components/) then into `json/`. Confirm against the existing claude_code-sibling imports if needed.

(The implementer should re-count by hand against the directory structure — see `frontend/src/components/session/detail/items/claude_code/Message.vue` for an analogous import depth.)

- [ ] **Step 5.3: Import the new component into `PendingRequestForm.vue`**

Open `frontend/src/components/message/PendingRequestForm.vue` and add (right after the existing component imports around line 12-14):

```js
import CodexPendingRequestBody from '../session/detail/items/codex/PendingRequestBody.vue'
```

(Adjust the relative path by counting from `message/` — `..` goes up to `components/`, then `session/detail/items/codex/PendingRequestBody.vue`. So `../session/detail/items/codex/PendingRequestBody.vue`.)

- [ ] **Step 5.4: Add provider branching at the template top**

The template currently looks like:

```vue
<template>
    <div ...>
        <template v-if="requestType === 'tool_approval'">
            <!-- Claude tool approval rendering ... -->
        </template>
        <template v-else-if="requestType === 'ask_user_question'">
            <!-- Claude ask user question rendering ... -->
        </template>
    </div>
</template>
```

The branching is on `requestType`. For Codex, the request_type is always `'tool_approval'` (Codex doesn't use `ask_user_question`), but we want to dispatch on **provider** to keep Claude's branches intact and isolate Codex's rendering.

Rewrite the template's top-level structure to dispatch on `provider` FIRST, then on `requestType` only within the Claude branch:

```vue
<template>
    <div ...>
        <template v-if="provider === 'codex'">
            <!-- Stub Codex body. PR3 specialises by tool_name. -->
            <CodexPendingRequestBody
                :session-id="sessionId"
                :pending-request="pendingRequest"
            />
        </template>

        <!-- Claude render — UNCHANGED -->
        <template v-else-if="provider === 'claude_code'">
            <template v-if="requestType === 'tool_approval'">
                <!-- existing Claude tool approval template — unchanged -->
            </template>
            <template v-else-if="requestType === 'ask_user_question'">
                <!-- existing Claude ask user question template — unchanged -->
            </template>
        </template>
    </div>
</template>
```

The two existing inner `<template v-if/v-else-if>` blocks for `requestType` move INSIDE the new `v-else-if="provider === 'claude_code'"` wrapper. No content within them changes.

If the existing top-level template structure is different from the shape sketched above (e.g. there's a wrapping `<div class="pending-request-form">` with header/footer), preserve everything outside the request-type branches and only re-nest the branches themselves. Read the actual template (lines ~600-1000 or so) before editing to confirm structure.

- [ ] **Step 5.5: HMR / browser sanity check**

Save the files. Vite reloads. Open the browser:
- Claude session: still works as before. Approval banner appears, buttons resolve correctly.
- Codex session with `permission_mode = "auto"` (now the default): trigger a shell exec → banner appears with the new Codex stub. Click Approve. Session continues.
- Codex session: trigger another approval → click Deny. Session continues.
- Codex session: trigger another approval → click Cancel turn (for command/file). Codex aborts the turn cleanly.

If the buttons don't fire the action, check the browser console for an error from the dispatcher. The most common mistake is `provider.value` not matching the keys in `PENDING_REQUEST_SENDERS`.

This is preview-only, the formal smoke test is Task 6.

- [ ] **Step 5.6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add frontend/src/components/session/detail/items/codex/PendingRequestBody.vue frontend/src/components/message/PendingRequestForm.vue
git commit -m "$(cat <<'EOF'
feat(codex/frontend): minimal pending-request body + provider branching

Create CodexPendingRequestBody.vue — a stub that renders the wire payload
via JsonHumanView and offers 3 buttons (Approve / Deny / Cancel turn for
command/file; Approve / Deny for permissions per spec §1.1.c which has
no cancel wire variant).

PendingRequestForm.vue dispatches by provider at the template top:
- provider === 'codex' → CodexPendingRequestBody
- provider === 'claude_code' → existing Claude render (unchanged)

PR3 will replace this stub with specialised cards per tool_name and the
split-button Approve menu.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: End-to-end smoke test + wrap-up

User-assisted verification. The goal is to confirm:
1. Fresh Codex sessions actually emit approvals now (default flipped).
2. The 3 buttons resolve the approval correctly through the dispatcher.
3. Claude sessions are unaffected.
4. Kill-mid-approval still unwinds cleanly.

- [ ] **Step 6.1: Restart backend (user)**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run ./devctl.py restart back
```

(User-only operation per CLAUDE.md.)

After restart, confirm `uv run ./devctl.py status` shows backend running. Frontend is HMR-reloaded automatically by Vite.

- [ ] **Step 6.2: Test fresh Codex session — Approve path**

Open a NEW Codex session at http://localhost:5174. Send a prompt that triggers a shell exec, e.g. "list the files in the current directory".

Expected:
- The CodexPendingRequestBody banner appears with the tool badge `commandExecution`.
- The payload (command + cwd) is visible in the JsonHumanView.
- 3 buttons are visible: Deny / Cancel turn / Approve.

Click **Approve**. Expected: the banner disappears, Codex continues the turn, the shell output appears.

If the banner DOES NOT appear: confirm the session's `permission_mode` is NOT explicitly set in the DB (Task 1 only handles the `NULL`/default case), and that the backend has been restarted to pick up `DEFAULT_MODE = "auto"`.

```bash
tail -50 /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log
```

- [ ] **Step 6.3: Test Deny path**

Send another prompt that triggers a shell exec. When the banner appears, click **Deny**. Expected: the banner disappears, Codex acknowledges the rejection in the turn (e.g. tries another approach or stops).

No errors in `logs/backend.log`.

- [ ] **Step 6.4: Test Cancel turn path**

Trigger another approval, click **Cancel turn**. Expected: Codex aborts the turn entirely (no further work for this user message), control returns to the user.

No `asyncio Task was destroyed` warnings in the log.

- [ ] **Step 6.5: Test fileChange approval**

Ask Codex to modify or create a file ("create test.md at the project root"). Expected:
- Banner appears with `fileChange` tool badge.
- Payload shows the diff in `_item_payload.changes[0].diff`.
- Click Approve → file gets created.

- [ ] **Step 6.6: Test Claude unchanged**

Open a Claude session. Trigger a non-allowlisted Bash. The Claude banner appears (unchanged from PR1). Click Approve / Deny. Both still work.

- [ ] **Step 6.7: Test kill mid-approval**

Trigger a Codex approval. Don't click any button — instead click Stop (kill the agent). Expected:
- Agent dies cleanly.
- No `Task was destroyed but it is pending`.
- No `coroutine was never awaited`.
- The Codex log line "Codex approval bridge failed" should NOT appear (PR2a's CancelledError fix ensures the cancel path is the expected branch).

```bash
tail -30 /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log
```

- [ ] **Step 6.8: Report verification result**

Report to the user:

- Backend boot: ✅ / ❌
- Codex Approve (6.2): ✅ / ❌
- Codex Deny (6.3): ✅ / ❌
- Codex Cancel turn (6.4): ✅ / ❌
- Codex fileChange Approve (6.5): ✅ / ❌
- Claude unchanged (6.6): ✅ / ❌
- Kill mid-approval (6.7): ✅ / ❌

If anything failed, surface to the user with the failing step's log excerpt — do NOT silently retry.

- [ ] **Step 6.9: Verify git log**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git log --oneline aa04c03e..HEAD
```

Expected (newest first):

```
<sha> feat(codex/frontend): minimal pending-request body + provider branching
<sha> refactor(frontend): route PendingRequestForm through the provider dispatcher
<sha> feat(frontend): add provider-agnostic respondToPendingRequest dispatcher
<sha> feat(codex/frontend): add respondToPendingRequest sender
<sha> feat(codex): flip DEFAULT_MODE from "yolo" to "auto"
```

Five commits, one per Task 1-5.

- [ ] **Step 6.10: Decide on the next step with the user**

PR2b is done. The Codex approval UX is now end-to-end functional with a minimal 3-button stub. The user can either:

- A — Write the PR3 plan (rich rendering per tool_name + split-button menus + `strict` mode + frontend `STRICT` constant).
- B — Pause here. The frontend stub is functional even if not pretty; PR3 can come later.
- C — Pause and switch to something else.

---

## Open considerations (not blocking PR2b)

- **Claude `PendingRequestBody` not extracted.** The spec § Étape 5.2 calls this "peut-être en symétrie". We defer to PR3 where the rich Codex rendering would justify a clearer per-provider component layout.
- **Frontend permission picker UI doesn't yet expose the 4 modes to the user clearly.** PR3 (or a separate small PR) may want to add labels + help text to the existing `AGENT_SETTINGS_CHOICES` entries. PR2b just flips the default; the picker already exists per `frontend/src/providers/codex/constants.js`.
- **No tests in PR2b.** Project policy. PR4 covers backend pure-function tests.
- **CSS theming.** The stub component uses Web Awesome neutral colours and matches the project's existing styling vocabulary. PR3 will probably refine the visual hierarchy per `tool_name`.
- **Cancel turn for permissions.** The stub hides the Cancel turn button for `permissions` since the wire shape has no cancel variant. PR3 may want to surface a different UI affordance ("ignore for this turn" = empty grant with `scope: turn`), but PR2b keeps it simple.
- **The `respondToPendingRequest` dispatcher throws on unknown provider.** That's intentional — any code reaching the dispatcher already has a session with a known provider. If a third provider is added later, the dispatcher needs a new entry — that's a 1-line registry update.
