// Session switches and changes between Home, Sessions, and Artifacts use the same root crossfade.
// beforeResolve sees the final route after redirects and holds navigation until the old state is captured.
// The update releases navigation, then waits for afterEach or an error before capturing the new state.

import { runViewTransition } from './viewTransition.js'

/** True when `to` shows another session than `from`, and `from` is not a draft being swapped. */
export function isSessionSwitch(to, from, isDraft = () => false) {
    const toId = to?.params?.sessionId
    const fromId = from?.params?.sessionId
    if (!toId || !fromId || toId === fromId) return false
    return !isDraft(fromId)
}

function routeMode(route) {
    const name = route?.name
    if (name === 'home') return 'home'
    if (name === 'project-artifacts' || name === 'projects-artifacts') return 'artifacts'
    if (typeof name === 'string' && /^(project|projects|session)(-|$)/.test(name)) return 'sessions'
    return null
}

/** Install the guards on `router`; returns uninstall(). */
export function installSessionSwitchTransition(router, { isDraft = () => false, run = runViewTransition } = {}) {
    let finish = null

    function settle() {
        const done = finish
        finish = null
        done?.()
    }

    const removeGuard = router.beforeResolve((to, from) => {
        const toMode = routeMode(to)
        const fromMode = routeMode(from)
        if (!toMode || !fromMode) return
        if (toMode === fromMode && !isSessionSwitch(to, from, isDraft)) return
        settle()
        return new Promise((resolve) => {
            run(() => new Promise((done) => {
                finish = done
                resolve()
            }), { kind: 'session', settle: true, updateTimeoutMs: 800 })
        })
    })
    const removeAfter = router.afterEach(settle)
    const removeError = router.onError(settle)

    return () => {
        removeGuard()
        removeAfter()
        removeError()
        settle()
    }
}
