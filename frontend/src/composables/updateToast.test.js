import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Exercise toast construction without initializing the WebSocket or Pinia.
const source = readFileSync(new URL('./useWebSocket.js', import.meta.url), 'utf8')
const handler = source.slice(source.indexOf('function createUpdateToast(version, onDismiss)'), source.indexOf('/**\n * Format the credit-detail'))

function harness(isUvxMode = false) {
    const notifications = []
    const createUpdateToast = new Function('useSettingsStore', 'toast', `${handler}; return createUpdateToast`)(
        () => ({ isUvxMode }), { custom: options => { notifications.push(options); return { id: 'toast' } } },
    )
    return { createUpdateToast, notifications }
}

test('instructions match the installation mode and retain the brand View changes button', () => {
    for (const mode of [false, true]) {
        const h = harness(mode)
        h.createUpdateToast('1.5.0', () => {})
        const notification = h.notifications[0]
        assert.equal(notification.duration, Infinity)
        assert.equal(notification.title, 'TwiCC v1.5.0 is available')
        assert.ok(notification.html.includes(mode ? 'uvx twicc@latest' : 'uv tool upgrade twicc'))
        assert.ok(notification.html.includes('<wa-button size="small" variant="brand"'))
        assert.ok(notification.html.includes('View changes'))
        assert.ok(notification.html.includes('open-changelog'))
    }
})

test('the persistent toast exposes its dismissal callback and handle', () => {
    const h = harness()
    let dismissed = false
    const result = h.createUpdateToast('1.5.0', () => { dismissed = true })
    assert.deepEqual(result, { id: 'toast' })
    assert.equal(dismissed, false)
    h.notifications[0].onManualClear()
    assert.equal(dismissed, true)
})

test('toast.custom forwards the dismissal callback to Notivue', () => {
    const toastSource = readFileSync(new URL('./useToast.js', import.meta.url), 'utf8')
    const customSource = toastSource.slice(toastSource.indexOf('function custom('), toastSource.indexOf('/**\n * Show a session-related toast'))
    let received
    const custom = new Function('push', 'markRaw', `${customSource}; return custom`)(
        { info: options => { received = options } }, value => value,
    )
    const callback = () => {}
    custom({ type: 'info', duration: Infinity, onManualClear: callback, html: 'Instructions' })
    assert.equal(received.onManualClear, callback)
    assert.equal(received.duration, Infinity)
    assert.equal(received.props.html, 'Instructions')
})
