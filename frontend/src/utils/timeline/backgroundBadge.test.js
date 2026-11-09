import assert from 'node:assert/strict'
import test from 'node:test'

import { formatClock } from './eventTime.js'
import { describeBackgroundWork } from './backgroundBadge.js'

const work = (fields) => ({ subagents: 0, shells: 0, monitors: 0, workflows: 0, scheduled_wakeup_at: null, goal: false, ...fields })

test('no badge when nothing runs or the shape is unknown', () => {
    for (const value of [null, undefined, work({}), 'x', 3, [], true]) {
        assert.equal(describeBackgroundWork(value), null, JSON.stringify(value))
    }
})

test('the non-zero keys are listed in the fixed order, pluralised', () => {
    const badge = describeBackgroundWork(work({ goal: true, workflows: 2, monitors: 1, shells: 2, subagents: 3 }))
    assert.equal(badge.text, '3 subagents, 2 shells, 1 monitor, 2 workflows, goal')
})

test('a single subagent is singular', () => {
    assert.equal(describeBackgroundWork(work({ subagents: 1 })).text, '1 subagent')
})

test('the wake-up shows its local clock time, after the counters and before the goal', () => {
    const iso = '2026-10-14T14:05:00.000Z'
    const clock = formatClock(Date.parse(iso))
    const badge = describeBackgroundWork(work({ shells: 1, scheduled_wakeup_at: iso, goal: true }))
    assert.equal(badge.text, `1 shell, wake-up at ${clock}, goal`)
})

test('an unreadable wake-up time is skipped', () => {
    assert.equal(describeBackgroundWork(work({ shells: 1, scheduled_wakeup_at: 'soon' })).text, '1 shell')
})

test('the tooltip is the exact dict', () => {
    const value = work({ monitors: 2 })
    assert.equal(describeBackgroundWork(value).title, JSON.stringify(value))
})

test('a snapshot written before the workflows key existed still reads', () => {
    const { workflows: _omitted, ...legacy } = work({ monitors: 1 })
    assert.equal(describeBackgroundWork(legacy).text, '1 monitor')
})
