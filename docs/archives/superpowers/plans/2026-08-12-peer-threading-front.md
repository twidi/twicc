# Peer Message Threading — Lot 2 Front Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/plans/2026-08-11-peer-threading-design.md` at commit `ba4567ab198d0875af25345929e0255713663c18` is the authority. This plan implements only lot 2 from §18.

**Goal:** Show peer-message reply relationships in the owner UI and safely propose the parent message's local session as the existing-session delivery target.

**Architecture:** A small pure utility owns candidate-or-load selection, the shared picker-eligibility predicate, and pagination recovery. `PeerMessageReviewDialog.vue` keeps its normal picker state and adds only request-lifetime state. It hydrates one target during initialization, seeds the existing selection once, and derives later actionability from live Pinia rows. Inbox, dialog, and toast surfaces read the serializer's existing `reply_to_ref` and `reply_target` fields.

**Tech Stack:** Vue 3 Composition API, Pinia 3, Vite 7, Web Awesome 3.3, Node `node:test`.

## Global Constraints

- **Lot boundary:** implement only §18 lot 2, Front. Do not modify backend Python, Django models or migrations, CLI, MCP, documentation, agent skills, plugin metadata, package metadata, or `CHANGELOG.md`.
- **Worktree:** every command starts with `cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && `. Never read or write `/home/twidi/dev/twicc-poc` for this work.
- **Historical design:** read the invariants in `docs/plans/2026-07-24-peer-messaging-design.md`. Never edit that frozen historical file.
- **Settled specification:** do not reopen the decisions in `docs/plans/2026-08-11-peer-threading-design.md`. The current backend and agent surfaces already work end to end.
- **Reply target:** resolve the local end. Pre-select only when it resolves and is picker-eligible. Otherwise keep no pre-selection and show one generic warning that never names why. Never distinguish hidden, deleted, and archived reasons because the reason is not knowable.
- **Session scope:** session types outside the peer system are out of subject. Do not introduce them as a target, origin, case, rejected value, or missing predicate.
- **Hidden sessions:** a hidden session is never a delivery target and is never made reachable.
- **Approval gate:** keep the receiving human's read-before-delivery gate unchanged. The toast only opens the review dialog. It never delivers, routes, opens a composer, sends a draft, or starts an agent.
- **Picker order:** never reorder the delivery picker. Pagination recovery inserts the eligible hydrated row with the existing `sessionSortComparator`; the picker scrolls without moving rows.
- **Inbox:** keep one inbox row per message. Keep `PeerInboxDialog.vue` flat. Thread grouping stays deferred.
- **Database invariant:** preserve the `("peer", "direction", "message_id")` unique constraint. This frontend lot never touches it.
- **Identifier contract:** preserve `[A-Za-z0-9_][A-Za-z0-9_-]{0,39}`. Leading hyphen, dot, and colon stay invalid. The settled leading-hyphen command-line option rationale is out of review scope.
- **Target loader:** use `dataStore.getSession(id)` first and `dataStore.loadSessionById(id)` only through the candidate-or-load decision. Do not change either store API or the owner session route.
- **Picker predicate:** share the exact non-pagination exclusions between the rendered picker and target hydration. Do not call `isSelectableProject` and do not require `getListableProjects` membership. Worktree and stale-project rows remain eligible when the normal picker would produce them.
- **Target state:** keep no suggestion id, dismissal flag, eligibility snapshot, mode-provenance flag, pending-scroll flag, or transition helper. `selectedSessionId` is the only selected target id. One hydration-pending boolean and one open-generation integer are request-lifetime state.
- **Selection behavior:** seed once during initialization. Later Pinia changes can change eligibility, warning visibility, rendered membership, and actionability. They never seed again. A human row click permanently replaces the seed.
- **Redelivery:** only pending inbound messages hydrate, warn, or seed. Delivered inbound messages remain redeliverable with `mode = null`. Other rows remain read-only.
- **Request lifetime:** increment the open generation synchronously on every `[props.open, props.messageId]` change, including close. Guard detail, Markdown, hydration, seed, and scroll writes with generation, open state, and exact message id.
- **Warning copy:** use exactly: `This message is part of a thread, but its session is not available for selection. Choose another session, or deliver to a new one.` Render it only after hydration settles for a pending inbound row with non-empty `reply_to` and no live picker-eligible target.
- **Reply copy:** use `In reply to your “<title>”` when `reply_to_ref.direction === 'out'`; use `In reply to their “<title>”` when it is `'in'`. Omit the line when the reference or title is absent.
- **Toast copy:** use `Reply from <peer>` when `reply_to_ref` is non-null. Keep `Message from <peer>` for roots. Do not modify `PeerToastContent.vue`.
- **Frontend tests:** use `node:test` through `cd frontend && npm test`. The project has no Vue component-test harness. Do not add one.
- **Dependencies:** do not install packages. Use `uv run` for project Python dependencies and `uvx` only for a standalone Python tool absent from project dependencies. This lot needs neither command.
- **Language:** all code, comments, test names, UI text, and commit subjects are English.
- **Commits:** one commit per task. Each commit step declares only the worktree, staged files, and Conventional Commit subject. The implementer follows `CLAUDE.md` and `AGENTS.md` for body and trailer rules.

