// Pure rules for the text-to-speech configs (shape normalized by src/twicc/tts/config.py).

/** The fields sent to the speech endpoint. `name` and `note` are not among them. */
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
        name: '',
        model: '',
        voice: '',
        response_format: 'mp3',
        speed: null,
        instructions: '',
        note: '',
        validated: null,
    }
}

/** The name shown for a config: its own name, else built from its model's name and its voice. */
export function ttsConfigDisplayName(config, models = []) {
    const name = config.name?.trim()
    if (name) return name
    return ttsConfigAutoName(config, models)
}

export function ttsConfigAutoName(config, models = []) {
    const model = config.model ? (models.find(entry => entry.id === config.model)?.name || config.model) : ''
    return [model, config.voice].filter(Boolean).join(' · ') || 'Unnamed config'
}

// One listed unit price per million units, short: 0.62, 15, 2500.
function perMillion(price) {
    const value = price * 1e6
    return value >= 100 ? value.toFixed(0) : String(Number(value.toPrecision(3)))
}

/**
 * The OpenRouter list price of a model, for the model picker: "Free", "$0.62/M in · $6/M out",
 * or '' when OpenRouter lists none. List prices only: the real cost can differ.
 */
export function formatTtsModelPrice(pricing) {
    const prompt = pricing?.prompt ?? null
    const completion = pricing?.completion ?? null
    if (prompt === null && completion === null) return ''
    // "Free" only when both prices are known zeros; a missing one is unknown, not free.
    if (prompt === 0 && completion === 0) return 'Free'
    const parts = []
    if (prompt) parts.push(`$${perMillion(prompt)}/M in`)
    if (completion) parts.push(`$${perMillion(completion)}/M out`)
    return parts.join(' · ')
}

// ── Known model facts (backend model_catalog.py) ─────────────────
//
// Each listed model carries `formats` and `streams` (lists, or null when TwiCC knows nothing
// about the model) and `max_chars`.

const FORMAT_LABELS = { mp3: 'MP3', pcm: 'PCM' }

/** The listed model `modelId`, or null when it is not in the loaded list. */
export function ttsModelFacts(models, modelId) {
    return models.find(model => model.id === modelId) ?? null
}

/** Whether the model streams: "Streaming", "Streaming in PCM", "No streaming", or '' when unknown. */
export function ttsStreamingLabel(model) {
    const streams = model?.streams
    if (!Array.isArray(streams)) return ''
    if (!streams.length) return 'No streaming'
    const formats = Array.isArray(model.formats) ? model.formats : []
    if (formats.every(format => streams.includes(format))) return 'Streaming'
    return `Streaming in ${streams.map(format => FORMAT_LABELS[format] ?? format).join(' and ')}`
}

/** The format switch options: a format the model does not accept stays shown, disabled. */
export function ttsFormatOptions(model) {
    const formats = Array.isArray(model?.formats) ? model.formats : null
    return Object.entries(FORMAT_LABELS).map(([value, label]) => (
        !formats || formats.includes(value)
            ? { value, label }
            : { value, label, disabled: true, title: `${label}: not supported by this model` }
    ))
}

/** The format to keep when the model changes: the current one, unless the new model refuses it. */
export function formatAfterModelChange(format, model) {
    const formats = Array.isArray(model?.formats) ? model.formats : null
    return !formats || formats.includes(format) ? format : formats[0]
}

/**
 * What the chosen format does for this model: '' when TwiCC knows nothing about the model.
 * A long text goes out in parts: the audio starts with the first one, unless the format is sent whole.
 */
export function ttsStreamingNote(model, format) {
    if (!Array.isArray(model?.streams)) return ''
    if (model.streams.includes(format)) return 'Streaming: the audio starts while it is generated.'
    return Array.isArray(model.whole_formats) && model.whole_formats.includes(format)
        ? 'No streaming: the audio starts once it is fully generated.'
        : 'No streaming: the audio starts once its first part is generated.'
}

/**
 * The model picker options, sorted by official name (case-insensitive), with the list price and
 * the streaming support on a quiet line. A stored model missing from the list stays first, so
 * the picker still shows it.
 */
export function ttsModelOptions(models, currentModel) {
    const options = models.map(model => ({
        value: model.id,
        label: model.name,
        description: [formatTtsModelPrice(model.pricing) || 'No list price', ttsStreamingLabel(model)]
            .filter(Boolean).join(' · '),
        keywords: model.id,
    })).sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }))
    if (!currentModel || models.some(model => model.id === currentModel)) return options
    // Not loaded (or OpenRouter unreachable) says nothing about the model: no mention then.
    const description = models.length ? 'Not in the OpenRouter list' : ''
    return [{ value: currentModel, label: currentModel, description }, ...options]
}

/**
 * The voice picker options of a model; a stored other voice stays first. `listed` says whether
 * the model is in the loaded list: if not, its voices are unknown, so no claim about the voice.
 */
