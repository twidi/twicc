import { remoteUiMessage } from '../i18n/remoteMessages.js'
import { i18n } from '../i18n/index.js'
import { defineStore } from 'pinia'
import { generateUUID } from '../utils/crypto.js'
import { captureTelemetry, reportActivity } from '../utils/telemetryClient.js'
import { isPcmType, parsePcmType, pcmToWavBlob } from '../utils/pcmAudio.js'
import { createPcmStreamPlayer } from '../utils/pcmStreamPlayer.js'
import { buffersSending, currentPlatform, playbackMode, speechEndResult, unreachableMessage } from '../utils/speechOutcome.js'
import { createSpeechPlayer } from '../utils/speechPlayer.js'
import {
    createScriptConfig,
    createTtsConfig,
    duplicateTtsConfig,
    isConfigValidated,
    isTtsAvailable,
    kindOf,
    normalizeAdapt,
    patchTtsConfig,
    requestedFormat,
    sameSpeechFields,
    speechFieldsOf,
} from '../utils/ttsConfig.js'

/**
 * App-wide text-to-speech service: the configs (persisted in <data_dir>/tts.json via
 * WebSocket sync) and the single playback of the app.
 *
 * One playback at a time. It is owned by no view: it survives every in-app navigation and
 * Web playback stops on page reload or the login page. Native playback remains in the Android service.
 *
 * A playback opens the speech on the backend (an audio id), plays the audio while the backend
 * still streams it, asks the backend how the audio ended, then releases the audio id.
 * The backend caches the audio: the browser keeps none.
 */

// Playback internals, kept out of the reactive state.
let controller = null // the open request
let fetchController = null // the whole-audio download of the 'buffered' mode
let player = null
let pcmPlayer = null
let generation = 0
// The audio id of the current playback, and the store that opened it: `{ id, store }`, or null.
let currentAudio = null
// The store of the latest playback: a page kept in the back-forward cache stops it.
let playbackStore = null
// Resolves the promise the current playback still owes (its start or its end) with ABORTED.
let abortPending = null
// The targets that already release the current audio on `pagehide`.
let pageHideTargets = new WeakSet()
// The model list loads: only the latest call applies its result.
let modelsGeneration = 0
const nativeClients = new WeakMap()

const ABORTED = Object.freeze({ ok: false, aborted: true })
const BACKEND_STATES = new Set(['complete', 'failed', 'running', 'unknown'])
// The ending request gives up after this delay: no answer, which the end table handles.
const ENDING_TIMEOUT_MS = 10000

/** Tests only: forget the in-flight requests, the players, the current audio and the pagehide targets. */
export function _resetSpeechForTests() {
    controller?.abort()
    controller = null
    fetchController?.abort()
    fetchController = null
    player = null
    pcmPlayer = null
    currentAudio = null
    playbackStore = null
    abortPending = null
    pageHideTargets = new WeakSet()
    // Never back to 0: a continuation left by a previous test must not match a new playback.
    generation += 1
    modelsGeneration += 1
}

/** The URL of an open speech audio: read it with GET, release it with DELETE. */
export function audioUrl(audioId) {
    return `/api/tts/audio/${encodeURIComponent(audioId)}/`
}

/**
 * Release the current audio when the page goes away (a reload, a closed tab): the backend
 * would otherwise keep it until its timeout. Adds one listener per target, however many calls.
 *
 * The DELETE leaves synchronously, inside the event (no lazy import: the page may be gone after
 * it). A page kept in the back-forward cache (`persisted`) also stops its playback: its audio id
 * is released. `fetchImpl` is a seam for the tests.
 */
export function installPageHideRelease(target, fetchImpl = (...args) => fetch(...args)) {
    if (pageHideTargets.has(target)) return
    pageHideTargets.add(target)
    target.addEventListener('pagehide', (event) => {
        const audio = currentAudio
        if (audio) {
            currentAudio = null
            fetchImpl(audioUrl(audio.id), { method: 'DELETE', keepalive: true }).catch(() => {})
        }
        if (event.persisted) playbackStore?.stop()
    })
}