## Existing Interfaces

- Produces: `backend summary/detail field reply_to_ref: {message_id, title, direction, status} | null`.

## Task map

| Task | Deliverable | Depends on |
|---|---|---|
| 1 | Pure reply-target decisions with exhaustive Node tests | — |
| 2 | Safe dialog hydration, live picker integration, one seed, warning, and scroll | 1 |
| 3 | Direction-correct reply lines, reply toast title, build, and manual gate verification | 2 |

---

### Task 1: Add the pure reply-target derivations

**Files:**
- Create: `frontend/src/utils/peerReplyTarget.test.js`
- Create: `frontend/src/utils/peerReplyTarget.js`

**Interfaces:**
- Produces: `chooseReplyTargetSource(sessionId: string, candidates: Object[]) -> {kind: 'candidate', session: Object} | {kind: 'load', sessionId: string}`.
- Produces: `isReplyTargetPickerEligible(session: Object | null, archivedProjectIds: Set<string>) -> boolean`.
- Produces: `recoverReplyTargetPagination(candidates: Object[], target: Object | null, archivedProjectIds: Set<string>, compareSessions: (a: Object, b: Object) -> number) -> Object[]`.

- [ ] **Step 1: Write the failing pure-helper tests**

Create `frontend/src/utils/peerReplyTarget.test.js` with this content:

```javascript
import test from 'node:test'
import assert from 'node:assert/strict'

import {
    chooseReplyTargetSource,
    isReplyTargetPickerEligible,
    recoverReplyTargetPagination,
} from './peerReplyTarget.js'

const archivedProjectIds = new Set(['project-archived'])

function session(id, overrides = {}) {
    return {
        id,
        project_id: 'project-live',
        hidden: false,
        draft: false,
        archived: false,
        mtime: 0,
        ...overrides,
    }
}

test('uses the exact normal candidate without requesting a load', () => {
    const target = session('target')
    const result = chooseReplyTargetSource('target', [session('other'), target])

    assert.deepEqual(result, { kind: 'candidate', session: target })
    assert.strictEqual(result.session, target)
    assert.equal('sessionId' in result, false)
})

test('requests a by-id load when normal candidates omit the target', () => {
    assert.deepEqual(
        chooseReplyTargetSource('target', [session('other')]),
        { kind: 'load', sessionId: 'target' },
    )
})

test('matches the unpaged picker exclusions without a project-list rule', () => {
    assert.equal(isReplyTargetPickerEligible(session('regular'), archivedProjectIds), true)
    assert.equal(isReplyTargetPickerEligible(
        session('worktree', { project_id: 'project-worktree' }),
        archivedProjectIds,
    ), true)
    assert.equal(isReplyTargetPickerEligible(
        session('stale-project', { project_id: 'project-stale' }),
        archivedProjectIds,
    ), true)

    assert.equal(isReplyTargetPickerEligible(null, archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(session('hidden', { hidden: true }), archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(session('draft', { draft: true }), archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(session('archived', { archived: true }), archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(
        session('archived-project', { project_id: 'project-archived' }),
        archivedProjectIds,
    ), false)
})

test('recovers one eligible page-omitted target in normal sort order', () => {
    const newest = session('newest', { mtime: 30 })
    const target = session('target', { mtime: 20 })
    const oldest = session('oldest', { mtime: 10 })
    const compareSessions = (a, b) => b.mtime - a.mtime

    const result = recoverReplyTargetPagination(
        [newest, oldest], target, archivedProjectIds, compareSessions,
    )

    assert.deepEqual(result.map(candidate => candidate.id), ['newest', 'target', 'oldest'])
    assert.equal(result.filter(candidate => candidate.id === 'target').length, 1)
})

test('leaves existing and ineligible candidate arrays unchanged', () => {
    const target = session('target', { mtime: 20 })
    const candidates = [session('newest', { mtime: 30 }), target]
    const compareSessions = (a, b) => b.mtime - a.mtime

    assert.strictEqual(
        recoverReplyTargetPagination(candidates, target, archivedProjectIds, compareSessions),
        candidates,
    )
    assert.strictEqual(
        recoverReplyTargetPagination(
            candidates,
            session('hidden-target', { hidden: true }),
            archivedProjectIds,
            compareSessions,
        ),
        candidates,
    )
    assert.strictEqual(
        recoverReplyTargetPagination(
            candidates,
            session('archived-project-target', { project_id: 'project-archived' }),
            archivedProjectIds,
            compareSessions,
        ),
        candidates,
    )
    assert.strictEqual(
        recoverReplyTargetPagination(candidates, null, archivedProjectIds, compareSessions),
        candidates,
    )
})
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/utils/peerReplyTarget.test.js
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `peerReplyTarget.js`. This catches a missing utility before component wiring can hide it.

- [ ] **Step 3: Implement the three pure derivations**

Create `frontend/src/utils/peerReplyTarget.js` with this content:

```javascript
/**
 * Choose whether reply-target initialization can use a normal picker candidate
 * or must ask the store's by-id loader for the session.
 */
