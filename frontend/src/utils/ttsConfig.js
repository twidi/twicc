import { i18n } from '../i18n/index.js'
import { localUiMessage } from '../i18n/uiMessages.js'

// Pure rules for the text-to-speech configs (shape normalized by src/twicc/tts/config.py).

import { PROVIDER, TITLE_SUGGESTION_MODEL_LABELS } from '../constants.js'

/** The speech fields of an OpenRouter config. */
export const SPEECH_FIELDS = ['model', 'voice', 'response_format', 'speed', 'instructions']

/** The speech fields of a script config (a user shell command gives the audio). */
export const SCRIPT_FIELDS = ['command', 'output', 'audio_format', 'pcm_rate', 'pcm_channels', 'max_chars']

// The PCM bounds of the backend (openrouter.MAX_PCM_RATE, openrouter.MAX_PCM_CHANNELS).
export const MAX_PCM_RATE = 384000
export const MAX_PCM_CHANNELS = 8

export const SCRIPT_OUTPUT_OPTIONS = [
    { value: 'stream', get label() { return i18n.global.t('settings.speech.output.stream') } },
    { value: 'whole', get label() { return i18n.global.t('settings.speech.output.whole') } },
    { value: 'path', get label() { return i18n.global.t('settings.speech.output.path') } },
]

export const SCRIPT_FORMAT_OPTIONS = [
    { value: 'mp3', label: 'MP3' },
    { value: 'wav', label: 'WAV' },
    { value: 'pcm', label: 'PCM' },
]

export const DEFAULT_TEST_TEXT = 'Hello! This is a TwiCC text-to-speech test.'

/** The kind of a config: 'script' for a script config, else 'openrouter' (the default). */
export function kindOf(config) {
    return config?.kind === 'script' ? 'script' : 'openrouter'
}

function fieldsOfKind(kind) {
    return kind === 'script' ? SCRIPT_FIELDS : SPEECH_FIELDS
}

/** The kind and the speech fields of a config (also the validation snapshot). */
export function speechFieldsOf(config) {
    const kind = kindOf(config)
    return { kind, ...Object.fromEntries(fieldsOfKind(kind).map(field => [field, config[field]])) }
}

/**
 * Whether two configs or snapshots have the same speech fields. The kind comes from whichever
 * one carries it: a stored snapshot has none. Two different kinds are never the same.
 */
export function sameSpeechFields(a, b) {
    if (!a || !b) return false
    if (a.kind && b.kind && kindOf(a) !== kindOf(b)) return false
    return fieldsOfKind(kindOf({ kind: a.kind ?? b.kind })).every(field => a[field] === b[field])
}

/** True when the current speech fields are the ones the user confirmed. */
export function isConfigValidated(config) {
    return Boolean(config?.validated) && sameSpeechFields(config, config.validated)
}