function isAbortError(error) {
    return error?.name === 'AbortError'
}

async function speechErrorOf(response) {
    try {
        const data = await response.json()
        if (typeof data?.error === 'string' && data.error) return { error: remoteUiMessage(data, data.error), summaryChanged: data.summary_changed === true }
    } catch {
        // Not a JSON body: fall back to the status.
    }
    return { error: i18n.global.t('session.speech.errors.requestFailed', { status: response.status }) }
}

/** The backend's ending answer `{ state, error }`, or null: a failed request, a non-2xx answer, another body. */
async function readEnding(store, audioId) {
    try {
        const response = await store._fetchEnding(audioId, AbortSignal.timeout(ENDING_TIMEOUT_MS))
        if (!response.ok) return null
        const data = await response.json()
        return BACKEND_STATES.has(data?.state) ? { state: data.state, error: remoteUiMessage(data, data.error ?? null) } : null
    } catch {
        return null
    }
}

/** Cancel playback resources without publishing a state between two readings. */
function cancelPlayback(store) {
    generation += 1
    controller?.abort()
    controller = null
    fetchController?.abort()
    fetchController = null
    if (currentAudio) store._releaseAudio(currentAudio.id)
    currentAudio = null
    player?.stop()
    pcmPlayer?.stop()
    const abort = abortPending
    abortPending = null
    abort?.()
}

/** Reuse the app's two players and unlock both output types inside a user gesture. */
function primePlayers() {
    player ??= createSpeechPlayer()
    pcmPlayer ??= createPcmStreamPlayer()
    player.prime()
    pcmPlayer.prime()
}

/**
 * Stop the current playback, then read `text` aloud with `fields`. Never rejects.
 * Every `await` is followed by a generation check: a newer play or a stop owns the state.
 *
 * Resolves `{ ok: true, ended }` when the sound starts (`ended` resolves the end result),
 * `{ ok: false, error }` when it cannot start, or ABORTED.
 */
