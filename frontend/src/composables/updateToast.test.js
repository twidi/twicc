import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compareVersions } from '../utils/version.js'

// Exercise the notification handlers without initializing the WebSocket or Pinia.
const source = readFileSync(new URL('./useWebSocket.js', import.meta.url), 'utf8')
const handlers = source.slice(source.indexOf('function handleUpdateAvailable(msg)'), source.indexOf('/**\n * Format the credit-detail'))
    .replace('export function showUpdateToast', 'function showUpdateToast')

function harness(isUvxMode = false) {
    const notifications = []
    const stored = new Map()
    const localStorage = {
        getItem: key => stored.get(key) ?? null,
        setItem: (key, value) => stored.set(key, value),
    }
    const api = new Function('localStorage', 'compareVersions', 'useSettingsStore', 'toast', 'UPDATE_NOTIFIED_VERSION_KEY',
        `${handlers}; return { handleUpdateAvailable, showUpdateToast }`)(
        localStorage, compareVersions, () => ({ isUvxMode }), { custom: options => notifications.push(options) }, 'notified',
    )
    return { ...api, notifications, stored }
}

test('automatic updates notify once while manual clicks can reopen the toast', () => {
    const h = harness()
    h.handleUpdateAvailable({ latest_version: '1.10.0' })
    h.handleUpdateAvailable({ latest_version: '1.10.0' })
    h.handleUpdateAvailable({ latest_version: '1.9.0' })
    assert.equal(h.notifications.length, 1)
    h.showUpdateToast('1.10.0')
    assert.equal(h.notifications.length, 2)
    assert.deepEqual(h.notifications[1], h.notifications[0])
    assert.equal(h.stored.get('notified'), '1.10.0')
    h.handleUpdateAvailable({ latest_version: '1.11.0' })
    assert.equal(h.notifications.length, 3)
})

test('instructions match the installation mode and retain View changes', () => {
    for (const mode of [false, true]) {
        const h = harness(mode)
        h.showUpdateToast('1.5.0')
        const notification = h.notifications[0]
        assert.equal(notification.duration, Infinity)
        assert.equal(notification.title, 'TwiCC v1.5.0 is available')
        assert.ok(notification.html.includes(mode ? 'uvx twicc@latest' : 'uv tool upgrade twicc'))
        assert.ok(notification.html.includes('View changes'))
        assert.ok(notification.html.includes('open-changelog'))
        assert.equal(h.stored.size, 0)
    }
})
