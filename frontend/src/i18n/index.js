import { createI18n } from 'vue-i18n'
import en from './locales/en.json' with { type: 'json' }

export const DEFAULT_LOCALE = 'en'
export const SUPPORTED_LOCALES = Object.freeze([
    { value: 'en', label: 'English' },
    { value: 'de', label: 'Deutsch' },
    { value: 'es', label: 'Español' },
    { value: 'fr', label: 'Français' },
    { value: 'pt-BR', label: 'Português (Brasil)' },
])

export function isSupportedLocale(value) {
    return SUPPORTED_LOCALES.some(locale => locale.value === value)
}

// Read only this local preference before authentication/bootstrap, so the login
// screen also uses the saved language. The settings store owns persistence.
export function readInitialLocale(storage) {
    try {
        storage ??= globalThis.localStorage
        const value = JSON.parse(storage?.getItem('twicc-settings') || '{}')?.locale
        return isSupportedLocale(value) ? value : DEFAULT_LOCALE
    } catch {
        return DEFAULT_LOCALE
    }
}

// Keep English in the entry bundle for fallback, including failed downloads.
// Each additional language loads its catalog and component/date translations together.
const localeLoaders = {
    de: async () => {
        const [messages] = await Promise.all([
            import('./locales/de.js'),
            import('dayjs/locale/de.js'),
            typeof window === 'undefined' ? null : import('@awesome.me/webawesome/dist/translations/de.js'),
        ])
        return messages.default
    },
    es: async () => {
        const [messages] = await Promise.all([
            import('./locales/es.js'),
            import('dayjs/locale/es.js'),
            typeof window === 'undefined' ? null : import('@awesome.me/webawesome/dist/translations/es.js'),
        ])
        return messages.default
    },
    fr: async () => {
        const [messages] = await Promise.all([
            import('./locales/fr.js'),
            import('dayjs/locale/fr.js'),
            typeof window === 'undefined' ? null : import('@awesome.me/webawesome/dist/translations/fr.js'),
        ])
        return messages.default
    },
    'pt-BR': async () => {
        const [messages] = await Promise.all([
            import('./locales/pt-BR.js'),
            import('dayjs/locale/pt-br.js'),
            typeof window === 'undefined' ? null : import('@awesome.me/webawesome/dist/translations/pt.js'),
        ])
        return messages.default
    },
}

// French treats zero like one ("0 projet"); vue-i18n's default two-form rule treats it as plural.
// Three-form messages keep the default zero/one/other selection.
const pluralRules = {
    fr: (choice, choicesLength) => choicesLength === 2
        ? (Math.abs(choice) < 2 ? 0 : 1)
        : (choice ? (Math.abs(choice) === 1 ? 1 : 2) : 0),
}

export function createLocaleManager(loaders = localeLoaders) {
    const catalogs = new Map([[DEFAULT_LOCALE, en]])
    const pending = new Map()
    let latestRequest = 0

    function createAppI18n(locale = DEFAULT_LOCALE) {
        return createI18n({
            legacy: false,
            globalInjection: true,
            locale: isSupportedLocale(locale) ? locale : DEFAULT_LOCALE,
            fallbackLocale: DEFAULT_LOCALE,
            pluralRules,
            messages: Object.fromEntries([...catalogs].map(([language, messages]) => [language, { ...messages }])),
        })
    }

    // Entry points decide which language to load. Public viewers never read the preference.
    const i18n = createAppI18n()

    async function loadLocaleMessages(locale) {
        if (i18n.global.availableLocales.includes(locale)) return
        if (!isSupportedLocale(locale) || !Object.hasOwn(loaders, locale)) {
            throw new Error(`No catalog loader for locale: ${locale}`)
        }
        if (!pending.has(locale)) {
            const download = Promise.resolve().then(() => loaders[locale]()).then(messages => {
                catalogs.set(locale, messages)
                i18n.global.setLocaleMessage(locale, { ...messages })
            }).finally(() => pending.delete(locale))
            pending.set(locale, download)
        }
        await pending.get(locale)
    }

    function applyLocale(locale, document) {
        i18n.global.locale.value = locale
        if (document) {
            document.documentElement.lang = locale
            document.documentElement.style?.setProperty('--twicc-loading-label', JSON.stringify(i18n.global.t('common.status.loading')))
        }
    }

    // Resolves to the language actually applied, or undefined for superseded work.
    // Callers that previously ignored the result remain compatible.
    async function setLocale(value, document = globalThis.document) {
        const request = ++latestRequest
        let locale = isSupportedLocale(value) ? value : DEFAULT_LOCALE
        if (!i18n.global.availableLocales.includes(locale)) {
            try { await loadLocaleMessages(locale) }
            catch (error) {
                if (request !== latestRequest) return
                console.warn(`Could not load locale ${locale}; using English.`, error)
                locale = DEFAULT_LOCALE
            }
        }
        // A slower download must not replace a more recent language choice.
        if (request === latestRequest) {
            applyLocale(locale, document)
            return locale
        }
    }

    return { createAppI18n, i18n, loadLocaleMessages, setLocale }
}

export const { createAppI18n, i18n, loadLocaleMessages, setLocale } = createLocaleManager()