export function chooseReplyTargetSource(sessionId, candidates) {
    const session = candidates.find(candidate => candidate.id === sessionId)
    if (session) return { kind: 'candidate', session }
    return { kind: 'load', sessionId }
}

/**
 * The delivery picker's non-pagination exclusions. Project list membership and
 * project staleness are deliberately absent: the normal picker can render a
 * worktree or stale-project row when its explicit scope produces that row.
 */
export function isReplyTargetPickerEligible(session, archivedProjectIds) {
    return !!session
        && !session.hidden
        && !session.draft
        && !session.archived
        && !archivedProjectIds.has(session.project_id)
}

/**
 * Restore an eligible hydrated target omitted only by the current page bound.
 * Existing and ineligible targets preserve the exact input array reference.
 */
export function recoverReplyTargetPagination(
    candidates,
    target,
    archivedProjectIds,
    compareSessions,
) {
    if (!isReplyTargetPickerEligible(target, archivedProjectIds)) return candidates
    if (candidates.some(candidate => candidate.id === target.id)) return candidates
    return [...candidates, target].sort(compareSessions)
}
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/utils/peerReplyTarget.test.js
```

Expected: PASS with 5 tests. An unconditional load leaves `sessionId` on the candidate result and fails the first test. Any hidden, draft, archived-row, or archived-project eligibility leak fails the exclusion test. Reordering, duplication, or mutation in pagination recovery fails the last two tests.

- [ ] **Step 5: Run the complete frontend test suite**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test
```

Expected: PASS. This catches a module-level regression from the new utility across every existing Node test.

- [ ] **Step 6: Commit Task 1**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Stage:

```text
frontend/src/utils/peerReplyTarget.js
frontend/src/utils/peerReplyTarget.test.js
```

Commit subject:

```text
feat(peer): define reply target picker rules
```

---

### Task 2: Hydrate and seed the reply target in the review dialog

**Files:**
- Modify: `frontend/src/components/peer/PeerMessageReviewDialog.vue`
- Test: `frontend/src/utils/peerReplyTarget.test.js`

**Interfaces:**
- Consumes: `chooseReplyTargetSource(sessionId: string, candidates: Object[]) -> {kind: 'candidate', session: Object} | {kind: 'load', sessionId: string}` from Task 1.
- Consumes: `isReplyTargetPickerEligible(session: Object | null, archivedProjectIds: Set<string>) -> boolean` from Task 1.
- Consumes: `recoverReplyTargetPagination(candidates: Object[], target: Object | null, archivedProjectIds: Set<string>, compareSessions: (a: Object, b: Object) -> number) -> Object[]` from Task 1.
- Produces: local `buildSessionRows(projectScopeId: string, textFilter: string, paginationTarget?: Object | null) -> Array<{session, sectionKey, separator}>`.
- Produces: live `replyTargetSession`, `replyTargetPickerEligible`, and `showReplyTargetWarning` computed derivations.
- Produces: one generation-guarded initialization that seeds `scopeId`, `selectedSessionId`, and `mode` once.
- Produces: `generation-safe dialog detail snapshot: detail receives only a current generation's response and remains fixed until close or reopen`.

- [ ] **Step 1: Extend the Vue and store imports**

In `frontend/src/components/peer/PeerMessageReviewDialog.vue`, replace this exact block:

```javascript
import { ref, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { usePeersStore } from '../../stores/peers'
import { useDataStore, ALL_PROJECTS_ID } from '../../stores/data'
```

