import { i18n } from '../i18n/index.js'

// Route coordination stays outside the composer and the shared floating form.
export function createSelectionCommentBridge({ getComposer, showChat, onNavigationError = error => console.warn('Chat navigation failed:', error) }) {
    return {
        get available() { return !!getComposer()?.selectionCommentSendAvailable },
        get pending() { return !!getComposer()?.selectionCommentSendPending },
        async send(text, { attachScreenshot = null, onPrepared = () => {} } = {}) {
            const composer = getComposer()
            if (!composer?.selectionCommentSendAvailable) throw new Error(i18n.global.t('session.selectionComment.noComposer'))
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
