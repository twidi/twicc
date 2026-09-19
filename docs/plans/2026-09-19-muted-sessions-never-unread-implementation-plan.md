# Muted Sessions Never Read As Unread — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A session with `mute_on_user_turn = true` never appears as unread anywhere in the UI — no per-row eye, no project badge, no workspace/global count, no favicon dot, no sidebar promotion.

**Architecture:** A **display gate, not a storage change**. `Session.last_new_content_at` and `Session.last_viewed_at` keep being written exactly as today by the compute path and by the manual read/unread marking. The mute flag is applied where "unread" is *computed for display*: one frontend predicate module plus the one backend query that mirrors it in SQL. Nothing in the database changes, so unmuting restores the accumulated unread state with zero extra code.

**Tech Stack:** Vue 3 (Composition API), Pinia, node:test (frontend unit tests), Django 6 ASGI + Django ORM, pytest + pytest-django.

**Spec:** This document is self-contained; the decisions it implements are recorded below. Background on the flag itself: `docs/plans/2026-08-12-mute-on-user-turn-design.md` and `docs/plans/2026-08-31-mute-on-user-turn-implementation-plan.md` (historical documents — read only, never edit them).

## Context and decisions

The user validated these before the plan was written:

1. **Display gate only.** No storage change, no migration, no "catch-up" code. Unmuting makes the accumulated unread state reappear, by construction. All aggregation levels (session row, project badge, workspace/global count, favicon) must exclude muted sessions.
2. **Hide the read/unread toggle** (row menu, palette commands, batch selection actions) on a muted session, exactly like the existing rule for archived sessions.
3. **No new flag.** `mute_on_user_turn` keeps its name and gains this fourth effect.

**Scope of the wording updates (decision 3, narrowed by the user):** this is a **user-interface** change. Only two kinds of text are updated: what the **user** reads in the app, and the field's own definition in the codebase. The agent-facing surface — the plugin skills, the CLI help, `SKILLS-AND-CLI.md`, the MCP descriptions generated from them — is **out of scope**, and so is the plugin version bump that touching a skill would require. No shipped tip mentions the mute flag (checked: `frontend/public/tips/*.md`), so no tip changes either.

## Global Constraints

- **No database migration.** If a step seems to need one, the step is wrong — stop and re-read the architecture line.
- **No change to any write path** for `last_new_content_at` / `last_viewed_at`: `src/twicc/providers/compute_base.py`, `src/twicc/asgi.py` `_handle_mark_session_read_state`, and `notifySessionViewed` in `frontend/src/composables/useWebSocket.js` are out of scope.
- **Every line number here is indicative, not authoritative.** The user edits this tree while the plan waits. Before each edit, find the block by the quoted first and last lines given with it and re-derive the range. If a quoted fragment is not found verbatim, stop and report — do not improvise a nearby edit.
- **Commit only if the user explicitly asks.** Each task ends with a ready-to-run commit command; do not run it on your own initiative. Stop after the task's verification step and report.
- **Before writing to a file, check it is clean** (`git status --short <path>`). A file carrying uncommitted user work must be edited region by region, never overwritten wholesale, and staged with `git add -p`.
- All code, comments and UI strings in **English**.
- Python lint: `uvx ruff check .` (line-length 120). Ruff is **not** an installed dependency — always `uvx`, never `uv run ruff`.
- Backend tests: `uv run pytest` from the repo root. Frontend tests: `cd frontend && npm test`.
- Commit subjects are Conventional Commits (`type(scope): summary`), with a body, and a `Co-Authored-By: Claude MODEL <noreply@anthropic.com>` trailer — `MODEL` bare, no angle brackets, taken from the model actually running.
- **Do not add a CHANGELOG entry** unless the user explicitly asks.
- **Do not restart the dev servers.** At the end, tell the user a restart is needed for the backend change (Task 4).

## Non-goals (deliberate, do not "fix" these)

- **The agent-facing surface keeps its current wording.** Plugin skills, CLI help strings, `SKILLS-AND-CLI.md`, MCP tool descriptions and `plugin.json` are untouched, per the scope decision above.
- **`SessionToastContent.vue` keeps its local `isUnread`.** Its only consumer is `dismissOnRead`, opted into by the *user_turn* toast alone — a toast the mute flag already prevents from ever being created.
- **A muted session that is running still gets promoted** by the sidebar's "show active across filters" block through its *process* leg (Task 3 removes only the *unread* leg). Mute is not `hidden`: a live agent still deserves to be visible.
- **The pending-request toast stays unmuted.** Questions and approvals notify on a muted session; that is the existing design.

## File Structure

| File | Responsibility after this plan |
|---|---|
| `frontend/src/utils/sessions.js` | **The single home of the three unread rules**: `hasUnreadContent` (raw data-level unread, mute-aware), `isSessionUnread` (display predicate = raw + visibility/process refinements), `canToggleSessionReadState` (whether the read/unread actions apply). Imports nothing, so it stays unit-testable under plain node. |
| `frontend/src/utils/sessions.test.js` | **New.** Unit tests for the three helpers. The predicate has no test today; this is where the mute rule is pinned. |
| `frontend/src/utils/sidebarSessions.js` | Consumes `hasUnreadContent` instead of re-implementing the comparison inline (code + the module header that describes that block). |
| `frontend/src/components/session/list/SessionListItem.vue` · `frontend/src/views/SessionView.vue` · `frontend/src/components/session/list/SessionSelectionBar.vue` | Consume `canToggleSessionReadState` instead of three near-identical local copies. `SessionView.vue` also carries the command palette's mute label. |
| `src/twicc/views.py` | The SQL mirror of `hasUnreadContent` in `_get_sessions_page`'s sticky `unread_only` branch, plus the two docstrings that state that endpoint's contract. |
| `tests/test_sessions_sticky_unread.py` | **New.** Pins the backend sticky behaviour. |
| `frontend/src/composables/useSessionMute.js` · `frontend/src/components/session/detail/SessionHeader.vue` | Drop the "muting has no effect right now" warning: with this change, muting always has an observable effect. |
| `frontend/src/utils/userTurnChannels.js` + `.test.js` | **Deleted.** Their only consumer was that warning. |
| `src/twicc/core/models.py` · `CLAUDE.md` · `AGENTS.md` | The field's own definition and the project doc's one-line description of it. |

