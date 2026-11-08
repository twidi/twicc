import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createAndroidLocaleController } from './androidLocale.js'
import { createLocaleManager } from './index.js'

const languages = ['en', 'de', 'es', 'fr', 'pt-BR']
const language = (locale = 'en', revision = 0, supportedLocales = languages) => ({ locale, revision, supportedLocales })
function deferred() {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
}
const tick = () => new Promise(resolve => setImmediate(resolve))
function fixture({ initial = language(), browser = 'fr', request, applyLocale } = {}) {
    let native = initial
    const calls = [], applied = [], compatibility = []
    const listeners = new Set(), document = new EventTarget()
    document.visibilityState = 'visible'
    const client = {
        async request(command, args) {
            calls.push([command, args])
            if (request) return request(command, args)
            if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: native }
            if (command === 'locale.get') return native
            throw new Error('Unexpected mutation')
        },
        onStateChanged(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    }
    const controller = createAndroidLocaleController({ client, document,
        readBrowserLocale: () => browser,
        applyLocale: async locale => { applied.push(locale); await applyLocale?.(locale) },
        onCompatibility: message => compatibility.push(message),
    })
    return { controller, calls, applied, compatibility, document, listeners,
        change(value) { native = value; for (const listener of listeners) listener() },
        setNative(value) { native = value },
    }
}

test('nativeEnglishOverridesStaleProfileFrenchBeforeLogin', async () => {
    const page = fixture()
    assert.deepEqual({ ...await page.controller.initialize() }, { mode: 'native', ...language() })
    assert.deepEqual(page.applied, ['en'])
    assert.deepEqual(page.calls.map(([command]) => command), ['getState'])
    page.controller.dispose()
})

test('browserKeepsOwnPreference', async () => {
    const page = fixture({ request: async () => { throw new Error('Bounded client timeout') } })
    assert.equal((await page.controller.initialize()).mode, 'browser')
    assert.deepEqual(page.applied, ['fr'])
    await page.controller.select('de')
    assert.deepEqual(page.applied, ['fr', 'de'])
    assert.equal(page.calls.length, 1)
    page.controller.dispose()
})

test('selectionPropagatesOnResume', async () => {
    const pending = deferred()
    let resume = false
    const page = fixture({ request: async command => command === 'getState'
        ? { capabilities: { appLanguage: 1 }, appLanguage: language() }
        : resume ? pending.promise : language() })
    const state = await page.controller.initialize()
    resume = true
    page.document.dispatchEvent(new Event('visibilitychange'))
    const blocked = new Event('click', { cancelable: true })
    page.document.dispatchEvent(blocked)
    assert.equal(blocked.defaultPrevented, true)
    pending.resolve(language('es', 2))
    await tick()
    assert.equal(state.locale, 'es')
    const unblocked = new Event('click', { cancelable: true })
    page.document.dispatchEvent(unblocked)
    assert.equal(unblocked.defaultPrevented, false)
    page.controller.dispose()
    assert.equal(page.listeners.size, 0)
})

test('olderSelectionCannotWin', async () => {
    const oldRead = deferred()
    let reads = 0
    const page = fixture({ request: async command => {
        if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: language() }
        if (command === 'locale.get') return ++reads === 1 ? oldRead.promise : language('de', 2)
        return language('es', 1)
    } })
    const state = await page.controller.initialize()
    const old = page.controller.reconcile()
    await page.controller.select('es')
    await page.controller.reconcile()
    oldRead.resolve(language('fr', 0))
    await old
    assert.equal(state.locale, 'de')
    assert.equal(page.applied.at(-1), 'de')
    page.controller.dispose()
})

test('latestQueuedSelectionUsesNewRevision', async () => {
    const first = deferred()
    const page = fixture({ request: async (command, args) => {
        if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: language() }
        if (args.locale === 'fr') return first.promise
        return language(args.locale, 2)
    } })
    const state = await page.controller.initialize()
    const firstSelection = page.controller.select('fr')
    await tick()
    const skipped = page.controller.select('de')
    const latest = page.controller.select('es')
    first.resolve(language('fr', 1))
    await Promise.all([firstSelection, skipped, latest])
    assert.deepEqual(page.calls.filter(([command]) => command === 'locale.set'), [
        ['locale.set', { locale: 'fr', expectedRevision: 0 }],
        ['locale.set', { locale: 'es', expectedRevision: 1 }],
    ])
    assert.equal(state.locale, 'es')
    assert.ok(!page.applied.includes('fr'))
    page.controller.dispose()
})

