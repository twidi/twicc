import { createI18n } from 'vue-i18n'
import en from './locales/en.json' with { type: 'json' }
import ptBR from './locales/pt-BR.json' with { type: 'json' }

export const DEFAULT_LOCALE = 'en'
export const SUPPORTED_LOCALES = Object.freeze([
    { value: 'en', label: 'English' },
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

export function createAppI18n(locale = DEFAULT_LOCALE) {
    return createI18n({
        legacy: false,
        globalInjection: true,
        locale: isSupportedLocale(locale) ? locale : DEFAULT_LOCALE,
        fallbackLocale: DEFAULT_LOCALE,
        messages: { en: { ...en }, 'pt-BR': { ...ptBR } },
    })
}

export const i18n = createAppI18n(typeof window === 'undefined' ? DEFAULT_LOCALE : readInitialLocale())

export function setLocale(value, document = globalThis.document) {
    const locale = isSupportedLocale(value) ? value : DEFAULT_LOCALE
    i18n.global.locale.value = locale
    if (document) document.documentElement.lang = locale
}
