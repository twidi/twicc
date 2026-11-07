import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { effectScope, watchEffect } from 'vue'
import { DEFAULT_LOCALE, createLocaleManager, readInitialLocale } from './index.js'
import { artifactPageTitle, sharedPageTitle } from './pageTitles.js'

// Execute the real entries. Substitute browser I/O, CSS and component imports.
async function startViewer(entry, data, savedLocale) {
    const source = readFileSync(new URL(`../${entry}/main.js`, import.meta.url), 'utf8')
        .replace(/^import .+$/gm, '')
        .replace('void initializeArtifactShell()', 'return initializeArtifactShell()')
    const { i18n, setLocale } = createLocaleManager()
    const savedSettings = JSON.stringify({ locale: savedLocale, theme: 'dark' })
    const localStorage = {
        getItem: key => key === 'twicc-settings' ? savedSettings : null,
        setItem() { assert.fail('Viewer startup must not write browser preferences') },
        removeItem() { assert.fail('Viewer startup must not remove browser preferences') },
    }
    const document = {
        documentElement: { lang: '', style: { setProperty() {} } },
        getElementById: () => ({ textContent: JSON.stringify(data) }),
        body: { classList: { remove() {} } },
    }
    let mounted
    const plugins = []
    const app = {
        use(plugin) { plugins.push(plugin) },
        mount() {
            mounted = {
                locale: i18n.global.locale.value,
                lang: document.documentElement.lang,
                title: document.title,
                loadingLabel: i18n.global.t('common.status.loading'),
            }
        },
    }
    const noop = () => {}
    const scope = effectScope()
    const dependencies = {
        DEFAULT_LOCALE, i18n, document, localStorage,
        sharedPageTitle: data => sharedPageTitle(data, i18n.global),
        artifactPageTitle: data => artifactPageTitle(data, i18n.global),
        readInitialLocale: () => readInitialLocale(localStorage),
        watchEffect: callback => scope.run(() => watchEffect(callback)),
        setLocale: value => setLocale(value, document),
        createApp: () => app, createPinia: () => ({}),
        ShareSessionApp: {}, ShareDocApp: {}, ShareRecentApp: {}, ArtifactShellApp: {},
        initShareTheme: noop, initReducedMotion: noop, installGlassArrowGap: noop,
        installDetailsMotion: noop, installWaMotionStyles: noop,
        installDetachedAnchorGuard: noop, recordShareView: noop, initArtifactShellColorScheme: noop,
    }
    try {
        await new Function(...Object.keys(dependencies), source)(...Object.values(dependencies))
        assert.ok(plugins.includes(i18n), 'Reused components and helpers must share the same composer')
        assert.equal(readInitialLocale(localStorage), savedLocale)
        if (entry === 'share-session' || data.mode === 'share') {
            assert.deepEqual(i18n.global.availableLocales, ['en'], 'Public viewers must not load the saved locale')
        }
        return mounted
    } finally {
        scope.stop()
    }
}

const title = 'Título original {literal}'
for (const savedLocale of ['en', 'pt-BR']) {
    for (const [entry, data, expectedLocale, expectedTitle] of [
        ['share-session', { mode: 'recent' }, 'en', 'TwiCC - Shared with you'],
        ['share-session', { mode: 'session', meta: { title } }, 'en', `TwiCC - Shared session - ${title}`],
        ['share-session', { mode: 'doc', meta: { title } }, 'en', `TwiCC - Shared artifact - ${title}`],
        ['artifact-shell', { mode: 'share', title }, 'en', `TwiCC - Shared artifact - ${title}`],
        ['artifact-shell', { mode: 'owner' }, savedLocale, savedLocale === 'en' ? 'Artifact' : 'Artefato'],
        ['artifact-shell', {}, savedLocale, savedLocale === 'en' ? 'Artifact' : 'Artefato'],
    ]) {
        test(`${entry}/${data.mode ?? 'default'}: saved ${savedLocale}, viewer ${expectedLocale}`, async () => {
            const result = await startViewer(entry, data, savedLocale)
            assert.deepEqual(result, {
                locale: expectedLocale,
                lang: expectedLocale,
                title: expectedTitle,
                loadingLabel: expectedLocale === 'en' ? 'Loading...' : 'Carregando...',
            })
        })
    }
}
