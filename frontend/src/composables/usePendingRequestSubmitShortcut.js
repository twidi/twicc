import { onMounted, onBeforeUnmount } from 'vue'

/**
 * Cmd/Ctrl+Enter to validate a pending-request body.
 *
 * The pending-request bodies have no single root element to carry a
 * ``@keydown`` and the shortcut must fire whatever control has focus, so we
 * listen on the document and gate on focus being inside the
 * ``.pending-request-form`` (so it never triggers from an unrelated input
 * elsewhere on the page). Mirrors the Claude body's own ``onSubmitShortcut``.
 *
 * ``onShortcut(event)`` performs the primary action AND its own
 * ``preventDefault``/``stopPropagation`` when it actually acts — so a
 * not-yet-valid form lets the keystroke pass through, exactly like Claude
 * (which returns without preventing default when the form can't submit).
 *
 * @param {(e: KeyboardEvent) => void} onShortcut - primary-action callback.
 * @param {() => boolean} [isResponding] - optional gate; the shortcut is
 *   ignored while it returns true (a response is already in flight).
 */
export function usePendingRequestSubmitShortcut(onShortcut, isResponding) {
    function handler(e) {
        if (e.key !== 'Enter' || !(e.metaKey || e.ctrlKey)) return
        if (isResponding && isResponding()) return
        // Not a form fading out (footer motion): its wrapper carries data-footer-leaving.
        const form = document.querySelector('.pending-request-form:not([data-footer-leaving] *)')
        if (!form || !form.contains(document.activeElement)) return
        onShortcut(e)
    }
    onMounted(() => document.addEventListener('keydown', handler))
    onBeforeUnmount(() => document.removeEventListener('keydown', handler))
}
