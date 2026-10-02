import { getParsedContent, hasContent } from '../../src/utils/parsedContent.js'
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

export function installImportedScrollerQuarantine(document) {
    // Install before importing the original fixture. Preserve its status/report nodes and viewport allocation.
    const style = document.createElement('style')
    style.textContent = '#fixture-controls button,#fixture-controls input,#fixture-controls select,#fixture-controls textarea{visibility:hidden!important;pointer-events:none!important}'
    document.head.append(style)
    const block = event => {
        if (!event.target?.closest?.('#fixture-controls')) return
        event.preventDefault()
        event.stopImmediatePropagation()
    }
    for (const type of ['click', 'change', 'input', 'keydown', 'pointerdown', 'submit']) document.addEventListener(type, block, true)
    const quarantine = () => {
        const imported = document.querySelector('#fixture-controls')
        if (!imported) return
        if (!imported.inert) imported.inert = true
        for (const action of imported.querySelectorAll('button,input,select,textarea')) {
            if (!action.disabled) action.disabled = true
            if (!action.hidden) action.hidden = true
        }
    }
    // A timed-out import can mount controls later. Renderer readiness updates can also restore disabled attributes.
    // The capture gate protects that interval; the observer restores inert/disabled/hidden properties before input.
    if (document.defaultView?.MutationObserver) {
        const observer = new document.defaultView.MutationObserver(quarantine)
        observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'hidden', 'inert'] })
    }
    quarantine()
    return quarantine
}


export function makeScrollerRecoveryItem(item, content) {
    // Do not spread a parsed source row into a fresh network response.
    return { line_num: item.line_num, kind: item.kind, display_level: item.display_level,
        group_head: item.group_head ?? null, group_tail: item.group_tail ?? null, timestamp: item.timestamp ?? null, content }
}
export function replaceScrollerContentWithMetadata(fixture, line) {
    const id = fixture.ids.mainId
    fixture.store.updateSessionItemsContent(id, [{ line_num: line, content: null }])
    const raw = fixture.store.sessionItems[id]?.[line - 1]
    const visual = fixture.store.getSessionVisualItems(id).find(item => item.lineNum === line)
    if (!raw || !visual) throw new Error('Missing-content substitution loses row membership')
    const result = { rawHasContent: hasContent(raw), rawParsedAvailable: getParsedContent(raw) !== null,
        visualHasContent: hasContent(visual), visualParsedAvailable: getParsedContent(visual) !== null }
    if (Object.values(result).some(Boolean)) throw new Error('Public content update does not remove raw/visual content availability')
    return result
}