---

### Task 1: The three unread rules, in one module

**Files:**
- Modify: `frontend/src/utils/sessions.js:19-46`
- Create: `frontend/src/utils/sessions.test.js`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces, all named exports of `frontend/src/utils/sessions.js`:
  - `hasUnreadContent(session) -> boolean`
  - `isSessionUnread(session, processState) -> boolean` (unchanged signature)
  - `canToggleSessionReadState(session, processState) -> boolean`
  - `getSessionCutoffMs(session) -> number` (pre-existing, untouched)

A `session` is a plain record from the Pinia data store. The fields read here: `hidden`, `draft`, `ephemeral`, `archived`, `parent_session_id`, `last_new_content_at`, `last_viewed_at`, `mute_on_user_turn`. Timestamps are ISO-8601 strings compared lexicographically (this is how the existing code compares them — keep it). A `processState` is `{ state: 'user_turn' | 'assistant_turn' | ... }` or `null` / `undefined`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/utils/sessions.test.js`:

```javascript
// frontend/src/utils/sessions.test.js
//
// Pins the three unread rules. The mute cases are the point: a muted session
// must never read as unread on any surface, and its read/unread actions must
// disappear.

import test from 'node:test'
import assert from 'node:assert/strict'

import { hasUnreadContent, isSessionUnread, canToggleSessionReadState } from './sessions.js'

function makeSession(overrides = {}) {
    return {
        id: 'session_1',
        last_new_content_at: '2026-09-19T10:00:00Z',
        last_viewed_at: '2026-09-19T09:00:00Z',
        ...overrides,
    }
}

test('hasUnreadContent is true when content arrived after the last view', () => {
    assert.equal(hasUnreadContent(makeSession()), true)
})

test('hasUnreadContent is true when the session was never viewed', () => {
    assert.equal(hasUnreadContent(makeSession({ last_viewed_at: null })), true)
})

test('hasUnreadContent is false without content', () => {
    assert.equal(hasUnreadContent(makeSession({ last_new_content_at: null })), false)
})

test('hasUnreadContent is false when the last view is newer', () => {
    assert.equal(hasUnreadContent(makeSession({ last_viewed_at: '2026-09-19T11:00:00Z' })), false)
})

test('hasUnreadContent is false for a muted session', () => {
    assert.equal(hasUnreadContent(makeSession({ mute_on_user_turn: true })), false)
})

test('hasUnreadContent is false for a missing session', () => {
    assert.equal(hasUnreadContent(null), false)
})

test('isSessionUnread is true for an idle session with new content', () => {
    assert.equal(isSessionUnread(makeSession(), null), true)
})

test('isSessionUnread is false for a muted session, whatever the process state', () => {
    const muted = makeSession({ mute_on_user_turn: true })
    assert.equal(isSessionUnread(muted, null), false)
    assert.equal(isSessionUnread(muted, { state: 'user_turn' }), false)
})

test('isSessionUnread keeps its pre-existing exclusions', () => {
    assert.equal(isSessionUnread(makeSession({ hidden: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ archived: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ draft: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ ephemeral: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ parent_session_id: 'p' }), null), false)
    assert.equal(isSessionUnread(makeSession(), { state: 'assistant_turn' }), false)
    assert.equal(isSessionUnread(makeSession(), { state: 'user_turn' }), true)
})

test('canToggleSessionReadState allows an idle plain session', () => {
    assert.equal(canToggleSessionReadState(makeSession(), null), true)
    assert.equal(canToggleSessionReadState(makeSession(), { state: 'user_turn' }), true)
})

test('canToggleSessionReadState is false for a muted session', () => {
    assert.equal(canToggleSessionReadState(makeSession({ mute_on_user_turn: true }), null), false)
})

