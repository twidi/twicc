import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { i18n } from '../i18n/index.js'
import { resolveUiMessage } from '../i18n/uiMessages.js'
import { getUpdateInstructions, setUpdateInstructions } from '../utils/updateInstructions.js'

// Exercise toast construction and its real render component without opening the WebSocket or Pinia.
const source = readFileSync(new URL('./useWebSocket.js', import.meta.url), 'utf8')
const component = source.slice(source.indexOf('const UpdateToastContent = {'), source.indexOf('function createUpdateToast(version, onDismiss)'))
const handler = source.slice(source.indexOf('function createUpdateToast(version, onDismiss)'), source.indexOf('/**\n * Format the credit-detail'))

function harness(instructions = null) {
    setUpdateInstructions(instructions)
    const notifications = [], events = []
    const createUpdateToast = new Function('getUpdateInstructions', 'toast', 'h', 'i18n', 'window', `${component}\n${handler}; return createUpdateToast`)(
        getUpdateInstructions, { custom: (content, options) => { notifications.push({ ...options, content }); return { id: 'toast' } } },
        h, i18n, { dispatchEvent(event) { events.push(event.type) } },
    )
    return { createUpdateToast, notifications, events }
}

test('instructions match the installation mode and retain the brand View changes button', async () => {
    for (const command of ['uvx twicc@latest', 'uv tool upgrade twicc', '/custom/bin/python -m pip install --upgrade twicc']) {
        const fixture = harness({ before: 'Stop TwiCC, run:', command, after: 'Then restart TwiCC.' })
        fixture.createUpdateToast('1.5.0', () => {})
        const notification = fixture.notifications[0]
        assert.equal(notification.duration, Infinity)
        assert.equal(notification.title, 'TwiCC v1.5.0 is available')
        const body = notification.content.setup()()
        assert.equal(body.children[0].children[2].children, command)
        const button = body.children[1].children[0]
        assert.equal(button.type, 'wa-button')
        assert.equal(button.props.size, 'small')
        assert.equal(button.props.variant, 'brand')
        assert.equal(button.children, 'View changes')
        button.props.onClick()
        assert.deepEqual(fixture.events, ['open-changelog'])
        const html = await renderToString(h(notification.content))
        assert.ok(html.includes(command))
        assert.ok(html.includes('<code'))
        assert.ok(html.includes('Then restart TwiCC.'))
    }
})

test('instruction text stays escaped and the visible button follows the locale', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale; setUpdateInstructions(null) })
    const fixture = harness({ before: '<img src=x>', command: '<script>alert(1)</script>', after: '<b>restart</b>' })
    fixture.createUpdateToast('1.5.0', () => {})
    const content = fixture.notifications[0].content
    const html = await renderToString(h(content))
    assert.ok(html.includes('&lt;img src=x&gt;'))
    assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'))
    assert.ok(html.includes('&lt;b&gt;restart&lt;/b&gt;'))
    i18n.global.locale.value = 'pt-BR'
    assert.equal(content.setup()().children[1].children[0].children, i18n.global.t('session.notifications.viewChanges'))
    assert.notEqual(content.setup()().children[1].children[0].children, 'View changes')
})

test('the persistent toast exposes its dismissal callback and handle', () => {
    const fixture = harness()
    let dismissed = false
    const result = fixture.createUpdateToast('1.5.0', () => { dismissed = true })
    assert.deepEqual(result, { id: 'toast' })
    assert.equal(dismissed, false)
    fixture.notifications[0].onManualClear()
    assert.equal(dismissed, true)
})

test('toast.custom forwards the dismissal callback to Notivue', () => {
    const toastSource = readFileSync(new URL('./useToast.js', import.meta.url), 'utf8')
    const customSource = toastSource.slice(toastSource.indexOf('function custom('), toastSource.indexOf('/**\n * Show a session-related toast'))
    let received
    const custom = new Function('push', 'markRaw', 'resolveUiMessage', `${customSource}; return custom`)(
        { info: options => { received = options } }, value => value, resolveUiMessage,
    )
    const callback = () => {}
    custom({ type: 'info', duration: Infinity, onManualClear: callback, html: 'Instructions' })
    assert.equal(received.onManualClear, callback)
    assert.equal(received.duration, Infinity)
    assert.equal(received.props.html, 'Instructions')
})
