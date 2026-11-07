import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createAppI18n, loadLocaleMessages } from './index.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

// Execute the real pre-mount initialization with only browser I/O substituted.
const source = readFileSync(new URL('../main.js', import.meta.url), 'utf8')
const initializeSource = source.slice(source.indexOf('async function initializeApp()'), source.indexOf('// Keep draft hydration before mount'))

function startup(locale, { accessDenied = false, command = 'twicc password set', clipboardFailure = false } = {}) {
    const nodes = new Map()
    const copied = [], timers = []
    const node = () => ({ style: {}, listeners: {}, textContent: '', addEventListener(name, callback) { this.listeners[name] = callback } })
    const app = node()
    Object.defineProperty(app, 'innerHTML', {
        set(html) {
            this.markup = html
            for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], node())
            const commandNode = nodes.get('twicc-blocked-cmd')
            if (commandNode) {
                commandNode.code = node()
                commandNode.querySelector = selector => selector === 'code' ? commandNode.code : null
            }
        },
    })
    nodes.set('app', app)
    const i18n = createAppI18n(locale)
    const dependencies = {
        i18n,
        readInitialLocale: () => locale,
        setLocale: async value => { assert.equal(value, locale) },
        useAuthStore: () => ({ checkAuth: async () => {}, accessDenied, setPasswordCommand: command }),
        document: { getElementById: id => nodes.get(id) },
        navigator: { clipboard: { async writeText(value) { if (clipboardFailure) throw new Error('Clipboard denied'); copied.push(value) } } },
        window: { isSecureContext: true },
        fetch: async () => ({ ok: false }),
        setTimeout: callback => timers.push(callback),
    }
    const initialize = new Function(...Object.keys(dependencies), `${initializeSource}\nreturn initializeApp();`)
    return { run: () => initialize(...Object.values(dependencies)), nodes, copied, timers, i18n }
}

for (const locale of ['en', 'pt-BR']) {
    test(`${locale}: access block translates DOM text and preserves literal command data`, async () => {
        const command = "twicc password set '<img src=x onerror=alert(1)>'"
        const page = startup(locale, { accessDenied: true, command })
        await assert.rejects(page.run(), /Access blocked/)
        const t = page.i18n.global.t
        assert.equal(page.nodes.get('twicc-blocked-title').textContent, t('startup.accessBlocked.title'))
        assert.equal(page.nodes.get('twicc-blocked-description').textContent, t('startup.accessBlocked.description'))
        assert.equal(page.nodes.get('twicc-blocked-hint').textContent, t('startup.accessBlocked.copyHint'))
        const commandNode = page.nodes.get('twicc-blocked-cmd')
        assert.equal(commandNode.code.textContent, command)
        assert.equal(commandNode.title, t('startup.accessBlocked.commandTooltip'))
        assert.ok(!page.nodes.get('app').markup.includes(command))
        await commandNode.listeners.click()
        assert.deepEqual(page.copied, [command])
        assert.equal(page.nodes.get('twicc-blocked-hint').textContent, t('startup.accessBlocked.copied'))
        page.timers[0]()
        assert.equal(page.nodes.get('twicc-blocked-hint').textContent, t('startup.accessBlocked.copyHint'))
    })

    test(`${locale}: failed clipboard access gives the translated manual-copy instruction`, async () => {
        const page = startup(locale, { accessDenied: true, clipboardFailure: true })
        await assert.rejects(page.run(), /Access blocked/)
        await page.nodes.get('twicc-blocked-cmd').listeners.click()
        assert.equal(page.nodes.get('twicc-blocked-hint').textContent, page.i18n.global.t('startup.accessBlocked.manualCopy'))
        assert.equal(page.timers.length, 0)
    })

    test(`${locale}: unavailable bootstrap translates both DOM messages`, async () => {
        const page = startup(locale)
        await assert.rejects(page.run(), /Backend unreachable/)
        assert.equal(page.nodes.get('twicc-bootstrap-title').textContent, page.i18n.global.t('startup.backendUnavailable.title'))
        assert.equal(page.nodes.get('twicc-bootstrap-description').textContent, page.i18n.global.t('startup.backendUnavailable.description'))
    })
}