async function startPlayback(store, text, fields, { useCache, source, configId = null, scriptId = null, origin = null, owner = null, announcement = '', summaryRef = null, identity = null, nativeRequest = null }) {
    const telemetryToken = captureTelemetry()
    if (store.nativeNegotiating) return { ok: false, error: i18n.global.t('session.speech.errors.initializing') }
    const native = nativeClients.get(store)?.client
    if (store.nativeMode) {
        if (!native) return { ok: false, error: i18n.global.t('session.speech.errors.nativeUnavailable') }
        if (source === 'summary' && !nativeRequest) return { ok: false, error: i18n.global.t('session.speech.errors.summaryGone') }
        const speech = { ...fields, cache: useCache, ...(source !== 'summary' ? { text } : {}),
            ...(configId ? { config_id: configId } : {}), ...(scriptId ? { script_id: scriptId } : {}),
            ...(origin ? { origin } : {}), ...(announcement ? { announcement } : {}) }
        const loading = { text, status: 'loading', source, configId, owner, identity }
        store.playback = loading
        const pending = native.manual({ kind: source === 'summary' ? 'summary' : 'text', speech,
            ...(source === 'summary' ? { summary: JSON.parse(JSON.stringify(nativeRequest)) } : { source }),
            identity: identity?.selectionId ? null : identity, text: text ?? '', config_id: configId })
        if (identity?.selectionId) nativeClients.get(store).selection = { operationId: pending.operationId, identity: { ...identity } }
        loading.operationId = pending.operationId
        const result = await pending
        if (!result.ok && store.playback.operationId === pending.operationId && store.status === 'loading') {
            store.playback = { text: null, status: 'idle', source: null, configId: null, owner: null }
        }
        return result
    }
    cancelPlayback(store)
    const mine = ++generation

    // Synchronous, inside the click: later playback on the element and the context is user-activated.
    primePlayers()
    playbackStore = store
    if (typeof window !== 'undefined') installPageHideRelease(window)
    store.playback = { text, status: 'loading', source, configId, owner, ...(identity ? { identity } : {}) }

    let opened
    try {
        controller = new AbortController()
        // A settings test names its config: the backend keeps the run as its last test.
        // A script config names itself: the backend runs the command stored for it.
        // A platform that buffers the audio asks for it in one go: pacing would only delay it.
        let reference = null
        if (source === 'summary') {
            const cancelled = new Promise(resolve => { abortPending = () => resolve(ABORTED) })
            reference = await Promise.race([Promise.resolve(summaryRef), cancelled])
            if (generation !== mine || reference === ABORTED) return ABORTED
            abortPending = null
            const live = typeof reference?.id === 'string' && reference.id.trim()
            const saved = Number.isInteger(reference?.saved_id) && reference.saved_id > 0
                && /^[0-9a-f]{64}$/.test(reference?.result_hash)
            if ((!live && !saved) || (reference?.id !== undefined && reference?.saved_id !== undefined)) {
                backToIdle(store, mine)
                return { ok: false, error: i18n.global.t('session.speech.errors.summaryGone') }
            }
        }
        const body = {
            ...(reference ? reference.id
                ? { summary_id: reference.id }
                : { saved_summary_id: reference.saved_id, summary_result_hash: reference.result_hash }
                : { text }),
            ...fields,
            cache: useCache,
            ...(configId ? { config_id: configId } : {}),
            ...(scriptId ? { script_id: scriptId } : {}),
            ...(origin ? { origin } : {}),
            ...(announcement ? { announcement } : {}),
            ...(buffersSending(requestedFormat(fields), currentPlatform()) ? { buffered: true } : {}),
        }
        const response = await store._openSpeech(body, controller.signal)
        if (!response.ok) {
            if (generation !== mine) return ABORTED
            const failure = await speechErrorOf(response)
            if (generation !== mine) return ABORTED
            backToIdle(store, mine)
            return { ok: false, error: failure.error, ...(source === 'summary' && reference?.saved_id && response.status === 409 && failure.summaryChanged
                ? { summaryChanged: true, savedId: reference.saved_id } : {}) }
        }
        const data = await response.json()
        if (typeof data?.audio_id !== 'string' || typeof data?.content_type !== 'string') throw new TypeError('Bad speech answer')
        opened = { audioId: data.audio_id, contentType: data.content_type }
    } catch (error) {
        if (isAbortError(error) || generation !== mine) return ABORTED
        backToIdle(store, mine)
        return { ok: false, error: unreachableMessage() }
    }
    // The audio opened for a playback that a newer play or a stop replaced: nobody reads it.
    if (generation !== mine) {
        store._releaseAudio(opened.audioId)
        return ABORTED
    }
    controller = null
    currentAudio = { id: opened.audioId, store }
    return new Promise(resolveStart => playOpened(store, mine, opened, { text, source, configId, owner, telemetryToken, ...(identity ? { identity } : {}) }, resolveStart))
}

function backToIdle(store, mine) {
    if (generation !== mine) return
    store.playback = { text: null, status: 'idle', source: null, configId: null, owner: null }
    // The click primed the PCM context: suspend it until the next playback.
    pcmPlayer?.stop()
}

