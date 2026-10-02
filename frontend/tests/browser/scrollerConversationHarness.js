// Fixture wall-clock ownership. A deadline does not cancel unchanged production/native work.
const browserClock = {
    now: () => performance.now(),
    setTimeout: (callback, ms) => setTimeout(callback, ms),
    clearTimeout: handle => clearTimeout(handle),
}
export function runBoundedScrollerAction(action, { timeoutMs = 20000, clock = browserClock } = {}) {
    const startedAt = clock.now()
    return new Promise(resolve => {
        let settled = false
        const finish = outcome => {
            if (settled) return
            settled = true
            clock.clearTimeout(handle)
            resolve({ ...outcome, timeoutMs, startedAt, deadlineAt: startedAt + timeoutMs, elapsedMs: clock.now() - startedAt })
        }
        const handle = clock.setTimeout(() => finish({ status: 'failed/inconclusive', timedOut: true, requiresReload: true,
            lateSettlementIgnored: true,
            error: `Fixture action exceeds ${timeoutMs} ms; native work can remain pending. Reload this page before another action.` }), timeoutMs)
        // Attach both handlers immediately. Late native settlement cannot change the recorded result.
        Promise.resolve().then(action).then(
            result => finish({ status: 'passed', result, timedOut: false, requiresReload: false }),
            error => finish({ status: 'failed/inconclusive', error: String(error), timedOut: false, requiresReload: false }),
        )
    })
}
export async function recoverScrollerConversation(fixture) {
    if (fixture.router.currentRoute.value.params.sessionId !== fixture.ids.mainId) throw new Error('Recovery must start in the main session')
    // navigate() selects pane tabs. switchSession() owns the existing named session route.
    await fixture.switchSession()
    const hiddenSessionId = fixture.router.currentRoute.value.params.sessionId
    if (hiddenSessionId !== fixture.ids.otherId) throw new Error('Recovery does not activate the other session')
    await fixture.switchSession()
    const restoredSessionId = fixture.router.currentRoute.value.params.sessionId
    if (restoredSessionId !== fixture.ids.mainId) throw new Error('Recovery does not restore the main session')
    return { hiddenSessionId, restoredSessionId }
}
