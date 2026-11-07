import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import config, { injectRecoveryMessages } from '../../vite.config.js'
import en from '../i18n/locales/en.json' with { type: 'json' }
import ptBR from '../i18n/locales/pt-BR.json' with { type: 'json' }

const sourceHtml = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
const html = await injectRecoveryMessages.transformIndexHtml(sourceHtml)
const script = html.match(/<script data-twicc-recovery>([\s\S]*?)<\/script>/)?.[1] ?? ''

// Only browser I/O is simulated. Execute the actual inline startup guard, without the app bundle.
function page({ storage = new Map(), href = 'https://twicc.test/project/p/session/s?workspace=w#item', online = true, storageBlocked = false, settings = null, settingsBlocked = false } = {}) {
    const events = new Map()
    const navigation = []
    const panels = []
    const window = {
        addEventListener(name, handler) { events.set(name, handler) },
        location: { href, replace(value) { navigation.push(value) } },
        history: { state: null, replaceState(_state, _title, value) { window.location.href = String(value) } },
        localStorage: {
            getItem() { if (settingsBlocked) throw Error('Blocked'); return settings },
        },
        sessionStorage: {
            getItem(key) { if (storageBlocked) throw Error('Blocked'); return storage.get(key) ?? null },
            setItem(key, value) { if (storageBlocked) throw Error('Blocked'); storage.set(key, String(value)) },
        },
        navigator: { onLine: online },
    }
    const document = {
        readyState: 'complete',
        documentElement: {},
        body: { append(node) { panels.push(node) } },
        createElement(tag) { return { tagName: tag.toUpperCase(), style: {}, children: [], append(...nodes) { this.children.push(...nodes) }, setAttribute() {} } },
        addEventListener(name, handler) { events.set(name, handler) },
    }
    vm.runInNewContext(script, { window, document, URL, Date, setTimeout: () => 0 })
    function fail(tagName = 'SCRIPT', url = 'https://twicc.test/static/assets/index-old.js') {
        events.get('error')?.({ target: { tagName, src: url, href: url, rel: 'stylesheet', type: 'module' } })
    }
    return { window, document, events, navigation, panels, storage, fail }
}

test('a missing entry module refreshes the HTML once and preserves the route', () => {
    const p = page()
    p.fail()
    assert.equal(p.navigation.length, 1)
    const target = new URL(p.navigation[0])
    assert.equal(target.pathname, '/project/p/session/s')
    assert.equal(target.searchParams.get('workspace'), 'w')
    assert.equal(target.hash, '#item')
    assert.ok(target.searchParams.has('__twicc_reload'))
    p.fail('LINK')
    assert.equal(p.navigation.length, 1)
})

test('a second broken document shows Retry instead of looping', () => {
    const first = page()
    first.fail()
    const second = page({ storage: first.storage, href: first.navigation[0] })
    second.fail('LINK')
    assert.equal(second.navigation.length, 0)
    assert.equal(second.panels.length, 1)
    const button = second.panels[0].children.find(node => node.tagName === 'BUTTON')
    assert.ok(button)
    button.onclick()
    assert.equal(second.navigation.length, 1)
})

test('a dynamic import failure also recovers an already loaded page', () => {
    const p = page()
    let prevented = false
    p.events.get('vite:preloadError')?.({ payload: new TypeError('error loading dynamically imported module: https://twicc.test/static/assets/editor-old.js'), preventDefault() { prevented = true } })
    assert.equal(p.navigation.length, 1)
    assert.equal(prevented, true)
})

test('optional locale failures stay with their English fallback instead of reloading the page', () => {
    for (const filename of ['pt-BR-catalog.js', 'pt-webawesome.js']) {
        const p = page()
        const url = `https://twicc.test/static/assets/${filename}`
        let prevented = false
        p.events.get('vite:preloadError')?.({ payload: new TypeError(`Failed to fetch dynamically imported module: ${url}`), preventDefault() { prevented = true } })
        p.fail('SCRIPT', url)
        assert.equal(prevented, false, 'The locale loader must receive its rejected import')
        assert.equal(p.navigation.length, 0)
        assert.equal(p.panels.length, 0)
    }
})

test('blocked storage still prevents a reload loop through the recovery URL', () => {
    const first = page({ storageBlocked: true })
    first.fail()
    const second = page({ storageBlocked: true, href: first.navigation[0] })
    second.fail()
    assert.equal(second.navigation.length, 0)
    assert.equal(second.panels.length, 1)
})

test('offline failure offers Retry without navigating and unrelated resources are ignored', () => {
    const p = page({ online: false })
    p.fail('IMG')
    p.fail('SCRIPT', 'https://other.test/static/assets/external.js')
    assert.equal(p.panels.length, 0)
    p.fail()
    assert.equal(p.navigation.length, 0)
    assert.equal(p.panels.length, 1)
})

