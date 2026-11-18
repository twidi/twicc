/**
 * Dictation for one composer: start/stop, the engine, and the text insertion.
 *
 * The caller hands three accessors over the composer's text:
 * - ``getValue()`` → the current text
 * - ``getSelection()`` → ``{ start, end }`` of the caret/selection
 * - ``setValue(value, cursor)`` → write the text and place the caret
 *
 * Browser engine: partial results are written into the text live, at the
 * caret where dictation started, and replaced as recognition refines them —
 * like the operating system's own dictation. Chrome on Android ends a
 * recognition after a short silence: while the user has not stopped, it is
 * restarted, so a long dictation keeps going.
 *
 * Server engines (OpenRouter, script): the microphone is recorded, then the
 * recording is sent to ``/api/stt/transcribe/`` and the text inserted at the
 * caret. A recording stops by itself after ``MAX_RECORDING_SECONDS``.
 *
 * Design: docs/plans/2026-11-18-speech-to-text-design.md.
 */
import { computed, onScopeDispose, ref, shallowRef } from 'vue'
import {
    MAX_RECORDING_SECONDS,
    canRecordAudio,
    getSpeechRecognition,
    insertDictation,
    pickRecorderMimeType,
    recognitionLang,
    resolveSttEngine,
    sttUnavailableReason,
} from '../utils/speechToText.js'

// Recognition errors that must not end a dictation (Chrome raises no-speech after silence).
const BENIGN_RECOGNITION_ERRORS = new Set(['no-speech', 'aborted'])

