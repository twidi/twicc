import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createAppI18n } from './index.js'
import { localUiMessage, resolveUiMessage } from './uiMessages.js'
import { remoteUiMessage } from './remoteMessages.js'
import { parseProcessError } from '../utils/errorParsing.js'

function calculations(file, names, context) {
    const source = readFileSync(new URL(`../components/${file}`, import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    const ast = ts.createSourceFile(file, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
    const statements = ast.statements.filter(node => node.declarationList?.declarations.some(d => names.includes(d.name.text)))
    assert.equal(statements.length, names.length)
    const values = runInNewContext(`${statements.map(n => n.getText(ast)).join('\n')}; ({ ${names.join(', ')} })`, { computed: Vue.computed, ...context })
    return { values, descriptor }
}

// These tests fail when a retained notification stores translated text or drops message parameters.
test('session toast error and detail descriptors change language while external text stays literal', async () => {
    const composer = createAppI18n('en').global
    const props = Vue.reactive({
        errorMessage: 'Raw backend fallback', detail: 'Raw detail fallback',
        errorMessageDescriptor: localUiMessage('session.rename.errors.saveFailed'),
        detailMessage: localUiMessage('artifacts.network.errors.request', { status: 503 }),
    })
    const { values, descriptor } = calculations('session/SessionToastContent.vue', ['resolvedErrorMessage', 'resolvedDetail', 'parsedError'], {
        props, resolveUiMessage: value => resolveUiMessage(value, composer), parseProcessError,
    })
    const compiled = compileTemplate({ source: descriptor.template.content, filename: 'SessionToastContent.vue', id: 'session-toast-error-test', compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const render = runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
    const app = Vue.createSSRApp({ render, setup: () => ({ ...values, projectId: null, sessionTitle: 'User title', isCurrentSession: true }) })
    app.component('ProjectBadge', { render: () => null })
    app.use({ install: app => { app.config.globalProperties.$t = composer.t } })
    assert.ok((await renderToString(app)).includes(composer.t('session.rename.errors.saveFailed')))
    composer.locale.value = 'pt-BR'
    const html = await renderToString(app)
    assert.ok(html.includes(composer.t('session.rename.errors.saveFailed')))
    assert.ok(html.includes(composer.t('artifacts.network.errors.request', { status: 503 })))
    props.errorMessageDescriptor = null
    props.detailMessage = null
    assert.equal(values.resolvedErrorMessage.value, 'Raw backend fallback')
    assert.equal(values.resolvedDetail.value, 'Raw detail fallback')
})

test('failed-send messages retain external parameters and follow locale changes', () => {
    const composer = createAppI18n('en').global
    const failedSend = Vue.ref({ messageKey: 'session.notifications.invalidTitleDetail', messageParams: { error: 'External title detail' }, message: 'Legacy fallback' })
    const { values } = calculations('session/detail/items/FailedSendBanner.vue', ['displayMessage'], {
        failedSend, composerReadyAgain: Vue.ref(false), t: composer.t, te: composer.te,
        localUiMessage, remoteUiMessage, resolveUiMessage: value => resolveUiMessage(value, composer),
    })
    assert.match(values.displayMessage.value, /External title detail/)
    composer.locale.value = 'pt-BR'
    assert.equal(values.displayMessage.value, composer.t(failedSend.value.messageKey, failedSend.value.messageParams))
    failedSend.value = { messageKey: 'unknown.external.key', message: 'External prose' }
    assert.equal(values.displayMessage.value, 'External prose')
    const params = { session_id: 'session_1' }
    failedSend.value = JSON.parse(JSON.stringify({ message: 'Retained English fallback',
        ui_message: { key: 'backend.websocket.session_missing', params } }))
    assert.equal(values.displayMessage.value, composer.t('backend.websocket.session_missing', params))
    composer.locale.value = 'en'
    assert.equal(values.displayMessage.value, composer.t('backend.websocket.session_missing', params))
    assert.equal(failedSend.value.message, 'Retained English fallback')
})