with:

```javascript
import { ref, computed, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { usePeersStore } from '../../stores/peers'
import { useDataStore, ALL_PROJECTS_ID, sessionSortComparator } from '../../stores/data'
```

Replace this exact import block:

```javascript
import { computeSidebarSessionBlocks } from '../../utils/sidebarSessions'
import { dateBucketSeparator } from '../../utils/datePresets'
import { matchQuery } from '../../utils/textFilter'
```

with:

```javascript
import { computeSidebarSessionBlocks } from '../../utils/sidebarSessions'
import {
    chooseReplyTargetSource,
    isReplyTargetPickerEligible,
    recoverReplyTargetPagination,
} from '../../utils/peerReplyTarget'
import { dateBucketSeparator } from '../../utils/datePresets'
import { matchQuery } from '../../utils/textFilter'
```

- [ ] **Step 2: Add request-lifetime state**

Find this exact state block:

```javascript
const selectedSessionId = ref(null)

const peerName = computed(() => peersStore.peerLabel(detail.value?.peer_id))
```

Replace it with:

```javascript
const selectedSessionId = ref(null)

// Ordinary request-lifetime state. The boolean carries no target identity or
// reason. The generation invalidates every result from a closed or reused
// dialog before that result can write state.
const targetHydrationSettled = ref(false)
let openGeneration = 0

const peerName = computed(() => peersStore.peerLabel(detail.value?.peer_id))
```

- [ ] **Step 3: Factor the explicit-scope candidate builder and live target derivations**

Replace the exact block from the current `sessionRows` comment through its computed body:

```javascript
// 'Existing session' picker: the sidebar's session list — same order and
// blocks (pinned/active/natural + date buckets) — minus archived (never a
// delivery target here) and drafts (no real session to inject into).
//
// The sidebar's cross-filter blocks ("Pinned elsewhere", "Active elsewhere")
// are deliberately dropped: they exist to keep out-of-scope sessions reachable
// while browsing, which is exactly what a scope select must not do. Sessions
// of another project are reached by picking that project.
const sessionRows = computed(() => {
    if (mode.value !== 'existing') return []
    const blocks = computeSidebarSessionBlocks({
        data: dataStore,
        workspaces: workspacesStore,
        effectiveProjectId: scopeId.value,
        activeWorkspaceId: activeWorkspaceId.value,
        sessionId: null,
        showArchived: false,
        showArchivedProjects: false,
        showActiveAcrossFilters: false,
    })
    const processStates = dataStore.processStates
    const nowMs = Date.now()
    const entries = []
    const push = (session, sectionKey, separator) => {
        if (session.draft || session.archived) return
        entries.push({ session, sectionKey, separator })
    }
    for (const s of blocks.natural) {
        if (s.pinned) push(s, 'n-pinned', { label: 'Pinned' })
        else if (processStates[s.id] != null) push(s, 'n-active', { label: 'Active' })
        else {
            const bucket = dateBucketSeparator(s.mtime, nowMs)
            push(s, `n-${bucket.key}`, bucket.entry)
        }
    }
    // Same matching as the sidebar's filter: fuzzy by default, exact
    // substring when the query is wrapped/prefixed with a quote.
    const query = sessionFilter.value.trim()
    const visible = query
        ? entries.filter(e => matchQuery(query, e.session.title || e.session.id))
        : entries
    // A separator lands on the first VISIBLE session of each section.
    let prevSection = null
    return visible.map((entry) => {
        const withSeparator = entry.sectionKey !== prevSection
        prevSection = entry.sectionKey
        return { ...entry, separator: withSeparator ? entry.separator : null }
    })
})
```

with:

