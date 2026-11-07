import { i18n } from '../i18n/index.js'

/** Helpers of the Read button of the text selection widget (pure). */

export const READ_TITLES = { get idle() { return i18n.global.t('session.selection.read.idle') }, get loading() { return i18n.global.t('session.selection.read.loading') }, get playing() { return i18n.global.t('session.selection.read.playing') } }

/** The icon of the Read button: Read while idle, Stop while loading or playing. */
export function readIcon(status) {
    return status === 'idle' ? 'volume-up' : 'stop'
}

/**
 * True when the collapsed widget shows the Read button: not persistent, a known origin,
 * and the service available (or its own reading still busy, so the user can stop it).
 */
export function canShowRead({ collapsed, persistent, readOrigin, available, busy }) {
    return Boolean(collapsed)
        && !persistent
        && (readOrigin === 'rendered' || readOrigin === 'file')
        && Boolean(available || busy)
}
