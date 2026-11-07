import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createAppI18n, readInitialLocale, loadLocaleMessages } from './index.js'
import { localUiMessage, resolveUiMessage } from './uiMessages.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

const source = readFileSync(new URL('../views/LoginView.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileTemplate({
    source: descriptor.template.content,
    filename: 'LoginView.vue',
    id: 'login-i18n',
    compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') },
})
assert.deepEqual(compiled.errors, [])
const render = runInNewContext(`(function() { ${compiled.code} })()`, { Vue })

async function renderLogin(savedLocale, error = '') {
    const locale = readInitialLocale({ getItem: () => JSON.stringify({ locale: savedLocale }) })
    const instance = createAppI18n(locale)
    const app = Vue.createSSRApp({
        render,
        data: () => ({ password: '', error, loading: false, handleSubmit() {}, resolveUiMessage: message => resolveUiMessage(message, instance.global) }),
    })
    app.use(instance)
    app.component('BrandLogo', { render: () => Vue.h('span', 'TwiCC') })
    return renderToString(app)
}

test('login renders the restored locale before the authenticated settings bootstrap', async () => {
    const english = await renderLogin('en')
    assert.match(english, /placeholder="Enter password"/)
    assert.match(english, /Sign in/)
    const portuguese = await renderLogin('pt-BR')
    assert.match(portuguese, /placeholder="Digite a senha"/)
    assert.match(portuguese, /Entrar/)
    assert.doesNotMatch(portuguese, /Password required to continue/)
    const fallback = await renderLogin('unknown')
    assert.match(fallback, /placeholder="Enter password"/)
})

test('login translates known errors and preserves server-supplied error text', async () => {
    assert.match(await renderLogin('pt-BR', localUiMessage('auth.errors.invalidPassword')), /Senha inválida/)
    assert.match(await renderLogin('pt-BR', localUiMessage('auth.errors.network')), /Erro de rede/)
    assert.match(await renderLogin('pt-BR', 'Custom server error'), /Custom server error/)
    assert.match(await renderLogin('pt-BR', 'Network error'), /Network error/)
})