export function createTtsConfig(id) {
    return {
        id,
        kind: 'openrouter',
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

export function createScriptConfig(id) {
    return {
        id,
        kind: 'script',
        name: '',
        command: '',
        output: 'stream',
        audio_format: 'mp3',
        pcm_rate: null,
        pcm_channels: 1,
        max_chars: null,
        // Not in SCRIPT_FIELDS: a change keeps the validation.
        delete_file: true,
        note: '',
        validated: null,
    }
}

/** The name shown for a config: its own name, else its automatic name (see `ttsConfigAutoName`). */
export function ttsConfigDisplayName(config, models = []) {
    const name = config.name?.trim()
    if (name) return name
    return ttsConfigAutoName(config, models)
}

export function ttsConfigAutoName(config, models = []) {
    if (kindOf(config) === 'script') {
        // The base name of the command's first word.
        const program = (config.command ?? '').trim().split(/\s+/)[0].split('/').pop()
        return program ? i18n.global.t('settings.speech.scriptName', { program }) : i18n.global.t('settings.speech.unnamedScript')
    }
    const model = config.model ? (models.find(entry => entry.id === config.model)?.name || config.model) : ''
    return [model, config.voice].filter(Boolean).join(' · ') || i18n.global.t('settings.speech.unnamedConfig')
}

function formatPrice(value, significantDigits = 3) {
    return new Intl.NumberFormat(i18n.global.locale.value, { style: 'currency', currency: 'USD', maximumSignificantDigits: significantDigits, ...(significantDigits === 2 ? { minimumSignificantDigits: 2 } : {}) }).format(value)
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
    if (prompt === 0 && completion === 0) return i18n.global.t('settings.speech.free')
    const parts = []
    if (prompt) parts.push(i18n.global.t('settings.speech.priceIn', { price: formatPrice(prompt * 1e6) }))
    if (completion) parts.push(i18n.global.t('settings.speech.priceOut', { price: formatPrice(completion * 1e6) }))
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
    if (!streams.length) return i18n.global.t('settings.speech.noStreaming')
    const formats = Array.isArray(model.formats) ? model.formats : []
    if (formats.every(format => streams.includes(format))) return i18n.global.t('settings.speech.streaming')
    return i18n.global.t('settings.speech.streamingFormats', { formats: new Intl.ListFormat(i18n.global.locale.value, { type: 'conjunction' }).format(streams.map(format => FORMAT_LABELS[format] ?? format)) })
}

/** The format switch options: a format the model does not accept stays shown, disabled. */
export function ttsFormatOptions(model) {
    const formats = Array.isArray(model?.formats) ? model.formats : null
    return Object.entries(FORMAT_LABELS).map(([value, label]) => (
        !formats || formats.includes(value)
            ? { value, label }
            : { value, label, disabled: true, title: i18n.global.t('settings.speech.unsupported', { format: label }) }
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
    if (model.streams.includes(format)) return i18n.global.t('settings.speech.streamingNote')
    return Array.isArray(model.whole_formats) && model.whole_formats.includes(format)
        ? i18n.global.t('settings.speech.wholeNote')
        : i18n.global.t('settings.speech.firstPartNote')
}

/** What the output kind of a script config does: a whole audio or a file path starts after its first part. */
export function scriptStreamingNote(config) {
    if (config.output === 'stream') return i18n.global.t('settings.speech.streamingNote')
    return config.max_chars
        ? i18n.global.t('settings.speech.firstPartNote')
        : i18n.global.t('settings.speech.wholeNote')
}

/** The audio format the browser receives: a script's WAV and PCM both reach it as PCM. */
export function requestedFormat(fields) {
    if (kindOf(fields) === 'script') return fields.audio_format === 'mp3' ? 'mp3' : 'pcm'
    return fields.response_format
}

/** A script config can run a test with a command, and with a sample rate for PCM. */
export function canTestScript(config) {
    return Boolean(config.command?.trim()) && (config.audio_format !== 'pcm' || Boolean(config.pcm_rate))
}

/**
 * A positive integer input: '' gives null, an integer within 1..max gives that number, anything
 * else gives undefined (the caller keeps the stored value).
 */
export function positiveIntegerInput(text, max) {
    const value = String(text ?? '').trim()
    if (!value) return null
    if (!/^\d+$/.test(value)) return undefined
    const number = Number(value)
    return number >= 1 && number <= max ? number : undefined
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
        description: [formatTtsModelPrice(model.pricing) || i18n.global.t('settings.speech.noPrice'), ttsStreamingLabel(model)]
            .filter(Boolean).join(' · '),
        keywords: model.id,
    })).sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }))
    if (!currentModel || models.some(model => model.id === currentModel)) return options
    // Not loaded (or OpenRouter unreachable) says nothing about the model: no mention then.
    const description = models.length ? i18n.global.t('settings.speech.notListed') : ''
    return [{ value: currentModel, label: currentModel, description }, ...options]
}

/**
 * The voice picker options of a model; a stored other voice stays first. `listed` says whether
 * the model is in the loaded list: if not, its voices are unknown, so no claim about the voice.
 */
export function ttsVoiceOptions(voices, currentVoice, { listed = true } = {}) {
    const options = voices.map(voice => ({ value: voice, label: voice }))
    if (!currentVoice || voices.includes(currentVoice)) return options
    const description = listed ? i18n.global.t('settings.speech.voiceNotListed') : ''
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
    const name = config.name ? i18n.global.t('settings.speech.copy', { name: config.name }) : ''
    return { ...config, id, name, validated: config.validated && { ...config.validated } }
}

