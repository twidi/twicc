import { computed, reactive, toRaw } from 'vue'
import { hasUnreadContent, isSessionUnread } from './sessions.js'
import { noteStageRun } from './stageRuns.js'

// Per-session unread memos (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 5.5).
// One memo set per raw `sessions` object; inside it, one lazily created
// computed per memo and per raw session record. A removed record releases its
// memos (WeakMap keys); `updateSession` keeps the record object, so a memo
// lives as long as its record.
//
// The two memos stay apart on purpose: for the open session `hasUnreadContent`
// flips back and forth while its process is in `assistant_turn`, and
// `isSessionUnread` stays false meanwhile. A combined memo would change on each
// of those flips.
const memoSets = new WeakMap()

/**
 * The unread memos of the sessions of `owner`. Each memo returns a boolean and
 * reads through the reactive record, whatever reference (raw or reactive) the
 * caller looks it up with, so a memo first evaluated during startup with a raw
 * record still tracks the record after startup. A timestamp change that does
 * not flip the result stops at the memo.
 *
 * - `unread(session)`: `isSessionUnread(session, owner.processStates[session.id])`.
 * - `unreadContent(session)`: `hasUnreadContent(session)`.
 *
 * @param {{ sessions: Object, processStates: Object }} owner - a store or its state
 * @returns {{ unread: (session: Object) => boolean, unreadContent: (session: Object) => boolean }}
 */
export function getUnreadMemos(owner) {
    const key = toRaw(owner.sessions)
    let memos = memoSets.get(key)
    if (!memos) {
        memos = createMemoSet(owner)
        memoSets.set(key, memos)
    }
    return memos
}

function createMemoSet(owner) {
    const unreadBySession = new WeakMap()
    const unreadContentBySession = new WeakMap()

    function memoOf(bySession, session, build) {
        const raw = toRaw(session)
        let memo = bySession.get(raw)
        if (!memo) {
            memo = build(reactive(raw))
            bySession.set(raw, memo)
        }
        return memo.value
    }

    return {
        unread(session) {
            if (!session) return false
            return memoOf(unreadBySession, session, record => computed(() => {
                noteStageRun('memo:unread')
                return isSessionUnread(record, owner.processStates[record.id])
            }))
        },
        unreadContent(session) {
            if (!session) return false
            return memoOf(unreadContentBySession, session, record => computed(() => {
                noteStageRun('memo:unreadContent')
                return hasUnreadContent(record)
            }))
        },
    }
}
