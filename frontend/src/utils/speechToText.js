/**
 * Speech to text (dictation in the composer): pure helpers.
 *
 * Engines (synced setting ``sttEngine``):
 * - ``browser``    — the Web Speech API: free, live partial results, runs in the
 *                    browser (Chrome/Edge send the audio to their vendor; Safari
 *                    may transcribe on device). Absent from Firefox.
 * - ``openrouter`` — the recording is sent to TwiCC, which transcribes it with
 *                    OpenRouter (Whisper by default). Needs the OpenRouter key.
 * - ``script``     — the recording is sent to TwiCC, which runs the user's
 *                    command (e.g. a local whisper.cpp / faster-whisper).
 * - ``auto``       — script when configured, else OpenRouter when the key is
 *                    set, else the browser engine.
 * - ``off``        — no dictation button.
 *
 * Design: docs/plans/2026-11-18-speech-to-text-design.md.
 */

export const STT_ENGINES = ['auto', 'browser', 'openrouter', 'script', 'off']
export const DEFAULT_STT_MODEL = 'openai/whisper-large-v3'
// A recording is stopped automatically after this long (OpenRouter's upstream
// providers stop processing at 60 s; Whisper handles minutes of audio within it).
export const MAX_RECORDING_SECONDS = 5 * 60

// Recording formats in preference order; Safari (macOS/iOS) only does MP4/AAC.
const RECORDER_MIME_TYPES = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4;codecs=mp4a.40.2',
    'audio/mp4',
    'audio/aac',
]

/** The SpeechRecognition constructor of this browser, or null. */
export function getSpeechRecognition(win = globalThis) {
    return win?.SpeechRecognition || win?.webkitSpeechRecognition || null
}

/** Whether this browser can record audio for the server engines. */
export function canRecordAudio(win = globalThis) {
    return Boolean(win?.MediaRecorder && win?.navigator?.mediaDevices?.getUserMedia)
}

/** The first recording MIME type this browser supports ('' lets the browser choose). */
export function pickRecorderMimeType(MediaRecorderCtor) {
    if (!MediaRecorderCtor?.isTypeSupported) return ''
    return RECORDER_MIME_TYPES.find(type => MediaRecorderCtor.isTypeSupported(type)) || ''
}

/**
 * The engine a dictation will really use, or null when none can run here.
 *
 * ``setting`` is the stored choice; ``caps`` says what this browser and
 * instance offer: ``{ browser, recorder, keyPresent, scriptConfigured }``.
 * An explicit choice that cannot run falls back to null (the button explains
 * why) rather than silently switching engine — except ``auto``.
 */
export function resolveSttEngine(setting, caps) {
    const choice = STT_ENGINES.includes(setting) ? setting : 'auto'
    if (choice === 'off') return null
    if (choice === 'browser') return caps.browser ? 'browser' : null
    if (choice === 'openrouter') return caps.recorder && caps.keyPresent ? 'openrouter' : null
    if (choice === 'script') return caps.recorder && caps.scriptConfigured ? 'script' : null
    if (caps.recorder && caps.scriptConfigured) return 'script'
    if (caps.recorder && caps.keyPresent) return 'openrouter'
    if (caps.browser) return 'browser'
    return null
}

/**
 * Why no engine can run, as a stable reason code for the UI, or null.
 * Only meaningful when :func:`resolveSttEngine` answered null.
 */
export function sttUnavailableReason(setting, caps) {
    if (setting === 'off') return 'off'
    if (!caps.secureContext) return 'insecure'
    if (setting === 'browser') return 'noBrowserEngine'
    if (setting === 'openrouter') return caps.recorder ? 'noKey' : 'noRecorder'
    if (setting === 'script') return caps.recorder ? 'noScript' : 'noRecorder'
    return 'noEngine'
}

/** The BCP-47 tag the browser engine expects ('' = the browser's language). */
export function recognitionLang(sttLanguage, uiLocale) {
    const code = (sttLanguage || '').trim()
    if (code) return code
    return uiLocale || ''
}

/**
 * Insert dictated ``text`` into ``value`` replacing ``[start, end)``, with a
 * space before it when it would otherwise glue onto a word, and none doubled.
 * Returns ``{ value, cursor }`` (cursor right after the inserted text).
 */
export function insertDictation(value, start, end, text) {
    const before = value.slice(0, start)
    const after = value.slice(end)
    let insertion = (text || '').trim()
    // Nothing to insert: the range is removed (a live partial result that came to nothing).
    if (!insertion) return { value: before + after, cursor: start }
    if (before && !/\s$/.test(before)) insertion = ' ' + insertion
    if (after && !/^[\s.,;:!?)]/.test(after)) insertion = insertion + ' '
    return { value: before + insertion + after, cursor: before.length + insertion.trimEnd().length }
}

/** ``m:ss`` for a recording timer. */
export function formatElapsed(seconds) {
    const total = Math.max(0, Math.floor(seconds))
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}