export function ttsVoiceOptions(voices, currentVoice, { listed = true } = {}) {
    const options = voices.map(voice => ({ value: voice, label: voice }))
    if (!currentVoice || voices.includes(currentVoice)) return options
    const description = listed ? "Not in this model's list" : ''
    return [{ value: currentVoice, label: currentVoice, description }, ...options]
}

/** The voice to keep when the model changes: only one the new model lists. */
export function voiceAfterModelChange(voice, nextVoices) {
    return voice && nextVoices.includes(voice) ? voice : ''
}

/** The OpenRouter page of a model (its id is the page path). */
export function openRouterModelUrl(modelId) {
    return `https://openrouter.ai/${encodeURI(modelId)}`
}

/** Where the settings test of a config stands: 'loading', 'playing' or 'idle'. */
export function ttsTestState(playback, configId) {
    if (playback?.source !== 'test' || playback.configId !== configId) return 'idle'
    return playback.status === 'loading' || playback.status === 'playing' ? playback.status : 'idle'
}

/**
 * After a successful test: ask "Did you hear it correctly?" only when the config still has the
 * tested fields (no edit while loading) and they are not already the validated ones.
 */
export function shouldAskAfterTest(config, testedFields) {
    return sameSpeechFields(speechFieldsOf(config), testedFields) && !sameSpeechFields(config.validated, testedFields)
}

/** A new config with the patch applied. A speech field change drops the validation. */
export function patchTtsConfig(config, patch) {
    const next = { ...config, ...patch }
    if (!sameSpeechFields(config, next)) next.validated = null
    return next
}

export function duplicateTtsConfig(config, id) {
    // An unnamed config stays unnamed: its copy shows the same automatic name until edited.
    const name = config.name ? `${config.name} (copy)` : ''
    return { ...config, id, name, validated: config.validated && { ...config.validated } }
}

/** The message buttons show only with the key, an active config, and its validation. */
export function isTtsAvailable({ keyPresent, configs, activeId }) {
    if (!keyPresent || activeId == null) return false
    const active = (configs ?? []).find(config => config.id === activeId)
    return isConfigValidated(active)
}

// ── Last test run ────────────────────────────────────────────────

// Two significant digits, no trailing zero: 0.4, 1.1, 12.
function shortNumber(value) {
    return value >= 10 ? String(Math.round(value)) : String(Number(value.toPrecision(2)))
}

function pendingPrice(lastTest) {
    // No generation id: OpenRouter's lookup cannot run, the price will not come.
    return lastTest.generation_id ? 'price pending…' : 'price unknown'
}

/**
 * The compact stats of a config's last test run, comparable across texts of any length:
 * "0.4¢ / 1k ch · 0.3¢ / mn · 1.1 s / 1k ch" (cost per 1,000 characters, per minute of audio,
 * generation time per 1,000 characters). '' without a usable run.
 */
export function ttsTestStats(lastTest) {
    if (!lastTest?.chars) return ''
    const parts = []
    if (lastTest.cost == null) {
        parts.push(pendingPrice(lastTest))
    } else {
        parts.push(`${shortNumber((lastTest.cost / lastTest.chars) * 1000 * 100)}¢ / 1k ch`)
        if (lastTest.audio_seconds) parts.push(`${shortNumber((lastTest.cost / lastTest.audio_seconds) * 60 * 100)}¢ / mn`)
    }
    parts.push(`${shortNumber(lastTest.generation_ms / lastTest.chars)} s / 1k ch`)
    if (lastTest.first_audio_ms != null) parts.push(`1st audio ${shortNumber(lastTest.first_audio_ms / 1000)} s`)
    return parts.join(' · ')
}

function audioLength(seconds) {
    if (seconds < 60) return `${shortNumber(seconds)} s`
    const minutes = Math.floor(seconds / 60)
    return `${minutes} min ${Math.round(seconds - minutes * 60)} s`
}

/**
 * The exact figures of the last test run: "2,000 chars · 2 min 23 s of audio · generated in 2.2 s · $0.0080".
 * A text sent in several parts adds "· 4 parts" after the characters.
 */
export function ttsLastTestFacts(lastTest) {
    if (!lastTest?.chars) return ''
    const parts = [`${lastTest.chars.toLocaleString('en')} chars`]
    if (lastTest.parts > 1) parts.push(`${lastTest.parts} parts`)
    if (lastTest.audio_seconds) parts.push(`${audioLength(lastTest.audio_seconds)} of audio`)
    parts.push(`generated in ${shortNumber(lastTest.generation_ms / 1000)} s`)
    if (lastTest.first_audio_ms != null) parts.push(`first audio after ${shortNumber(lastTest.first_audio_ms / 1000)} s`)
    parts.push(lastTest.cost == null ? pendingPrice(lastTest) : `$${lastTest.cost.toPrecision(2)}`)
    return parts.join(' · ')
}
