// frontend/src/utils/inflightStorage.js
// IndexedDB persistence for in-flight send snapshots (send-failure recovery).
//
// A snapshot is written when a send_message frame leaves the socket. It is
// deleted when the send is confirmed delivered (a matching user_message line
// arrived) or when a surfaced failure is restored/dismissed — NOT when the
// failure is merely surfaced, so an unhandled failure callout survives a page
// reload. Persisting snapshots lets a killed or frozen tab rediscover sends
// whose error frame was lost along with the WebSocket: the audit in the data
// store re-checks them against the session's items at opening time.

import { getDb, INFLIGHT_SENDS_STORE } from './draftStorage.js'

// Above this combined legacy attachment payload (chars of encoded data), the
// snapshot is persisted without its medias (``mediasDropped: true``): after
// a reload only the text can be restored. Legacy drafts allow up to 32 MB of
// attachments — blindly writing that on EVERY send would bloat IndexedDB
// for a rarely-needed safety net. Composer attachments (``attachments``) are
// staged refs with metadata only: no bytes, so no cap.
const MEDIA_PERSIST_LIMIT = 8 * 1024 * 1024

/**
 * The stored form of an in-flight send snapshot. Medias and attachment
 * metadata are copied to plain objects (the store hands out Vue reactive
 * proxies, which the structured-clone algorithm rejects).
 *
 * - `attachments` ({bucket, id, name, size, mimeType, kind}): composer sends,
 *   kept whole (spec 2026-10-03 §9.5);
 * - `medias`: legacy sends (Retry of an older snapshot), capped as before.
 *
 * @param {Object} snapshot
 * @returns {Object}
 */
export function toStoredInflightSend(snapshot) {
    const stored = { ...snapshot }
    if (Array.isArray(snapshot.attachments)) {
        stored.attachments = snapshot.attachments.map(attachment => ({
            bucket: attachment.bucket,
            id: attachment.id,
            name: attachment.name,
            size: attachment.size,
            mimeType: attachment.mimeType,
            kind: attachment.kind,
        }))
    }
    const medias = (snapshot.medias || []).map(media => ({ ...media }))
    const mediaBytes = medias.reduce((sum, media) => sum + (media.data?.length || 0), 0)
    // ``mediaCount`` survives the drop: a message made only of attachments has
    // no text, and the count is what identifies it when the store matches a
    // rediscovered snapshot against the session's user_message lines.
    if (mediaBytes > MEDIA_PERSIST_LIMIT) {
        return { ...stored, medias: [], mediasDropped: true, mediaCount: medias.length }
    }
    // A re-saved snapshot whose medias were already dropped keeps its count.
    return { ...stored, medias, mediaCount: medias.length || snapshot.mediaCount || 0 }
}

/**
 * Persist an in-flight send snapshot (see `toStoredInflightSend`).
 * @param {string} requestId
 * @param {Object} snapshot - { sessionId, text, attachments, medias, optimisticShown, startingSet, sentAt }
 * @returns {Promise<void>}
 */
export async function saveInflightSend(requestId, snapshot) {
    const db = await getDb()
    const toStore = toStoredInflightSend(snapshot)
    return new Promise((resolve, reject) => {
        const tx = db.transaction(INFLIGHT_SENDS_STORE, 'readwrite')
        const request = tx.objectStore(INFLIGHT_SENDS_STORE).put(toStore, requestId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete a persisted in-flight send snapshot.
 * @param {string} requestId
 * @returns {Promise<void>}
 */
export async function deleteInflightSend(requestId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(INFLIGHT_SENDS_STORE, 'readwrite')
        const request = tx.objectStore(INFLIGHT_SENDS_STORE).delete(requestId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all persisted in-flight send snapshots (app-startup hydration).
 * @returns {Promise<Object>} Object mapping requestId to snapshot
 */
export async function getAllInflightSends() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(INFLIGHT_SENDS_STORE, 'readonly')
        const store = tx.objectStore(INFLIGHT_SENDS_STORE)
        const snapshots = {}
        const request = store.openCursor()
        request.onsuccess = (event) => {
            const cursor = event.target.result
            if (cursor) {
                snapshots[cursor.key] = cursor.value
                cursor.continue()
            } else {
                resolve(snapshots)
            }
        }
        request.onerror = () => reject(request.error)
    })
}
