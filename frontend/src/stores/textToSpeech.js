import { defineStore } from 'pinia'
import { generateUUID } from '../utils/crypto.js'
import { SpeechCache } from '../utils/speechCache.js'
import { createSpeechPlayer } from '../utils/speechPlayer.js'
import {
    createTtsConfig,
    duplicateTtsConfig,
    isConfigValidated,
    isTtsAvailable,
    patchTtsConfig,
    sameSpeechFields,
    speechCacheKey,
    speechFieldsOf,
} from '../utils/ttsConfig.js'

/**
 * App-wide text-to-speech service: the configs (persisted in <data_dir>/tts.json via
 * WebSocket sync) and the single playback of the app.
 *
 * One playback at a time. It is owned by no view: it survives every in-app navigation and
 * stops only on a page reload or when the app goes to the login page (see router.js).
 */

// Playback internals, kept out of the reactive state.
let controller = null
let player = null
let generation = 0
const cache = new SpeechCache()

const ABORTED = Object.freeze({ ok: false, aborted: true })

/** Tests only: forget the in-flight request, the player and the cache. */
export function _resetSpeechForTests() {
    controller?.abort()
    controller = null
    player = null
    cache.clear()
    // Never back to 0: a continuation left by a previous test must not match a new playback.
    generation += 1
}

function isAbortError(error) {
    return error?.name === 'AbortError'
}

async function errorMessageOf(response) {
    try {
        const data = await response.json()
        if (typeof data?.error === 'string' && data.error) return data.error
    } catch {
        // Not a JSON body: fall back to the status.
    }
    return `Request failed (${response.status})`
}

/**
 * Stop the current playback, then read `text` aloud with `fields`. Never rejects.
 * Every `await` is followed by a generation check: a newer play or a stop owns the state.
 */
async function startPlayback(store, text, fields, { useCache, source }) {
    store.stop()
    const mine = ++generation
    const backToIdle = () => {
        if (generation === mine) store.playback = { text: null, status: 'idle', source: null }
    }

    player ??= createSpeechPlayer()
    // Synchronous, inside the click: later playback on the element is user-activated.
    player.prime()
    store.playback = { text, status: 'loading', source }

    try {
        const key = speechCacheKey(text, fields)
        let blob = useCache ? cache.get(key) : null
        if (!blob) {
            controller = new AbortController()
            const response = await store._requestSpeech({ text, ...fields }, controller.signal)
            if (generation !== mine) return ABORTED
            if (!response.ok) {
                const error = await errorMessageOf(response)
                if (generation !== mine) return ABORTED
                backToIdle()
                return { ok: false, error }
            }
            blob = await response.blob()
            if (generation !== mine) return ABORTED
            // An audio larger than the cache cap is not stored, but still plays.
            if (useCache) cache.set(key, blob)
        }

        store.playback = { text, status: 'playing', source }
        try {
            await player.play(blob, backToIdle, backToIdle)
        } catch (error) {
            if (isAbortError(error) || generation !== mine) return ABORTED
            backToIdle()
            return { ok: false, error: 'The browser refused to play the audio' }
        }
        if (generation !== mine) return ABORTED
        return { ok: true }
    } catch (error) {
        if (isAbortError(error) || generation !== mine) return ABORTED
        backToIdle()
        return { ok: false, error: 'Could not reach TwiCC' }
    }
}

export const useTextToSpeechStore = defineStore('textToSpeech', {
    state: () => ({
        configs: [],
        activeId: null,
        keyPresent: false,
        envPath: '',
        models: [], // [{ id, name, voices: [string] }] suggestions from OpenRouter
        playback: { text: null, status: 'idle', source: null }, // source: 'message' | 'test' | null
    }),

    getters: {
        activeConfig: (state) => state.configs.find(config => config.id === state.activeId) ?? null,

        /** True when the key is present and the active config is validated. */
        available: (state) => isTtsAvailable({ keyPresent: state.keyPresent, configs: state.configs, activeId: state.activeId }),

        /** The global playback state: 'idle' | 'loading' | 'playing'. */
        status: (state) => state.playback.status,

        /** The state of a message playback for this text: 'idle' | 'loading' | 'playing'. */
        statusForText: (state) => (text) => (
            state.playback.source === 'message' && state.playback.text === text ? state.playback.status : 'idle'
        ),
    },

    actions: {
        /**
         * Apply the state received from the bootstrap or the WebSocket.
         * @param {{ config: { active_id, configs }, key_present: boolean, env_path: string }} payload
         */
        applyConfig(payload) {
            const config = payload?.config ?? {}
            // Copies: the mutations below never touch the payload received.
            this.configs = Array.isArray(config.configs) ? config.configs.map(entry => ({ ...entry })) : []
            this.activeId = config.active_id ?? null
            this.keyPresent = Boolean(payload?.key_present)
            this.envPath = payload?.env_path ?? ''
            // Another device edited, deactivated or deleted the active config, or the key is gone.
            if (this.playback.source === 'message' && !this.available) this.stop()
        },

        /**
         * Send the full config to the backend via WebSocket.
         * Uses lazy import to avoid circular dependency with useWebSocket.
         */
        async _sendConfig() {
            const { sendTtsConfig } = await import('../composables/useWebSocket')
            sendTtsConfig({ active_id: this.activeId, configs: this.configs })
        },

        // ── Config mutations ─────────────────────────────

        addConfig() {
            const id = generateUUID()
            this.configs.push(createTtsConfig(id))
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

        // ── Model suggestions ────────────────────────────

        /** Fetch the model suggestions. Never rejects: any failure leaves `models` empty. */
        async loadModels() {
            try {
                const response = await this._requestModels()
                if (!response.ok) {
                    this.models = []
                    return
                }
                const data = await response.json()
                this.models = Array.isArray(data?.models) ? data.models : []
            } catch {
                this.models = []
            }
        },

        // ── Playback ─────────────────────────────────────

        /** Read a message aloud with the active config (cached). */
        play(text) {
            const config = this.activeConfig
            if (!config) return Promise.resolve({ ok: false, error: 'No active text-to-speech config' })
            return startPlayback(this, text, speechFieldsOf(config), { useCache: true, source: 'message' })
        },

        /** Read a text aloud with explicit speech fields (the settings test: no cache by default). */
        playWith(text, fields, { useCache = false, source = 'test' } = {}) {
            return startPlayback(this, text, speechFieldsOf(fields), { useCache, source })
        },

        /** Abort the in-flight request or stop the audio. Safe on a cold app. */
        stop() {
            generation += 1
            controller?.abort()
            controller = null
            player?.stop()
            this.playback = { text: null, status: 'idle', source: null }
        },

        // ── Network (lazy api.js import: node --test cannot resolve its imports) ──

        async _requestSpeech(body, signal) {
            const { apiFetch } = await import('../utils/api.js')
            return apiFetch('/api/tts/speech/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal,
            })
        },

        async _requestModels() {
            const { apiFetch } = await import('../utils/api.js')
            return apiFetch('/api/tts/models/')
        },
    },
})
