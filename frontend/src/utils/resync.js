// frontend/src/utils/resync.js
//
// Handles the backend's `resync_required` frame.
//
// The server sends it when its WebSocket layer had to discard updates for this
// client (see `twicc/channel_layer.py`). At that point the local state is torn
// in a way we cannot describe: the lost frames could have been sessions, items,
// process states, tool links, settings, workspaces — anything. A reconciliation
// only re-reads part of that, so the only honest repair is a full reload, which
// rebuilds everything from REST and opens a fresh connection with an empty
// queue.
//
// Two guards keep that from making things worse:
//
// - The loss happens because the client could not keep up. Reloading gives it
//   *more* work, so a client that is chronically behind would reload in a loop.
//   At most one automatic reload per RELOAD_COOLDOWN_MS; beyond that the user
//   is told and decides.
// - A hidden tab is not reloaded. It would re-download and re-render for
//   nobody, and it is the tab most likely to have fallen behind in the first
//   place. It reloads when the user comes back to it.

// Survives the reload it triggers, and is per-tab — exactly the scope of the
// decision. localStorage would let one tab's reload suppress another's.
const LAST_RELOAD_KEY = 'twicc:lastResyncReload'

export const RELOAD_COOLDOWN_MS = 5 * 60 * 1000

/**
 * Decide what to do about a `resync_required` frame.
 *
 * Pure, so the policy can be tested without a browser: every input is passed
 * in and the answer is a plain string.
 *
 * @param {Object} ctx
 * @param {boolean} ctx.visible    - Is the tab in the foreground?
 * @param {?number} ctx.lastReload - Epoch ms of the last automatic reload, or null.
 * @param {number} ctx.now         - Epoch ms.
 * @returns {'reload'|'defer'|'ask'}
 *   `reload` — do it now. `defer` — wait until the tab is visible again.
 *   `ask` — too soon after the last one; hand the decision to the user.
 */
export function decideResyncAction({ visible, lastReload, now }) {
    if (lastReload != null && now - lastReload < RELOAD_COOLDOWN_MS) return 'ask'
    return visible ? 'reload' : 'defer'
}

function readLastReload() {
    const raw = sessionStorage.getItem(LAST_RELOAD_KEY)
    const value = raw == null ? NaN : Number(raw)
    return Number.isFinite(value) ? value : null
}

function reloadNow() {
    sessionStorage.setItem(LAST_RELOAD_KEY, String(Date.now()))
    window.location.reload()
}

let deferred = false

/**
 * Act on a `resync_required` frame. Idempotent: repeated frames while a reload
 * is already pending do nothing.
 * @param {string} [reason] - Why the server broke the stream (for the toast).
 */
export function handleResyncRequired(reason) {
    if (deferred) return

    const action = decideResyncAction({
        visible: document.visibilityState === 'visible',
        lastReload: readLastReload(),
        now: Date.now(),
    })

    console.warn(`[resync] server dropped updates for this client (${reason || 'unknown reason'}) → ${action}`)

    if (action === 'reload') {
        reloadNow()
        return
    }

    if (action === 'defer') {
        deferred = true
        document.addEventListener('visibilitychange', function onVisible() {
            if (document.visibilityState !== 'visible') return
            document.removeEventListener('visibilitychange', onVisible)
            reloadNow()
        })
        return
    }

    // 'ask' — a second loss inside the cooldown. Reloading again would very
    // likely loop, so stop and surface it. Persistent on purpose: until the
    // page is reloaded this tab shows stale data, and that must not scroll away.
    showResyncToast()
}

function showResyncToast() {
    // Lazy import: this module is pulled in by the WebSocket layer, and
    // useToast reaches back into the stores.
    import('../composables/useToast').then(({ toast }) => {
        toast.custom({
            type: 'warning',
            title: 'Updates were lost',
            duration: Infinity,
            html: `
                <div style="display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.25rem;">
                    <span>This tab is out of sync with the server and stopped receiving updates.</span>
                    <a href="#" onclick="window.location.reload(); return false;"
                       style="color: var(--wa-color-text-link); text-decoration: underline;">Reload now</a>
                </div>
            `,
        })
    })
}

/** Test seam: forget the pending-reload latch. */
export function __resetResyncState() {
    deferred = false
}
