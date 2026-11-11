import { generateUUID } from './crypto.js'

// No stores/router imports: playback stores and the WebSocket both use this module.
let send = null, enabled = () => false, epoch = null, android = null, memoryId = null
let metadataChanged = () => {}
const PROFILE_KEY = 'twicc-telemetry-profile'
const WEB_METRICS = new Set(['manual_tts_messages', 'timeline_tts_messages', 'timeline_tts_activations'])

export function configureTelemetry(options) {
    send = options.send ?? null
    enabled = options.enabled ?? (() => false)
    metadataChanged = options.metadataChanged ?? (() => {})
}
export function setTelemetryEpoch(value) { epoch = typeof value === 'string' ? value : null }
export function captureTelemetry() { return send && enabled() && epoch ? epoch : null }
export function reportActivity(metric, token = captureTelemetry()) {
    if (!WEB_METRICS.has(metric) || !token || captureTelemetry() !== token) return
    send({ type: 'telemetry_activity', epoch: token, metric })
}

export function profileId(storage) {
    try {
        if (storage === undefined) storage = globalThis.localStorage
        const saved = storage?.getItem(PROFILE_KEY)
        if (typeof saved === 'string' && /^[0-9a-f-]{36}$/.test(saved)) return saved
        const id = memoryId ??= generateUUID()
        storage?.setItem(PROFILE_KEY, id)
        return id
    } catch { return memoryId ??= generateUUID() }
}

export function browserDescription(nav = globalThis.navigator) {
    const ua = nav?.userAgent ?? ''
    // Order matters: Chromium derivatives and iOS variants also advertise Safari/Chrome.
    let browser = 'other'
    if (nav?.brave) browser = 'brave'
    else if (/Edg(?:e|A|iOS)?\//i.test(ua)) browser = 'edge'
    else if (/(?:OPR|Opera|OPiOS)\//i.test(ua)) browser = 'opera'
    else if (/SamsungBrowser\//i.test(ua)) browser = 'samsung_internet'
    else if (/Vivaldi\//i.test(ua)) browser = 'vivaldi'
    else if (/(?:Firefox|FxiOS)\//i.test(ua)) browser = 'firefox'
    else if (/(?:Chrome|Chromium|CriOS)\//i.test(ua)) browser = 'chrome'
    else if (/Safari\//i.test(ua)) browser = 'safari'
    const mobile = nav?.userAgentData?.mobile === true || /Android|iPhone|iPad|iPod|Mobile/i.test(ua)
        || /Macintosh/i.test(ua) && nav?.maxTouchPoints > 1
    return { browser, device_type: mobile ? 'mobile' : 'desktop' }
}

export function setAndroidTelemetry(state) {
    const engine = typeof state?.engine === 'string' && (/^Gecko/i.test(state.engine) ? 'gecko'
        : /WebView/i.test(state.engine) ? 'webview' : null)
    android = engine && typeof state.version === 'string'
        ? { browser: 'android_app', device_type: 'mobile', app_version: state.version, engine } : null
    metadataChanged()
}

export async function reportClient({ locale, navigator = globalThis.navigator, storage } = {}) {
    const token = captureTelemetry()
    if (!token || !['en', 'de', 'es', 'fr', 'pt-BR'].includes(locale)) return
    try {
        // Serialize first creation across tabs. Re-read storage while holding
        // the origin-wide lock; each tab must send the same winning key.
        const clientId = navigator?.locks?.request
            ? await navigator.locks.request(PROFILE_KEY, () => profileId(storage))
            : profileId(storage)
        if (captureTelemetry() !== token) return
        send({ type: 'telemetry_activity', epoch: token, client_id: clientId,
            client: { ...(android ?? browserDescription(navigator)), locale } })
    } catch { /* Telemetry must never affect presence or the Android bridge. */ }
}
