/** Temporary snapshot completion fetches. Live shares retain their existing channel. */
export function createShareInlineCompletion({ adapter, isSnapshot, accessClosed, onAccessClosed,
    refresh = options => adapter.refresh(options),
    setTimer = setTimeout, clearTimer = clearTimeout,
}) {
    let pending = false, disposed = false, timer = null, controller = null, generation = 0
    const eligible = () => !disposed && pending && isSnapshot() && !accessClosed()

    function stop() {
        generation++
        if (timer !== null) clearTimer(timer)
        timer = null
        controller?.abort()
        controller = null
    }
    function schedule() {
        if (!eligible() || timer !== null || controller) return
        timer = setTimer(poll, 1000)
    }
    async function poll() {
        timer = null
        if (!eligible()) return
        const captured = generation
        const activeController = new AbortController()
        controller = activeController
        try { await refresh({ signal: activeController.signal }) }
        catch (error) {
            if (captured !== generation || activeController.signal.aborted) return
            if ([401, 403, 404].includes(error.status)) {
                stop()
                onAccessClosed?.()
            }
        } finally {
            if (controller === activeController) controller = null
            if (captured === generation) schedule()
        }
    }
    const unsubscribe = adapter.subscribe(manifest => {
        pending = manifest.descriptors.some(entry => entry.status === 'pending')
        if (!isSnapshot() || accessClosed() || !pending) {
            stop()
            return
        }
        schedule()
    })
    return {
        accessChanged() { if (!isSnapshot() || accessClosed()) stop(); else schedule() },
        dispose() { if (disposed) return; disposed = true; stop(); unsubscribe() },
    }
}
