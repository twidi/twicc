import assert from 'node:assert/strict'
import test from 'node:test'

import {
    activeCronCount,
    aggregatedBackgroundSuffix,
    archiveStopLabel,
    backgroundShellCount,
    backgroundShellsKillSentence,
    backgroundWorkStatusKey,
    batchStopConfirmationMessage,
    buildBackgroundWorkStatusLines,
    formatClockTime,
    getStopConfirmation,
    idleWithShellsSuffix,
    pluralize,
    processStateTooltip,
    startupSettingsDeferredText,
    stopConfirmationTitle,
    userTurnBackgroundShellCount,
} from './backgroundWork.js'

const NOW = 1_800_000_000

function work(shells) {
    return { subagents: 0, shells, monitors: 0, scheduled_wakeup_at: null, goal: false }
}

function cron(nextFire) {
    return { id: 'c', cron_expr: '*/5 * * * *', recurring: true, prompt: 'x', created_at: 0, next_fire: nextFire }
}

test('pluralize picks the singular for exactly one', () => {
    assert.equal(pluralize(1, 'background shell'), '1 background shell')
    assert.equal(pluralize(2, 'background shell'), '2 background shells')
    assert.equal(pluralize(0, 'background shell'), '0 background shells')
    assert.equal(pluralize(3, 'running process', 'running processes'), '3 running processes')
})

test('shell counts tolerate missing or malformed data', () => {
    assert.equal(backgroundShellCount(null), 0)
    assert.equal(backgroundShellCount({ state: 'user_turn', background_work_in_progress: null }), 0)
    assert.equal(backgroundShellCount({ state: 'user_turn', background_work_in_progress: { shells: -1 } }), 0)
    assert.equal(backgroundShellCount({ state: 'assistant_turn', background_work_in_progress: work(2) }), 2)
})

test('only a user_turn counts its shells as idle-state background work', () => {
    assert.equal(userTurnBackgroundShellCount({ state: 'user_turn', background_work_in_progress: work(2) }), 2)
    assert.equal(userTurnBackgroundShellCount({ state: 'assistant_turn', background_work_in_progress: work(2) }), 0)
    assert.equal(userTurnBackgroundShellCount({ state: 'starting', background_work_in_progress: work(1) }), 0)
    assert.equal(userTurnBackgroundShellCount(null), 0)
})

test('active cron count', () => {
    assert.equal(activeCronCount(null), 0)
    assert.equal(activeCronCount({ active_crons: null }), 0)
    assert.equal(activeCronCount({ active_crons: [cron(1), cron(2)] }), 2)
})

test('process tooltip mentions shells (user_turn only) then crons', () => {
    assert.equal(processStateTooltip('Claude', { state: 'user_turn' }), 'Claude state: User turn')
    assert.equal(
        processStateTooltip('Claude', { state: 'user_turn', background_work_in_progress: work(1), active_crons: [cron(1), cron(2)] }),
        'Claude state: User turn — 1 background shell running (2 active crons)',
    )
    assert.equal(
        processStateTooltip('Codex', { state: 'user_turn', background_work_in_progress: work(3) }),
        'Codex state: User turn — 3 background shells running',
    )
    assert.equal(
        processStateTooltip('Claude', { state: 'user_turn', active_crons: [cron(1)] }),
        'Claude state: User turn (1 active cron)',
    )
    assert.equal(
        processStateTooltip('Claude', { state: 'assistant_turn', background_work_in_progress: work(1) }),
        'Claude state: Assistant turn',
    )
})

test('aggregated suffix lists shells before crons', () => {
    assert.equal(aggregatedBackgroundSuffix({ shells: 0, crons: 0 }), '')
    assert.equal(aggregatedBackgroundSuffix({ shells: 1, crons: 0 }), ' (1 background shell)')
    assert.equal(aggregatedBackgroundSuffix({ shells: 0, crons: 2 }), ' (2 active crons)')
    assert.equal(aggregatedBackgroundSuffix({ shells: 2, crons: 1 }), ' (2 background shells, 1 active cron)')
})

test('clock time is local HH:MM, zero-padded', () => {
    const date = new Date(2026, 8, 25, 4, 7, 30)
    assert.equal(formatClockTime(date.getTime() / 1000), '04:07')
})

test('status lines are empty outside user_turn or with nothing in the background', () => {
    assert.deepEqual(buildBackgroundWorkStatusLines(null, NOW), [])
    assert.deepEqual(buildBackgroundWorkStatusLines({ state: 'user_turn' }, NOW), [])
    assert.deepEqual(
        buildBackgroundWorkStatusLines({ state: 'assistant_turn', background_work_in_progress: work(1), active_crons: [cron(NOW + 60)] }, NOW),
        [],
    )
})

