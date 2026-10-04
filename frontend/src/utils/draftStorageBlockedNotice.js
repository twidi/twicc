// frontend/src/utils/draftStorageBlockedNotice.js
// Startup notice for a blocked draft-storage upgrade (spec 2026-10-03 §9.1).
//
// The draft hydration waits for the IndexedDB upgrade. An older TwiCC tab that
// keeps the previous version open blocks it, and that tab has no handler that
// closes its connection: only the user can close it. The bootstrap shows this
// notice before Vue mounts, so it uses plain DOM and no Vue component.

export const DRAFT_STORAGE_BLOCKED_MESSAGE = 'Draft storage upgrade blocked. Close other TwiCC tabs, then keep this page open.'

/**
 * Show the notice while the upgrade is blocked, and hide it once it is not.
 *
 * @param {(callback: (blocked: boolean) => void) => (() => void)} subscribe
 *     - `subscribeDraftStorageBlocked` of `draftStorage.js`
 * @param {Document} [doc]
 * @returns {() => void} unsubscribe and remove the notice (call before the app mount)
 */
export function installDraftStorageBlockedNotice(subscribe, doc = document) {
    let notice = null

    function show() {
        if (notice) return
        notice = doc.createElement('div')
        notice.setAttribute('role', 'alert')
        notice.setAttribute('aria-live', 'assertive')
        Object.assign(notice.style, {
            position: 'fixed',
            top: '1rem',
            left: '50%',
            transform: 'translateX(-50%)',
            maxWidth: 'min(520px, calc(100vw - 2rem))',
            padding: '1rem 1.25rem',
            borderRadius: '12px',
            background: '#422006',
            border: '1px solid #854d0e',
            color: '#fcd34d',
            fontFamily: 'system-ui, sans-serif',
            lineHeight: '1.5',
            zIndex: '2147483647',
        })
        notice.textContent = DRAFT_STORAGE_BLOCKED_MESSAGE
        doc.body.appendChild(notice)
    }

    function hide() {
        notice?.remove()
        notice = null
    }

    const unsubscribe = subscribe(blocked => (blocked ? show() : hide()))
    return () => {
        unsubscribe()
        hide()
    }
}
