export const MARKDOWN_RENDER_CANCELLED = Symbol('markdownRenderCancelled')

const sameInput = (left, right) => left && right
    && left.source === right.source && left.theme === right.theme && left.slashTag === right.slashTag
    && left.inlineContextKey === right.inlineContextKey

/** Own one document operation and one replaceable latest request. */
export function createMarkdownRenderCoordinator({ render, commit, onError, onState, schedule = queueMicrotask }) {
    let latest = null
    let successful = null
    let revision = 0
    let eligible = false
    let disposed = false
    let unfinished = false
    let invalidatedUnfinished = false
    let active = null
    let queued = false
    let rendering = false

    function report(error) {
        try { onError?.(error) } catch { /* Error reporting must not retain the active slot. */ }
    }

    function publishState() {
        const next = !disposed && eligible && unfinished
        if (next === rendering) return
        rendering = next
        onState?.({ rendering })
    }

    function enqueue() {
        if (disposed || !eligible || !unfinished || active || queued) return
        queued = true
        schedule(() => {
            queued = false
            if (disposed || !eligible || !unfinished || active) return
            const operation = { revision, input: latest }
            active = operation
            void run(operation)
        })
    }

    async function run(operation) {
        const isCurrent = () => !disposed && eligible && revision === operation.revision
        try {
            const result = await render(operation.input, { isCurrent })
            if (!isCurrent()) return
            if (result !== MARKDOWN_RENDER_CANCELLED) {
                commit(result, operation.input, { isCurrent })
                // Commit callbacks can synchronously replace the request or hide the view.
                if (!isCurrent()) return
                successful = operation.input
            }
            unfinished = false
        } catch (error) {
            if (isCurrent()) {
                unfinished = false
                report(error)
            }
        } finally {
            active = null
            publishState()
            enqueue()
        }
    }

    function request(input) {
        if (disposed) return
        const snapshot = { source: input.source, theme: input.theme, slashTag: input.slashTag }
        if (input.inlineContext) {
            snapshot.inlineContext = input.inlineContext
            snapshot.inlineContextKey = input.inlineContextKey
        }
        if (sameInput(snapshot, latest)) return
        latest = snapshot
        revision++
        unfinished = invalidatedUnfinished || !sameInput(snapshot, successful)
        publishState()
        enqueue()
    }

    function setEligible(value) {
        if (disposed || eligible === value) return
        eligible = value
        revision++
        if (!eligible) {
            invalidatedUnfinished ||= unfinished
        } else {
            unfinished = Boolean(latest) && (invalidatedUnfinished || !sameInput(latest, successful))
            invalidatedUnfinished = false
        }
        publishState()
        enqueue()
    }

    function dispose() {
        if (disposed) return
        disposed = true
        revision++
        latest = null
        unfinished = false
        publishState()
    }

    return { request, setEligible, dispose }
}
