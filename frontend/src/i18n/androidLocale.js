import { reactive, shallowRef } from 'vue'
import { DEFAULT_LOCALE, SUPPORTED_LOCALES, isSupportedLocale, readInitialLocale, setLocale } from './index.js'
import { localUiMessage } from './uiMessages.js'
import { getAndroidAppClient } from '../utils/androidAppClient.js'
import { markAndroidApp } from '../utils/androidEnvironment.js'

const browserLocales = SUPPORTED_LOCALES.map(({ value }) => value)
const guardedEvents = ['click', 'pointerdown', 'keydown', 'change', 'submit']

function validLanguage(value) {
    return typeof value?.locale === 'string' && Number.isSafeInteger(value.revision) && value.revision >= 0
        && Array.isArray(value.supportedLocales) && value.supportedLocales.includes(value.locale)
        && value.supportedLocales.every(locale => typeof locale === 'string')
}

/** Owns one document. Reads and restoration never represent language selection. */
export function createAndroidLocaleController({ client, document, applyLocale, readBrowserLocale, onCompatibility = () => {} }) {
    const state = reactive({ mode: 'browser', locale: DEFAULT_LOCALE, revision: null, supportedLocales: [...browserLocales] })
    let initialization, unsubscribe, disposed = false, native = false
    let authoritative, generation = 0, selection = 0, mutationQueue = Promise.resolve(), pendingMutations = 0, guards = 0
    let renderedPreference, initializing = true, initializationDirty = false

    function guard(event) {
        if (!guards) return
        event.preventDefault()
        event.stopImmediatePropagation()
    }
    function compatibility(key) { onCompatibility(key ? localUiMessage(key) : null) }

    async function render(locale, ticket) {
        if (disposed || ticket !== generation || renderedPreference === locale) return
        renderedPreference = locale
        await applyLocale(isSupportedLocale(locale) ? locale : DEFAULT_LOCALE)
    }

    async function accept(value, ticket) {
        if (!validLanguage(value) || disposed) return
        // Even a superseded local mutation supplies the next queued revision.
        if (authoritative && value.revision < authoritative.revision) return
        authoritative = { ...value, supportedLocales: [...value.supportedLocales] }
        if (ticket !== generation) return
        Object.assign(state, authoritative, { mode: isSupportedLocale(value.locale) ? 'native' : 'incompatible' })
        compatibility(state.mode === 'incompatible' ? 'android.language.errors.updateServer' : null)
        await render(value.locale, ticket)
    }

    function backgroundReconcile() {
        if (disposed) return
        if (initializing) {
            // Native events can arrive before the initial capability response.
            initializationDirty = true
            return
        }
        if (!native) return
        guards++
        void reconcile().catch(error => {
            if (!disposed && state.mode !== 'incompatible') onCompatibility(error.uiMessage || localUiMessage('common.android.errors.unreachable'))
        }).finally(() => { guards-- })
    }
    function visibilityChanged() {
        if (document.visibilityState === 'visible') backgroundReconcile()
    }

    function initialize() {
        return initialization ??= (async () => {
            const ticket = ++generation
            // Subscribe before probing so a delayed snapshot cannot lose changes.
            unsubscribe = client.onStateChanged(backgroundReconcile)
            document?.addEventListener('visibilitychange', visibilityChanged)
            for (const type of guardedEvents) document?.addEventListener(type, guard, true)
            let fullState
            try { fullState = await client.request('getState') }
            catch { /* Ordinary browsers use the client's bounded probe timeout. */ }
            if (disposed) return state
            if (fullState?.capabilities?.appLanguage === 1 && validLanguage(fullState.appLanguage)) {
                native = true
                if (!initializationDirty) await accept(fullState.appLanguage, ticket)
                // Changes during either the snapshot or a catalog download extend
                // startup. Do not call reconcile(), which awaits this same promise.
                while (initializationDirty && !disposed) {
                    initializationDirty = false
                    const current = ++generation
                    let language
                    try { language = await client.request('locale.get') }
                    catch (error) {
                        // One failed read must not poison the cached startup promise.
                        // Render the last valid snapshot and recover on a later event.
                        await accept(authoritative || fullState.appLanguage, ++generation)
                        if (!disposed && state.mode !== 'incompatible') onCompatibility(error.uiMessage || localUiMessage('common.android.errors.unreachable'))
                        break
                    }
                    await accept(language, current)
                }
            } else {
                state.mode = fullState ? 'incompatible' : 'browser'
                const saved = readBrowserLocale()
                state.locale = isSupportedLocale(saved) ? saved : DEFAULT_LOCALE
                compatibility(fullState ? 'android.language.errors.updateApp' : null)
                await render(state.locale, ticket)
            }
            initializing = false
            // An event received while loading the degraded snapshot still counts.
            if (native && initializationDirty && !disposed) {
                initializationDirty = false
                backgroundReconcile()
            }
            return state
        })()
    }

    async function reconcile() {
        await initialize()
        if (disposed || !native) return
        const ticket = ++generation
        await accept(await client.request('locale.get'), ticket)
    }

    async function select(locale) {
        const intent = ++selection
        await initialize()
        if (disposed || intent !== selection) return
        if (!native) {
            const ticket = ++generation
            state.locale = isSupportedLocale(locale) ? locale : DEFAULT_LOCALE
            await render(state.locale, ticket)
            return
        }
        const expectedRevision = authoritative.revision
        const predecessor = pendingMutations ? mutationQueue : Promise.resolve()
        ++pendingMutations
        const action = predecessor.then(async previous => {
            if (disposed || intent !== selection) return previous
            const ticket = ++generation
            try {
                const result = await client.request('locale.set', { locale, expectedRevision: previous?.revision ?? expectedRevision })
                // A newer queued choice cancels display, but keeps this result's revision.
                // Serialize bridge mutations, not optional catalog downloads.
                return { revision: result.revision, rendering: accept(result, intent === selection ? ticket : -1) }
            } catch (error) {
                // Never replay intent after a revision or document rejection.
                ++selection
                if (error.state) await accept(error.state, ++generation)
                try { await reconcile() } catch { /* Retain the rejection's authoritative state. */ }
                if (!disposed) onCompatibility(error.uiMessage || localUiMessage('android.language.errors.changed'))
                throw error
            }
        })
        mutationQueue = action.then(result => {
            --pendingMutations
            return result
        }, () => { --pendingMutations })
        const result = await action
        await result?.rendering
    }

    function dispose() {
        disposed = true
        ++generation
        ++selection
        unsubscribe?.()
        document?.removeEventListener('visibilitychange', visibilityChanged)
        for (const type of guardedEvents) document?.removeEventListener(type, guard, true)
    }
    return { initialize, select, reconcile, dispose }
}

// Page-owned state survives settings/sidebar remounts. Keep preference even when
// an optional catalog fails and setLocale temporarily renders English instead.
export const interfaceLocaleState = shallowRef(null)
export const interfaceLocaleCompatibility = shallowRef(null)
let controller
function pageController() {
    if (!controller) {
        controller = createAndroidLocaleController({
            client: getAndroidAppClient(), document: globalThis.document,
            applyLocale: locale => setLocale(locale), readBrowserLocale: readInitialLocale,
            onCompatibility: message => { interfaceLocaleCompatibility.value = message },
        })
        // A bfcache page keeps its controller. A final document unload releases it.
        globalThis.window?.addEventListener('pagehide', event => {
            if (!event.persisted) controller.dispose()
        })
    }
    return controller
}

export async function initializeInterfaceLocale() {
    const state = await pageController().initialize()
    interfaceLocaleState.value = state
    if (state.mode === 'native' || (state.mode === 'incompatible' && state.revision !== null)) markAndroidApp()
    return state
}

export async function selectInterfaceLocale(locale) {
    await initializeInterfaceLocale()
    await pageController().select(locale)
}
