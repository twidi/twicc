// Tab-panel crossfade (visual refresh step 5c): a user's switch on a tab bar runs in a view
// transition, so the old panel fades out under the new one.
// Design: docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §12.4.
//
// Web Awesome switches the panels synchronously in its own click / keydown handlers
// (setActiveTab), before the transition could capture the old state: the switch is
// deferred into the transition's update callback by shadowing the host's setActiveTab.
// Only a gesture on a tab of this bar arms it (Web Awesome's own test): a click or a key
// inside a panel, a tab's close icon or its menu never do, and neither does any
// programmatic switch (route, palette, shortcut).

import { runViewTransition } from './viewTransition.js'

const ARMING_KEYS = new Set(['Enter', ' ', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'])

const isTagName = (el, name) => typeof el?.tagName === 'string' && el.tagName.toLowerCase() === name

/** Wrap `host.setActiveTab` (a wa-tab-group); returns uninstall(). */
export function installTabCrossfade(host, { onStart, run = runViewTransition, env = globalThis }) {
    const original = host.setActiveTab.bind(host)
    let armed = false
    let armedTab = null
    let fallback = null

    function disarm() {
        armed = false
        armedTab = null
        if (fallback !== null) env.clearTimeout(fallback)
        fallback = null
    }

    function arm(tab) {
        disarm()
        armed = true
        armedTab = tab
        // Fallback: the bubble listener normally disarms in the same dispatch.
        fallback = env.setTimeout(disarm, 0)
    }

    function onCaptureClick(event) {
        const target = event.target
        const tab = target?.closest?.('wa-tab')
        if (tab?.closest('wa-tab-group') !== host) return
        // A dock tab's placement menu stops its click (the bubble disarm would not run).
        if (target.closest('.tab-close-icon, wa-dropdown')) return
        arm(tab)
    }

    function onCaptureKeydown(event) {
        const target = event.target
        if (!isTagName(target, 'wa-tab') || target.closest('wa-tab-group') !== host) return
        if (!ARMING_KEYS.has(event.key)) return
        // The key picks another tab.
        arm(null)
    }

    host.setActiveTab = function setActiveTab(tab, options) {
        if (!armed || !tab || tab === host.activeTab || tab.disabled
            || tab.closest?.('wa-tab-group') !== host || (armedTab && armedTab !== tab)) {
            return original(tab, options)
        }
        disarm()
        onStart?.()
        run(() => original(tab, options), { kind: 'tab', settle: true, env })
    }

    host.addEventListener('click', onCaptureClick, true)
    host.addEventListener('keydown', onCaptureKeydown, true)
    host.addEventListener('click', disarm, false)
    host.addEventListener('keydown', disarm, false)

    return function uninstall() {
        disarm()
        delete host.setActiveTab
        host.removeEventListener('click', onCaptureClick, true)
        host.removeEventListener('keydown', onCaptureKeydown, true)
        host.removeEventListener('click', disarm, false)
        host.removeEventListener('keydown', disarm, false)
    }
}
