/** Observe failures without delaying clipboard access or changing its result. */
export function createTerminalClipboardObserver({ enabled, context, report }) {
    return function observe({ operation, trigger, method, text }, run) {
        let snapshot
        try {
            if (enabled()) snapshot = {
                ...context(), timestamp: new Date().toISOString(), operation, trigger, method,
                ...(typeof text === 'string' ? { textLength: text.length, multiline: /[\r\n]/.test(text) } : {}),
            }
        } catch { /* Diagnostics must never prevent a clipboard operation. */ }
        if (!snapshot) return run()

        function failed(error) {
            try {
                const diagnostic = { ...snapshot,
                    errorName: String(error?.name || 'Error').slice(0, 128),
                    errorMessage: String(error?.message || 'Clipboard operation failed').slice(0, 512),
                }
                Promise.resolve(report(diagnostic)).catch(() => {})
            } catch { /* The Android transport can disappear during navigation. */ }
        }
        try {
            const result = run()
            if (result === false && method === 'execCommand') {
                failed({ name: 'CopyCommandFailed', message: 'execCommand(copy) returned false' })
            } else if (result?.then) result.then(undefined, failed)
            return result
        } catch (error) {
            failed(error)
            throw error
        }
    }
}
