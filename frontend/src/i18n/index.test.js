import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { baseCompile } from '@intlify/message-compiler'
import { createRenderer, h, nextTick } from 'vue'
import { createAppI18n, DEFAULT_LOCALE, i18n, isSupportedLocale, readInitialLocale, setLocale, loadLocaleMessages } from './index.js'
import en from './locales/en.json' with { type: 'json' }
import fr from './locales/fr.json' with { type: 'json' }
import ptBR from './locales/pt-BR.json' with { type: 'json' }

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')
await loadLocaleMessages('fr')

const storage = value => ({ getItem: () => value })

test('English is the default; only registered locales are accepted', () => {
    assert.equal(DEFAULT_LOCALE, 'en')
    for (const value of [undefined, null, '', 'pt', 'de', '__proto__', {}, 1]) {
        assert.equal(isSupportedLocale(value), false)
        assert.equal(createAppI18n(value).global.locale.value, 'en')
    }
    assert.equal(isSupportedLocale('pt-BR'), true)
    assert.equal(isSupportedLocale('fr'), true)
})

test('saved locale is restored before login; corrupt or unavailable storage is harmless', () => {
    assert.equal(readInitialLocale(storage('{"locale":"pt-BR","colorScheme":"dark"}')), 'pt-BR')
    assert.equal(readInitialLocale(storage('{"locale":"fr"}')), 'fr')
    for (const value of [null, '{', 'null', '{}', '{"locale":"de"}', '{"locale":12}']) {
        assert.equal(readInitialLocale(storage(value)), 'en')
    }
    assert.equal(readInitialLocale({ getItem() { throw new Error('Storage denied') } }), 'en')
})

test('all messages compile and every translation covers the English keys', () => {
    assert.deepEqual(Object.keys(ptBR).sort(), Object.keys(en).sort())
    assert.deepEqual(Object.keys(fr).sort(), Object.keys(en).sort())
    for (const [locale, messages] of Object.entries({ en, fr, 'pt-BR': ptBR })) {
        for (const [key, message] of Object.entries(messages)) {
            assert.equal(typeof message, 'string', `${locale}.${key}`)
            assert.ok(message.trim(), `${locale}.${key} must not be empty`)
            const errors = []
            baseCompile(message, { onError: error => errors.push(error.message) })
            assert.deepEqual(errors, [], `${locale}.${key}`)
            const parameters = value => [...new Set([...value.matchAll(/\{([a-zA-Z]\w*)\}/g)]
                .map(match => match[1]))].sort()
            assert.deepEqual(parameters(message), parameters(en[key]), `${locale}.${key} parameters`)
        }
    }
})

test('Portuguese interpolates and pluralizes complete session and project messages', () => {
    const { t } = createAppI18n('pt-BR').global
    assert.equal(t('home.allSessions', { count: 1 }, 1), '1 sessão')
    assert.equal(t('home.allSessions', { count: 2 }, 2), 'Todas as 2 sessões')
    assert.equal(t('workspace.projectCount', { count: 1 }, 1), '1 projeto')
    assert.equal(t('workspace.projectCount', { count: 3 }, 3), '3 projetos')
    assert.equal(t('session.selection.stopProcesses', 2), 'Parar os processos')
    assert.equal(t('session.selection.count', { count: 1 }, 1), '1 selecionada')
    assert.equal(t('session.selection.count', { count: 2 }, 2), '2 selecionadas')
    assert.equal(t('composer.status.startingProvider', { provider: 'Codex' }), 'Iniciando processo do Codex...')
})

test('French interpolates and treats zero and one as singular', () => {
    const { t } = createAppI18n('fr').global
    for (const [count, noun] of [[0, 'session'], [1, 'session'], [2, 'sessions'], [21, 'sessions']]) {
        assert.equal(t('common.sessionCount', { count }, count), `${count} ${noun}`)
    }
    assert.equal(t('workspace.projectCount', { count: 0 }, 0), '0 projet')
    assert.equal(t('workspace.projectCount', { count: 3 }, 3), '3 projets')
    assert.match(t('activity.heatmap.sessionsOn', { count: 0, date: 'x' }, 0), /^Aucune session/)
    assert.match(t('activity.heatmap.sessionsOn', { count: 1, date: 'x' }, 1), /^1 session /)
    assert.match(t('activity.heatmap.sessionsOn', { count: 4, date: 'x' }, 4), /^4 sessions /)
})

