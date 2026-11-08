import test from 'node:test'
import assert from 'node:assert/strict'
import dayjs from 'dayjs'
import { createLocaleManager } from './index.js'

function deferred() {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
}

function browserDocument() {
    const properties = new Map()
    return { documentElement: { lang: 'en', style: { setProperty: (key, value) => properties.set(key, value) } }, properties }
}

test('the runtime loader registers Portuguese dates only when Portuguese is requested', async () => {
    const manager = createLocaleManager()
    await manager.setLocale('en', null)
    assert.equal(dayjs.Ls['pt-br'], undefined)
    assert.deepEqual(manager.i18n.global.availableLocales, ['en'])
    await manager.setLocale('pt-BR', null)
    assert.equal(dayjs('2026-11-07').locale('pt-br').format('MMMM'), 'novembro')
    assert.equal(dayjs.locale(), 'en', 'Loading date messages must not change the global Day.js locale')
    assert.equal(manager.i18n.global.t('home.welcome'), 'Boas-vindas ao TwiCC')
})

test('English startup and invalid locales do not request another catalog', async () => {
    let requests = 0
    const manager = createLocaleManager({ 'pt-BR': () => { requests++; return {} } })
    assert.deepEqual(manager.i18n.global.availableLocales, ['en'])
    await manager.setLocale('en', null)
    await manager.setLocale('unsupported', null)
    assert.equal(requests, 0)
    assert.equal(manager.i18n.global.locale.value, 'en')
})

test('a locale becomes visible only after its catalog loads; later switches use the cache', async () => {
    const catalog = deferred()
    let requests = 0
    const manager = createLocaleManager({ 'pt-BR': () => { requests++; return catalog.promise } })
    const document = browserDocument()
    const loading = manager.setLocale('pt-BR', document)
    assert.equal(manager.i18n.global.locale.value, 'en')
    assert.equal(document.documentElement.lang, 'en')
    catalog.resolve({ 'common.status.loading': 'Carregando...', 'test.message': 'Português' })
    await loading
    assert.equal(manager.i18n.global.locale.value, 'pt-BR')
    assert.equal(manager.i18n.global.t('test.message'), 'Português')
    assert.equal(document.documentElement.lang, 'pt-BR')
    assert.equal(document.properties.get('--twicc-loading-label'), '"Carregando..."')
    await manager.setLocale('en', document)
    await manager.setLocale('pt-BR', document)
    assert.equal(requests, 1)
})

test('simultaneous requests share the download and the last language choice wins', async () => {
    const catalog = deferred()
    let requests = 0
    const manager = createLocaleManager({ 'pt-BR': () => { requests++; return catalog.promise } })
    const document = browserDocument()
    const first = manager.setLocale('pt-BR', document)
    const second = manager.setLocale('pt-BR', document)
    await manager.setLocale('en', document)
    catalog.resolve({ 'common.status.loading': 'Carregando...' })
    await Promise.all([first, second])
    assert.equal(requests, 1)
    assert.equal(manager.i18n.global.locale.value, 'en')
    assert.equal(document.documentElement.lang, 'en')
    await manager.setLocale('pt-BR', document)
    assert.equal(document.documentElement.lang, 'pt-BR')
    assert.equal(requests, 1)
})

test('a failed download uses English and allows a later retry', async context => {
    const warning = context.mock.method(console, 'warn', () => {})
    let requests = 0
    const manager = createLocaleManager({ 'pt-BR': () => {
        if (++requests === 1) throw new Error('Offline')
        return { 'common.status.loading': 'Carregando...' }
    } })
    const document = browserDocument()
    await manager.setLocale('pt-BR', document)
    assert.equal(manager.i18n.global.locale.value, 'en')
    assert.equal(document.documentElement.lang, 'en')
    assert.equal(warning.mock.calls.length, 1)
    await manager.setLocale('pt-BR', document)
    assert.equal(manager.i18n.global.locale.value, 'pt-BR')
    assert.equal(requests, 2)
})

test('loaded catalogs retain English fallback and isolated messages in new composers', async () => {
    const manager = createLocaleManager({ 'pt-BR': async () => ({ 'common.status.loading': 'Carregando...' }) })
    await manager.loadLocaleMessages('pt-BR')
    const instance = manager.createAppI18n('pt-BR')
    instance.global.missingWarn = false
    instance.global.fallbackWarn = false
    assert.equal(instance.global.t('common.actions.cancel'), 'Cancel')
    instance.global.mergeLocaleMessage('pt-BR', { 'test.private': 'Only this composer' })
    assert.equal(manager.i18n.global.te('test.private', 'pt-BR'), false)
    assert.equal(manager.i18n.global.locale.value, 'en')
})

test('setLocale reports the rendered language after fallback and explicit retry', async () => {
    let requests = 0
    const manager = createLocaleManager({ fr: async () => {
        if (++requests === 1) throw new Error('Offline catalog')
        return {}
    } })
    assert.equal(await manager.setLocale('fr'), 'en')
    assert.equal(await manager.setLocale('fr'), 'fr')
    assert.equal(manager.i18n.global.locale.value, 'fr')
})
