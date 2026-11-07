import { i18n } from '../../i18n/index.js'

// frontend/src/utils/timeline/backgroundBadge.js
//
// The badge of a `turn.ended` row that left work running (display design section 6.5).
// Reuses `pluralize` and `formatClockTime` of utils/backgroundWork.js.

import { formatClockTime, pluralize } from '../backgroundWork.js'

/**
 * @param {*} work - `data.background_work`: `{subagents, shells, monitors, workflows, scheduled_wakeup_at, goal}` or null.
 * @returns {{text: string, title: string}|null} null when nothing runs or the shape is unknown.
 */
export function describeBackgroundWork(work) {
    if (!work || typeof work !== 'object' || Array.isArray(work)) return null
    const parts = []
    if (work.subagents > 0) parts.push(pluralize(work.subagents, 'subagent'))
    if (work.shells > 0) parts.push(pluralize(work.shells, 'shell'))
    if (work.monitors > 0) parts.push(pluralize(work.monitors, 'monitor'))
    if (work.workflows > 0) parts.push(pluralize(work.workflows, 'workflow'))
    const wakeup = typeof work.scheduled_wakeup_at === 'string' ? Date.parse(work.scheduled_wakeup_at) : NaN
    if (Number.isFinite(wakeup)) parts.push(i18n.global.t('session.timeline.background.wakeup', { time: formatClockTime(wakeup / 1000) }))
    if (work.goal === true) parts.push(i18n.global.t('session.timeline.background.goal'))
    if (!parts.length) return null
    return { text: new Intl.ListFormat(i18n.global.locale.value, { style: 'short', type: 'unit' }).format(parts), title: JSON.stringify(work) }
}
