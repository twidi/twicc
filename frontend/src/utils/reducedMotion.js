// Reduced motion and reduced effects, as two classes on <html>.
//
//   reduce-motion   movement goes (fades and status indicators stay). On when the system asks
//                   for it (prefers-reduced-motion) OR when "Reduce effects" is on.
//   reduce-effects  the "Reduce effects" setting (local to the device): costly effects go too
//                   (glass blur, filters). It implies reduce-motion.
//
// Styles read the classes, never `@media (prefers-reduced-motion)`: a media query cannot see an
// app setting. Scripts call isReducedMotion(). The share viewer and the artifact shell have no
// settings: they call initReducedMotion() only, so the system preference alone applies.
import { ref } from 'vue'

export const REDUCE_MOTION_CLASS = 'reduce-motion'
export const REDUCE_EFFECTS_CLASS = 'reduce-effects'
const QUERY = '(prefers-reduced-motion: reduce)'

// The same facts as refs, for a component that must switch on them: the styles read the classes
// on <html>, which Vue cannot watch.
export const systemReducedMotion = ref(false)
export const reduceEffects = ref(false)

/** Whether movement must be cut: the class on <html> (system or setting), else the system query. */
export function isReducedMotion(env = globalThis) {
    const classes = env.document?.documentElement?.classList
    if (typeof classes?.contains === 'function' && classes.contains(REDUCE_MOTION_CLASS)) return true
    return env.matchMedia?.(QUERY)?.matches === true
}

/** Whether "Reduce effects" is on (the class on <html>): the costly effects go, view transitions included. */
export function isReduceEffects(env = globalThis) {
    const classes = env.document?.documentElement?.classList
    return typeof classes?.contains === 'function' && classes.contains(REDUCE_EFFECTS_CLASS)
}

function apply() {
    const root = document.documentElement
    root.classList.toggle(REDUCE_EFFECTS_CLASS, reduceEffects.value)
    root.classList.toggle(REDUCE_MOTION_CLASS, systemReducedMotion.value || reduceEffects.value)
}

/** Apply the "Reduce effects" setting (the settings store calls this). */
export function applyReduceEffects(enabled) {
    reduceEffects.value = enabled === true
    apply()
}

/** Apply the system preference now, and again whenever it changes. */
export function initReducedMotion() {
    const query = window.matchMedia(QUERY)
    systemReducedMotion.value = query.matches
    query.addEventListener('change', () => {
        systemReducedMotion.value = query.matches
        apply()
    })
    apply()
}