test('restorationDoesNotSendLocaleSet', async () => {
    const page = fixture()
    await page.controller.initialize()
    page.change(language('fr', 1))
    await tick()
    assert.deepEqual(page.applied, ['en', 'fr'])
    assert.ok(page.calls.every(([command]) => command !== 'locale.set'))
    page.controller.dispose()
})

test('failedChunkDoesNotOverwriteNativeChoice', async () => {
    let downloads = 0
    const manager = createLocaleManager({ fr: async () => { downloads++; throw new Error('Offline chunk') } })
    const page = fixture({ initial: language('fr', 1), applyLocale: locale => manager.setLocale(locale) })
    const warn = console.warn
    console.warn = () => {}
    try {
        const state = await page.controller.initialize()
        assert.equal(state.locale, 'fr')
        assert.equal(manager.i18n.global.locale.value, 'en')
        await page.controller.reconcile()
        assert.equal(downloads, 1)
        assert.ok(page.calls.every(([command]) => command !== 'locale.set'))
    } finally { console.warn = warn; page.controller.dispose() }
})

test('unsupportedApkLanguageRestoresPage', async () => {
    const error = new Error('Unsupported')
    error.state = language('en', 4, ['en'])
    error.uiMessage = { type: 'local-ui-message', key: 'android.language.errors.unsupported', params: {} }
    const page = fixture({ initial: language('en', 4, ['en']), request: async command => {
        if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: error.state }
        if (command === 'locale.set') throw error
        return error.state
    } })
    const state = await page.controller.initialize()
    await assert.rejects(page.controller.select('fr'), rejected => rejected === error)
    assert.equal(state.locale, 'en')
    assert.equal(state.revision, 4)
    assert.equal(page.calls.filter(([command]) => command === 'locale.set').length, 1)
    assert.deepEqual(error.state, language('en', 4, ['en']))
    page.controller.dispose()
})

test('stale mutation reconciles without replaying user intent', async () => {
    const error = Object.assign(new Error('Changed'), { state: language('de', 2) })
    const page = fixture({ request: async command => {
        if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: language() }
        if (command === 'locale.set') throw error
        return language('es', 3)
    } })
    const state = await page.controller.initialize()
    await assert.rejects(page.controller.select('fr'), error)
    assert.equal(state.locale, 'es')
    assert.equal(state.revision, 3)
    assert.equal(page.calls.filter(([command]) => command === 'locale.set').length, 1)
    page.controller.dispose()
})

test('settings initialization preserves native bootstrap before its immediate watcher', async () => {
    const source = readFileSync(new URL('../stores/settings.js', import.meta.url), 'utf8')
    const start = source.indexOf('export function initSettings()')
    const end = source.indexOf('// Dispatch the parsed localStorage values', start)
    const code = source.slice(start, end).replace('export ', '') + '\n}'
    const page = fixture()
    const state = await page.controller.initialize()
    const store = { locale: 'fr' }, rendered = []
    const watch = (read, effect, options) => { if (options?.immediate) effect(read()) }
    new Function('useSettingsStore', 'watch', 'interfaceLocaleState', 'setLocale', `${code}; initSettings()`)(
        () => store, watch, { value: state }, locale => rendered.push(locale))
    assert.equal(store.locale, 'en')
    assert.deepEqual(rendered, [])
    page.controller.dispose()
})

test('native change during the initial state request reaches the startup result', async () => {
    const snapshot = deferred()
    const page = fixture({ request: async command => command === 'getState'
        ? snapshot.promise : language('de', 2) })
    const startup = page.controller.initialize()
    page.change(language('de', 2))
    snapshot.resolve({ capabilities: { appLanguage: 1 }, appLanguage: language('fr', 1) })
    const state = await startup
    assert.equal(state.locale, 'de')
    assert.equal(state.revision, 2)
    assert.equal(page.applied.at(-1), 'de')
    assert.ok(page.calls.every(([command]) => command !== 'locale.set'))
    page.controller.dispose()
})

