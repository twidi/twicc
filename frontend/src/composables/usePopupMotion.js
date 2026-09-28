// Picker entrances (visual refresh step 5b).
// Design: docs/plans/2026-09-28-overlay-motion-design.md §6, §14.6.
//
// The pickers are bare <wa-popup>s: `active=false` is display: none at once, and their
// logic assumes an instant close (their data is cleared in the same tick). So they get
// the entrance only: the panel grows from the side next to its anchor; the close stays
// instant.

import { nextTick, onScopeDispose, watch } from 'vue'

const DURATION_MS = 180
const SCALE_DELTA = 0.06

// The panel is a glass surface: an opacity animation on it would stop its blur (design
// §14). It only scales; the fade animates the registered --twicc-reveal, which its glass
// layers read as their opacity, and, through this filter set for the entrance only, its
// content (glass.css's content rule).
const REVEAL_FILTER = '--twicc-reveal-filter'

const VERTICAL_ORIGIN = { top: 'bottom', bottom: 'top' }
const HORIZONTAL_ORIGIN = { start: 'left', end: 'right' }

/** `top-start` → `left bottom`, `bottom-end` → `right top`, `bottom` → `center top`; none → `center bottom`. */
function originForPlacement(placement) {
    const [side, alignment] = (placement || '').split('-')
    return `${HORIZONTAL_ORIGIN[alignment] ?? 'center'} ${VERTICAL_ORIGIN[side] ?? 'bottom'}`
}

const noop = () => {}

/**
 * Play a picker panel's entrance each time its wa-popup opens. The popup's `active` stays
 * bound to the component's own open state.
 * @param {Ref<boolean>} isOpen
 * @param {Ref<Element|null>} panel   the element that moves (the picker panel)
 * @param {Ref<Element|null>} popup   the wa-popup (for its data-current-placement)
 * @param {object} env   globalThis by default; its functions are called through bound
 *                       wrappers (roadmap §6g.2)
 */
export function usePopupMotion(isOpen, panel, popup, env = globalThis) {
    // Bound wrappers: a bare env.requestAnimationFrame reference throws when called.
    const requestFrame = (fn) => env.requestAnimationFrame(fn)
    const cancelFrame = (id) => env.cancelAnimationFrame(id)
    const computedStyle = (el) => env.getComputedStyle(el)

    let generation = 0
    let frameId = 0
    let animation = null

    function stop() {
        generation++
        if (frameId) cancelFrame(frameId)
        frameId = 0
        animation?.cancel()
        animation = null
        const el = panel.value
        if (el) {
            el.style.removeProperty('transform-origin')
            el.style.removeProperty(REVEAL_FILTER)
        }
    }

    function play() {
        const el = panel.value
        if (!el) return
        // The popup has positioned itself by now (computePosition resolves in microtasks);
        // it drops data-current-placement on each close, hence the fallbacks.
        const host = popup.value
        const placement = host?.getAttribute('data-current-placement') || host?.getAttribute('placement')
        el.style.setProperty('transform-origin', originForPlacement(placement))

        const style = computedStyle(el)
        const amount = parseFloat(style.getPropertyValue('--motion-amount'))
        const scale = 1 - SCALE_DELTA * (Number.isFinite(amount) ? amount : 1)
        const easing = style.getPropertyValue('--motion-ease-out').trim() || 'ease-out'
        el.style.setProperty(REVEAL_FILTER, 'opacity(var(--twicc-reveal))')
        const entrance = el.animate(
            [{ '--twicc-reveal': 0, scale }, { '--twicc-reveal': 1, scale: 1 }],
            { duration: DURATION_MS, easing },
        )
        animation = entrance
        // The inline origin stays until the next close. A cancelled animation rejects its
        // `finished` (stop() has already cleared the state).
        entrance.finished.then(() => {
            if (animation !== entrance) return
            animation = null
            el.style.removeProperty(REVEAL_FILTER)
        }, noop)
    }

    function start() {
        stop()
        const token = generation
        nextTick(() => {
            if (token !== generation) return
            frameId = requestFrame(() => {
                frameId = 0
                if (token === generation) play()
            })
        })
    }

    watch(isOpen, (open) => {
        if (open) start()
        else stop()
    })
    onScopeDispose(stop)
}