/** The message buttons show only with an active config, its validation, and the key (not needed by a script). */
export function isTtsAvailable({ keyPresent, configs, activeId }) {
    if (activeId == null) return false
    const active = (configs ?? []).find(config => config.id === activeId)
    return isConfigValidated(active) && (keyPresent || kindOf(active) === 'script')
}

// ── Last test run ────────────────────────────────────────────────

// Two significant digits, no trailing zero: 0.4, 1.1, 12.
function shortNumber(value) {
    return new Intl.NumberFormat(i18n.global.locale.value, value >= 10 ? { maximumFractionDigits: 0 } : { maximumSignificantDigits: 2 }).format(value)
}

function pendingPrice(lastTest) {
    // No generation id: OpenRouter's lookup cannot run, the price will not come.
    return lastTest.generation_id ? i18n.global.t('settings.speech.pricePending') : i18n.global.t('settings.speech.priceUnknown')
}

/**
 * The compact stats of a config's last test run, comparable across texts of any length:
 * "0.4¢ / 1k ch · 0.3¢ / mn · 1.1 s / 1k ch" (cost per 1,000 characters, per minute of audio,
 * generation time per 1,000 characters). '' without a usable run. With `priced: false` (a
 * script config), no cost part.
 */
export function ttsTestStats(lastTest, { priced = true } = {}) {
    if (!lastTest?.chars) return ''
    const parts = []
    if (priced && lastTest.cost == null) {
        parts.push(pendingPrice(lastTest))
    } else if (priced) {
        parts.push(i18n.global.t('settings.speech.costPerCharacters', { value: shortNumber((lastTest.cost / lastTest.chars) * 1000 * 100) }))
        if (lastTest.audio_seconds) parts.push(i18n.global.t('settings.speech.costPerMinute', { value: shortNumber((lastTest.cost / lastTest.audio_seconds) * 60 * 100) }))
    }
    parts.push(i18n.global.t('settings.speech.timePerCharacters', { value: shortNumber(lastTest.generation_ms / lastTest.chars) }))
    if (lastTest.first_audio_ms != null) parts.push(i18n.global.t('settings.speech.firstAudio', { value: shortNumber(lastTest.first_audio_ms / 1000) }))
    return parts.join(' · ')
}

function audioLength(seconds) {
    if (seconds < 60) return `${shortNumber(seconds)} s`
    const minutes = Math.floor(seconds / 60)
    return `${minutes} min ${Math.round(seconds - minutes * 60)} s`
}

/**
 * The exact figures of the last test run: "2,000 chars · 2 min 23 s of audio · generated in 2.2 s · $0.0080".
 * A text sent in several parts adds "· 4 parts" after the characters. With `priced: false` (a
 * script config), no cost part.
 */
export function ttsLastTestFacts(lastTest, { priced = true } = {}) {
    if (!lastTest?.chars) return ''
    const parts = [i18n.global.t('settings.speech.characters', { count: new Intl.NumberFormat(i18n.global.locale.value).format(lastTest.chars) })]
    if (lastTest.parts > 1) parts.push(i18n.global.t('settings.speech.parts', { count: lastTest.parts }, lastTest.parts))
    if (lastTest.audio_seconds) parts.push(i18n.global.t('settings.speech.audioLength', { length: audioLength(lastTest.audio_seconds) }))
    parts.push(i18n.global.t('settings.speech.generationTime', { time: shortNumber(lastTest.generation_ms / 1000) }))
    if (lastTest.first_audio_ms != null) parts.push(i18n.global.t('settings.speech.firstAudioDelay', { time: shortNumber(lastTest.first_audio_ms / 1000) }))
    if (priced) parts.push(lastTest.cost == null ? pendingPrice(lastTest) : formatPrice(lastTest.cost, 2))
    return parts.join(' · ')
}