test('card session counts use complete plural forms, including zero', () => {
    for (const locale of ['en', 'pt-BR']) {
        const { t } = createAppI18n(locale).global
        for (const count of [0, 1, 2, 21]) {
            const noun = locale === 'en'
                ? (count === 1 ? 'session' : 'sessions')
                : (count === 1 ? 'sessão' : 'sessões')
            assert.equal(t('common.sessionCount', { count }, count), `${count} ${noun}`)
        }
    }
})

test('a missing translation falls back to English', () => {
    const instance = createAppI18n('pt-BR')
    instance.global.fallbackWarn = false
    instance.global.missingWarn = false
    instance.global.mergeLocaleMessage('en', { fallback_test: 'English fallback' })
    assert.equal(instance.global.t('fallback_test'), 'English fallback')
})

test('language changes update mounted templates immediately', async () => {
    const renderer = createRenderer({
        createElement: () => ({ children: [], text: '' }),
        createText: text => ({ text }), createComment: () => ({}),
        insert: (child, parent) => parent.children.push(child), remove() {},
        setElementText: (node, text) => { node.text = text }, setText: (node, text) => { node.text = text },
        patchProp() {}, parentNode() {}, nextSibling() {},
    })
    const instance = createAppI18n()
    const root = { children: [] }
    const app = renderer.createApp({ render() { return h('p', this.$t('home.welcome')) } })
    app.use(instance).mount(root)
    assert.equal(root.children[0].text, 'Welcome to TwiCC')
    instance.global.locale.value = 'pt-BR'
    await nextTick()
    assert.equal(root.children[0].text, 'Boas-vindas ao TwiCC')
    instance.global.locale.value = 'en'
    await nextTick()
    assert.equal(root.children[0].text, 'Welcome to TwiCC')
    app.unmount()
})

test('document language follows the selected locale, including fallback', () => {
    const document = { documentElement: { lang: 'en' } }
    setLocale('pt-BR', document)
    assert.equal(document.documentElement.lang, 'pt-BR')
    assert.equal(i18n.global.locale.value, 'pt-BR')
    setLocale('unknown', document)
    assert.equal(document.documentElement.lang, 'en')
    assert.equal(i18n.global.locale.value, 'en')
})

test('CSS loading text follows the locale as a quoted CSS string', () => {
    const properties = new Map()
    const document = { documentElement: { style: { setProperty: (key, value) => properties.set(key, value) } } }
    for (const [locale, expected] of [['en', 'Loading...'], ['pt-BR', 'Carregando...'], ['unknown', 'Loading...']]) {
        setLocale(locale, document)
        assert.equal(properties.get('--twicc-loading-label'), JSON.stringify(expected))
    }
})

test('component translations reference existing catalog messages', () => {
    function checkComponents(directory) {
        for (const entry of readdirSync(directory, { withFileTypes: true })) {
            const file = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory)
            if (entry.isDirectory()) checkComponents(file)
            else if (entry.name.endsWith('.vue')) {
                const source = readFileSync(file, 'utf8')
                const keys = [...source.matchAll(/\b(?:t|\$t)\('([^']+)'/g),
                    ...source.matchAll(/(?<!:)keypath="([^"]+)"/g)]
                for (const [, key] of keys) assert.ok(Object.hasOwn(en, key), `${file.pathname}: ${key}`)
            }
        }
    }
    checkComponents(new URL('../', import.meta.url))
    const source = readFileSync(new URL('../components/app/SettingsPopover.vue', import.meta.url), 'utf8')
    assert.match(source, /:value\.prop="store.locale"/)
    assert.match(source, /@change="store.locale = \$event.target.value"/)
})


test('command argument markers render as literal text', () => {
    for (const [locale, question, objective] of [['en', 'question', 'objective'], ['pt-BR', 'pergunta', 'objetivo']]) {
        const composer = createAppI18n(locale).global
        assert.equal(composer.t('composer.commands.claude_code.researchHint'), `<${question}>`)
        assert.equal(composer.t('composer.commands.codex.goalHint'), `<${objective}> | clear`)
    }
})