export function useSpeechToText({ settings, speech, locale, getValue, getSelection, setValue, onInserted }) {
    // 'idle' | 'starting' | 'listening' (browser) | 'recording' (server) | 'transcribing'
    const status = ref('idle')
    const elapsed = ref(0)
    // A reason code for the UI ('micDenied', 'network', 'failed', …) or a localized server message.
    const error = shallowRef(null)
    const activeEngine = ref(null)

    const caps = computed(() => ({
        browser: Boolean(getSpeechRecognition()),
        recorder: canRecordAudio(),
        keyPresent: Boolean(speech?.keyPresent),
        scriptConfigured: Boolean((settings.sttScriptCommand || '').trim()),
        secureContext: globalThis.isSecureContext !== false,
    }))
    const engine = computed(() => resolveSttEngine(settings.sttEngine, caps.value))
    const unavailableReason = computed(() => engine.value ? null : sttUnavailableReason(settings.sttEngine, caps.value))
    const visible = computed(() => (settings.sttEngine || 'auto') !== 'off')
    const isActive = computed(() => status.value !== 'idle')

    let recognition = null
    let stopRequested = false
    // Live region of the text owned by the current browser dictation.
    let region = null
    let committed = ''
    let wroteLive = false
    let recorder = null
    let mediaStream = null
    let chunks = []
    let timer = null
    let startedAt = 0
    let abortController = null

    function startTimer(limitSeconds) {
        startedAt = Date.now()
        elapsed.value = 0
        timer = setInterval(() => {
            elapsed.value = (Date.now() - startedAt) / 1000
            if (limitSeconds && elapsed.value >= limitSeconds) stop()
        }, 250)
    }

    function stopTimer() {
        if (timer) clearInterval(timer)
        timer = null
    }

    function releaseMicrophone() {
        mediaStream?.getTracks().forEach(track => track.stop())
        mediaStream = null
    }

    // ── browser engine ─────────────────────────────────────────────────────

    function writeLive(text) {
        const value = getValue()
        const { value: next, cursor } = insertDictation(value, region.start, region.end, text)
        region = { start: region.start, end: region.start + (next.length - (value.length - (region.end - region.start))) }
        wroteLive = true
        setValue(next, cursor)
    }

    function startBrowser() {
        const Recognition = getSpeechRecognition()
        recognition = new Recognition()
        recognition.lang = recognitionLang(settings.sttLanguage, locale?.value ?? locale)
        recognition.continuous = true
        recognition.interimResults = true
        recognition.maxAlternatives = 1
        const selection = getSelection()
        region = { start: selection.start, end: selection.end }
        committed = ''
        wroteLive = false

        recognition.onstart = () => {
            if (status.value === 'starting') {
                status.value = 'listening'
                startTimer(0)
            }
        }
        recognition.onresult = (event) => {
            let finalText = ''
            let interim = ''
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const result = event.results[i]
                if (result.isFinal) finalText += result[0].transcript
                else interim += result[0].transcript
            }
            if (finalText) committed = joinWords(committed, finalText)
            writeLive(joinWords(committed, interim))
        }
        recognition.onerror = (event) => {
            if (BENIGN_RECOGNITION_ERRORS.has(event.error)) return
            error.value = event.error === 'not-allowed' || event.error === 'service-not-allowed'
                ? 'micDenied'
                : event.error === 'network' ? 'network' : event.error === 'audio-capture' ? 'noMicrophone' : 'failed'
            stopRequested = true
        }
        recognition.onend = () => {
            if (!stopRequested && (status.value === 'listening' || status.value === 'starting')) {
                // Ended by the engine (silence, Android's short sessions): keep dictating.
                try {
                    recognition.start()
                    return
                } catch {
                    // Fall through: end the dictation.
                }
            }
            finishBrowser()
        }
        recognition.start()
    }

    function finishBrowser() {
        stopTimer()
        if (region && committed) {
            // Drop any interim text that never became final.
            writeLive(committed)
            onInserted?.()
        } else if (region && wroteLive) {
            // Only partial results were written, none confirmed: remove them.
            writeLive('')
        }
        recognition = null
        region = null
        committed = ''
        status.value = 'idle'
        activeEngine.value = null
    }

    // ── server engines ─────────────────────────────────────────────────────

    async function startRecorder(selectedEngine) {
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            })
        } catch (exc) {
            error.value = exc?.name === 'NotAllowedError' || exc?.name === 'SecurityError' ? 'micDenied' : 'noMicrophone'
            status.value = 'idle'
            activeEngine.value = null
            return
        }
        if (status.value !== 'starting') {
            // Cancelled while the permission prompt was open.
            releaseMicrophone()
            return
        }
        const mimeType = pickRecorderMimeType(globalThis.MediaRecorder)
        recorder = new MediaRecorder(mediaStream, mimeType ? { mimeType } : undefined)
        chunks = []
        recorder.ondataavailable = (event) => { if (event.data?.size) chunks.push(event.data) }
        recorder.onstop = () => {
            stopTimer()
            releaseMicrophone()
            const type = recorder?.mimeType || mimeType || 'audio/webm'
            const blob = new Blob(chunks, { type })
            recorder = null
            chunks = []
            if (stopRequested === 'cancel' || !blob.size) {
                status.value = 'idle'
                activeEngine.value = null
                return
            }
            transcribe(blob, type, selectedEngine)
        }
        recorder.start(1000)
        status.value = 'recording'
        startTimer(MAX_RECORDING_SECONDS)
    }

    async function transcribe(blob, type, selectedEngine) {
        status.value = 'transcribing'
        abortController = new AbortController()
        const form = new FormData()
        const extension = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm'
        form.append('audio', blob, `dictation.${extension}`)
        form.append('engine', selectedEngine)
        form.append('format', type)
        try {
            const { apiFetch } = await import('../utils/api.js')
            const response = await apiFetch('/api/stt/transcribe/', {
                method: 'POST', body: form, signal: abortController.signal,
            })
            const payload = await response.json().catch(() => ({}))
            if (!response.ok) {
                const { remoteUiMessage } = await import('../i18n/remoteMessages.js')
                error.value = remoteUiMessage(payload, payload?.error || 'failed')
                return
            }
            const text = (payload.text || '').trim()
            if (!text) {
                error.value = 'nothingHeard'
                return
            }
            const selection = getSelection()
            const { value, cursor } = insertDictation(getValue(), selection.start, selection.end, text)
            setValue(value, cursor)
            onInserted?.()
        } catch (exc) {
            if (exc?.name !== 'AbortError') error.value = 'network'
        } finally {
            abortController = null
            status.value = 'idle'
            activeEngine.value = null
        }
    }

    // ── public API ─────────────────────────────────────────────────────────

    function start() {
        if (isActive.value) return
        error.value = null
        const selected = engine.value
        if (!selected) {
            error.value = unavailableReason.value
            return
        }
        stopRequested = false
        activeEngine.value = selected
        status.value = 'starting'
        if (selected === 'browser') {
            try {
                startBrowser()
            } catch {
                error.value = 'failed'
                status.value = 'idle'
                activeEngine.value = null
            }
        } else {
            startRecorder(selected)
        }
    }

    /** Stop listening and keep (or transcribe) what was said. */
    function stop() {
        if (status.value === 'listening' || (status.value === 'starting' && recognition)) {
            stopRequested = true
            recognition?.stop()
        } else if (status.value === 'recording') {
            stopRequested = true
            recorder?.state !== 'inactive' && recorder?.stop()
        } else if (status.value === 'starting') {
            status.value = 'idle'
            activeEngine.value = null
        }
    }

    /** Stop and discard: no text is inserted (Escape). */
    function cancel() {
        if (status.value === 'listening' || (status.value === 'starting' && recognition)) {
            stopRequested = true
            committed = ''
            recognition?.abort()
        } else if (status.value === 'recording') {
            stopRequested = 'cancel'
            recorder?.state !== 'inactive' && recorder?.stop()
        } else if (status.value === 'transcribing') {
            abortController?.abort()
        } else if (status.value === 'starting') {
            status.value = 'idle'
            activeEngine.value = null
        }
    }

    /**
     * The composer text was taken (sent/cleared): a live browser dictation
     * stops without touching the text any more. A server recording goes on —
     * its text lands in the now-empty composer.
     */
    function detach() {
        if (recognition && !stopRequested) {
            stopRequested = true
            region = null
            committed = ''
            recognition.abort()
        }
    }

    function toggle() {
        if (status.value === 'transcribing') return
        if (isActive.value) stop()
        else start()
    }

    function clearError() {
        error.value = null
    }

    onScopeDispose(() => {
        cancel()
        stopTimer()
        releaseMicrophone()
    })

    return {
        status, elapsed, error, engine, activeEngine, unavailableReason, visible, isActive,
        start, stop, cancel, detach, toggle, clearError,
    }
}

function joinWords(left, right) {
    const a = (left || '').trim()
    const b = (right || '').trim()
    if (!a) return b
    if (!b) return a
    return `${a} ${b}`
}
