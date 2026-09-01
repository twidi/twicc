// frontend/src/providers/serviceStatusToast.js
//
// The provider-status toasts, as a function of ``providers-status.json``.
//
// ``reconcileProviderStatusToasts`` is called with the whole records map every
// time it changes (connect, reconnect, status change, acknowledgment from any
// tab or device) and makes the screen match it: at most one toast per
// provider, showing the record's current episode, and none once that episode
// is acknowledged or has nothing left to say. The rules are the pure ones in
// ``utils/providersStatus.js``; this module only owns the Notivue items.
//
// The status itself is polled by the BACKEND (``providers/statuspage_task``);
// nothing here ever fetches.

import { defineAsyncComponent } from 'vue'
import { toast } from '../composables/useToast.js'
import { planToast, toastTitle } from '../utils/providersStatus.js'

// Lazy body, like the peer toasts: keeps the component out of the main chunk
// and breaks the component → this module → component cycle.
const ProviderStatusToastContent = defineAsyncComponent(
    () => import('../components/app/ProviderStatusToastContent.vue'),
)

// One-off cleanup of the per-browser dedup keys the previous implementation
// kept in localStorage (the "have I shown this" memory now lives in
// providers-status.json, server-side and synced). Optional chaining: no
// localStorage under node --test.
for (const key of ['twicc-claude-status', 'twicc-claude-code-anthropic-status', 'twicc-codex-openai-status']) {
    globalThis.localStorage?.removeItem?.(key)
}

// provider → { key, item }: the one toast currently standing for it.
const _active = new Map()

// Items we are clearing ourselves — a newer episode replacing them, or an
// acknowledgment that arrived from elsewhere. The content component checks
// this in its unmount hook so only a clear by the user counts as a dismissal.
const _programmatic = new WeakSet()

function _clearProgrammatically(entry) {
    if (!entry?.item) return
    _programmatic.add(entry.item)
    entry.item.clear?.()
}

/** Whether ``item`` is being cleared by this module rather than by the user. */
export function isProgrammaticClear(item) {
    return !!item && _programmatic.has(item)
}

/**
 * Forget a toast the user just closed, so a later reconcile does not try to
 * clear a dead item. Called by the content component on unmount.
 */
export function forgetProviderStatusToast(provider, item) {
    if (_active.get(provider)?.item === item) _active.delete(provider)
}

/**
 * Make the toasts match ``records``.
 *
 * @param {Object} records - ``{ <provider>: record }`` from the store.
 * @param {Object} options
 * @param {(provider: string) => ({ productLabel, vendorLabel, statusUrl }|null)} options.identity
 *   How to name a provider in its toast; ``null`` means "no toast for this
 *   provider" (unknown, disabled, or no status page) — an existing one is cleared.
 * @param {number} [options.now] - Epoch ms, for the resolved-announce window.
 */
export function reconcileProviderStatusToasts(records, { identity, now = Date.now() }) {
    const providers = new Set([...Object.keys(records ?? {}), ..._active.keys()])
    for (const provider of providers) {
        const who = identity(provider)
        const plan = who ? planToast(records?.[provider], now) : null
        const active = _active.get(provider)

        if (!plan) {
            if (active) {
                _clearProgrammatically(active)
                _active.delete(provider)
            }
            continue
        }
        if (active?.key === plan.key) continue

        // A different episode: the standing toast is stale, whatever it said.
        if (active) _clearProgrammatically(active)
        const item = toast.custom(ProviderStatusToastContent, {
            type: plan.type,
            title: toastTitle(who.vendorLabel),
            duration: Infinity,
            props: { provider, episode: plan.episode, identity: who },
        })
        _active.set(provider, { key: plan.key, item })
    }
}

/** Test seam: the standing toasts, ``{ <provider>: key }``. */
export function __activeProviderStatusToasts() {
    return Object.fromEntries([..._active].map(([provider, { key }]) => [provider, key]))
}