/** Play the open audio with the player of its type and platform, then run the ending step. */
function playOpened(store, mine, { audioId, contentType }, { text, source, configId, owner, identity, telemetryToken }, resolveStart) {
    let started = false
    let finishing = false
    let resolveEnded
    const ended = new Promise(resolve => { resolveEnded = resolve })
    abortPending = () => (started ? resolveEnded : resolveStart)(ABORTED)

    // The end of the playback (`onEnded`, `onError`): the backend tells how the audio ended.
    async function finish(playerEnd) {
        if (generation !== mine || finishing) return
        finishing = true
        const backend = await readEnding(store, audioId)
        // A stop during the ending request released the audio and resolved ABORTED.
        if (generation !== mine) return
        const result = speechEndResult(playerEnd, backend)
        store._releaseAudio(audioId)
        currentAudio = null
        abortPending = null
        backToIdle(store, mine)
        if (started) resolveEnded(result)
        // Ended before any sound (an empty audio): the start is a success that is already over.
        else resolveStart(result.ok ? { ok: true, ended: Promise.resolve({ ok: true }) } : result)
    }

    const handlers = {
        onPlaying() {
            if (generation !== mine || started || finishing) return
            started = true
            if (!configId) reportActivity(owner === null ? 'manual_tts_messages' : 'timeline_tts_messages', telemetryToken)
            store.playback = { text, status: 'playing', source, configId, owner, ...(identity ? { identity } : {}) }
            resolveStart({ ok: true, ended })
        },
        onEnded() {
            finish({ kind: 'ended' })
        },
        onError(reason) {
            finish({ kind: 'error', reason, started })
        },
    }

    const url = audioUrl(audioId)
    const mode = playbackMode(contentType, currentPlatform())
    // Only the 'pcm-stream' mode plays through the PCM context the click primed: suspend it.
    if (mode !== 'pcm-stream') pcmPlayer.stop()
    if (mode === 'media') {
        player.playUrl(url, handlers)
        return
    }
    const pcm = isPcmType(contentType)
    const format = pcm ? parsePcmType(contentType) : null
    // A PCM audio without its rate and channel count cannot play.
    if (pcm && !format) handlers.onError('refused')
    else if (mode === 'pcm-stream') pcmPlayer.play(url, format, handlers)
    else playBuffered(store, mine, url, format, handlers)
}

/** The 'buffered' mode: download the whole audio, then play it from memory (PCM as a WAV file). */
async function playBuffered(store, mine, url, format, handlers) {
    fetchController = new AbortController()
    let blob
    try {
        const response = await store._fetchAudio(url, fetchController.signal)
        if (generation !== mine) return
        if (!response.ok) {
            response.body?.cancel().catch(() => {})
            handlers.onError('network')
            return
        }
        blob = format
            ? pcmToWavBlob([new Uint8Array(await response.arrayBuffer())], format.rate, format.channels)
            : await response.blob()
    } catch {
        // The handlers ignore a stopped playback (an aborted download).
        handlers.onError('network')
        return
    }
    if (generation !== mine) return
    fetchController = null
    player.playBlob(blob, handlers)
}

