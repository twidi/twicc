// wa-tooltip / wa-popover: no anchor lookup while the element is detached.
//
// Both components resolve their `for` anchor with `this.getRootNode().getElementById(this.for)`.
// Lit runs their first update in a microtask. When Vue removes the element before it runs (a
// timeline row that leaves the virtual scroller right after it mounts), the root node is the top
// element of the detached subtree, which has no getElementById, and the update throws. Web Awesome
// 3.14 guards wa-tooltip the same way upstream. wa-tooltip resolves its anchor again in
// connectedCallback; wa-popover only does so when it already had one.

import WaTooltip from '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'
import WaPopover from '@awesome.me/webawesome/dist/components/popover/popover.js'

const GUARDED = Symbol('detached anchor guard')

/** Wrap `handleForChange` of each target (idempotent). Called by the SPA and share viewer entries. */
export function installDetachedAnchorGuard(targets = [WaTooltip, WaPopover]) {
    for (const target of targets) {
        const proto = target?.prototype
        const original = proto?.handleForChange
        if (original?.[GUARDED]) continue
        if (typeof original !== 'function') {
            throw new Error(
                `installDetachedAnchorGuard: ${target?.name}.prototype.handleForChange is not a function; `
                + 'Web Awesome changed how it resolves the `for` anchor',
            )
        }
        const handleForChange = function (...args) {
            if (typeof this.getRootNode().getElementById !== 'function') return
            return original.apply(this, args)
        }
        handleForChange[GUARDED] = true
        proto.handleForChange = handleForChange
    }
}
