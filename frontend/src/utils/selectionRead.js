/** Helpers of the Read button of the text selection widget (pure). */

export const READ_TITLES = { idle: 'Read aloud', loading: 'Cancel the request', playing: 'Stop reading' }

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