test('successful asset startup removes only the temporary recovery query', () => {
    const p = page({ href: 'https://twicc.test/project/p/session/s?workspace=w&__twicc_reload=1#item' })
    p.events.get('twicc:assets-ready')?.()
    assert.equal(p.window.location.href, 'https://twicc.test/project/p/session/s?workspace=w#item')
})


test('module resolution and execution errors do not replace a working app with Retry', () => {
    for (const payload of [
        new TypeError("Failed to resolve module specifier '@codemirror/legacy-modes/mode/toml'"),
        new TypeError('The specifier “@codemirror/legacy-modes/mode/toml” was a bare specifier, but was not remapped to anything.'),
        new ReferenceError('missingEditorVariable is not defined'),
        new SyntaxError('Unexpected token'),
        new Error('Editor initialization failed'),
    ]) {
        const p = page({ storage: new Map([['twicc:asset-recovery', String(Date.now())]]) })
        p.events.get('twicc:assets-ready')?.()
        let prevented = false
        p.events.get('vite:preloadError')?.({ payload, preventDefault() { prevented = true } })
        assert.equal(p.navigation.length, 0, payload.message)
        assert.equal(p.panels.length, 0, payload.message)
        assert.equal(prevented, false, 'the importing feature must receive its error')
    }
})

test('only actual same-origin build resource loading failures trigger recovery', () => {
    for (const message of [
        'Failed to fetch dynamically imported module: https://twicc.test/static/assets/editor-old.js',
        'error loading dynamically imported module: https://twicc.test/static/assets/editor-old.js',
        'Unable to preload CSS for https://twicc.test/static/assets/editor-old.css',
    ]) {
        const p = page()
        let prevented = false
        p.events.get('vite:preloadError')?.({ payload: new TypeError(message), preventDefault() { prevented = true } })
        assert.equal(p.navigation.length, 1, message)
        assert.equal(prevented, true)
    }
    for (const message of [
        'Failed to fetch dynamically imported module: https://other.test/static/assets/editor.js',
        'error loading dynamically imported module: https://twicc.test/user-script.js',
    ]) {
        const p = page()
        p.events.get('vite:preloadError')?.({ payload: new TypeError(message), preventDefault() {} })
        assert.equal(p.navigation.length, 0, message)
        assert.equal(p.panels.length, 0, message)
    }
})


test('dev and build embed only the recovery catalog subset before modules', async () => {
    for (const command of ['serve', 'build']) {
        const plugins = config({ command }).plugins
        const plugin = plugins.find(plugin => plugin.name === 'twicc-inject-recovery-messages')
        assert.ok(plugin)
        const transformed = await plugin.transformIndexHtml(sourceHtml)
        assert.ok(!transformed.includes('__TWICC_RECOVERY_MESSAGES__'))
        const source = transformed.match(/const messages = (.+);/)[1]
        const messages = JSON.parse(source)
        for (const [locale, catalog] of [['en', en], ['pt-BR', ptBR]]) {
            assert.deepEqual(Object.keys(messages[locale]).sort(), ['common.actions.retry', 'startup.assetsUnavailable.description'])
            for (const [key, value] of Object.entries(messages[locale])) assert.equal(value, catalog[key])
        }
        assert.ok(transformed.indexOf('const messages =') < transformed.indexOf('src="/src/main.js"'))
    }
})

test('recovery uses the saved locale before the app bundle loads', () => {
    for (const [locale, catalog] of [['en', en], ['pt-BR', ptBR]]) {
        const p = page({ online: false, settings: JSON.stringify({ locale }) })
        p.fail()
        assert.equal(p.document.documentElement.lang, locale)
        const panel = p.panels[0]
        assert.equal(panel.children[0].textContent, catalog['startup.assetsUnavailable.description'])
        assert.equal(panel.children[1].textContent, catalog['common.actions.retry'])
    }
})

test('recovery defaults to English with invalid, missing or inaccessible settings', () => {
    for (const options of [
        {},
        { settings: '{' },
        { settings: 'null' },
        { settings: JSON.stringify({ locale: 'unknown' }) },
        { settings: JSON.stringify({ locale: 'pt-BR' }), settingsBlocked: true },
    ]) {
        const p = page({ online: false, ...options })
        p.fail()
        assert.equal(p.document.documentElement.lang, 'en')
        assert.equal(p.panels[0].children[0].textContent, en['startup.assetsUnavailable.description'])
        assert.equal(p.panels[0].children[1].textContent, en['common.actions.retry'])
    }
})


test('recovery text follows a locale switch after startup', () => {
    const p = page({ online: false })
    p.document.documentElement.lang = 'pt-BR'
    p.fail()
    assert.equal(p.panels[0].children[0].textContent, ptBR['startup.assetsUnavailable.description'])
    assert.equal(p.panels[0].children[1].textContent, ptBR['common.actions.retry'])
})