```javascript
// `computeSidebarSessionBlocks` already applies these project exclusions to
// normal rows. The same set lets a hydrated page-omitted row use the exact
// non-pagination rule instead of an eligibility override.
const archivedProjectIds = computed(() => new Set(
    dataStore.getProjects.filter(project => project.archived).map(project => project.id),
))

/** Whether a hydrated row belongs to the supplied picker scope before any
 *  pagination bound. This checks scope only; eligibility stays in the shared
 *  pure predicate. */
function sessionBelongsToScope(session, projectScopeId) {
    if (!session) return false
    if (projectScopeId === ALL_PROJECTS_ID) return true
    if (isWorkspaceProjectId(projectScopeId)) {
        const workspaceId = extractWorkspaceId(projectScopeId)
        return workspacesStore.getVisibleProjectIds(workspaceId).includes(session.project_id)
    }
    return dataStore.getProjectScopeIds(projectScopeId).includes(session.project_id)
}

/** Build the existing-session rows from explicit inputs. Initialization uses
 *  the target's project and an empty filter without mutating live picker state. */
function buildSessionRows(projectScopeId, textFilter, paginationTarget = null) {
    const blocks = computeSidebarSessionBlocks({
        data: dataStore,
        workspaces: workspacesStore,
        effectiveProjectId: projectScopeId,
        activeWorkspaceId: activeWorkspaceId.value,
        sessionId: null,
        showArchived: false,
        showArchivedProjects: false,
        showActiveAcrossFilters: false,
    })
    const processStates = dataStore.processStates
    const compareSessions = sessionSortComparator(processStates)
    const normalCandidates = blocks.natural.filter(session =>
        isReplyTargetPickerEligible(session, archivedProjectIds.value),
    )
    const recoveryTarget = sessionBelongsToScope(paginationTarget, projectScopeId)
        ? paginationTarget
        : null
    const candidates = recoveryTarget
        ? recoverReplyTargetPagination(
            normalCandidates,
            recoveryTarget,
            archivedProjectIds.value,
            compareSessions,
        )
        : normalCandidates
    const nowMs = Date.now()
    const entries = candidates.map((session) => {
        if (session.pinned) {
            return { session, sectionKey: 'n-pinned', separator: { label: 'Pinned' } }
        }
        if (processStates[session.id] != null) {
            return { session, sectionKey: 'n-active', separator: { label: 'Active' } }
        }
        const bucket = dateBucketSeparator(session.mtime, nowMs)
        return { session, sectionKey: `n-${bucket.key}`, separator: bucket.entry }
    })
    // Same matching as the sidebar's filter: fuzzy by default, exact
    // substring when the query is wrapped/prefixed with a quote.
    const query = textFilter.trim()
    const visible = query
        ? entries.filter(entry => matchQuery(query, entry.session.title || entry.session.id))
        : entries
    // A separator lands on the first VISIBLE session of each section.
    let prevSection = null
    return visible.map((entry) => {
        const withSeparator = entry.sectionKey !== prevSection
        prevSection = entry.sectionKey
        return { ...entry, separator: withSeparator ? entry.separator : null }
    })
}

const replyTargetSession = computed(() =>
    dataStore.getSession(detail.value?.reply_target) || null,
)
const replyTargetPickerEligible = computed(() =>
    isReplyTargetPickerEligible(replyTargetSession.value, archivedProjectIds.value),
)
const showReplyTargetWarning = computed(() =>
    isPending.value
    && targetHydrationSettled.value
    && detail.value?.reply_to !== ''
    && !replyTargetPickerEligible.value,
)

// 'Existing session' picker: the sidebar's natural block, with the same
// ordering, section labels and text matching. A hydrated target is inserted
// only when the current page bound is the reason the normal rows omitted it.
const sessionRows = computed(() => {
    if (mode.value !== 'existing') return []
    return buildSessionRows(scopeId.value, sessionFilter.value, replyTargetSession.value)
})
```

- [ ] **Step 4: Replace the open watcher with guarded detail, Markdown, hydration, seed, and scroll work**

Replace this exact watcher:

```javascript
watch(() => [props.open, props.messageId], async ([open]) => {
    if (!open || props.messageId == null) return
    detail.value = null
    loadError.value = ''
    renderedText.value = ''
    note.value = ''
    actionError.value = ''
    mode.value = null
    pickedProjectId.value = ''
    sessionFilter.value = ''
    scopeId.value = defaultScopeId()
    selectedSessionId.value = null
    confirmingRefuse.value = false
    try {
        const response = await apiFetch(`/api/peer-messages/${props.messageId}/`)
        if (!response.ok) {
            loadError.value = 'Could not load the message.'
            return
        }
        detail.value = await response.json()
        // Redelivery: bring back the note typed the first time (empty on a
        // never-delivered message).
        note.value = detail.value?.recipient_note || ''
    } catch {
        // fetch rejects on network failure — never leave the dialog blank.
        loadError.value = 'Could not load the message — is the server reachable?'
        return
    }
    renderedText.value = await renderMarkdown(detail.value?.payload?.text || '')
}, { immediate: true })
```

with:

