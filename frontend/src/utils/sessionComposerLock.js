import { i18n } from '../i18n/index.js'

export function resolveSessionComposerLock({
    hasAnswerablePendingRequest,
    isComputePending,
}) {
    if (hasAnswerablePendingRequest) {
        return {
            locked: true,
            reason: i18n.global.t('composer.lock.pendingRequest'),
            presentation: 'paused',
        }
    }
    if (isComputePending) {
        return {
            locked: true,
            reason: i18n.global.t('composer.lock.preparing'),
            presentation: 'disabled',
        }
    }
    return { locked: false, reason: '', presentation: 'paused' }
}
