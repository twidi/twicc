// One composer-owned operation. The floating form can disappear after preparation.
export function createSelectionCommentSendController({
    insert,
    attemptSend,
    onPendingChange,
    onScreenshotChange = () => {},
    notifyUploadWait,
    schedule = (callback, delay) => {
        const timer = setTimeout(callback, delay)
        return () => clearTimeout(timer)
    },
}) {
    let current = null
    let disposed = false

    function stopTimer(operation) {
        operation.cancelTimer?.()
        operation.cancelTimer = null
    }

    function finish(operation) {
        stopTimer(operation)
        operation.resume?.()
        if (current !== operation) return
        current = null
        onScreenshotChange(null)
        onPendingChange(false)
    }

    return {
        get pending() { return !!current },
        get waiting() { return current?.phase === 'waiting' },

        async start(text, { attachScreenshot = null, onPrepared = () => {} } = {}) {
            if (disposed || current) return
            const operation = { phase: 'staging', state: null, screenshotId: null, cancelTimer: null, resume: null }
            current = operation
            onPendingChange(true)
            try {
                if (attachScreenshot) {
                    const record = await attachScreenshot({
                        onUploadStarted(record) {
                            if (current !== operation || disposed) return
                            operation.screenshotId = record.id
                            operation.cancelTimer = schedule(() => {
                                if (current !== operation || !operation.cancelTimer) return
                                stopTimer(operation)
                                notifyUploadWait()
                            }, 3000)
                            onScreenshotChange(record.id)
                        },
                    })
                    if (current !== operation || disposed) return
                    if (!operation.screenshotId) {
                        operation.screenshotId = record.id
                        onScreenshotChange(record.id)
                    }
                }
                insert(text)
                operation.phase = 'waiting'
                onPrepared()
                if (current !== operation || disposed) return
                if (attachScreenshot && !operation.state) {
                    await new Promise(resolve => { operation.resume = resolve })
                }
                if (current !== operation || disposed) return
                if (attachScreenshot && operation.state !== 'ready') return
                stopTimer(operation)
                operation.phase = 'sending'
                await attemptSend()
            } finally {
                finish(operation)
            }
        },

        observeScreenshot({ id, present, state }) {
            const operation = current
            if (!operation || operation.screenshotId !== id || operation.phase === 'sending') return
            if (!present || state === 'failed' || state === 'missing') {
                operation.state = 'failed'
            } else if (state === 'ready' && operation.state !== 'failed') {
                operation.state = 'ready'
            }
            if (operation.state) {
                stopTimer(operation)
                operation.resume?.()
            }
        },

        cancel() {
            if (current && current.phase !== 'sending') finish(current)
        },

        dispose() {
            disposed = true
            if (current) finish(current)
        },
    }
}
