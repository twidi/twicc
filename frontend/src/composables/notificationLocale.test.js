import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { i18n } from '../i18n/index.js'
import { localUiMessage, resolveUiMessage } from '../i18n/uiMessages.js'

const toastSource = readFileSync(new URL('./useToast.js', import.meta.url), 'utf8')
const appSource = readFileSync(new URL('../App.vue', import.meta.url), 'utf8')
function harness() {
    let payload
    const push = Object.fromEntries(['info', 'error', 'success', 'warning'].map(type => [type, options => { payload = options }]))
    const toast = new Function('push', 'markRaw', 'defineAsyncComponent', 'resolveUiMessage',
        toastSource.replace(/^import .*$/gm, '').replace(/^export /gm, '') + '; return toast')(
        push, value => value, () => ({ setup() {} }), resolveUiMessage)
    const source = appSource.slice(appSource.indexOf('function notificationTitle('), appSource.indexOf('// Notivue theme:'))
    const display = new Function('resolveUiMessage', 't', 'ProviderAuthToastContent', source + '; return {notificationTitle,notificationItem}')(
        resolveUiMessage, i18n.global.t, {})
    return { toast, display, get payload() { return payload } }
}

test('normal and custom retained notifications preserve string payloads and resolve local metadata', context => {
    context.after(() => { i18n.global.locale.value = 'en' })
    const fixture = harness()
    fixture.toast.error(localUiMessage('session.summary.httpFailed', { status: 503 }), {
        title: localUiMessage('session.notifications.updateAvailable', { version: '1.2.3' }),
    })
    const normal = fixture.payload
    assert.equal(typeof normal.message, 'string')
    assert.equal(typeof normal.title, 'string')
    i18n.global.locale.value = 'pt-BR'
    const rendered = fixture.display.notificationItem(normal)
    assert.equal(rendered.message, i18n.global.t('session.summary.httpFailed', { status: 503 }))
    assert.equal(rendered.title, i18n.global.t('session.notifications.updateAvailable', { version: '1.2.3' }))
    fixture.toast.custom({ title: 'Original title', titleKey: 'session.notifications.updateAvailable', titleParams: { version: '9.8' } })
    assert.equal(fixture.display.notificationTitle(fixture.payload), i18n.global.t('session.notifications.updateAvailable', { version: '9.8' }))
    fixture.toast.info('External message', { title: 'External title' })
    assert.equal(fixture.display.notificationItem(fixture.payload).message, 'External message')
    assert.equal(fixture.display.notificationItem(fixture.payload).title, 'External title')
})

test('session notification forwards local detail and error descriptors unchanged', () => {
    const fixture = harness(), detail = localUiMessage('session.notifications.shells', { count: 2 }, 2)
    const error = localUiMessage('session.notifications.unknownError')
    fixture.toast.session('s', { title: localUiMessage('session.notifications.invalidTitle'), detailMessage: detail, errorMessageDescriptor: error })
    assert.equal(fixture.payload.props.titleMessage.key, 'session.notifications.invalidTitle')
    assert.deepEqual(fixture.payload.props.contentProps.detailMessage, detail)
    assert.deepEqual(fixture.payload.props.contentProps.errorMessageDescriptor, error)
})

test('extra usage content renders locale-aware numbers and text without interpreting HTML', async context => {
    context.after(() => { i18n.global.locale.value = 'en' })
    const source = readFileSync(new URL('./useWebSocket.js', import.meta.url), 'utf8')
    const component = source.slice(source.indexOf('const ExtraUsageToastContent = {'), source.indexOf('function createUpdateToast('))
    const formatter = source.slice(source.indexOf('function formatExtraUsageStartedDetail('), source.indexOf('\nexport function useWebSocket()'))
    const content = new Function('h', 'i18n', 'getProviderLabel', 'getProviderIcon', 'getProviderIconColor', 'formatExtraUsageAmount',
        component + formatter + '; return ExtraUsageToastContent')(h, i18n, () => '<Provider>', () => null, () => null, () => null)
    const props = { provider: 'test', extra: { utilization: 1, used_credits: 1234.5, monthly_limit: 2000 } }
    const en = await renderToString(h(content, props))
    assert.match(en, /1,234\.5/)
    assert.match(en, /&lt;Provider&gt;/)
    i18n.global.locale.value = 'pt-BR'
    const pt = await renderToString(h(content, props))
    assert.match(pt, /1\.234,5/)
    assert.match(pt, /&lt;Provider&gt;/)
    assert.notEqual(pt, en)
    assert.equal(props.extra.used_credits, 1234.5)
})
