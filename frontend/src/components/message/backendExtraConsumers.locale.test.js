import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { i18n, loadLocaleMessages } from '../../i18n/index.js'
import { remoteUiMessage } from '../../i18n/remoteMessages.js'
import { resolveUiMessage } from '../../i18n/uiMessages.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

function componentSource(path) {
    return readFileSync(new URL(path, import.meta.url), 'utf8')
}

function renderer(template, bindings) {
    const compiled = compileTemplate({ source: template, id: 'retained-backend-errors',
        compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const render = runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
    return () => renderToString(Vue.createSSRApp({ render, setup: () => ({ ...bindings, resolveUiMessage }) }))
}

test('history API failure renders translated prose after a locale change without fetching again', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    i18n.global.locale.value = 'en'
    const source = componentSource('./MessageHistoryPickerPopup.vue')
    const fn = source.match(/async function fetchMessages\([^]*?\n}/)?.[0]
    assert.ok(fn)
    const state = { loading: Vue.ref(false), error: Vue.ref(null), allMessages: Vue.ref([]) }
    let requests = 0
    let payload = { error: 'Session missing', ui_message: {
        key: 'backend.websocket.session_missing', params: { session_id: 'session_1' },
    } }
    const fetchMessages = runInNewContext(`(${fn})`, { ...state, remoteUiMessage,
        props: { projectId: 'project_1', sessionId: 'session_1' },
        apiFetch: async () => { requests++; return { ok: false, json: async () => payload } },
    })
    await fetchMessages()
    const template = source.match(/<div class="picker-status picker-error">[^]*?<\/div>/)?.[0]
    assert.ok(template)
    const render = renderer(template, { error: state.error })
    const english = i18n.global.t('backend.websocket.session_missing', { session_id: 'session_1' })
    assert.ok((await render()).includes(english))
    i18n.global.locale.value = 'pt-BR'
    const html = await render()
    assert.ok(html.includes(i18n.global.t('backend.websocket.session_missing', { session_id: 'session_1' })))
    assert.ok(!html.includes('local-ui-message'))
    assert.equal(requests, 1)

    payload = { error: 'External diagnostic stays literal' }
    await fetchMessages()
    assert.ok((await render()).includes(payload.error))
    i18n.global.locale.value = 'en'
    assert.ok((await render()).includes(payload.error))
})

test('selection summary renders its retained descriptor and preserves legacy error prose', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    i18n.global.locale.value = 'en'
    const source = componentSource('../session/detail/SelectionSummaryBody.vue')
    const template = source.match(/<wa-callout\b[^]*?<\/wa-callout>/)?.[0]
    assert.ok(template)
    const payload = { error: 'Cannot read file', ui_message: { key: 'backend.file_content.read_failed', params: {} } }
    const entry = Vue.ref({ error: payload.error, errorMessage: remoteUiMessage(payload) })
    const render = renderer(template, { entry })
    assert.ok((await render()).includes(i18n.global.t('backend.file_content.read_failed')))
    i18n.global.locale.value = 'pt-BR'
    const html = await render()
    assert.ok(html.includes(i18n.global.t('backend.file_content.read_failed')))
    assert.ok(!html.includes('local-ui-message'))
    entry.value = { error: 'Raw provider error' }
    assert.ok((await render()).includes('Raw provider error'))
    i18n.global.locale.value = 'en'
    assert.ok((await render()).includes('Raw provider error'))
    entry.value = { error: null, errorMessage: remoteUiMessage(payload) }
    assert.ok((await render()).includes(i18n.global.t('backend.file_content.read_failed')))
})