export const useTextToSpeechStore = defineStore('textToSpeech', {
    state: () => ({
        nativeMode: false, nativeNegotiating: false, nativeSnapshot: null,
        configs: [],
        activeId: null,
        keyPresent: false,
        envPath: '',
        testText: '', // the settings test sentence; '' means the default one
        adapt: null, // the text adaptation choice: null (on, first enabled provider), 'haiku', 'luna', 'openrouter' or 'off'
        models: [], // [{ id, name, voices: [string], pricing: { prompt, completion } }] from OpenRouter
        modelsStatus: 'idle', // 'idle' | 'loading' | 'loaded' | 'failed'
        // source: 'message' | 'selection' | 'test' | null; configId: the config a settings test is about, else null.
        // owner: an automatic listener's activation token, or null for manual speech and idle.
        playback: { text: null, status: 'idle', source: null, configId: null, owner: null },
    }),

    getters: {
        activeConfig: (state) => state.configs.find(config => config.id === state.activeId) ?? null,

        /** True when the active config is validated, and the key is present (a script config needs none). */
        available: (state) => !state.nativeNegotiating && isTtsAvailable({ keyPresent: state.keyPresent, configs: state.configs, activeId: state.activeId }),

        /** The global playback state: 'idle' | 'loading' | 'playing'. */
        status: (state) => state.playback.status,

        /** The state of a message playback for this text: 'idle' | 'loading' | 'playing'. */
        statusForText: (state) => (text) => (
            state.playback.source === 'message' && state.playback.text === text ? state.playback.status : 'idle'
        ),

        /** A snapshot identity keeps summary status stable across views and reference acquisition. */
        statusForSummary: (state) => (identity) => {
            const current = state.playback.identity
            return state.playback.source === 'summary' && current && identity
                && Object.keys(current).length === Object.keys(identity).length
                && Object.keys(current).every(key => current[key] === identity[key])
                ? state.playback.status : 'idle'
        },

        /** The state of a selection playback for this text: 'idle' | 'loading' | 'playing'. */
        statusForSelection: (state) => (text) => (
            state.playback.source === 'selection' && state.playback.text === text ? state.playback.status : 'idle'
        ),
    },

    actions: {
        async attachNative(client) {
            this.nativeNegotiating = true
            const previous = nativeClients.get(this)
            previous?.unsubscribe?.()
            const unsubscribe = client.subscribe((snapshot, refresh) => this.applyNativeSnapshot(snapshot, refresh))
            const entry = { client, unsubscribe }
            nativeClients.set(this, entry)
            const supported = await client.attach()
            if (nativeClients.get(this) !== entry) return null
            this.nativeMode = supported
            if (this.nativeMode) this.applyNativeSnapshot(client.state())
            this.nativeNegotiating = false
            return this.nativeMode
        },
        applyNativeSnapshot(snapshot, refresh = false) {
            if (!snapshot || (this.nativeSnapshot && (snapshot.revision < this.nativeSnapshot.revision
                || snapshot.revision === this.nativeSnapshot.revision && !refresh))) return
            this.nativeSnapshot = refresh ? { ...snapshot } : snapshot
            const playback = snapshot.playback ?? { status: 'idle' }
            const entry = nativeClients.get(this), selection = entry?.selection
            const identity = selection && playback.operationId === selection.operationId ? selection.identity : playback.identity
            if (entry && playback.status === 'idle') entry.selection = null
            this.playback = { text: playback.text ?? null, source: playback.source ?? null,
                configId: playback.configId ?? null, identity: identity ?? null,
                operationId: playback.operationId ?? null,
                readingId: playback.readingId ?? null, phase: playback.phase,
                status: playback.status ?? 'idle', owner: playback.automatic ? snapshot.listening?.activationId : null }
        },
        nativeClient() { return nativeClients.get(this)?.client },
        detachNative() {
            const entry = nativeClients.get(this)
            entry?.unsubscribe?.(); entry?.client.detach()
            nativeClients.delete(this)
            this.nativeNegotiating = false
        },
        async stopAll() {
            if (this.nativeMode || this.nativeNegotiating) return nativeClients.get(this)?.client.stopAll()
            this.stop()
        },
        skip() {
            if (this.nativeMode) return nativeClients.get(this)?.client.skip()
            this.stop()
        },
        /**
         * Apply the state received from the bootstrap or the WebSocket.
         * @param {{ config: { active_id, configs, test_text, adapt }, key_present: boolean, env_path: string }} payload
         */
        applyConfig(payload) {
            const config = payload?.config ?? {}
            // Copies: the mutations below never touch the payload received.
            this.configs = Array.isArray(config.configs) ? config.configs.map(entry => ({ ...entry })) : []
            this.activeId = config.active_id ?? null
            this.testText = typeof config.test_text === 'string' ? config.test_text : ''
            this.adapt = normalizeAdapt(config.adapt)
            this.keyPresent = Boolean(payload?.key_present)
            this.envPath = payload?.env_path ?? ''
            // Another device edited, deactivated or deleted the active config, or the key is gone.
            if ((['message', 'selection', 'summary'].includes(this.playback.source)) && !this.available) this.stop()
        },

        /** The full config the backend stores: every write carries all of it (a missing key resets it). */
        _configPayload() {
            return { active_id: this.activeId, configs: this.configs, test_text: this.testText, adapt: this.adapt }
        },

        /**
         * Send the full config to the backend via WebSocket.
         * Uses lazy import to avoid circular dependency with useWebSocket.
         */
        async _sendConfig() {
            const { sendTtsConfig } = await import('../composables/useWebSocket')
            sendTtsConfig(this._configPayload())
        },

        // ── Config mutations ─────────────────────────────

        /** Add a new config of this kind: 'openrouter' or 'script'. */
        addConfig(kind = 'openrouter') {
            const id = generateUUID()
            this.configs.push(kind === 'script' ? createScriptConfig(id) : createTtsConfig(id))
            this._sendConfig()
            return id
        },

        updateConfig(id, patch) {
            const index = this.configs.findIndex(config => config.id === id)
            if (index === -1) return false
            this.configs[index] = patchTtsConfig(this.configs[index], patch)
            this._sendConfig()
            return true
        },

        duplicateConfig(id) {
            const index = this.configs.findIndex(config => config.id === id)
            if (index === -1) return null
            const copyId = generateUUID()
            this.configs.splice(index + 1, 0, duplicateTtsConfig(this.configs[index], copyId))
            this._sendConfig()
            return copyId
        },

        deleteConfig(id) {
            const index = this.configs.findIndex(config => config.id === id)
            if (index === -1) return false
            this.configs.splice(index, 1)
            if (this.activeId === id) this.activeId = null
            this._sendConfig()
            return true
        },

        /** Make a config the active one. Refuses a config that is not validated. */
        activateConfig(id) {
            const config = this.configs.find(entry => entry.id === id)
            if (!isConfigValidated(config)) return false
            this.activeId = id
            this._sendConfig()
            return true
        },

        deactivateConfig() {
            this.activeId = null
            this._sendConfig()
        },

        /** Keep the settings test sentence ('' brings back the default one). */
        setTestText(text) {
            if (text === this.testText) return
            this.testText = text
            this._sendConfig()
        },

        /**
         * Keep the text adaptation choice: 'haiku', 'luna', 'openrouter', 'off', or null (back to the default).
         * Refuses any other value.
         */
        setAdapt(choice) {
            if (choice !== null && normalizeAdapt(choice) === null) return false
            if (choice === this.adapt) return true
            this.adapt = choice
            this._sendConfig()
            return true
        },

        /** Mark a config validated, only when its speech fields are still the tested ones. */
        markValidated(id, testedFields) {
            const index = this.configs.findIndex(config => config.id === id)
            if (index === -1) return false
            const config = this.configs[index]
            if (!sameSpeechFields(config, testedFields)) return false
            this.configs[index] = { ...config, validated: speechFieldsOf(config) }
            this._sendConfig()
            return true
        },

        // ── Model list ───────────────────────────────────

        /**
         * Fetch the OpenRouter speech models, the only choices of the model and voice pickers.
         * Never rejects. `modelsStatus` ends 'loaded', or 'failed' on any failure or an empty
         * list (the backend answers [] when OpenRouter is unreachable).
         */
        async loadModels() {
            // Only the latest call applies: a slow earlier one must not overwrite a newer result.
            const mine = ++modelsGeneration
            this.modelsStatus = 'loading'
            let models = []
            try {
                const response = await this._requestModels()
                if (response.ok) {
                    const data = await response.json()
                    if (Array.isArray(data?.models)) models = data.models
                }
            } catch {
                models = []
            }
            if (mine !== modelsGeneration) return
            this.models = models
            this.modelsStatus = models.length ? 'loaded' : 'failed'
        },

        // ── Playback ─────────────────────────────────────

        /** Unlock the existing players for listening activation without interrupting a reading. */
        prime() {
            if (this.nativeMode || this.nativeNegotiating || this.status !== 'idle') return
            primePlayers()
        },

        /**
         * Read a message aloud with the active config (the backend may answer from its cache).
         * With an `origin` ('rendered' | 'file'), the text is a selection: the body carries the origin.
         * An owned read starts only when idle. The announcement leaves the original message identity unchanged.
         */
        play(text, { origin = null, owner = null, announcement = '' } = {}) {
            if (owner !== null && this.status !== 'idle') return Promise.resolve({ ...ABORTED, busy: true })
            const config = this.activeConfig
            if (!config) return Promise.resolve({ ok: false, error: i18n.global.t('session.speech.errors.noConfig') })
            const fields = speechFieldsOf(config)
            const scriptId = kindOf(fields) === 'script' ? config.id : null
            return startPlayback(this, text, fields, { useCache: true, source: origin ? 'selection' : 'message', scriptId, origin, owner, announcement })
        },

        /** Reserve the shared player before awaiting an authoritative live or saved summary reference. */
        playSummary(summaryRef, { identity, owner = null, announcement = '', nativeRequest = null } = {}) {
            // A rejected acquisition is still observed when this player cannot accept ownership.
            if (typeof summaryRef !== 'function') Promise.resolve(summaryRef).catch(() => {})
            if (owner !== null && this.status !== 'idle') return Promise.resolve({ ...ABORTED, busy: true })
            const config = this.activeConfig
            if (!config) return Promise.resolve({ ok: false, error: i18n.global.t('session.speech.errors.noConfig') })
            if (!identity || typeof identity !== 'object') return Promise.resolve({ ok: false, error: i18n.global.t('session.speech.errors.identityRequired') })
            const fields = speechFieldsOf(config)
            const scriptId = kindOf(fields) === 'script' ? config.id : null
            return startPlayback(this, null, fields, {
                useCache: true, source: 'summary', scriptId, owner, announcement,
                summaryRef: this.nativeMode ? null : typeof summaryRef === 'function' ? Promise.resolve().then(summaryRef) : summaryRef,
                nativeRequest, identity: { ...identity },
            })
        },

        /**
         * Read a text aloud with explicit speech fields (the settings test: no cache by default).
         * A script config's `configId` also names the script to run: a script config needs its `configId`.
         */
        playWith(text, fields, { useCache = false, source = 'test', configId = null } = {}) {
            const scriptId = kindOf(fields) === 'script' ? configId : null
            return startPlayback(this, text, speechFieldsOf(fields), { useCache, source, configId, scriptId })
        },

        /**
         * Abort the in-flight requests or stop the audio, and release the audio. Safe on a cold app.
         * The pending start or end resolves ABORTED, without an ending request.
         * A non-null owner stops only its own reading; null stops any reading.
         */
        stop({ owner = null } = {}) {
            if (owner !== null && this.playback.owner !== owner) return
            if (this.nativeMode) {
                const readingId = this.playback.readingId
                void nativeClients.get(this)?.client.skip(readingId, this.playback.operationId)
                return
            }
            cancelPlayback(this)
            this.playback = { text: null, status: 'idle', source: null, configId: null, owner: null }
        },

        // ── Network (lazy api.js import: node --test cannot resolve its imports) ──

        /** Open the speech: `{ audio_id, content_type }`, or a JSON error. */
        async _openSpeech(body, signal) {
            const { apiFetch } = await import('../utils/api.js')
            return apiFetch('/api/tts/speech/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal,
            })
        },

        /** The whole audio, for the 'buffered' mode. */
        async _fetchAudio(url, signal) {
            const { apiFetch } = await import('../utils/api.js')
            return apiFetch(url, { signal })
        },

        /** How the audio ended on the backend: `{ state, error }`. `signal` ends a request that takes too long. */
        async _fetchEnding(audioId, signal) {
            const { apiFetch } = await import('../utils/api.js')
            return apiFetch(`${audioUrl(audioId)}ending/`, { signal })
        },

        /** Free the audio on the backend. Never rejects: the backend expires an audio nobody releases. */
        async _releaseAudio(audioId) {
            try {
                const { apiFetch } = await import('../utils/api.js')
                await apiFetch(audioUrl(audioId), { method: 'DELETE' })
            } catch {
                // Ignored.
            }
        },

        async _requestModels() {
            const { apiFetch } = await import('../utils/api.js')
            return apiFetch('/api/tts/models/')
        },
    },
})
