// Test-observable stage runs. Every derivation stage of the sidebar calls
// `noteStageRun(name)` at the start of each run; tests count runs per name
// with `observeStageRuns`. Without an observer a call does nothing.
// Leaf module: it imports nothing.

const observers = new Set()

/** Report one run of the stage `name`. No-op unless an observer is registered. */
export function noteStageRun(name) {
    if (!observers.size) return
    for (const callback of observers) callback(name)
}

/**
 * Call `callback(name)` on each stage run until the returned `stop()` is called. Tests only.
 *
 * @param {(name: string) => void} callback
 * @returns {() => void} stop
 */
export function observeStageRuns(callback) {
    observers.add(callback)
    return () => { observers.delete(callback) }
}