// ── Text adaptation ──────────────────────────────────────────────
//
// The global `adapt` setting of tts.json: null (no stored choice: on, with the first enabled
// provider), 'haiku', 'luna', 'openrouter' or 'off'. 'openrouter' runs on OpenRouter, not on a
// provider: it needs the OpenRouter key, and it is never the default.

/** The provider each agent engine runs on, in the default order: Claude, then Codex. */
export const ADAPT_PROVIDERS = Object.freeze({ haiku: PROVIDER.CLAUDE_CODE, luna: PROVIDER.CODEX })

/** The engine that runs on OpenRouter with the OpenRouter key (`OPENROUTER_MODEL_DESCRIPTION`). */
export const OPENROUTER_ADAPT = 'openrouter'

const ADAPT_CHOICES = new Set(['haiku', 'luna', OPENROUTER_ADAPT, 'off'])

// The provider labels of the disabled-option titles (providers/*/helpers.js `static label`).
export const ADAPT_PROVIDER_LABELS = { [PROVIDER.CLAUDE_CODE]: 'Claude', [PROVIDER.CODEX]: 'Codex' }

const ADAPT_REASON_TEXTS = {
    get off() { return i18n.global.t('settings.speech.adaptReason.off') },
    get no_provider() { return i18n.global.t('settings.speech.adaptReason.no_provider') },
    get failed() { return i18n.global.t('settings.speech.adaptReason.failed') },
    get stalled() { return i18n.global.t('settings.speech.adaptReason.stalled') },
    get empty() { return i18n.global.t('settings.speech.adaptReason.empty') },
    // No parentheses: the text goes inside the "failed (…)" of a named engine.
    get refused() { return i18n.global.t('settings.speech.adaptReason.refused') },
}

// The reasons of an engine that ran and failed (the others: no engine ran).
const ADAPT_FAILURE_REASONS = new Set(['failed', 'stalled', 'empty', 'refused'])

/** A stored `adapt` value, or null (no stored choice) for anything else, as the backend reads it. */
export function normalizeAdapt(value) {
    return ADAPT_CHOICES.has(value) ? value : null
}

function enabledSet(enabledProviders) {
    return enabledProviders instanceof Set ? enabledProviders : new Set(enabledProviders || [])
}

function anyAdaptProvider(enabled) {
    return Object.values(ADAPT_PROVIDERS).some(provider => enabled.has(provider))
}

/**
 * The adaptation that really runs, given which providers are enabled and whether the OpenRouter
 * key is set: 'haiku', 'luna', 'openrouter' or 'off'. Display only: the stored choice is never
 * rewritten, so enabling its provider again (or setting the key) restores it.
 * 'off' stays off. 'openrouter' runs when the key is set, with or without a provider; without the
 * key it is no stored choice. No stored choice takes the first enabled agent; a forced agent whose
 * provider is disabled takes the other enabled one. No enabled provider gives 'off'.
 */
export function effectiveAdapt(stored, enabledProviders, keyPresent = false) {
    const choice = normalizeAdapt(stored)
    if (choice === 'off') return 'off'
    if (choice === OPENROUTER_ADAPT && keyPresent) return OPENROUTER_ADAPT
    const enabled = enabledSet(enabledProviders)
    if (choice && enabled.has(ADAPT_PROVIDERS[choice])) return choice
    return Object.keys(ADAPT_PROVIDERS).find(engine => enabled.has(ADAPT_PROVIDERS[engine])) ?? 'off'
}

/** Summary generation follows the existing engine routing, independently of saved text and audio. */
export function summaryGenerationAvailability(model, enabledProviders, keyPresent = false, openrouterEligible = true) {
    const requested = model === 'auto' ? null : model
    const engine = effectiveAdapt(requested, enabledProviders, keyPresent && openrouterEligible)
    const unavailableKey = engine === 'off' ? (requested === 'openrouter' && keyPresent && !openrouterEligible
        ? 'settings.speech.sourceLimit' : 'settings.speech.noSummaryEngine') : null
    return { engine, available: engine !== 'off', unavailableReason: unavailableKey ? i18n.global.t(unavailableKey) : '',
        unavailableReasonMessage: unavailableKey ? localUiMessage(unavailableKey) : null }
}