```javascript
function isCurrentOpen(generation, messageId) {
    return generation === openGeneration
        && props.open
        && props.messageId === messageId
}

async function renderDetailText(text, generation, messageId) {
    const rendered = await renderMarkdown(text)
    if (!isCurrentOpen(generation, messageId)) return
    renderedText.value = rendered
}

async function scrollSeededTarget(generation, messageId, targetId) {
    await nextTick()
    if (!isCurrentOpen(generation, messageId)) return
    if (mode.value !== 'existing' || selectedSessionId.value !== targetId) return
    const picker = dialogRef.value?.querySelector('.pr-picker')
    if (!picker) return
    const expectedId = `session-button-${targetId}`
    const row = [...picker.querySelectorAll('.session-item')]
        .find(candidate => candidate.id === expectedId)
    row?.scrollIntoView({ block: 'nearest' })
}

async function initializeReplyTarget(loadedDetail, generation, messageId) {
    if (!(loadedDetail.direction === 'in' && loadedDetail.status === 'pending')) {
        if (isCurrentOpen(generation, messageId)) targetHydrationSettled.value = true
        return
    }
    const targetId = loadedDetail.reply_target
    if (targetId == null) {
        if (isCurrentOpen(generation, messageId)) targetHydrationSettled.value = true
        return
    }

    const current = dataStore.getSession(targetId)
    const normalRows = current
        ? buildSessionRows(current.project_id, '')
        : []
    const source = chooseReplyTargetSource(
        targetId,
        normalRows.map(row => row.session),
    )
    let target = null
    let candidateRows = normalRows
    if (source.kind === 'candidate') {
        target = source.session
    } else {
        try {
            target = await dataStore.loadSessionById(source.sessionId)
        } catch {
            target = null
        }
        if (!isCurrentOpen(generation, messageId)) return
        if (isReplyTargetPickerEligible(target, archivedProjectIds.value)) {
            candidateRows = buildSessionRows(target.project_id, '', target)
        } else {
            candidateRows = []
        }
    }

    if (!isCurrentOpen(generation, messageId)) return
    targetHydrationSettled.value = true
    const targetIsCandidate = target
        && candidateRows.some(row => row.session.id === targetId)
    if (!targetIsCandidate) return

    scopeId.value = target.project_id
    selectedSessionId.value = targetId
    mode.value = 'existing'
    await scrollSeededTarget(generation, messageId, targetId)
}

watch(() => [props.open, props.messageId], async ([open, messageId]) => {
    const generation = ++openGeneration
    if (!open || messageId == null) return
    detail.value = null
    loadError.value = ''
    renderedText.value = ''
    note.value = ''
    actionError.value = ''
    mode.value = null
    pickedProjectId.value = ''
    sessionFilter.value = ''
    scopeId.value = defaultScopeId()
    selectedSessionId.value = null
    targetHydrationSettled.value = false
    confirmingRefuse.value = false

    let loadedDetail
    try {
        const response = await apiFetch(`/api/peer-messages/${messageId}/`)
        if (!isCurrentOpen(generation, messageId)) return
        if (!response.ok) {
            loadError.value = 'Could not load the message.'
            return
        }
        loadedDetail = await response.json()
        if (!isCurrentOpen(generation, messageId)) return
        detail.value = loadedDetail
        // Redelivery: bring back the note typed the first time (empty on a
        // never-delivered message).
        note.value = loadedDetail.recipient_note || ''
    } catch {
        if (!isCurrentOpen(generation, messageId)) return
        // fetch rejects on network failure — never leave the dialog blank.
        loadError.value = 'Could not load the message — is the server reachable?'
        return
    }

    // Markdown and target hydration are independent. Each result carries the
    // same generation guard, so neither stale branch can overwrite a reused
    // dialog.
    const markdownPromise = renderDetailText(
        loadedDetail.payload?.text || '', generation, messageId,
    )
    await initializeReplyTarget(loadedDetail, generation, messageId)
    await markdownPromise
}, { immediate: true, flush: 'sync' })
```

- [ ] **Step 5: Preserve normal selection across mode changes**

Replace this exact block:

```javascript
/** Toggle a delivery mode; every picker starts fresh on each switch. */
function setMode(next) {
    mode.value = mode.value === next ? null : next
    pickedProjectId.value = ''
    sessionFilter.value = ''
    scopeId.value = defaultScopeId()
    selectedSessionId.value = null
}
```

with:

```javascript
/** Toggle a delivery mode. Mode-specific controls reset; the ordinary session
 *  scope and selection survive and become actionable only when rendered. */
function setMode(next) {
    mode.value = mode.value === next ? null : next
    pickedProjectId.value = ''
    sessionFilter.value = ''
}
```

- [ ] **Step 6: Render the one generic warning after hydration**

Find this exact template transition:

```vue
            <!-- Actions -->
            <template v-if="canDeliver">
                <div class="pr-note">
```

