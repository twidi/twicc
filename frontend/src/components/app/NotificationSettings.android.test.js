import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'

async function render(android, permission) {
    const { descriptor } = parse(readFileSync(new URL('./NotificationSettings.vue', import.meta.url), 'utf8'))
    const store = { getExternalNotificationTargets: [], getNotifUserTurnSound:'none', getNotifPendingRequestSound:'none',
        getNotifExtraUsageStartSound:'none', getNotifPeerSound:'none', isNotifUserTurnBrowser:true }
    const script = compileScript(descriptor, { id: 'notifications' }).content
        .replace(/^import .*$/gm, '').replace('export default', 'const component =')
    const component = runInNewContext(`${script}; component`, { ...Vue,
        useSettingsStore:() => store, generateUUID:() => 'id', getAvailableSoundOptions:() => [], NOTIFICATION_SOUNDS:{},
        playNotificationSound() {}, isAndroidApp:Vue.ref(android), androidPushState:Vue.ref({ permission }),
        requestAndroidPushPermission() {}, retryAndroidPush() {}, window:{},
    })
    const ctx = component.setup({}, { expose() {}, emit() {} })
    const compiled = compileTemplate({ source: descriptor.template.content, filename:'NotificationSettings.vue', id:'notifications',
        compilerOptions:{ mode:'function', isCustomElement:tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const template = runInNewContext(`(function(){${compiled.code}})()`, { Vue })
    const app = Vue.createSSRApp({ render:template, data:() => ctx })
    app.config.warnHandler = () => {}
    return renderToString(app)
}
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
