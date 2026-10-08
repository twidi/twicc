import assert from 'node:assert/strict'
import test from 'node:test'

import {
    AUTO_DENY_DELAY_SECONDS,
    formatAutoDenyRemaining,
    isAutoDenyLocked,
    withAutoDenyDeadlines,
    withoutAutoDenyKeys,
} from './autoDeny.js'

const armed = (id, seconds) => ({ request_id: id, tool_name: 'Bash', auto_deny_in_seconds: seconds })

test('the delay mirrors the backend constant', () => {
    assert.equal(AUTO_DENY_DELAY_SECONDS, 120)
})

test('an armed entry gets a local deadline and loses the wire key', () => {
    const [entry] = withAutoDenyDeadlines([armed('r-1', 42.5)], 10_000)
    assert.equal(entry.autoDenyDeadlineMs, 52_500)
    assert.equal(Object.hasOwn(entry, 'auto_deny_in_seconds'), false)
    assert.equal(entry.tool_name, 'Bash')
})

test('an unarmed entry is returned unchanged', () => {
    const plain = { request_id: 'r-1', tool_name: 'AskUserQuestion' }
    const [entry] = withAutoDenyDeadlines([plain], 10_000)
    assert.equal(entry, plain)
})

test('a close re-stamp keeps the previous deadline', () => {
    const [first] = withAutoDenyDeadlines([armed('r-1', 60)], 10_000)
    const [second] = withAutoDenyDeadlines([armed('r-1', 55)], 15_300, [first])
    assert.equal(second.autoDenyDeadlineMs, first.autoDenyDeadlineMs)
    assert.deepEqual(second, first)
})

test('a far re-stamp takes the new deadline', () => {
    const [first] = withAutoDenyDeadlines([armed('r-1', 60)], 10_000)
    const [second] = withAutoDenyDeadlines([armed('r-1', 60)], 12_000, [first])
    assert.equal(second.autoDenyDeadlineMs, 72_000)
})

test('the previous deadline is matched by request id', () => {
    const [first] = withAutoDenyDeadlines([armed('r-1', 60)], 10_000)
    const [other] = withAutoDenyDeadlines([armed('r-2', 30)], 10_100, [first])
    assert.equal(other.autoDenyDeadlineMs, 40_100)
})

test('the lock starts when less than one second is left', () => {
    assert.equal(isAutoDenyLocked(10_000, 8_999), false)
    assert.equal(isAutoDenyLocked(10_000, 9_001), true)
    assert.equal(isAutoDenyLocked(10_000, 12_000), true)
})

test('the remaining time reads m:ss, rounded down, floored at 0:00', () => {
    assert.equal(formatAutoDenyRemaining(130_000, 10_000), '2:00')
    assert.equal(formatAutoDenyRemaining(112_400, 10_000), '1:42')
    assert.equal(formatAutoDenyRemaining(19_999, 10_000), '0:09')
    assert.equal(formatAutoDenyRemaining(10_900, 10_000), '0:00')
    assert.equal(formatAutoDenyRemaining(5_000, 10_000), '0:00')
})

test('a reconnect after the deadline is locked at once', () => {
    const [entry] = withAutoDenyDeadlines([armed('r-1', 0)], 10_000)
    assert.equal(isAutoDenyLocked(entry.autoDenyDeadlineMs, 10_000), true)
    assert.equal(formatAutoDenyRemaining(entry.autoDenyDeadlineMs, 10_000), '0:00')
})

test('the draft hash input drops both deadline keys', () => {
    const base = { request_id: 'r-1', tool_name: 'Bash', created_at: 1 }
    assert.deepEqual(withoutAutoDenyKeys({ ...base, autoDenyDeadlineMs: 5 }), base)
    assert.deepEqual(withoutAutoDenyKeys({ ...base, auto_deny_in_seconds: 5 }), base)
    assert.equal(withoutAutoDenyKeys(base), base)
})
