// Route coordination stays outside the composer and the shared floating form.
export function createSelectionCommentBridge({ getComposer, showChat, onNavigationError = error => console.warn('Chat navigation failed:', error) }) {
    return {
        get available() { return !!getComposer()?.selectionCommentSendAvailable },
        get pending() { return !!getComposer()?.selectionCommentSendPending },
        async send(text, { attachScreenshot = null, onPrepared = () => {} } = {}) {
            const composer = getComposer()
            if (!composer?.selectionCommentSendAvailable) throw new Error('No session composer is available')
            return composer.startSelectionCommentSend(text, {
                attachScreenshot,
                onPrepared() {
                    onPrepared()
                    try {
                        Promise.resolve(showChat()).catch(onNavigationError)
                    } catch (error) {
                        onNavigationError(error)
                    }
                },
            })
        },
    }
}