test('native change during initial catalog loading extends the startup catalog barrier', async () => {
    const french = deferred(), german = deferred()
    let rendered, finished = false
    const page = fixture({ initial: language('fr', 1), applyLocale: async locale => {
        if (locale === 'fr') await french.promise
        if (locale === 'de') await german.promise
        rendered = locale
    } })
    const startup = page.controller.initialize().then(state => { finished = true; return state })
    await tick()
    page.change(language('de', 2))
    french.resolve()
    await tick()
    try {
        assert.equal(finished, false, 'startup must await the newly selected catalog')
        assert.equal(page.applied.at(-1), 'de')
        german.resolve()
        const state = await startup
        assert.equal(state.locale, 'de')
        assert.equal(rendered, 'de')
    } finally {
        german.resolve()
        await startup
        page.controller.dispose()
    }
})

test('older APK preserves browser-local selection and reactive compatibility', async () => {
    const page = fixture({ request: async () => ({ capabilities: {} }) })
    const state = await page.controller.initialize()
    assert.equal(state.mode, 'incompatible')
    assert.equal(state.locale, 'fr')
    assert.equal(page.compatibility.at(-1).key, 'android.language.errors.updateApp')
    await page.controller.select('es')
    assert.equal(state.locale, 'es')
    assert.equal(page.calls.length, 1)
    page.controller.dispose()
})

test('newer APK language retains its preference while this page renders English', async () => {
    const page = fixture({ initial: language('it', 5, [...languages, 'it']) })
    const state = await page.controller.initialize()
    assert.equal(state.mode, 'incompatible')
    assert.equal(state.locale, 'it')
    assert.deepEqual(page.applied, ['en'])
    assert.equal(page.compatibility.at(-1).key, 'android.language.errors.updateServer')
    page.controller.dispose()
})

test('a rejected stale mutation also cancels already queued selections', async () => {
    const pending = deferred()
    const error = Object.assign(new Error('Changed'), { state: language('de', 3) })
    const page = fixture({ request: async command => {
        if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: language() }
        if (command === 'locale.set') return pending.promise
        return error.state
    } })
    const state = await page.controller.initialize()
    const rejected = assert.rejects(page.controller.select('fr'), error)
    await tick()
    const queued = page.controller.select('es')
    pending.reject(error)
    await rejected
    await queued
    assert.equal(state.locale, 'de')
    assert.equal(page.calls.filter(([command]) => command === 'locale.set').length, 1)
    page.controller.dispose()
})

test('a pending older catalog does not delay the latest native selection', async () => {
    const download = deferred()
    const manager = createLocaleManager({ fr: () => download.promise, de: async () => ({}) })
    let revision = 0
    const page = fixture({ applyLocale: locale => manager.setLocale(locale), request: async (command, args) => {
        if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: language() }
        return language(args.locale, ++revision)
    } })
    await page.controller.initialize()
    const first = page.controller.select('fr')
    await tick()
    const latest = page.controller.select('de')
    await tick()
    try {
        assert.equal(manager.i18n.global.locale.value, 'de')
        download.resolve({})
        await Promise.all([first, latest])
        assert.equal(manager.i18n.global.locale.value, 'de')
    } finally {
        download.resolve({})
        await Promise.all([first, latest])
        page.controller.dispose()
    }
})

test('queued intent cannot adopt a broadcast revision after another page changes language', async () => {
    const pending = deferred()
    const error = Object.assign(new Error('Changed'), { state: language('de', 2) })
    const page = fixture({ request: async (command, args) => {
        if (command === 'getState') return { capabilities: { appLanguage: 1 }, appLanguage: language() }
        if (command === 'locale.get') return error.state
        if (args.locale === 'fr') return pending.promise
        if (args.expectedRevision !== 2) throw error
        return language('es', 3)
    } })
    const state = await page.controller.initialize()
    const first = page.controller.select('fr')
    await tick()
    const queued = page.controller.select('es').catch(rejection => rejection)
    page.change(error.state)
    await tick()
    pending.resolve(language('fr', 1))
    await first
    const rejection = await queued
    assert.equal(rejection, error)
    assert.equal(state.locale, 'de')
    assert.deepEqual(page.calls.filter(([command]) => command === 'locale.set'), [
        ['locale.set', { locale: 'fr', expectedRevision: 0 }],
        ['locale.set', { locale: 'es', expectedRevision: 1 }],
    ])
    page.controller.dispose()
})

