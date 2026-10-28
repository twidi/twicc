import test from 'node:test'
import assert from 'node:assert/strict'
import { dayKeyOfTimestamp, formatDayKey, refreshDayKeysTimeZone } from './date.js'

const uncached = value => {
    const ms = Date.parse(value)
    return Number.isFinite(ms) ? formatDayKey(ms) : null
}

test('dayKeyOfTimestamp gives the day key of formatDayKey(Date.parse(value)), cached or not', () => {
    const values = ['2026-10-28T08:00:00.123456+00:00', '2026-10-28T23:59:59Z', '2026-10-29T00:00:00.000+02:00',
        '2026-01-01T00:30:00-05:00', '1970-01-01T00:00:00Z', 'not a date', '', null, undefined, 0, 1_800_000_000_000,
        new Date('2026-10-28T12:00:00Z')]
    for (const value of values) {
        assert.equal(dayKeyOfTimestamp(value), uncached(value), `first read of ${value}`)
        assert.equal(dayKeyOfTimestamp(value), uncached(value), `cached read of ${value}`)
    }
})

test('a time zone change drops the cached keys once refreshed', () => {
    const late = '2026-10-28T23:30:00Z'
    process.env.TZ = 'UTC'
    refreshDayKeysTimeZone()
    assert.equal(dayKeyOfTimestamp(late), '2026-10-28')
    process.env.TZ = 'Europe/Paris'
    refreshDayKeysTimeZone()
    assert.equal(dayKeyOfTimestamp(late), '2026-10-29')
})

test('the keys stay right past the cache limit', () => {
    const base = Date.parse('2026-01-01T00:00:00Z')
    for (let i = 0; i < 60_000; i++) {
        const value = new Date(base + i * 3_600_000).toISOString()
        assert.equal(dayKeyOfTimestamp(value), uncached(value))
    }
    assert.equal(dayKeyOfTimestamp('2026-10-28T08:00:00Z'), uncached('2026-10-28T08:00:00Z'))
})
