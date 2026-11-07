import { i18n } from '../i18n/index.js'

import { formatFullDateTime } from './date.js'

// Duration presets shared by full-text search, bulk archive, and the sidebar's
// session-list date separators. Order matches display order in lists, from the
// most recent threshold to the oldest.
export const DURATION_PRESETS = [
    { value: '24h', get label() { return i18n.global.t('common.dates.presets.24hours') } },
    { value: '3d',  get label() { return i18n.global.t('common.dates.presets.3days') } },
    { value: '7d',  get label() { return i18n.global.t('common.dates.presets.7days') } },
    { value: '10d', get label() { return i18n.global.t('common.dates.presets.10days') } },
    { value: '20d', get label() { return i18n.global.t('common.dates.presets.20days') } },
    { value: '30d', get label() { return i18n.global.t('common.dates.presets.30days') } },
    { value: '2m',  get label() { return i18n.global.t('common.dates.presets.2months') } },
    { value: '3m',  get label() { return i18n.global.t('common.dates.presets.3months') } },
    { value: '6m',  get label() { return i18n.global.t('common.dates.presets.6months') } },
]

/**
 * Cutoff Date for a preset, relative to `now`. A timestamp is "older than
 * <preset>" when it falls before this cutoff. Returns null for an unknown preset.
 *
 * @param {string} preset - A DURATION_PRESETS value (e.g. '24h', '7d', '3m').
 * @param {Date} now - Reference "now".
 * @returns {?Date}
 */
function presetCutoff(preset, now) {
    const d = new Date(now)
    switch (preset) {
        case '24h': d.setDate(d.getDate() - 1); break
        case '3d':  d.setDate(d.getDate() - 3); break
        case '7d':  d.setDate(d.getDate() - 7); break
        case '10d': d.setDate(d.getDate() - 10); break
        case '20d': d.setDate(d.getDate() - 20); break
        case '30d': d.setDate(d.getDate() - 30); break
        case '2m':  d.setMonth(d.getMonth() - 2); break
        case '3m':  d.setMonth(d.getMonth() - 3); break
        case '6m':  d.setMonth(d.getMonth() - 6); break
        default: return null
    }
    return d
}

/**
 * Convert a duration preset (e.g. '7d', '3m') to an ISO timestamp in the past.
 * Returns null if the preset is unknown.
 */
export function presetToDate(preset) {
    const cutoff = presetCutoff(preset, new Date())
    return cutoff ? cutoff.toISOString() : null
}

/**
 * Bucket a session by the OLDEST preset it is already older than, for the
 * sidebar's date separators. Uses the very same rolling cutoffs as bulk archive,
 * so a separator marks exactly what "Archive sessions older than <preset>" would
 * select.
 *
 * Walks presets newest-first; the bucket is the last one whose cutoff the session
 * predates (once a session is not older than preset P, it cannot be older than
 * any longer preset, so we stop). Sessions newer than the smallest preset (24h)
 * fall in `recent` and get no separator.
 *
 * @param {number} mtime - Last-activity Unix timestamp in seconds.
 * @param {number} nowMs - Reference "now" in epoch milliseconds.
 * @returns {{ key: string, duration: ?string, cutoffMs: ?number }} `duration` is
 *   the preset label (e.g. "24 hours"), `cutoffMs` the exact threshold this
 *   bucket starts at; both null for the `recent` bucket (no separator).
 */
export function sessionPresetBucket(mtime, nowMs) {
    const mtimeMs = mtime * 1000
    const now = new Date(nowMs)
    let bucket = null
    let cutoffMs = null
    for (const preset of DURATION_PRESETS) {
        const c = presetCutoff(preset.value, now).getTime()
        if (mtimeMs < c) {
            bucket = preset
            cutoffMs = c
        } else {
            break
        }
    }
    return bucket
        ? { key: bucket.value, duration: bucket.label, cutoffMs }
        : { key: 'recent', duration: null, cutoffMs: null }
}

/**
 * Build the date-section separator for a timestamp, shared by the sidebar's
 * session and artifact lists. Returns the bucket key (to detect group changes
 * while walking a recency-sorted list) and the <SidebarListSeparator> props to
 * render before the group's first item: the freshest bucket reads "Last 24
 * hours"; the rest read "Older than <X>" with the exact cutoff as a tooltip.
 *
 * @param {number} ts - Timestamp in seconds (e.g. session mtime, bookmark updated_at).
 * @param {number} nowMs - Reference "now" in epoch milliseconds.
 * @returns {{ key: string, entry: { label: string, prefix?: string, title?: string } }}
 */
export function dateBucketSeparator(ts, nowMs) {
    const { key, duration, cutoffMs } = sessionPresetBucket(ts, nowMs)
    const entry = key === 'recent'
        ? { label: i18n.global.t('common.dates.presets.last24Hours') }
        : { label: duration, fullLabel: i18n.global.t('common.dates.presets.olderThan', { duration }), prefix: i18n.global.t('common.dates.presets.olderThan', { duration: '' }), title: formatFullDateTime(cutoffMs) }
    return { key, entry }
}
