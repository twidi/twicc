import test from 'node:test'
import assert from 'node:assert/strict'
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { createAppI18n } from '../../i18n/index.js'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'

async function fixture(android, permission, status = 'ready', soundLibrary = 'ready', overrides = {}) {
    const { descriptor } = parse(readFileSync(new URL('./NotificationSettings.vue', import.meta.url), 'utf8'))
    const store = { getExternalNotificationTargets: [], getNotifUserTurnSound:'chime', getNotifPendingRequestSound:'signal',
        getNotifExtraUsageStartSound:'sweep', getNotifPeerSound:'ripple', isNotifUserTurnBrowser:true,
        shouldNotifyOnExtraUsageStart:true, isNotifExtraUsageStartBrowser:true, isNotifUserTurnToast:true, ...overrides.store }
    const script = compileScript(descriptor, { id: 'notifications' }).content
        .replace(/^import .*$/gm, '').replace('export default', 'const component =')
    const component = runInNewContext(`${script}; component`, { ...Vue,
        useI18n: () => createAppI18n().global, useSettingsStore:() => store, generateUUID:() => 'id', getAvailableSoundOptions:() => [], NOTIFICATION_SOUNDS:{},
        playNotificationSound() {}, isAndroidApp:Vue.ref(android), androidPushState:Vue.ref({ permission, status, soundLibrary }),
        requestAndroidPushPermission() {}, retryAndroidPush() {}, openAndroidNotificationSoundSettings() {}, prepareAndroidNotificationSounds() {},
        localUiMessage, resolveUiMessage, window:{}, Notification: overrides.window?.Notification, ...overrides,
    })
    const ctx = component.setup({}, { expose() {}, emit() {} })
    const compiled = compileTemplate({ source: descriptor.template.content, filename:'NotificationSettings.vue', id:'notifications',
        compilerOptions:{ mode:'function', isCustomElement:tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const template = runInNewContext(`(function(){${compiled.code}})()`, { Vue })
    let rendered
    function render() {
        const app = Vue.createSSRApp({ render(...args) { rendered = template(...args); return rendered }, data:() => ctx }).use(createAppI18n())
        app.config.warnHandler = () => {}
        return renderToString(app)
    }
    function categoryActions() {
        const nodes = []
        function visit(node) {
            if (node?.props?.class === 'notif-android-sound-settings') nodes.push(node)
            if (Array.isArray(node?.children)) node.children.forEach(visit)
        }
        visit(rendered)
        return nodes
    }
    return { ctx, render, store, categoryActions }
}
async function render(...args) { return (await fixture(...args)).render() }

test('Android shows four independent switches even when system permission is denied', async () => {
    const html = await render(true, 'denied')
    assert.equal((html.match(/Android notification<\/wa-switch>/g) || []).length, 4)
    assert.match(html, /Open Android settings/)
    assert.doesNotMatch(html, /Enable browser notifications|Browser notifications not supported/)
})
test('ordinary browsers retain browser permission gating and never offer Android settings', async () => {
    const html = await render(false)
    assert.match(html, /Browser notifications not supported/)
    assert.doesNotMatch(html, /Android notification|Open Android settings/)
})


test('compatibility states show explicit app and server update instructions', async () => {
    assert.match(await render(true, 'granted', 'update_app'), /Update the Android app/)
    assert.match(await render(true, 'granted', 'update_server'), /Update this TwiCC server.*generic message/)
    assert.match(await render(true, 'denied', 'update_app'), /notifications are blocked/)
})


test('Android replaces all four web selectors and previews with independent category actions', async () => {
    const html = await render(true, 'denied')
    assert.equal((html.match(/class="notif-android-sound-settings"/g) || []).length, 4)
    assert.doesNotMatch(html, /<wa-select|notif-test-link/)
    assert.match(html, /saved instances/)
    assert.match(html, /In-app toast/)
    assert.match(html, /<wa-switch checked size="small">Notify me/)
})
test('ordinary browsers keep all four selectors and sound previews', async () => {
    const html = await render(false)
    assert.equal((html.match(/<wa-select/g) || []).length, 4)
    assert.equal((html.match(/class="notif-test-link"/g) || []).length, 4)
    assert.doesNotMatch(html, /notif-android-sound-settings|saved instances/)
})
test('sound publication hints use soundLibrary independently of registration status', async () => {
    const preparing = await render(true, 'granted', 'server', 'preparing')
    assert.match(preparing, /Preparing notification sounds/)
    assert.match(preparing, /server/)
    const failed = await render(true, 'granted', 'ready', 'error')
    assert.match(failed, /Could not prepare notification sounds/)
    assert.match(failed, /Retry sound preparation/)
    assert.doesNotMatch(await render(true, 'granted', 'ready', 'ready'), /Preparing notification sounds|Could not prepare|Retry sound preparation/)
})
test('category actions pass their kind without changing ordinary browser sound preferences', async () => {
    const kinds = []
    const f = await fixture(true, 'denied', 'ready', 'ready', { openAndroidNotificationSoundSettings: async kind => kinds.push(kind) })
    await f.render()
    const actions = f.categoryActions()
    assert.equal(actions.length, 4)
    for (const action of actions) {
        assert.notEqual(action.props.disabled, true)
        await action.props.onClick()
    }
    assert.deepEqual(kinds, ['user_turn', 'pending_request', 'extra_usage', 'peer'])
    assert.equal(f.store.getNotifUserTurnSound, 'chime')
    assert.equal(f.ctx.androidSoundError.value, null)
})
test('rejected category action and preparation retry show localized feedback', async () => {
    let attempts = 0
    const f = await fixture(true, 'denied', 'ready', 'error', {
        openAndroidNotificationSoundSettings: async () => { throw new Error('Bridge internals') },
        prepareAndroidNotificationSounds: async () => { attempts++; throw new Error('Bridge internals') },
    })
    await f.ctx.openSoundSettings('peer')
    assert.match(await f.render(), /Could not complete this action/)
    assert.doesNotMatch(await f.render(), /Bridge internals/)
    await f.ctx.retrySoundPreparation()
    assert.equal(attempts, 1)
    assert.match(await f.render(), /Could not complete this action/)
})


test('ordinary browser notification controls keep permission gating', async () => {
    for (const permission of ['default', 'denied', 'granted']) {
        const f = await fixture(false, undefined, 'ready', 'ready', { window: { Notification: { permission } } })
        const html = await f.render()
        assert.equal((html.match(/Browser notification<\/wa-switch>/g) || []).length, permission === 'granted' ? 4 : 0)
        if (permission === 'default') assert.match(html, /Enable browser notifications/)
        if (permission === 'denied') assert.match(html, /Browser notifications blocked/)
    }
})
test('successful sound preparation retry clears action errors without claiming selection', async () => {
    let attempts = 0
    const state = Vue.ref({ permission: 'granted', status: 'ready', soundLibrary: 'error' })
    const f = await fixture(true, 'granted', 'ready', 'error', { androidPushState: state,
        openAndroidNotificationSoundSettings: async () => { throw new Error() },
        prepareAndroidNotificationSounds: async () => { attempts++; state.value.soundLibrary = 'preparing' },
    })
    await f.ctx.openSoundSettings('user_turn')
    await f.ctx.retrySoundPreparation()
    assert.equal(attempts, 1)
    assert.equal(f.ctx.androidSoundError.value, null)
    assert.match(await f.render(), /Preparing notification sounds/)
    state.value.soundLibrary = 'ready'
    assert.doesNotMatch(await f.render(), /Preparing notification sounds|Could not complete this action/)
})