Replace it with:

```vue
            <!-- Actions -->
            <template v-if="canDeliver">
                <wa-callout v-if="showReplyTargetWarning" variant="warning" size="small">
                    This message is part of a thread, but its session is not available for selection.
                    Choose another session, or deliver to a new one.
                </wa-callout>
                <div class="pr-note">
```

- [ ] **Step 7: Run focused helpers, the full frontend suite, and the Vue build**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && node --test src/utils/peerReplyTarget.test.js && npm test && npm run build
```

Expected: all helper tests and all frontend tests PASS. All Vite builds complete. The helper tests fail if the dialog's shared predicate changes eligibility rules without updating the executable contract. The build fails on unresolved imports, invalid Composition API syntax, invalid Vue template syntax, or an invalid standalone bundle dependency.

- [ ] **Step 8: Perform the deterministic dialog acceptance checks**

Use the running development UI and the development rows described in spec §16. Perform M2 through M14 and M16 through M18 exactly. Record each id as PASS or FAIL before continuing.

Required observations:

- M2: already-present worktree and stale-project targets seed, highlight, stay actionable, and show no generic warning.
- M3: a Pinia-absent page-omitted target causes one by-id request, appears once in normal order, seeds, scrolls, and stays actionable.
- M4: the generic warning stays absent during the delayed by-id request and the target seeds after success.
- M5: a failed by-id lookup gives one reason-free warning and no selection.
- M6: a live archived target gives the same warning, keeps `mode = null`, and selects nothing.
- M7: a non-empty unresolved `reply_to` gives the same warning without any by-id request.
- M8: a root gives no warning, seed, or by-id request.
- M9: delivered replies open with null mode and no warning; refused, failed, and outbound rows stay read-only.
- M10: mode-specific filter and project inputs reset, while scope and selected id survive every mode switch and toggle.
- M11: filter and scope changes disable delivery only while the selected row is not rendered. They never change the warning.
- M12: live archive disables delivery and shows the warning. Unarchive clears it and restores actionability from the preserved id.
- M13: a target ineligible at initialization can become eligible and clear the warning, but it never seeds late.
- M14: after clicking row B, hiding and restoring seeded row A never restores A's selection.
- M16: late detail or attachment work from message A cannot overwrite message B or scroll B toward A.
- M17: late Markdown rendering from message A cannot appear in message B.
- M18: late by-id hydration from message A cannot change message B's warning, mode, scope, selection, or scroll.

Expected: every listed observation occurs. Any warning flash during M4 catches an unsettled-state bug. Any A-to-B overwrite in M16–M18 catches a missing generation guard. Any late seed in M13 or restored original seed in M14 catches forbidden parallel suggestion state. The callback-only post-render interval remains unverified, exactly as spec §16.2 states; do not invent a harness for it.

- [ ] **Step 9: Commit Task 2**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Stage:

```text
frontend/src/components/peer/PeerMessageReviewDialog.vue
```

Commit subject:

```text
feat(peer): seed reply delivery targets
```

---

### Task 3: Show reply relationships in the inbox, dialog, and toast

**Files:**
- Modify: `frontend/src/components/peer/PeerInboxRow.vue`
- Modify: `frontend/src/components/peer/PeerMessageReviewDialog.vue`
- Modify: `frontend/src/composables/useWebSocket.js`

**Interfaces:**
- Consumes: `backend summary/detail field reply_to_ref: {message_id, title, direction, status} | null` from Existing Interfaces.
- Consumes: `generation-safe dialog detail snapshot: detail receives only a current generation's response and remains fixed until close or reopen` from Task 2.
- Produces: inbox and dialog reply lines with direction derived only from `reply_to_ref.direction`.
- Produces: incoming toast title `Reply from <peer>` for a non-null `reply_to_ref`, otherwise `Message from <peer>`.

- [ ] **Step 1: Add the reply line above the inbox row's local route**

In `frontend/src/components/peer/PeerInboxRow.vue`, replace this exact block inside `routes`:

```javascript
const routes = computed(() => {
    const message = props.message
    const lines = []
    // The title and project come with the message, read live from the session
```

with:

```javascript
const routes = computed(() => {
    const message = props.message
    const lines = []
    const reply = message.reply_to_ref
    if (reply?.title) {
        lines.push({
            key: 'reply',
            label: reply.direction === 'out' ? 'In reply to your' : 'In reply to their',
            title: reply.title,
            display: shortTitle(reply.title),
            projectId: null,
        })
    }
    // The title and project come with the message, read live from the session
```

