import { compareVersions } from './version.js'

export const UPDATE_REMINDER_KEY = 'twicc-update-reminder'
export const UPDATE_REMINDER_DELAY = 24 * 60 * 60 * 1000
const LEGACY_KEY = 'twicc-update-notified-version'

/**
 * Own one persistent update toast. Only an explicit dismissal starts the delay.
 * Hold a per-version Web Lock while the toast is open; closing the page releases it.
 * Browser event listeners and the check timer belong to the caller.
 */
export function createUpdateReminder({ storage, getVersions, isVisible, show, locks, now = Date.now }) {
    let active = null
    let pending = null
    let disposed = false
    let memoryRecord = null

    function readRecord() {
        try {
            const raw = storage.getItem(UPDATE_REMINDER_KEY)
            if (raw) {
                const record = JSON.parse(raw)
                if (typeof record?.version === 'string' && record.version
                    && (record.dismissedAt === null || (Number.isFinite(record.dismissedAt) && record.dismissedAt >= 0))) {
                    memoryRecord = record
                    return record
                }
            }
            // Old records have no dismissal date. Give existing users one full
            // day before the first reminder, and persist this migration once.
            const legacy = storage.getItem(LEGACY_KEY)
            if (legacy && !memoryRecord) {
                const record = { version: legacy, dismissedAt: now() }
                writeRecord(record)
                return record
            }
        } catch {
            // Storage can be unavailable. The current tab still tracks its toast.
        }
        return memoryRecord
    }

    function writeRecord(record) {
        memoryRecord = record
        try { storage.setItem(UPDATE_REMINDER_KEY, JSON.stringify(record)) } catch { /* Use in-memory toast state. */ }
    }

    function closeActive() {
        const previous = active
        active = null
        previous?.handle?.destroy()
        previous?.release?.()
    }

    function display(version, release) {
        const token = { version, release, handle: null }
        active = token
        writeRecord({ version, dismissedAt: null })
        token.handle = show(version, () => {
            if (active !== token) return
            active = null
            const record = readRecord()
            if (!record || compareVersions(record.version, version) <= 0) {
                writeRecord({ version, dismissedAt: now() })
            }
            token.release?.()
        })
        return token
    }

    function eligible(version) {
        if (disposed || !isVisible()) return false
        const { current, latest } = getVersions()
        if (!current || latest !== version || compareVersions(latest, current) <= 0) return false
        const record = readRecord()
        if (!record) return true
        const comparison = compareVersions(record.version, version)
        if (comparison > 0) return false
        return comparison < 0 || record.dismissedAt === null || now() - record.dismissedAt >= UPDATE_REMINDER_DELAY
    }

    function check() {
        if (disposed) return
        const { current, latest } = getVersions()
        const record = readRecord()
        if (active && (
            (current && compareVersions(current, active.version) >= 0)
            || (latest && compareVersions(latest, active.version) > 0)
            || (record && (compareVersions(record.version, active.version) > 0
                || (record.version === active.version && record.dismissedAt !== null)))
        )) closeActive()
        if (!latest || active?.version === latest || pending === latest || !eligible(latest)) return

        if (!locks) {
            display(latest)
            return
        }
        pending = latest
        // ifAvailable never queues a second toast behind the currently open one.
        // Recheck fresh versions and timestamps when the browser grants the lock.
        locks.request(`${UPDATE_REMINDER_KEY}:${latest}`, { ifAvailable: true }, lock => {
            if (pending === latest) pending = null
            if (!lock || active?.version === latest || !eligible(latest)) return
            return new Promise(resolve => display(latest, resolve))
        }).catch(() => {
            if (pending === latest) pending = null
        })
    }

    function showManually(version) {
        if (disposed || !version || active?.version === version) return
        closeActive()
        // An explicit click always opens the instructions, even if another tab
        // owns the automatic toast. Dismissal is still shared across the browser.
        const token = display(version)
        // Take the automatic slot too, if free, so periodic checks in other
        // tabs do not duplicate instructions opened through Settings.
        locks?.request(`${UPDATE_REMINDER_KEY}:${version}`, { ifAvailable: true }, lock => {
            if (!lock || active !== token || disposed) return
            return new Promise(resolve => { token.release = resolve })
        }).catch(() => {})
    }

    function dispose() {
        disposed = true
        closeActive()
    }

    return { check, showManually, dispose }
}