test('canToggleSessionReadState keeps its pre-existing exclusions', () => {
    assert.equal(canToggleSessionReadState(makeSession({ draft: true }), null), false)
    assert.equal(canToggleSessionReadState(makeSession({ ephemeral: true }), null), false)
    assert.equal(canToggleSessionReadState(makeSession({ archived: true }), null), false)
    assert.equal(canToggleSessionReadState(makeSession(), { state: 'assistant_turn' }), false)
    assert.equal(canToggleSessionReadState(null, null), false)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /home/twidi/dev/twicc-poc/frontend && npm test
```

Expected: FAIL — `hasUnreadContent` and `canToggleSessionReadState` are not exported yet (`SyntaxError: ... does not provide an export named 'canToggleSessionReadState'`).

- [ ] **Step 3: Write the implementation**

In `frontend/src/utils/sessions.js`, replace the whole `isSessionUnread` block — its JSDoc comment (opens with `/**` on line 19) plus the function, down to and including its closing `}` on **line 46**, which is the file's last line. Replacing only up to 45 would leave an orphan `}` and a `SyntaxError`. New content:

```javascript
/**
 * Raw "does this session carry content the user has not seen" check — the
 * data-level rule, without any visibility or process refinement.
 *
 * This is the frontend twin of the SQL sticky filter in
 * `_get_sessions_page` (`src/twicc/views.py`): both must stay in step, so
 * both carry the same three clauses (content exists, it is newer than the
 * last view, the session is not muted).
 *
 * Muting a session suppresses its unread state as well as its
 * finished-working notifications: the point of muting is that nothing about
 * the session surfaces on its own. Nothing is erased — `last_new_content_at`
 * and `last_viewed_at` keep being written — so unmuting brings the
 * accumulated unread state straight back.
 *
 * @param {Object} session - A session record from the data store.
 * @returns {boolean}
 */
export function hasUnreadContent(session) {
    if (!session) return false
    if (session.mute_on_user_turn) return false
    if (!session.last_new_content_at) return false
    if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) return false
    return true
}

/**
 * Canonical "is this session unread" predicate — the single source of truth
 * shared by every surface that counts unread sessions (project/workspace
 * badges via AggregatedProcessIndicator, the command palette, the favicon and
 * the data-store unread getters). Keeping one definition prevents the surfaces
 * from drifting apart (they previously disagreed on `hidden` and on whether a
 * running session could still read as unread).
 *
 * A session is unread when `hasUnreadContent` holds. Drafts, archived,
 * subagent (`parent_session_id`), hidden and muted sessions never count. When
 * a process is running for the session it only counts while waiting for the
 * user (`user_turn`): during the agent's own work the activity indicator takes
 * over, so an "unread" eye would mislead.
 *
 * @param {Object} session - A session record from the data store.
 * @param {Object|null|undefined} processState - The session's live process
 *   state (data store `processStates[session.id]`), or null/undefined if none.
 * @returns {boolean}
 */
export function isSessionUnread(session, processState) {
    if (!session) return false
    if (session.hidden) return false
    if (session.draft || session.ephemeral || session.archived || session.parent_session_id) return false
    if (!hasUnreadContent(session)) return false
    if (processState && processState.state !== 'user_turn') return false
    return true
}

/**
 * Whether the "mark as read / mark as unread" actions apply to a session.
 *
 * One definition for the three surfaces that offer them: the row's Session
 * Actions menu, the command palette on the open session, and the multi-select
 * bar. They used to carry three copies of this rule and could drift.
 *
 * Dropped for drafts, ephemerals, archived sessions and sessions whose process
 * is running outside `user_turn` — and for muted sessions, which never read as
 * unread, so both actions would be invisible no-ops.
 *
 * @param {Object} session - A session record from the data store.
 * @param {Object|null|undefined} processState - The session's live process state.
 * @returns {boolean}
 */
export function canToggleSessionReadState(session, processState) {
    if (!session) return false
    if (session.draft || session.ephemeral || session.archived) return false
    if (session.mute_on_user_turn) return false
    if (processState && processState.state !== 'user_turn') return false
    return true
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /home/twidi/dev/twicc-poc/frontend && npm test
```

Expected: PASS, and no other frontend test regresses.

- [ ] **Step 5: Commit (only if the user asked for commits)**

```bash
cd /home/twidi/dev/twicc-poc
git add frontend/src/utils/sessions.js frontend/src/utils/sessions.test.js
git commit -m "feat(sessions): a muted session never reads as unread

Add hasUnreadContent (the raw data-level rule) and canToggleSessionReadState
(the read/unread action gate) next to isSessionUnread, all three mute-aware.
Muting a session now suppresses its unread state, not only its finished-working
notifications: nothing about a muted session surfaces on its own.

Nothing is erased — last_new_content_at and last_viewed_at keep being written,
so unmuting restores the accumulated unread state. The predicate had no unit
test; it has one now."
```

(Append the `Co-Authored-By:` trailer required by `CLAUDE.md`, with the model actually running.)

---

### Task 2: Wire the three read/unread action surfaces

**Files:**
- Modify: `frontend/src/components/session/list/SessionListItem.vue:19` (import) and `:198-209`
- Modify: `frontend/src/views/SessionView.vue:59` (import anchor) and `:1719-1733`
- Modify: `frontend/src/components/session/list/SessionSelectionBar.vue:22` (import) and `:76-89`

**Interfaces:**
- Consumes: `canToggleSessionReadState` and `hasUnreadContent` from Task 1.
- Produces: nothing new.

Each file currently carries its own copy of the same rule. Replace all three with the shared helper. Nothing else in these files changes: the unread eye and the badges already go through `isSessionUnread`, which Task 1 made mute-aware.

- [ ] **Step 1: Update `SessionListItem.vue`**

Extend the existing import (it already imports `isSessionUnread` on line 19):

```javascript
import { canToggleSessionReadState, isSessionUnread } from '../../../utils/sessions'
```

Replace the `canToggleReadState` block — its JSDoc comment (opens with `/**` on **line 198**) plus the computed, down to its closing `})` on line 209 — with:

```javascript
/**
 * Whether the mark as read/unread menu items should be shown.
 * Delegates to the shared gate so this menu, the palette and the multi-select
 * bar stay in step. Active session is allowed (mark-unread will deselect it).
 */
const canToggleReadState = computed(() =>
    canToggleSessionReadState(props.session, processState.value)
)
```

- [ ] **Step 2: Update `SessionView.vue`**

`SessionView.vue` has no import from `utils/sessions` today. Add exactly this line, once, immediately after the existing `import { toggleSessionMute } from '../composables/useSessionMute'` (currently line 59):

```javascript
import { canToggleSessionReadState, hasUnreadContent } from '../utils/sessions'
```

Replace the `currentSessionReadState` block (its comment plus the function, currently lines 1719-1733 — the comment starts with `// Read/unread gate for the current session,` and the function ends with `}` after `return { unread }`) with:

```javascript
// Read/unread gate for the current session, mirroring SessionListItem — minus
// the "is this the active row" guard, since here the session IS the one on
// screen. Returns `{ unread }` (the raw unread flag), or null when toggling
// read state isn't allowed (draft, archived, muted, or a process running
// outside user_turn).
function currentSessionReadState() {
    const s = store.getSession(sessionId.value)
    const ps = store.getProcessState(sessionId.value)
    if (!canToggleSessionReadState(s, ps)) return null
    return { unread: hasUnreadContent(s) }
}
```

The gate already excluded muted sessions, so the mute clause inside `hasUnreadContent` can never be the reason it returns `false` here. The two palette commands (`session.mark-read` / `session.mark-unread`) therefore keep behaving exactly as before on unmuted sessions, and both vanish on muted ones.

`PROCESS_STATE` is still used elsewhere in this file (currently lines 1846 and 1858) — leave its import alone. Confirm with `grep -n "PROCESS_STATE" frontend/src/views/SessionView.vue`.

- [ ] **Step 3: Update `SessionSelectionBar.vue`**

Extend the existing import (line 22):

```javascript
import { canToggleSessionReadState, isSessionUnread } from '../../../utils/sessions'
```

Replace **lines 76-89 as one contiguous block** — the `canToggleRead` doc comment (`/** Mirrors SessionListItem's canToggleReadState …`), the function, the blank line 83, and both computeds. The function goes away and its single call site (line 88, inside `markUnreadCandidates`) moves to the shared gate; `markReadCandidates` is restated unchanged, so a partial edit would duplicate it:

```javascript
const markReadCandidates = computed(() =>
    selectedSessions.value.filter(s => isSessionUnread(s, store.getProcessState(s.id)))
)
const markUnreadCandidates = computed(() =>
    selectedSessions.value.filter(s =>
        canToggleSessionReadState(s, store.getProcessState(s.id))
        && !isSessionUnread(s, store.getProcessState(s.id)))
)
```

`PROCESS_STATE` becomes unused in this file (its only use was line 80). Confirm with `grep -n "PROCESS_STATE" frontend/src/components/session/list/SessionSelectionBar.vue` and drop the import when the count reaches zero.

- [ ] **Step 4: Verify**

```bash
cd /home/twidi/dev/twicc-poc/frontend && npm test
cd /home/twidi/dev/twicc-poc && grep -rn "canToggleRead\b" frontend/src
```

Expected: tests PASS, and the grep returns nothing (`\b` does not match inside `canToggleReadState`, which stays).

- [ ] **Step 5: Commit (only if the user asked for commits)**

```bash
cd /home/twidi/dev/twicc-poc
git add frontend/src/components/session/list/SessionListItem.vue frontend/src/views/SessionView.vue frontend/src/components/session/list/SessionSelectionBar.vue
git commit -m "refactor(sessions): one gate for the read/unread actions

The row menu, the command palette and the multi-select bar each carried their
own copy of the rule. They now share canToggleSessionReadState, which also
drops both actions on a muted session: they would be invisible no-ops there."
```

---

### Task 3: Stop promoting muted sessions in the sidebar's cross-filter block

**Files:**
- Modify: `frontend/src/utils/sidebarSessions.js:11-13` (module header), `:22-23` (imports), `:127-149` (the `crossFilterActive` block: its comment on line 127 through the closing `}` of `if (showActiveAcrossFilters) {` on line 149)

**Interfaces:**
- Consumes: `hasUnreadContent` from Task 1.
- Produces: nothing new.

The "show active across filters" setting promotes, from any project, sessions that either have a live process or unread content. Its unread leg re-implements the comparison inline and so ignores the mute flag. Point it at the shared helper; leave the process leg alone (see Non-goals).

- [ ] **Step 1: Add the import**

Next to the existing imports at the top of the file:

```javascript
import { hasUnreadContent } from './sessions'
```

`sessions.js` imports nothing, so this creates no cycle.

- [ ] **Step 2: Replace the inline check**

Replace lines 127-149 — from `    // 3. Cross-filter active — running process OR unread content from any` down to the `}` closing `if (showActiveAcrossFilters) {` — with:

```javascript
    // 3. Cross-filter active — running process OR unread content from any
    //    project, excluding sessions already covered by blocks above.
    //    "Unread" goes through `hasUnreadContent`, the same rule the backend's
    //    `/api/sessions/?unread=1` endpoint applies in SQL, so a muted session
    //    is never promoted on unread grounds. The "user_turn only" refinement
    //    used for the *unread indicator* in SessionListItem is intentionally
    //    skipped here — a session mid-assistant-turn still deserves surfacing,
    //    and a muted one still surfaces through its process, since mute is not
    //    `hidden`.
    let crossFilterActive = []
    if (showActiveAcrossFilters) {
        const processStates = data.processStates
        crossFilterActive = Object.values(data.sessions).filter(s => {
            if (s.parent_session_id) return false
            if (s.draft || s.ephemeral) return false
            if (s.archived) return false
            if (naturalIds.has(s.id) || crossFilterPinnedIds.has(s.id)) return false
            const ps = processStates[s.id]
            const hasProcess = ps != null
            return hasProcess || hasUnreadContent(s)
        })
            .filter(passesArchiveFilter)
            .sort(sessionSortComparator(processStates))
    }
```

- [ ] **Step 3: Update the module header's description of block 3**

The file's top comment lists the four blocks. Find (currently lines 11-13):

```javascript
//   3. `crossFilterActive`  — sessions with a running process or unread
//                             content from outside the current filter
//                             (gated by `showActiveAcrossFilters`)
```

replace with:

```javascript
//   3. `crossFilterActive`  — sessions with a running process or unread
//                             content (never a muted one) from outside the
//                             current filter (gated by
//                             `showActiveAcrossFilters`)
```

- [ ] **Step 4: Verify no inline copy of the rule survives**

```bash
cd /home/twidi/dev/twicc-poc && grep -rn "last_new_content_at > " frontend/src
```

Expected: only `frontend/src/components/session/SessionToastContent.vue` remains (a deliberate non-goal). `frontend/src/utils/sidebarSessions.js` must not appear.

- [ ] **Step 5: Run the frontend tests**

```bash
cd /home/twidi/dev/twicc-poc/frontend && npm test
```

Expected: PASS.

- [ ] **Step 6: Commit (only if the user asked for commits)**

```bash
cd /home/twidi/dev/twicc-poc
git add frontend/src/utils/sidebarSessions.js
git commit -m "fix(sidebar): a muted session is no longer promoted as unread

The cross-filter 'active' block re-implemented the unread comparison inline and
so ignored the mute flag. It now uses hasUnreadContent. The process leg is
untouched: a muted session that is running still surfaces — mute is not hidden."
```

---

### Task 4: Exclude muted sessions from the backend's sticky unread preload

**Files:**
- Modify: `src/twicc/views.py:118-121` (the `unread_only` branch **only** — line 115 `sticky = Q()` and the `pinned_only` branch on 116-117 must survive untouched), plus its two docstrings at `:89-90` and `:153`
- Create: `tests/test_sessions_sticky_unread.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (this is the SQL mirror of `hasUnreadContent`).
- Produces: no new symbol; `_get_sessions_page(..., unread_only=True)` changes behaviour only.

`_get_sessions_page` is the backend of `/api/sessions/?pinned=1&unread=1&has_process=1`, the "sticky" preload the frontend fires at startup (`loadStickySessions` in `frontend/src/stores/data.js`, called from `frontend/src/App.vue`). Without this change a muted session is still force-fetched into the store by its unread leg. Task 3 already keeps it out of the sidebar's active block, but the backend predicate must mirror `hasUnreadContent` or the two drift. Only the `unread_only` branch changes: a muted session that is pinned, or that has a live process, still preloads through its own branch.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sessions_sticky_unread.py`:

```python
"""The sticky ``unread`` preload must skip muted sessions.

``_get_sessions_page(unread_only=True)`` is the SQL twin of the frontend's
``hasUnreadContent``. A muted session carries no unread state for the user, so
it must not be force-fetched into the sidebar — while its *other* sticky
reasons (pinned, live process) still apply.
"""

import asyncio

import pytest
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionType
from twicc.views import _get_sessions_page


def _run(coro):
    return asyncio.run(coro)


def _make_session(session_id, *, muted=False, pinned=None):
    project, _ = Project.objects.get_or_create(
        id="-sticky-unread", defaults={"directory": "/tmp/sticky-unread"}
    )
    now = timezone.now()
    return Session.objects.create(
        id=session_id,
        # `file_path` is non-null and unique with no default (models.py); give
        # each row its own value so a test may create several sessions.
        file_path=f"/tmp/sticky-unread/{session_id}.jsonl",
        project=project,
        provider=Provider.CODEX.value,
        type=SessionType.SESSION,
        created_at=now,
        user_message_count=1,
        mtime=now.timestamp(),
        last_new_content_at=now,
        last_viewed_at=None,
        mute_on_user_turn=muted,
        pinned=pinned,
    )


@pytest.mark.django_db(transaction=True)
def test_unread_preload_returns_an_unmuted_session():
    _make_session("sticky-unmuted")
    page = _run(_get_sessions_page(None, None, unread_only=True))
    assert [s["id"] for s in page["sessions"]] == ["sticky-unmuted"]


@pytest.mark.django_db(transaction=True)
def test_unread_preload_skips_a_muted_session():
    _make_session("sticky-muted", muted=True)
    page = _run(_get_sessions_page(None, None, unread_only=True))
    assert page["sessions"] == []


@pytest.mark.django_db(transaction=True)
def test_a_muted_session_still_preloads_when_pinned():
    _make_session("sticky-muted-pinned", muted=True, pinned="all")
    page = _run(_get_sessions_page(None, None, pinned_only=True, unread_only=True))
    assert [s["id"] for s in page["sessions"]] == ["sticky-muted-pinned"]


@pytest.mark.django_db(transaction=True)
def test_a_muted_session_still_preloads_when_its_process_is_active():
    _make_session("sticky-muted-active", muted=True)
    page = _run(_get_sessions_page(
        None, None, unread_only=True, active_session_ids=["sticky-muted-active"],
    ))
    assert [s["id"] for s in page["sessions"]] == ["sticky-muted-active"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_sessions_sticky_unread.py -v
```

Expected: exactly `1 failed, 3 passed` — `test_unread_preload_skips_a_muted_session` is the failure (the muted session is returned).

If `test_unread_preload_returns_an_unmuted_session` also fails, the fixture is wrong, not the code — `_get_sessions_page` filters on `type=SESSION`, `created_at__isnull=False`, `user_message_count__gt=0` and `hidden=False`; fix the factory before going on.

- [ ] **Step 3: Write the implementation**

In `src/twicc/views.py`, in `_get_sessions_page`, replace **lines 118-121 only** — the `if unread_only:` line and its three-line body. Do not touch line 115 (`sticky = Q()`) or the `pinned_only` branch above it: deleting them raises `NameError: sticky` on the first sticky request and silently drops the pinned preload. New content for 118-121:

```python
    if unread_only:
        # The SQL twin of the frontend's ``hasUnreadContent``
        # (``frontend/src/utils/sessions.js``): content newer than the last
        # view, and the session not muted. Muting suppresses the unread state
        # as well as the finished-working notifications, so a muted session
        # must not be force-fetched into the sidebar on this ground. Its other
        # sticky reasons still apply: the pinned and active branches are
        # OR-ed with this one and are deliberately mute-blind.
        sticky |= (
            Q(mute_on_user_turn=False)
            & Q(last_new_content_at__isnull=False)
            & (Q(last_viewed_at__isnull=True) | Q(last_new_content_at__gt=F("last_viewed_at")))
        )
```

- [ ] **Step 4: Update the two docstrings that state this endpoint's contract**

`_get_sessions_page`'s own docstring (currently `:89-90`) — find `unread_only: If True, include sessions with unread content (last_new_content_at` and replace the two-line entry with:

```python
        unread_only: If True, include sessions with unread content (not muted,
            last_new_content_at set AND later than last_viewed_at, or
            last_viewed_at null) in the union.
```

The caller-facing twin, the documented contract of `/api/sessions/?unread=1` (currently `:153`) — find:

```python
        unread: When "1"/"true", include sessions with unread content in the result.
```

replace with:

```python
        unread: When "1"/"true", include sessions with unread content in the
            result. A muted session never counts as unread.
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_sessions_sticky_unread.py -v
cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/views.py tests/test_sessions_sticky_unread.py
```

Expected: 4 passed; ruff clean on both files (the repo-wide lint baseline is out of scope — do not fix unrelated findings).

- [ ] **Step 6: Commit (only if the user asked for commits)**

```bash
cd /home/twidi/dev/twicc-poc
git add src/twicc/views.py tests/test_sessions_sticky_unread.py
git commit -m "fix(api): the sticky unread preload skips muted sessions

/api/sessions/?unread=1 is the SQL twin of the frontend hasUnreadContent
predicate. Without the mute clause a muted session was still force-fetched into
the sidebar at startup. Only the unread branch changes: a muted session that is
pinned or has a live process still preloads through its own branch."
```

---

### Task 5: Muting is never inert any more — drop the warning

**Files:**
- Modify: `frontend/src/composables/useSessionMute.js` (whole file)
- Modify: `frontend/src/components/session/detail/SessionHeader.vue:21` and `:455-466`
- Delete: `frontend/src/utils/userTurnChannels.js`
- Delete: `frontend/src/utils/userTurnChannels.test.js`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `frontend/src/composables/useSessionMute.js` now exports `toggleSessionMute(sessionId) -> void` **only**. `isUserTurnMuteInert` and `USER_TURN_SETTINGS_PATH` are gone, and so is `hasAnyUserTurnChannel`.

Today, muting a session with every "finished working" channel switched off changes nothing observable, so the UI warns the user. After Tasks 1-3 that warning is a lie: muting always suppresses the unread state. Remove it rather than leave a false message on screen.

- [ ] **Step 1: Rewrite `useSessionMute.js`**

First run `git status --short frontend/src/composables/useSessionMute.js`. If it shows uncommitted user work, edit the three regions in place instead of overwriting. Otherwise replace the whole file with:

```javascript
/**
 * useSessionMute — the "mute this session" action.
 *
 * Single entry point for every UI that flips `mute_on_user_turn` (session
 * header bell, command palette). The write itself lives in
 * `utils/sessionMute.js`, which stays free of app imports so its unit test can
 * load it under plain node; what belongs here is the part that needs the store.
 *
 * The flag suppresses two things at once: the "agent finished working"
 * notification family (in-app toast, sound, browser notification, Apprise
 * push) and the session's unread state (the row's eye, the project and
 * workspace badges, the favicon, the sidebar's cross-filter promotion). The
 * unread half always applies, so flipping the flag is never a no-op — no
 * warning to show.
 */
import { useDataStore } from '../stores/data'

/**
 * Flip `mute_on_user_turn` on one session.
 *
 * @param {string} sessionId
 */
export function toggleSessionMute(sessionId) {
    const store = useDataStore()
    const session = store.getSession(sessionId)
    if (!session || session.draft) return
    store.setSessionMuteOnUserTurn(session.project_id, sessionId, !session.mute_on_user_turn)
}
```

- [ ] **Step 2: Update `SessionHeader.vue`**

Replace the import on line 21:

```javascript
import { toggleSessionMute } from '../../../composables/useSessionMute'
```

Replace **lines 455-466** — the three-line comment that explains the warning (it starts with `// The mute button gates four channels at once` and ends with "but it says so", pointing at the toast Step 1 deletes), the `noUserTurnChannel` computed, the blank line, and the `muteTooltip` computed — with:

```javascript
// The mute button suppresses two things at once: the "finished working"
// notifications (toast, sound, browser, Apprise) and the session's unread
// state. The unread half always applies, so the button is never a no-op.
const muteTooltip = computed(() => (
    session.value?.mute_on_user_turn
        ? 'Muted — click to restore the unread flag and the "finished working" notification'
        : 'Notifications on — click to mute the unread flag and the "finished working" notification'
))
```

The template is untouched: it already binds `muteTooltip`. Confirm `noUserTurnChannel` is referenced nowhere else with `grep -n "noUserTurnChannel" frontend/src/components/session/detail/SessionHeader.vue` — the expected count after the edit is zero.

- [ ] **Step 3: Delete the now-unused helper and its test**

```bash
cd /home/twidi/dev/twicc-poc
git rm frontend/src/utils/userTurnChannels.js frontend/src/utils/userTurnChannels.test.js
grep -rn "userTurnChannels\|hasAnyUserTurnChannel\|isUserTurnMuteInert\|USER_TURN_SETTINGS_PATH" frontend/src
```

Expected: the grep returns nothing. If it still reports a consumer, that consumer was missed — wire it out before going on.

- [ ] **Step 4: Run the frontend tests**

```bash
cd /home/twidi/dev/twicc-poc/frontend && npm test
```

Expected: PASS (`userTurnChannels.test.js` is gone; `sessionMute.test.js` covers the persistence path and is untouched).

- [ ] **Step 5: Commit (only if the user asked for commits)**

```bash
cd /home/twidi/dev/twicc-poc
git add frontend/src/composables/useSessionMute.js frontend/src/components/session/detail/SessionHeader.vue
git commit -m "refactor(mute): drop the 'this has no effect right now' warning

Muting now also suppresses the session's unread state, so the flag always does
something observable even with every finished-working channel switched off. The
warning became false; hasAnyUserTurnChannel, its only consumer gone, goes with
it. The header tooltip now names both effects."
```

---

### Task 6: The two texts that became false

**Files:**
- Modify: `frontend/src/views/SessionView.vue:1797-1803` (the command palette's mute entry)
- Modify: `src/twicc/core/models.py:469-472` (the field's own definition)
- Modify: `CLAUDE.md:123` and `AGENTS.md:124` (the `Session` bullet — same sentence in both, keep them identical)

**Interfaces:**
- Consumes: nothing. Documentation and one UI label — no behaviour change.
- Produces: nothing.

Three texts state the flag's scope where the user or the codebase reads it. The agent-facing surface (skills, CLI help, `SKILLS-AND-CLI.md`, MCP descriptions) stays as it is — see the scope decision at the top.

- [ ] **Step 1: Update the command palette's mute entry**

`frontend/src/views/SessionView.vue` — the palette label is the most visible text of all, and its comment refers to the warning Task 5 deleted. Replace **lines 1797-1803** as one contiguous block, from `label: 'Mute "Finished Working" Notification',` down to the last line of the comment (`// is a durable preference, and the toggle says so itself.`). The `icon:` and `category:` lines in between are restated unchanged, so a two-part edit would duplicate them:

```javascript
            label: 'Mute Notifications and Unread Flag',
            icon: 'bell-slash',
            category: 'session',
            // Mirrors the session header's bell. Silences the "finished
            // working" notification family for this session only, and keeps
            // the session from ever showing as unread; every other alert still
            // comes through.
```

- [ ] **Step 2: Update the model comment**

In `src/twicc/core/models.py`, replace **lines 469-472** — the three comment lines starting with `# Suppress only the finished-working notification family for this session.` **and** the field declaration they sit above. The block below restates the field, so replacing the comment alone would declare it twice:

```python
    # Suppress this session's finished-working notification family AND its
    # unread state (the row's eye, the project/workspace badges, the favicon,
    # the sidebar's cross-filter promotion). Questions, approvals, failures,
    # usage alerts, and process-state broadcasts remain active. Nothing is
    # erased: last_new_content_at / last_viewed_at keep being written, so
    # unmuting restores the accumulated unread state. The unread half is a
    # pure display gate — applied by `frontend/src/utils/sessions.js` and by
    # the sticky `unread_only` filter in `views._get_sessions_page`, never by
    # a write path. This is TwiCC UI behavior, not an AgentSettings field.
    mute_on_user_turn = models.BooleanField(default=False)
```

- [ ] **Step 3: Update `CLAUDE.md` and `AGENTS.md`**

In the `Session` bullet of both files, find this fragment (present byte-identically in both):

```
`mute_on_user_turn` (mutable per-session UI state outside that bundle; suppresses only the finished-working notification family and is independent of `hidden`)
```

replace with:

```
`mute_on_user_turn` (mutable per-session UI state outside that bundle; suppresses the finished-working notification family **and** the session's unread state — a display gate in `frontend/src/utils/sessions.js` + the sticky `unread_only` filter in `views._get_sessions_page`, never a write path, so unmuting restores the accumulated unread; independent of `hidden`)
```

The two files must stay identical on this bullet (`AGENTS.md` mirrors `CLAUDE.md`).

- [ ] **Step 4: Verify**

```bash
cd /home/twidi/dev/twicc-poc
grep -rn "the toggle says so itself" frontend/src
grep -Fc "unread state (the row's eye" src/twicc/core/models.py
grep -Fc "Suppress only the finished-working" src/twicc/core/models.py || true
diff <(grep -n "mute_on_user_turn.*mutable per-session" CLAUDE.md | cut -d: -f2-) \
     <(grep -n "mute_on_user_turn.*mutable per-session" AGENTS.md | cut -d: -f2-)
uvx ruff check src/twicc/core/models.py
cd /home/twidi/dev/twicc-poc/frontend && npm test
```

Expected: the first grep returns nothing; the second prints `1` (the new text is in); the third prints `0` (the old claim is gone — `|| true` because `grep -c` exits 1 on a zero count); the `diff` is empty (the two bullets match); ruff clean; frontend tests pass.

A bare `grep -c "unread" src/twicc/core/models.py` would **not** work as a check: `models.py:449` already contains `# Content tracking (for "unread" detection)`, so it passes even if this task was skipped.

- [ ] **Step 5: Commit (only if the user asked for commits)**

```bash
cd /home/twidi/dev/twicc-poc
git add frontend/src/views/SessionView.vue src/twicc/core/models.py CLAUDE.md AGENTS.md
git commit -m "docs(mute): the flag also suppresses the unread state

The command palette's label, the model comment and the CLAUDE.md/AGENTS.md
Session bullet all said the flag suppressed only the finished-working
notification family. They now name both effects. The agent-facing surface
(skills, CLI help, SKILLS-AND-CLI.md) is deliberately left for later."
```

---

## Final verification

Record the starting point **before Task 1** so the diff checks below are exact even if the user commits in between:

```bash
cd /home/twidi/dev/twicc-poc && git rev-parse HEAD   # note this SHA as $BASE
```

- [ ] `cd /home/twidi/dev/twicc-poc && uv run pytest` — full backend suite passes.
- [ ] `cd /home/twidi/dev/twicc-poc/frontend && npm test` — full frontend suite passes.
- [ ] `cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/views.py src/twicc/core/models.py tests/test_sessions_sticky_unread.py` — clean (repo-wide lint baseline is out of scope).
- [ ] No file outside the File Structure table was touched. **If the user never asked for commits**, `HEAD == $BASE` and the work sits in the working tree, so use `git status --short` and `git diff --stat`. Only when the six commits exist does `git diff --stat $BASE HEAD` answer.
- [ ] No migration was added — this plan adds none:

```bash
cd /home/twidi/dev/twicc-poc
# uncommitted path:
! git status --porcelain | grep -q "migrations/" && echo OK
# committed path:
! git log --name-only --pretty=format: $BASE..HEAD | grep -q "migrations/" && echo OK
```

(Use `grep -q` inside a negation, not `grep -c`: `grep -c` prints `0` but *exits 1*, which a `set -e` wrapper reads as a failed check.)
- [ ] Tell the user: the backend change (Task 4) needs a dev server restart, which is theirs to run — never restart it yourself.

## Manual smoke test (for the user, after their restart)

1. Open a session with unread content: the eye shows, the project badge counts it.
2. Click the bell in the session header. The eye disappears at once, the project/workspace badge and the favicon drop it, and the Session Actions menu no longer offers "Mark as read / unread".
3. Reload the page: the muted session must not be promoted into the sidebar's active block.
4. Click the bell again: the unread state comes straight back, untouched.

## Review history

Five independent adversarial review rounds. The first four ran on the previous, wider version (29 findings, all fixed); the fifth ran on this narrowed version and returned **PASS** with three minor findings, all fixed in place: Task 2 Step 3 now replaces lines 76-89 as one contiguous block (a partial edit would have duplicated `markReadCandidates`), Task 6 Step 4's `grep` now anchors on the new text (`models.py:449` already contains the word "unread", so the old check passed even on a skipped edit), and the Final verification now covers the uncommitted path and stops using `grep -c` inside a success test.

What the rounds established and that still holds:

- **Completeness of the unread sweep.** Every consumer funnels through `isSessionUnread` (`data.js:1076` project badge, `data.js:1150` global count → `useFavicon.js:74`, `AggregatedProcessIndicator.vue:99`, `staticCommands.js:212/317`, `SessionSwitcher.vue:54`, `SessionListItem.vue:195`, `SessionSelectionBar.vue:85/88`). The only inline re-implementations are `sidebarSessions.js` (Task 3), `SessionView.vue` (Task 2) and `SessionToastContent.vue` (non-goal, verified unreachable for a muted session). `views.py` is the only SQL predicate.
- **Tests, actually executed by a reviewer.** The backend test gives exactly `1 failed, 3 passed` against unmodified code; the 12 frontend assertions pass against the proposed implementation and fail at link time before it.
- **Correctness.** `F` and `Q` are already imported in `views.py`; `mute_on_user_turn` is non-null so `Q(mute_on_user_turn=False)` has no NULL hole; `sessions.js` imports nothing, so `sidebarSessions → sessions` creates no cycle; `PROCESS_STATE` genuinely becomes unused only in `SessionSelectionBar.vue`.
- **Line-range traps closed.** `sessions.js` 19-**46** (46 is the last line; stopping at 45 orphans a `}`), `views.py` **118**-121 (115-117 hold `sticky = Q()` and the pinned branch), `models.py` 469-**472** (the block restates the field), `SessionHeader.vue` 455-466 (the false comment sits outside both computeds), `SessionView.vue` 1797-**1803** (one contiguous block).

**Scope note:** this revision removes the agent-facing documentation work (plugin skills, CLI help, `SKILLS-AND-CLI.md`, `plugin.json` bump) and the peripheral docstrings that the review rounds had accumulated. The user's call: the change is a user-interface change, and the skills are an agent-facing surface with no bearing on it.
