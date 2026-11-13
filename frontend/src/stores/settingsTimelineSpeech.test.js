import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createPinia, defineStore, setActivePinia } from 'pinia'
import * as Vue from 'vue'
import * as constants from '../constants.js'

const source = readFileSync(new URL('./settings.js', import.meta.url), 'utf8')

// Run the real store with browser dependencies supplied at the module boundary.
// Node cannot resolve the production module's extensionless browser imports.
function settingsModule(t, saved = {}) {
    let storage = JSON.stringify(saved)
    const sent = []
    const noop = () => {}
    const modules = {
        pinia: { defineStore }, vue: Vue, '../constants': constants,
        '../utils/notificationSounds': { NOTIFICATION_SOUNDS: { NONE: 'none' } },
        './textToSpeech': {}, '../providers': { getRegisteredProviders: () => [], getProviderHelpers: noop },
        '../utils/theme': { setColorScheme: noop, setSystemSchemeChangeHandler: noop },
        '../utils/colorSchemeTransition': {}, '../utils/worktreePath': {}, '../utils/publicOrigin': {},
        '../utils/originSettingsForm': {}, '../utils/compactHeight': { watchCompactHeight: noop },
        '../utils/reducedMotion': { applyReduceEffects: noop },
        '../i18n': { DEFAULT_LOCALE: 'en', isSupportedLocale: value => value === 'en', setLocale: noop },
        '../i18n/androidLocale.js': { interfaceLocaleState: Vue.ref(null) },
        '../composables/useWebSocket': { sendSyncedSettings: (...args) => sent.push(args) },
    }
    const code = source.slice(0, source.indexOf('// Pinia HMR support'))
        .replace(/^import\s+([\s\S]*?)\s+from\s+['"]([^'"]+)['"]/gm,
            (_, names, path) => `const ${names.replace(/\bas\b/g, ':')} = modules[${JSON.stringify(path)}]`)
        .replace(/\bexport /g, '')
        .replace(/import\(['"]([^'"]+)['"]\)/g,
            (_, path) => `Promise.resolve(modules[${JSON.stringify(path)}])`)
    const api = new Function('modules', 'localStorage', 'document', 'window', 'navigator', `${code}; return { useSettingsStore, applyDefaultSettings, initSettings }`)(
        modules, { getItem: () => storage, setItem: (_key, value) => { storage = value } },
        { documentElement: { style: {} } }, { matchMedia: () => ({ matches: false }) }, {},
    )
    const pinia = createPinia()
    setActivePinia(pinia)
    const scope = Vue.effectScope()
    t.after(() => scope.stop())
    return { ...api, sent, saved: () => JSON.parse(storage), init: () => scope.run(api.initSettings) }
}

test('the final-only preference joins generic settings sync and defaults to on', t => {
    assert.equal(constants.SYNCED_SETTINGS_KEYS.has('timelineSpeechFinalOnly'), true)
    const api = settingsModule(t)
    // Synced keys are null until bootstrap: consumers treat an unset value as on.
    assert.equal(api.useSettingsStore().timelineSpeechFinalOnly, null)
    api.applyDefaultSettings({ timelineSpeechFinalOnly: true }, {})
    const next = createPinia()
    assert.equal(api.useSettingsStore(next).timelineSpeechFinalOnly, true)
})

test('saving off persists through reload and backend bootstrap overrides the saved value', async t => {
    const api = settingsModule(t)
    api.applyDefaultSettings({ timelineSpeechFinalOnly: true }, {})
    const store = api.useSettingsStore()
    api.init()
    store.setTimelineSpeechFinalOnly(false)
    await Vue.nextTick()
    assert.equal(api.saved().timelineSpeechFinalOnly, false)
    assert.equal(api.sent.at(-1)[0].timelineSpeechFinalOnly, false)

    const reload = settingsModule(t, api.saved())
    reload.applyDefaultSettings({ timelineSpeechFinalOnly: true }, { timelineSpeechFinalOnly: true }, false, false, 4)
    const restored = reload.useSettingsStore()
    assert.equal(restored.timelineSpeechFinalOnly, false)
    reload.init()
    assert.equal(restored.timelineSpeechFinalOnly, true)
    await Vue.nextTick()
    assert.equal(reload.sent.length, 0, 'remote bootstrap does not echo to the server')
})

test('the preference rejects invalid local and remote values', async t => {
    const api = settingsModule(t, { timelineSpeechFinalOnly: 'false' })
    api.applyDefaultSettings({ timelineSpeechFinalOnly: true }, {})
    const store = api.useSettingsStore()
    assert.equal(store.timelineSpeechFinalOnly, true)
    store.setTimelineSpeechFinalOnly(false)
    store.setTimelineSpeechFinalOnly('true')
    assert.equal(store.timelineSpeechFinalOnly, false)
    store.applySyncedSettings({ timelineSpeechFinalOnly: true }, 5)
    await Vue.nextTick()
    assert.equal(store.timelineSpeechFinalOnly, true)
    store.applySyncedSettings({ timelineSpeechFinalOnly: false }, 4)
    store.applySyncedSettings({ timelineSpeechFinalOnly: 'false' }, 6)
    assert.equal(store.timelineSpeechFinalOnly, true)
})