test('status lines: shells, then crons with the earliest future run', () => {
    const lines = buildBackgroundWorkStatusLines({
        state: 'user_turn',
        background_work_in_progress: work(1),
        active_crons: [cron(NOW + 3600), cron(NOW - 10), cron(NOW + 600)],
    }, NOW)
    assert.deepEqual(lines, [
        { kind: 'shells', text: '1 background shell still running' },
        { kind: 'crons', text: `3 active crons — next run at ${formatClockTime(NOW + 600)}` },
    ])
})

test('status lines: crons alone, count only when no run is in the future', () => {
    assert.deepEqual(
        buildBackgroundWorkStatusLines({ state: 'user_turn', active_crons: [cron(NOW - 5), cron(null)] }, NOW),
        [{ kind: 'crons', text: '2 active crons' }],
    )
    assert.deepEqual(
        buildBackgroundWorkStatusLines({ state: 'user_turn', background_work_in_progress: work(2) }, NOW),
        [{ kind: 'shells', text: '2 background shells still running' }],
    )
})

test('status key changes with the counts and is null without a line', () => {
    const ps = { state: 'user_turn', background_work_in_progress: work(1) }
    assert.equal(backgroundWorkStatusKey({ state: 'user_turn' }, NOW), null)
    assert.equal(backgroundWorkStatusKey(null, NOW), null)
    const key1 = backgroundWorkStatusKey(ps, NOW)
    assert.notEqual(key1, null)
    assert.equal(backgroundWorkStatusKey({ ...ps }, NOW), key1)
    assert.notEqual(backgroundWorkStatusKey({ ...ps, background_work_in_progress: work(2) }, NOW), key1)
})

test('stop confirmation: crons and/or shells, whatever the state', () => {
    assert.equal(getStopConfirmation({ state: 'user_turn' }), null)
    assert.deepEqual(
        getStopConfirmation({ state: 'user_turn', background_work_in_progress: work(1) }),
        { cronCount: 0, shellCount: 1 },
    )
    assert.deepEqual(
        getStopConfirmation({ state: 'assistant_turn', background_work_in_progress: work(2), active_crons: [cron(1)] }),
        { cronCount: 1, shellCount: 2 },
    )
    assert.deepEqual(getStopConfirmation({ state: 'user_turn', active_crons: [cron(1)] }), { cronCount: 1, shellCount: 0 })
})

test('stop confirmation title and shells sentence', () => {
    assert.equal(stopConfirmationTitle({ cronCount: 1, shellCount: 0 }), 'Active crons will be lost')
    assert.equal(stopConfirmationTitle({ cronCount: 0, shellCount: 1 }), 'Background shells will be killed')
    assert.equal(stopConfirmationTitle({ cronCount: 1, shellCount: 1 }), 'Background work will be lost')
    assert.equal(backgroundShellsKillSentence(1), 'Stopping will kill 1 background shell.')
    assert.equal(backgroundShellsKillSentence(2), 'Stopping will kill 2 background shells.')
})

test('batch stop confirmation message', () => {
    assert.equal(
        batchStopConfirmationMessage({ mode: 'archive', processCount: 2, cronCount: 1, shellCount: 1 }),
        '2 running processes will be stopped. 1 background shell will be killed. 1 active cron job will be cancelled.',
    )
    assert.equal(
        batchStopConfirmationMessage({ mode: 'stop', processCount: 3, cronCount: 0, shellCount: 2 }),
        '2 background shells will be killed.',
    )
    assert.equal(batchStopConfirmationMessage({ mode: 'archive', processCount: 1 }), '1 running process will be stopped.')
})

test('archive label mentions the shells it kills', () => {
    assert.equal(archiveStopLabel('Archive session', 'Claude', 0), 'Archive session (it will stop the Claude process)')
    assert.equal(
        archiveStopLabel('Archive', 'Codex', 2),
        'Archive (it will stop the Codex process and kill 2 background shells)',
    )
})

test('deferred startup settings text', () => {
    assert.equal(
        startupSettingsDeferredText({ label: 'Claude', shellCount: 1, hasMessageText: false }),
        'The restart these settings need will happen once the 1 background shell ends, or when you stop the Claude process.',
    )
    assert.equal(
        startupSettingsDeferredText({ label: 'Claude', shellCount: 2, hasMessageText: true }),
        'The restart these settings need will happen once the 2 background shells end, or when you stop the Claude process. '
            + 'Your message will be sent now, before the restart.',
    )
    assert.equal(
        startupSettingsDeferredText({ label: 'Claude', shellCount: 1, hasMessageText: false, working: true }),
        'The restart these settings need will happen once Claude finishes its current work and the 1 background shell ends, '
            + 'or when you stop the Claude process.',
    )
})

test('orchestration idle-with-shells suffix', () => {
    assert.equal(idleWithShellsSuffix(0), '')
    assert.equal(idleWithShellsSuffix(1), ', 1 with background shells')
})
