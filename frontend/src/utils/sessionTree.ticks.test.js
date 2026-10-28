// The session tree on active and `user_turn` ticks (design:
// docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 3.7 follow-up).
//
// `buildSessionTree` reads `mtime` only for inactive subtrees, and the sidebar
// gives it the per-session `unread` memo, so a tick of an active session, or of
// a `user_turn` session that stays unread, rebuilds no tree. The parity oracle
// is the frozen baseline `buildSessionTree` (tests/helpers/sidebarBaseline/).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { computed, effect, toRaw } from 'vue'
import { buildSessionTree } from './sessionTree.js'
import { buildSessionTree as baselineBuildSessionTree } from '../../tests/helpers/sidebarBaseline/sessionTree.js'
import { computeSidebarSessionBlocks, useSidebarSessionBlocks } from './sidebarSessions.js'
import { getUnreadMemos } from './unreadMemos.js'
import { isSessionUnread } from './sessions.js'
import { buildSidebarState, createShippedData, tick, applyStep, FIXTURE_IDS, FIXTURE_SCOPES,
    FIXTURE_UPDATE_SEQUENCE } from '../../tests/helpers/sidebarFixture.js'

const ID = FIXTURE_IDS
const SCOPES = ['p0', 'ws:w']

const blockOptions = (data, workspaces, effectiveProjectId) => ({
    data, workspaces, effectiveProjectId, activeWorkspaceId: null, sessionId: null,
    showArchived: false, showArchivedProjects: false, showActiveAcrossFilters: true, showAsTree: true,
})

/** An ISO time 5 seconds before the session's last content: the session reads as unread. */
const unreadViewedAt = (session) => new Date(Date.parse(session.last_new_content_at) - 5000).toISOString()

/**
 * The fixture with spawned_by chains inside p0 (own setup, before any counter):
 * - `processAssistant` (assistant_turn) is a child of `p0Recent` (no process): an active child
 *   under an active-by-descendant ancestor;
 * - `leaf` (the most recent regular p0 root, no process) is a child of `processAssistant`: an
 *   inactive leaf inside an active subtree; it is made unread, so its tick does not flip it;
 * - `processShell` (user_turn, background shell) is a child of `p0Second`; it is made unread,
 *   so its tick does not flip it;
 * - `child` -> `ancestor` comes from the fixture (both read, no process): an inactive subtree.
 */
function treeFixture() {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 40 })
    const leaf = Object.values(state.sessions)
        .filter(s => !s.parent_session_id && s.project_id === 'p0' && /^r\d+$/.test(s.id))
        .sort((a, b) => b.mtime - a.mtime)[0]
    state.sessions[ID.processAssistant].spawned_by = ID.p0Recent
    state.sessions[leaf.id].spawned_by = ID.processAssistant
    state.sessions[leaf.id].last_viewed_at = unreadViewedAt(state.sessions[leaf.id])
    state.sessions[ID.processShell].spawned_by = ID.p0Second
    state.sessions[ID.processShell].last_viewed_at = unreadViewedAt(state.sessions[ID.processShell])
    return { state, leafId: leaf.id }
}

/** Replace `field` of the raw record by a counting accessor. */
function countReads(record, field) {
    const raw = toRaw(record)
    let value = raw[field]
    const counter = { reads: 0 }
    Object.defineProperty(raw, field, {
        configurable: true, enumerable: true,
        get() { counter.reads++; return value },
        set(next) { value = next },
    })
    return counter
}

/** The comparable shape of a tree: every field the sidebar reads, `dateKey` of inactive nodes only. */
function treeShape(tree) {
    const nodes = {}
    for (const [id, node] of tree.nodes) {
        const { pinned, active, activeKey, dateKey } = node.effective
        nodes[id] = {
            parentId: node.parentId, childIds: node.childIds, hintParent: node.hintParent?.id ?? null,
            pinned, active, activeKey, dateKey: active ? 'active' : dateKey,
            descendantIds: node.descendantIds, triggerIds: node.triggerIds,
        }
    }
    return { rootIds: tree.rootIds, nodes }
}