test('failed startup reconciliation completes with the native snapshot and later broadcasts recover', async () => {
    const snapshot = deferred()
    let reads = 0
    const error = Object.assign(new Error('Transient read timeout'), { uiMessage: {
        type: 'local-ui-message', key: 'common.android.errors.unreachable', params: {},
    } })
    const page = fixture({ request: async command => {
        if (command === 'getState') return snapshot.promise
        if (command === 'locale.get' && ++reads === 1) throw error
        return language('de', 2)
    } })
    const startup = page.controller.initialize()
    page.change(language('de', 2))
    snapshot.resolve({ capabilities: { appLanguage: 1 }, appLanguage: language('fr', 1) })
    try {
        const state = await startup
        assert.equal(state.locale, 'fr')
        assert.equal(state.revision, 1)
        assert.equal(page.applied.at(-1), 'fr')
        assert.equal(page.compatibility.at(-1), error.uiMessage)
        await tick()
        assert.equal(reads, 1, 'startup failure must not trigger an automatic retry loop')
        page.change(language('de', 2))
        await tick()
        assert.equal(state.locale, 'de')
        assert.equal(page.applied.at(-1), 'de')
        assert.equal(page.compatibility.at(-1), null)
        assert.equal(await page.controller.initialize(), state)
        await page.controller.reconcile()
        assert.equal(reads, 3)
        assert.ok(page.calls.every(([command]) => command !== 'locale.set'))
    } finally { page.controller.dispose() }
})

test('failed startup read keeps native preference separate from English catalog fallback', async () => {
    const snapshot = deferred()
    const manager = createLocaleManager({ fr: async () => { throw new Error('Offline catalog') } })
    const page = fixture({ applyLocale: locale => manager.setLocale(locale), request: async command => {
        if (command === 'getState') return snapshot.promise
        throw new Error('Transient read timeout')
    } })
    const warn = console.warn
    console.warn = () => {}
    try {
        const startup = page.controller.initialize()
        page.change(language('de', 2))
        snapshot.resolve({ capabilities: { appLanguage: 1 }, appLanguage: language('fr', 1) })
        const state = await startup
        assert.equal(state.locale, 'fr')
        assert.equal(state.revision, 1)
        assert.equal(manager.i18n.global.locale.value, 'en')
        assert.equal(page.compatibility.at(-1).key, 'common.android.errors.unreachable')
        assert.ok(page.calls.every(([command]) => command !== 'locale.set'))
    } finally { console.warn = warn; page.controller.dispose() }
})

test('a healthy event during degraded catalog loading remains recoverable', async () => {
    const snapshot = deferred(), catalog = deferred()
    let reads = 0
    const page = fixture({ applyLocale: locale => locale === 'fr' ? catalog.promise : undefined, request: async command => {
        if (command === 'getState') return snapshot.promise
        if (++reads === 1) throw new Error('Transient read timeout')
        return language('de', 2)
    } })
    const startup = page.controller.initialize()
    page.change(language('de', 2))
    snapshot.resolve({ capabilities: { appLanguage: 1 }, appLanguage: language('fr', 1) })
    await tick()
    page.change(language('de', 2))
    catalog.resolve()
    const state = await startup
    await tick()
    assert.equal(reads, 2)
    assert.equal(state.locale, 'de')
    assert.equal(page.compatibility.at(-1), null)
    page.controller.dispose()
})

test('startup read failure retains the unsupported-language update status', async () => {
    const snapshot = deferred()
    const page = fixture({ request: async command => {
        if (command === 'getState') return snapshot.promise
        throw new Error('Transient read timeout')
    } })
    const startup = page.controller.initialize()
    page.change(language('it', 3, [...languages, 'it']))
    snapshot.resolve({ capabilities: { appLanguage: 1 }, appLanguage: language('it', 3, [...languages, 'it']) })
    const state = await startup
    assert.equal(state.locale, 'it')
    assert.equal(state.mode, 'incompatible')
    assert.deepEqual(page.applied, ['en'])
    assert.equal(page.compatibility.at(-1).key, 'android.language.errors.updateServer')
    page.change(language('it', 3, [...languages, 'it']))
    await tick()
    assert.equal(page.compatibility.at(-1).key, 'android.language.errors.updateServer')
    page.controller.dispose()
})