/**
 * The adaptation switch options: the agents, OpenRouter, then Off. An engine that cannot run (its
 * provider disabled, or no OpenRouter key) stays shown, disabled, with the reason as its title.
 */
export function adaptOptions(enabledProviders, keyPresent = false) {
    const enabled = enabledSet(enabledProviders)
    const engines = Object.entries(ADAPT_PROVIDERS).map(([value, provider]) => {
        const label = TITLE_SUGGESTION_MODEL_LABELS[value]
        return enabled.has(provider)
            ? { value, label }
            : { value, label, disabled: true, title: i18n.global.t('settings.speech.providerDisabled', { model: label, provider: ADAPT_PROVIDER_LABELS[provider] }) }
    })
    const openrouterLabel = TITLE_SUGGESTION_MODEL_LABELS[OPENROUTER_ADAPT]
    const openrouter = keyPresent
        ? { value: OPENROUTER_ADAPT, label: openrouterLabel }
        : { value: OPENROUTER_ADAPT, label: openrouterLabel, disabled: true, title: i18n.global.t('settings.speech.missingKey', { model: openrouterLabel }) }
    return [...engines, openrouter, { value: 'off', label: i18n.global.t('settings.speech.off') }]
}

/**
 * The hints under the adaptation switch, for the effective `choice` (`effectiveAdapt`):
 * `noProvider` when it is 'off' with no Claude or Codex provider enabled (that hint replaces the
 * generic one), and `openrouter` (the model OpenRouter uses, where it sends the text and who pays)
 * whenever the OpenRouter key is set, whatever the choice: the switch has no room for a note per
 * option, so the user reads it before choosing OpenRouter. Without the key, the key-missing
 * callout already says that OpenRouter is unavailable.
 */
export function adaptHint(choice, enabledProviders, keyPresent = false) {
    return {
        noProvider: choice === 'off' && !anyAdaptProvider(enabledSet(enabledProviders)),
        openrouter: !!keyPresent,
    }
}

/**
 * The adaptation of the last test run: "Adapted by Claude Haiku 5.5 · first text 2.1 s", or
 * "Not adapted: the option is off". When the engine of the last attempt (`adapt_engine`) failed,
 * it is named: "Not adapted: Claude Haiku 5.5 failed (the model failed)" (a record without
 * `adapt_engine`, written before it, keeps "Not adapted: the model failed"). After an OpenRouter
 * failure, why OpenRouter failed comes last: "· OpenRouter failed (the model did not answer in
 * time)". '' for a record written before the text adaptation.
 */
export function ttsAdaptationFacts(lastTest) {
    const fallback = ADAPT_REASON_TEXTS[lastTest?.fallback_reason]
    const withFallback = text => (fallback ? i18n.global.t('settings.speech.adaptationFallback', { text, engine: TITLE_SUGGESTION_MODEL_LABELS[OPENROUTER_ADAPT], reason: fallback }) : text)
    const engine = lastTest?.adapted_by
    if (engine && TITLE_SUGGESTION_MODEL_LABELS[engine]) {
        const parts = [i18n.global.t('settings.speech.adaptedBy', { engine: TITLE_SUGGESTION_MODEL_LABELS[engine] })]
        // No wait for an adaptation served from the cache.
        if (lastTest.adapt_first_ms != null) parts.push(i18n.global.t('settings.speech.firstText', { time: shortNumber(lastTest.adapt_first_ms / 1000) }))
        return withFallback(parts.join(' · '))
    }
    const reason = ADAPT_REASON_TEXTS[lastTest?.adapt_reason]
    if (!reason) return ''
    const failedEngine = TITLE_SUGGESTION_MODEL_LABELS[lastTest.adapt_engine]
    if (failedEngine && ADAPT_FAILURE_REASONS.has(lastTest.adapt_reason)) {
        return withFallback(i18n.global.t('settings.speech.notAdaptedEngine', { engine: failedEngine, reason }))
    }
    return withFallback(i18n.global.t('settings.speech.notAdapted', { reason }))
}
