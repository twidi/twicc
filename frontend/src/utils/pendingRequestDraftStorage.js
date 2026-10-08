// frontend/src/utils/pendingRequestDraftStorage.js
// IndexedDB persistence for in-progress answers to a pending request.
//
// A pending request (question widget, MCP elicitation form, tool approval) only
// exists while the agent process is alive, but the answer the user is typing
// into it is lost on any page reload — unlike the composer draft, which the
// data store already persists. This module gives that form the same safety net.
//
// Keyed by (sessionId, requestId). A request id is unique and never reused:
// Claude Code SDK mints a uuid4 per `can_use_tool` call, hybrid mode uses the
// hook drop-file nonce (suffixed with a nanosecond timestamp), and Codex passes
// through its own `approvalId`/`itemId`. So a restarted process asking a
// *different* question can never match a stored draft — the mismatch case needs
// no special handling. `payloadHash` is the belt-and-braces check: the hash of
// the serialized request as the client received it, compared on restore.
//
// The entry is deleted as soon as the user answers — whatever the button
// (approve, deny, submit, decline, cancel, dismiss). Drafts of requests that
// never came back (the process restarted or died) are collected by
// `sweepPendingRequestDrafts`, driven from the data store.

import { getDb, PENDING_REQUEST_DRAFTS_STORE } from './draftStorage'
import { hashString } from './hash'
import { withoutAutoDenyKeys } from './autoDeny'

// Above this serialized size (chars), the draft is not written at all. The
// answer fields themselves are tiny; the one field that can blow up is Claude's
// "Approve with changes" edited tool input (e.g. a Write tool carrying a whole
// file). Re-writing that on every keystroke would hammer IndexedDB for a rarely
// needed safety net.
const MAX_STATE_CHARS = 512 * 1024

/**
 * Hash of a pending request as the client received it, used to confirm on
 * restore that the stored draft belongs to this exact request.
 *
 * The whole object is hashed except the auto-deny deadline keys: the local
 * deadline is computed at reception, so it differs between two page loads
 * and would make every stored draft unrestorable. `request_id` and
 * `created_at` are fixed for the lifetime of a request, so including them
 * costs nothing.
 *
 * @param {Object} pendingRequest - The pending request object from the store
 * @returns {string} base36 hash
 */
export function hashPendingRequest(pendingRequest) {
    return hashString(JSON.stringify(withoutAutoDenyKeys(pendingRequest)))
}

/**
 * Persist the in-progress answer state for a pending request.
 *
 * @param {string} sessionId
 * @param {string} requestId
 * @param {string} payloadHash - From {@link hashPendingRequest}
 * @param {Object} state - Plain, structured-clonable answer state
 * @returns {Promise<boolean>} false when the state was too large to store
 */
export async function savePendingRequestDraft(sessionId, requestId, payloadHash, state) {
    // Vue hands out reactive proxies, which the structured-clone algorithm
    // rejects — round-tripping through JSON both plain-ifies the state and
    // gives us its size for free.
    const serialized = JSON.stringify(state)
    if (serialized === undefined) return false
    if (serialized.length > MAX_STATE_CHARS) return false

    const db = await getDb()
    const record = {
        sessionId,
        requestId,
        payloadHash,
        savedAt: Date.now(),
        state: JSON.parse(serialized),
    }
    return new Promise((resolve, reject) => {
        const tx = db.transaction(PENDING_REQUEST_DRAFTS_STORE, 'readwrite')
        const request = tx.objectStore(PENDING_REQUEST_DRAFTS_STORE).put(record)
        request.onsuccess = () => resolve(true)
        request.onerror = () => reject(request.error)
    })
}

/**
 * Read back the in-progress answer state for a pending request.
 *
 * Returns null when nothing is stored, or when the stored `payloadHash` does
 * not match the request being restored (a stale entry under a reused id).
 *
 * @param {string} sessionId
 * @param {string} requestId
 * @param {string} payloadHash - From {@link hashPendingRequest}
 * @returns {Promise<Object|null>} The stored state, or null
 */
export async function getPendingRequestDraft(sessionId, requestId, payloadHash) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(PENDING_REQUEST_DRAFTS_STORE, 'readonly')
        const request = tx.objectStore(PENDING_REQUEST_DRAFTS_STORE).get([sessionId, requestId])
        request.onsuccess = () => {
            const record = request.result
            if (!record || record.payloadHash !== payloadHash) {
                resolve(null)
                return
            }
            resolve(record.state ?? null)
        }
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete the stored draft for a pending request. Called as soon as the user
 * answers, whatever the decision.
 *
 * @param {string} sessionId
 * @param {string} requestId
 * @returns {Promise<void>}
 */
export async function deletePendingRequestDraft(sessionId, requestId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(PENDING_REQUEST_DRAFTS_STORE, 'readwrite')
        const request = tx.objectStore(PENDING_REQUEST_DRAFTS_STORE).delete([sessionId, requestId])
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Key of a live pending request, as {@link sweepPendingRequestDrafts} expects
 * it. NUL separates the two ids: neither a session id nor a request id can
 * contain it, so the concatenation is unambiguous.
 *
 * @param {string} sessionId
 * @param {string} requestId
 * @returns {string}
 */
export function liveDraftKey(sessionId, requestId) {
    return `${sessionId}\u0000${requestId}`
}

/**
 * Drop the drafts that no longer belong to a live pending request.
 *
 * A pending request only exists while its agent process is alive, so a draft
 * outlives its request whenever the process restarts (or dies) — that is the
 * one thing the (session, request) key cannot clean up on its own, since the
 * body that owned it is long unmounted.
 *
 * ``savedBefore`` is what makes this safe to run against a snapshot: an entry
 * written *after* the snapshot was taken cannot possibly appear in it, and must
 * be kept even though it is missing from ``liveKeys``.
 *
 * The whole store is scanned rather than range-queried: it holds a handful of
 * records (one per unanswered request being typed into), so one cursor pass and
 * one code path beat a clever compound-key range.
 *
 * @param {Set<string>} liveKeys - Keys from {@link liveDraftKey} for every live
 *   pending request. An empty set deletes every eligible entry.
 * @param {number} savedBefore - Only entries written strictly before this
 *   timestamp are eligible.
 * @param {string|null} [sessionId] - Restrict the sweep to one session
 *   (null = every session).
 * @returns {Promise<number>} How many entries were deleted
 */
export async function sweepPendingRequestDrafts(liveKeys, savedBefore, sessionId = null) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(PENDING_REQUEST_DRAFTS_STORE, 'readwrite')
        const request = tx.objectStore(PENDING_REQUEST_DRAFTS_STORE).openCursor()
        let deleted = 0
        request.onsuccess = (event) => {
            const cursor = event.target.result
            if (!cursor) {
                resolve(deleted)
                return
            }
            const record = cursor.value
            const inScope = sessionId === null || record.sessionId === sessionId
            const stale = (record.savedAt || 0) < savedBefore
            if (inScope && stale && !liveKeys.has(liveDraftKey(record.sessionId, record.requestId))) {
                cursor.delete()
                deleted++
            }
            cursor.continue()
        }
        request.onerror = () => reject(request.error)
    })
}
