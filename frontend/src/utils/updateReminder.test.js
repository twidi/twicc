import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createUpdateReminder, UPDATE_REMINDER_KEY, UPDATE_REMINDER_DELAY } from './updateReminder.js'

function harness({ storage = new Map(), locks, visible = true } = {}) {
    let time = 1000
    let versions = { current: '1.94.1', latest: '1.95.0' }
    let shown = visible
    const notifications = []
    const api = createUpdateReminder({
        storage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
        locks,
        now: () => time,
        isVisible: () => shown,
        getVersions: () => versions,
        show(version, dismiss) {
            const notification = { version, dismiss, destroyed: false }
            notifications.push(notification)
            return { destroy() { notification.destroyed = true; dismiss() } }
        },
    })
    return {
        api, notifications, storage,
        advance: duration => { time += duration },
        versions: value => { versions = value },
        visible: value => { shown = value },
        record: () => JSON.parse(storage.get(UPDATE_REMINDER_KEY)),
    }
}

test('the 24-hour interval starts at dismissal, even after the toast stays open for 23 hours', () => {
    const h = harness()
    h.api.check()
    h.advance(23 * 60 * 60 * 1000)
    h.api.check()
    assert.equal(h.notifications.length, 1)
    h.notifications[0].dismiss()
    const dismissedAt = h.record().dismissedAt
    h.advance(60 * 60 * 1000)
    h.api.check()
    assert.equal(h.notifications.length, 1)
    h.advance(23 * 60 * 60 * 1000)
    h.api.check()
    assert.equal(h.notifications.length, 2)
    assert.equal(h.record().dismissedAt, null)
    h.notifications[1].dismiss()
    assert.equal(h.record().dismissedAt, dismissedAt + UPDATE_REMINDER_DELAY)
})

test('a reload preserves the dismissal time and restores an undismissed toast', () => {
    const h = harness()
    h.api.check()
    h.notifications[0].dismiss()
    h.api.dispose()
    const reloaded = harness({ storage: h.storage })
    reloaded.api.check()
    assert.equal(reloaded.notifications.length, 0)
    reloaded.advance(UPDATE_REMINDER_DELAY)
    reloaded.api.check()
    assert.equal(reloaded.notifications.length, 1)
    reloaded.api.dispose()
    assert.equal(reloaded.record().dismissedAt, null)
    const again = harness({ storage: h.storage })
    again.api.check()
    assert.equal(again.notifications.length, 1)
})

test('a newer version notifies immediately and an obsolete toast cannot postpone it', () => {
    const h = harness()
    h.api.check()
    h.versions({ current: '1.94.1', latest: '1.96.0' })
    h.api.check()
    assert.equal(h.notifications[0].destroyed, true)
    assert.equal(h.notifications[1].version, '1.96.0')
    h.notifications[0].dismiss()
    assert.deepEqual(h.record(), { version: '1.96.0', dismissedAt: null })
})

test('installation clears the toast without starting a dismissal interval', () => {
    const h = harness()
    h.api.check()
    h.versions({ current: '1.95.0', latest: '1.95.0' })
    h.api.check()
    assert.equal(h.notifications[0].destroyed, true)
    h.advance(UPDATE_REMINDER_DELAY)
    h.api.check()
    assert.equal(h.notifications.length, 1)
    assert.equal(h.record().dismissedAt, null)
})

test('hidden tabs wait for visibility and manual clicks bypass the dismissal interval', () => {
    const h = harness({ visible: false })
    h.api.check()
    assert.equal(h.notifications.length, 0)
    h.visible(true)
    h.api.check()
    h.notifications[0].dismiss()
    h.api.showManually('1.95.0')
    h.api.showManually('1.95.0')
    assert.equal(h.notifications.length, 2)
    h.notifications[1].dismiss()
    h.api.check()
    assert.equal(h.notifications.length, 2)
})

test('another tab dismissal closes the local toast without changing its timestamp', () => {
    const h = harness()
    h.api.check()
    h.storage.set(UPDATE_REMINDER_KEY, JSON.stringify({ version: '1.95.0', dismissedAt: 2000 }))
    h.api.check()
    assert.equal(h.notifications[0].destroyed, true)
    assert.equal(h.record().dismissedAt, 2000)
})

test('legacy once-per-version notifications migrate to a single persisted 24-hour interval', () => {
    const h = harness({ storage: new Map([['twicc-update-notified-version', '1.95.0']]) })
    h.api.check()
    assert.equal(h.notifications.length, 0)
    assert.equal(h.record().dismissedAt, 1000)
    h.advance(UPDATE_REMINDER_DELAY)
    h.api.check()
    assert.equal(h.notifications.length, 1)
})

test('missing versions and malformed records do not block future notifications', () => {
    const h = harness({ storage: new Map([[UPDATE_REMINDER_KEY, '{broken']]) })
    h.versions({ current: null, latest: '1.95.0' })
    h.api.check()
    assert.equal(h.notifications.length, 0)
    h.versions({ current: '1.94.1', latest: '1.95.0' })
    h.api.check()
    assert.equal(h.notifications.length, 1)
})

test('Web Locks allow one toast per version and release the lock on disposal', async () => {
    const held = new Set()
    const locks = {
        async request(name, options, callback) {
            if (held.has(name)) return callback(null)
            held.add(name)
            try { await callback({ name }) } finally { held.delete(name) }
        },
    }
    const storage = new Map()
    const first = harness({ storage, locks })
    const second = harness({ storage, locks })
    first.api.check()
    second.api.check()
    assert.equal(first.notifications.length, 1)
    assert.equal(second.notifications.length, 0)
    first.api.dispose()
    await Promise.resolve()
    second.api.check()
    assert.equal(second.notifications.length, 1)
    second.api.dispose()
})

test('every dismissal starts a fresh full day, including manual reopenings', () => {
    const h = harness()
    h.api.check()
    for (let cycle = 0; cycle < 3; cycle++) {
        h.notifications.at(-1).dismiss()
        h.advance(UPDATE_REMINDER_DELAY - 1)
        h.api.check()
        assert.equal(h.notifications.length, cycle + 1)
        h.advance(1)
        h.api.check()
        assert.equal(h.notifications.length, cycle + 2)
    }
})

test('a newly published version bypasses the previous version dismissal delay', () => {
    const h = harness()
    h.versions({ current: '1.9.0', latest: '1.9.2' })
    h.api.check()
    h.notifications[0].dismiss()
    h.versions({ current: '1.9.0', latest: '1.10.0' })
    h.api.check()
    assert.equal(h.notifications[1].version, '1.10.0')
})

test('unavailable storage still respects the dismissal delay within the current tab', () => {
    let time = 0
    let dismiss
    let count = 0
    const api = createUpdateReminder({
        storage: { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } },
        getVersions: () => ({ current: '1.0.0', latest: '2.0.0' }),
        isVisible: () => true,
        now: () => time,
        show(version, onDismiss) { count++; dismiss = onDismiss; return { destroy() {} } },
    })
    api.check()
    dismiss()
    api.check()
    assert.equal(count, 1)
    time = UPDATE_REMINDER_DELAY
    api.check()
    assert.equal(count, 2)
})

test('delayed lock callbacks recheck installation and disposal before showing a toast', async () => {
    for (const dispose of [false, true]) {
        let grant
        const locks = { request(name, options, callback) { grant = () => callback({ name }); return Promise.resolve() } }
        const h = harness({ locks })
        h.api.check()
        if (dispose) h.api.dispose()
        else h.versions({ current: '1.95.0', latest: '1.95.0' })
        await grant()
        assert.equal(h.notifications.length, 0)
    }
})