The existing local-route push stays unchanged. The reply push comes first, so the template's existing `v-for="route in routes"` renders it above the local end.

- [ ] **Step 2: Add the same reply line to the detail snapshot**

In `frontend/src/components/peer/PeerMessageReviewDialog.vue`, find the exact end of `localRoute`:

```javascript
const localRoute = computed(() => {
    // Title and project ride with the message, read live from the session row
    // server-side — never resolved against the front's store, and never an id.
    // A session that no longer exists (FK nulled) shows no line at all.
    const local = isInbound.value ? detail.value?.delivered_to_session : detail.value?.origin_session
    if (!local) return null
    return {
        label: isInbound.value ? 'Delivered to session' : 'Sent from session',
        title: local.title || 'Untitled session',
        projectId: local.project_id || null,
        sessionId: local.id,
    }
})
```

Add this computed immediately after it:

```javascript
const replyRoute = computed(() => {
    const reply = detail.value?.reply_to_ref
    if (!reply?.title) return null
    return {
        label: reply.direction === 'out' ? 'In reply to your' : 'In reply to their',
        title: reply.title,
    }
})
```

Find this exact template block:

```vue
            <!-- Where it went / came from. The status tag above already names
                 the state, so this line states the place, nothing else. -->
            <p v-if="localRoute" class="pr-route">
```

Replace it with:

```vue
            <!-- Which message this one answers, then where it went / came
                 from. Both use the inbox row's label-then-value vocabulary. -->
            <p v-if="replyRoute" class="pr-route">
                <span class="pr-route__label">{{ replyRoute.label }}</span>
                <span class="pr-route__title" :title="replyRoute.title">“{{ replyRoute.title }}”</span>
            </p>
            <p v-if="localRoute" class="pr-route">
```

- [ ] **Step 3: Change only the incoming toast title**

In `frontend/src/composables/useWebSocket.js`, replace this exact block:

```javascript
                    toast.custom(PeerToastContent, {
                        type: 'info',
                        title: `Message from ${peerName}`,
                        duration: Infinity,
                        props: { mode: 'message', message: msg.message },
                    })
```

with:

```javascript
                    toast.custom(PeerToastContent, {
                        type: 'info',
                        title: `${msg.message?.reply_to_ref ? 'Reply' : 'Message'} from ${peerName}`,
                        duration: Infinity,
                        props: { mode: 'message', message: msg.message },
                    })
```

Do not change the toast's `read()` function or any `PeerToastContent.vue` action.

- [ ] **Step 4: Run the frontend suite and every Vite build**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && cd frontend && npm test && npm run build
```

Expected: PASS. The build fails if either Vue edit has invalid syntax or if the WebSocket edit breaks a standalone bundle. The helper suite remains green and proves Task 3 did not weaken target eligibility or pagination behavior.

- [ ] **Step 5: Perform the inbox, toast, and approval-gate checks**

Use the running development UI and a paired development instance. Perform spec checks M15 and M19 through M21 exactly.

Required observations:

- M15: the open dialog keeps the old reply line and target. Close and reopen shows the new reply line and target.
- M19: inbox count stays equal to the recorded message count. Every message keeps one row. Inbound and outbound replies show `your` or `their` from the parent's direction on both row and dialog. A root or empty parent title shows no reply line.
- M20: an incoming reply toast says `Reply from <peer>`. A root toast says `Message from <peer>`. Clicking the reply toast only opens the review dialog. The message stays pending. No composer opens.
- M21: a pending reply with an eligible target remains pending until the human clicks the existing-session delivery action. The click creates an unsent composer draft. It never sends the draft or starts the agent.

Expected: every listed observation occurs. A live reply-line change or a stale reopened parent snapshot fails M15. A grouped or missing row fails M19. A toast action that resolves or routes fails M20. Any resolution before the explicit delivery click, automatic draft send, or agent start fails M21 and blocks the lot.

- [ ] **Step 6: Run the final scope and diff checks**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/peer-system && git diff --check && git status --short
```

Expected: `git diff --check` exits 0. Before the task commit, `git status --short` lists only the three Task 3 frontend files. Any backend, documentation, skill, package, lockfile, generated bundle, or unrelated file blocks the commit.

- [ ] **Step 7: Commit Task 3**

Working directory: `/home/twidi/dev/twicc-poc/.worktrees/peer-system`

Stage:

```text
frontend/src/components/peer/PeerInboxRow.vue
frontend/src/components/peer/PeerMessageReviewDialog.vue
frontend/src/composables/useWebSocket.js
```

Commit subject:

```text
feat(peer): show message reply relationships
```
