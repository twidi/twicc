// frontend/src/stores/uploads.js
// Uploads store (spec §6.2): a thin Pinia wrapper around the upload lifecycle
// controller of `utils/uploads/controller.js`. It builds the controller with
// every real dependency (tus-js-client, apiFetch, toasts, clock and timers,
// the tab id, the auth store, the shared 401 helper, the app-navigation flag,
// the Screen Wake Lock, the page visibility and the window events) and exposes
// its reactive state and methods.
//
// The store lives outside every component, so an upload survives a session,
// project or layout switch. `useWebSocket.js` reaches it only through a lazy
// `import()`; this module never imports `useWebSocket.js`.

import { defineStore } from 'pinia'
import { Upload } from 'tus-js-client'
import { apiFetch, handleUnauthorized } from '../utils/api'
import { useAuthStore } from './auth'
import { useToast } from '../composables/useToast'
import { generateUUID } from '../utils/crypto'
import { isAppNavigation } from '../utils/appNavigation'
import { createUploadsController } from '../utils/uploads/controller'
import { randomHexFromUUID } from '../utils/uploads/ids'

/** `sessionStorage` key of the tab id (survives a reload of the same tab). */
const TAB_ID_STORAGE_KEY = 'twicc:uploads:tab-id'

/**
 * The tab id: one `generateUUID()` kept in `sessionStorage` (spec §6.2).
 * Falls back to a per-page id when `sessionStorage` is not usable.
 *
 * @returns {string}
 */
function readTabId() {
    try {
        const stored = sessionStorage.getItem(TAB_ID_STORAGE_KEY)
        if (stored) return stored
        const tabId = generateUUID()
        sessionStorage.setItem(TAB_ID_STORAGE_KEY, tabId)
        return tabId
    } catch {
        return generateUUID()
    }
}

/**
 * Adapter over `navigator.wakeLock` (spec §6.12). It keeps the sentinel, so
 * `release()` can release it; `request()` asks again when the browser dropped
 * the lock (page hidden). Null when the API is absent.
 *
 * @returns {{request(): Promise<void>, release(): Promise<void>}|null}
 */
function createWakeLockAdapter() {
    if (typeof navigator === 'undefined' || typeof navigator.wakeLock?.request !== 'function') return null
    let sentinel = null
    let wanted = false
    return {
        async request() {
            wanted = true
            if (sentinel && !sentinel.released) return
            const lock = await navigator.wakeLock.request('screen')
            // Released meanwhile, or another request won the race: drop this one.
            if (!wanted || (sentinel && !sentinel.released)) {
                await lock.release()
                return
            }
            sentinel = lock
        },
        async release() {
            wanted = false
            const lock = sentinel
            sentinel = null
            if (lock && !lock.released) await lock.release()
        },
    }
}

/**
 * Event source of the controller: `visibilitychange` on the document, the
 * other events (`online`, `beforeunload`) on the window.
 */
const pageEvents = {
    on(type, handler) {
        const target = type === 'visibilitychange' ? document : window
        target.addEventListener(type, handler)
        return () => target.removeEventListener(type, handler)
    },
}

function buildController() {
    const authStore = useAuthStore()
    const toast = useToast()
    return createUploadsController({
        apiFetch,
        createTusUpload: (file, options) => new Upload(file, options),
        toast: { success: toast.success, error: toast.error },
        now: () => Date.now(),
        setTimeout: (fn, ms) => window.setTimeout(fn, ms),
        clearTimeout: id => window.clearTimeout(id),
        tabId: readTabId(),
        randomHex: randomHexFromUUID,
        isAuthenticated: () => authStore.authenticated === true,
        onUnauthorized: () => {
            handleUnauthorized().catch(() => {})
        },
        isAppNavigation,
        wakeLock: createWakeLockAdapter(),
        isVisible: () => document.visibilityState === 'visible',
        events: pageEvents,
    })
}

export const useUploadsStore = defineStore('uploads', () => {
    const c = buildController()
    return {
        // State
        entries: c.entries,
        now: c.now,
        // Getter: `"<panel>|<key>" → { count, percent, allStalled }` (§6.11)
        statusByOrigin: c.statusByOrigin,
        // Server records and connection (§6.3)
        applyServerRecord: c.applyServerRecord,
        reconcile: c.reconcile,
        reconnected: c.reconnected,
        // User actions (§6.4–§6.8)
        startUploads: c.startUploads,
        cancel: c.cancel,
        retry: c.retry,
        retryFinalization: c.retryFinalization,
        resume: c.resume,
        autoRestart: c.autoRestart,
        deleteUpload: c.deleteUpload,
        // Completion event (§6.10): `onCompleted(callback) → unsubscribe`
        onCompleted: c.onCompleted,
        // Read helpers for the components
        shouldConfirmUnload: c.shouldConfirmUnload,
        isStalled: c.isStalled,
        entryActions: c.entryActions,
        entriesForOrigin: c.entriesForOrigin,
    }
})

// Dev hot reload (spec §8): a Pinia hot update would keep the old `entries`
// while a new controller is built, so a hot update of this store forces a full
// page reload instead. `invalidate()` alone is not enough: it propagates the
// update to the importers, and the Vue SFCs that import the store are HMR
// boundaries, so the page would not reload.
if (import.meta.hot) {
    import.meta.hot.accept(() => {
        window.location.reload()
    })
}