test('buildSessionTree reads no mtime of an active child nor of its active ancestor', () => {
    for (const scope of SCOPES) {
        const { state, leafId } = treeFixture()
        const { data, workspaces } = createShippedData(state)
        const { natural } = computeSidebarSessionBlocks(blockOptions(data, workspaces, scope))
        for (const id of [ID.processAssistant, ID.p0Recent, ID.processShell, ID.p0Second, leafId]) {
            assert.ok(natural.some(s => s.id === id), `${scope}: ${id} is a tree candidate`)
        }
        // Counters installed after the own setup; the baseline reads `mtime` first for every node.
        const activeChild = countReads(state.sessions[ID.processAssistant], 'mtime')
        const activeAncestor = countReads(state.sessions[ID.p0Recent], 'mtime')
        const userTurnChild = countReads(state.sessions[ID.processShell], 'mtime')
        const inactiveLeaf = countReads(state.sessions[leafId], 'mtime')
        const tree = buildSessionTree(natural, {
            lookup: id => state.sessions[id] ?? null, processStates: state.processStates, isUnread: isSessionUnread,
        })
        assert.equal(tree.nodes.get(ID.processAssistant).parentId, ID.p0Recent, `${scope}: active child nests`)
        assert.equal(tree.nodes.get(leafId).parentId, ID.processAssistant, `${scope}: inactive leaf nests`)
        assert.equal(tree.nodes.get(ID.p0Recent).effective.active, true, `${scope}: ancestor is active`)
        assert.equal(activeChild.reads, 0, `${scope}: active child mtime`)
        assert.equal(activeAncestor.reads, 0, `${scope}: active ancestor mtime`)
        assert.equal(userTurnChild.reads, 0, `${scope}: user_turn child mtime`)
        // Positive control: the same probe on an inactive leaf counts its read.
        assert.ok(inactiveLeaf.reads > 0, `${scope}: inactive leaf mtime is read`)
        assert.equal(tree.nodes.get(ID.p0Recent).effective.dateKey, null, `${scope}: active subtree has no dateKey`)
    }
})

test('the tree equals the baseline tree in every fixture scope along the update sequence', () => {
    for (const scope of FIXTURE_SCOPES) {
        const { state } = treeFixture()
        const { data, workspaces } = createShippedData(state)
        const memos = getUnreadMemos(data)
        const check = (label) => {
            const { natural } = computeSidebarSessionBlocks(blockOptions(data, workspaces, scope))
            const lookup = id => state.sessions[id] ?? null
            const expected = baselineBuildSessionTree(natural, {
                lookup, processStates: state.processStates, viewedId: ID.p0Second, isUnread: isSessionUnread,
            })
            const actual = buildSessionTree(natural, {
                lookup, processStates: state.processStates, viewedId: ID.p0Second,
                isUnread: session => memos.unread(session),
            })
            assert.deepEqual(treeShape(actual), treeShape(expected), `${String(scope)}: ${label}`)
        }
        check('initial')
        for (const step of FIXTURE_UPDATE_SEQUENCE) {
            applyStep(state, step)
            check(step.name)
        }
    }
})

/**
 * The tree the way SessionList.vue builds it: a computed over the natural block of
 * `useSidebarSessionBlocks`, with the unread memo of the data store. One subscriber
 * reads it once per flush, like the list's watchers; `runs()` counts the rebuilds.
 */
function mountTree(state, scope) {
    const { data, workspaces } = createShippedData(state)
    const { blocks } = useSidebarSessionBlocks(() => blockOptions(data, workspaces, scope))
    const natural = computed(() => blocks.value.natural)
    let runs = 0
    const tree = computed(() => {
        runs++
        return buildSessionTree(natural.value, {
            lookup: id => data.sessions[id] ?? null,
            processStates: data.processStates,
            viewedId: null,
            isUnread: session => getUnreadMemos(data).unread(session),
        })
    })
    let dirty = false
    const runner = effect(() => { tree.value }, { scheduler() { dirty = true } })
    return {
        tree,
        runs: () => runs,
        flush() { if (dirty) { dirty = false; runner() } },
        stop: () => runner.effect.stop(),
    }
}

test('an active child tick and a user_turn tick without flip rebuild no tree', () => {
    for (const scope of SCOPES) {
        const { state, leafId } = treeFixture()
        const view = mountTree(state, scope)
        const ids = new Set(view.tree.value.nodes.keys())
        for (const id of [ID.processAssistant, ID.processShell, leafId]) assert.ok(ids.has(id), `${scope}: ${id} in the tree`)
        const order = JSON.stringify(view.tree.value.rootIds)
        const measure = (label, apply) => {
            const before = view.runs()
            apply()
            view.flush()
            return view.runs() - before
        }

        // assistant_turn child: never unread, so the tick flips nothing.
        assert.equal(measure('active', () => tick(state, ID.processAssistant)), 0, `${scope}: active child tick`)
        // user_turn child, already unread (own setup): the tick keeps it unread.
        assert.equal(isSessionUnread(state.sessions[ID.processShell], state.processStates[ID.processShell]), true)
        assert.equal(measure('user_turn', () => tick(state, ID.processShell)), 0, `${scope}: user_turn tick without flip`)
        assert.equal(JSON.stringify(view.tree.value.rootIds), order, `${scope}: same root order`)

        // Positive control: the inactive leaf (already unread, own setup) is ticked; its mtime is a tree input.
        assert.ok(measure('inactive', () => tick(state, leafId)) >= 1, `${scope}: inactive tick rebuilds`)
        // Positive control: the user_turn child flips to read (its last_viewed_at follows its content).
        assert.ok(measure('flip', () => {
            state.sessions[ID.processShell].last_viewed_at = state.sessions[ID.processShell].last_new_content_at
        }) >= 1, `${scope}: user_turn unread flip rebuilds`)
        assert.equal(isSessionUnread(state.sessions[ID.processShell], state.processStates[ID.processShell]), false)
        view.stop()
    }
})
