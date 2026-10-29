// Pure rules for the text-to-speech configs (shape normalized by src/twicc/tts/config.py).

/** The fields sent to the speech endpoint. `name` is not one of them. */
export const SPEECH_FIELDS = ['model', 'voice', 'response_format', 'speed', 'instructions']

export const DEFAULT_TEST_TEXT = 'Hello! This is a TwiCC text-to-speech test.'

/** The speech fields of a config (also the validation snapshot). */
export function speechFieldsOf(config) {
    return Object.fromEntries(SPEECH_FIELDS.map(field => [field, config[field]]))
}

export function sameSpeechFields(a, b) {
    if (!a || !b) return false
    return SPEECH_FIELDS.every(field => a[field] === b[field])
}

/** True when the current speech fields are the ones the user confirmed. */
export function isConfigValidated(config) {
    return Boolean(config?.validated) && sameSpeechFields(config, config.validated)
}

export function createTtsConfig(id) {
    return {
        id,
        name: 'New voice',
        model: '',
        voice: '',
        response_format: 'mp3',
        speed: null,
        instructions: '',
        validated: null,
    }
}

/** A new config with the patch applied. A speech field change drops the validation. */
export function patchTtsConfig(config, patch) {
    const next = { ...config, ...patch }
    if (!sameSpeechFields(config, next)) next.validated = null
    return next
}

export function duplicateTtsConfig(config, id) {
    return { ...config, id, name: `${config.name} (copy)`, validated: config.validated && { ...config.validated } }
}

/** The message buttons show only with the key, an active config, and its validation. */
export function isTtsAvailable({ keyPresent, configs, activeId }) {
    if (!keyPresent || activeId == null) return false
    const active = (configs ?? []).find(config => config.id === activeId)
    return isConfigValidated(active)
}

export function speechCacheKey(text, fields) {
    return JSON.stringify([text, ...SPEECH_FIELDS.map(field => fields[field])])
}
