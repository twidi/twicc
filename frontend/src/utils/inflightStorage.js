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

import { getDb, INFLIGHT_SENDS_STORE, inflightSnapshotRecord } from './draftStorage.js'

/**
 * The stored form of an in-flight send snapshot (see
 * `inflightSnapshotRecord` in draftStorage.js, the single implementation).
 */
export const toStoredInflightSend = inflightSnapshotRecord

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
