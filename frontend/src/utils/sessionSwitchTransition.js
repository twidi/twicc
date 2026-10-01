// Session switch crossfade (visual refresh retouches): moving from one session to another runs in a
// document view transition (utils/viewTransition.js, kind `session`), so the old session fades into the new
// one like the tab panels do (styles/motion.css). Every navigation goes through the router, so the hook
// lives there: a `beforeResolve` guard holds the navigation until the transition has captured the old state,
// the transition's update then lets it through and waits for it to land (`afterEach`, or an error).
// Only a switch from one session to another: a tab change inside a session, a project, the home, a first load
// and the swap of a draft for its real session (a replace that keeps what is on screen) never animate.

import { runViewTransition } from './viewTransition.js'

/** True when `to` shows another session than `from`, and `from` is not a draft being swapped. */
export function isSessionSwitch(to, from, isDraft = () => false) {
    const toId = to?.params?.sessionId
    const fromId = from?.params?.sessionId
    if (!toId || !fromId || toId === fromId) return false
    return !isDraft(fromId)
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
        if (!isSessionSwitch(to, from, isDraft)) return
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
