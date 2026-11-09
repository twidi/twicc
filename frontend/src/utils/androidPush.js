import { watch } from 'vue'
import { androidPushState } from './androidEnvironment.js'

/** One serialized registration flow. Native Android owns subscriptions and subsequent preference uploads. */
export function createAndroidPush(client, { fetchImpl = fetch, onState = () => {} } = {}) {
    let pending = Promise.resolve(), stopped = false, configured = false, asked = false, compatibility = null
    function publish(native) {
        const status = compatibility && !['login', 'error', 'server', 'unavailable', 'retrying'].includes(native.status)
            ? compatibility : native.status
        onState({ ...native, status, compatibility })
    }
    async function synchronizeNow(preferences) {
        await client.request('push.preferences', { preferences })
        let native = await client.request('push.state')
        // A later native renewal can confirm an upgraded server without this page.
        if (configured && compatibility === 'update_server' && native.status === 'ready') compatibility = null
        publish(native)
        if (!Object.values(preferences).some(Boolean) && native.status === 'idle') return
        if (native.permission === 'default' && !asked && Object.values(preferences).some(Boolean)) {
            asked = true
            native = await client.request('push.permission')
            publish(native)
        }
        if (configured && !['login', 'error'].includes(native.status)) return
        const response = await fetchImpl('/api/android-push/', {
            method: 'POST', cache: 'no-store', signal: AbortSignal.timeout(15000),
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: native.device_id, name: native.name,
                enabled: native.permission === 'granted', preferences,
                ...(native.payloadVersion === 2 ? { notification_payload_version: 2 } : {}) }),
        })
        if (!response.ok) throw new Error(response.status === 401 ? 'login' : 'server')
        const config = await response.json()
        if (config.protocol !== 1 || typeof config.public_key !== 'string' || typeof config.token !== 'string') throw new Error('server')
        compatibility = native.payloadVersion === 2
            ? (config.notification_payload_version === 2 ? null : 'update_server')
            : (config.notification_payload_version === 2 ? 'update_app' : null)
        // Even during logout, retain the returned grant long enough for native removal to revoke it.
        native = await client.request('push.configure', { vapid: config.public_key, token: config.token, preferences,
            ...(native.payloadVersion === 2 && config.notification_payload_version === 2 ? { payloadVersion: 2 } : {}) })
        configured = true
        publish(native)
    }
    return {
        synchronize(preferences) {
            if (stopped) return pending
            const snapshot = { ...preferences }
            pending = pending.then(() => stopped ? undefined : synchronizeNow(snapshot)).catch(error => {
                onState({ status: ['login', 'server'].includes(error.message) ? error.message : 'error' })
            })
            return pending
        },
        async stop(remove = false) {
            stopped = true
            await pending
            if (remove) await client.request('push.remove')
        },
    }
}

let active = null, currentStore = null, stopWatching = null, stopping = false, retryAfter = 0, needsRetry = false
function preferences(store) {
    return { user_turn: store.isNotifUserTurnBrowser, pending_request: store.isNotifPendingRequestBrowser,
        extra_usage: store.isNotifExtraUsageStartBrowser, peer: store.isNotifPeerBrowser }
}
function displayState(value) {
    needsRetry = ['login', 'server', 'error'].includes(value?.status)
    androidPushState.value = { ...androidPushState.value, ...value }
}
export function startAndroidPush(client, store, auth) {
    if (stopWatching) return
    currentStore = store
    stopWatching = watch(() => [auth.authenticated, ...Object.values(preferences(store))], () => {
        if (auth.authenticated !== true || stopping) return
        active ??= createAndroidPush(client, { onState: displayState })
        retryAfter = Date.now() + 30000
        active.synchronize(preferences(store))
    }, { immediate: true })
}
/** Reuse the menu heartbeat for status and permission changes; no new polling loop. */
export function observeAndroidPush(value) {
    if (!value) return
    const compatibility = androidPushState.value?.compatibility
    // Old APK heartbeats cannot report their incompatibility with the current server.
    const observed = compatibility === 'update_app' && value.payloadVersion !== 2 &&
        !['error', 'login', 'unavailable', 'retrying'].includes(value.status) ? 'update_app' : value.status
    const status = needsRetry && !['error', 'login', 'unavailable'].includes(value.status) ? androidPushState.value?.status : observed
    androidPushState.value = { ...androidPushState.value, ...value, status }
    if (['login', 'error'].includes(value.status)) needsRetry = true
    if (active && Date.now() >= retryAfter && needsRetry) {
        retryAfter = Date.now() + 30000
        active.synchronize(preferences(currentStore))
    }
}
export async function requestAndroidPushPermission() {
    const { getAndroidAppClient } = await import('./androidAppClient.js')
    displayState(await getAndroidAppClient().request('push.permission'))
}
export async function openAndroidNotificationSoundSettings(kind) {
    const { getAndroidAppClient } = await import('./androidAppClient.js')
    await getAndroidAppClient().request('push.soundSettings', { kind })
}
export async function prepareAndroidNotificationSounds() {
    const { getAndroidAppClient } = await import('./androidAppClient.js')
    observeAndroidPush(await getAndroidAppClient().request('push.prepareSounds'))
}
export async function retryAndroidPush() {
    if (active) {
        await active.stop()
        const { getAndroidAppClient } = await import('./androidAppClient.js')
        active = createAndroidPush(getAndroidAppClient(), { onState: displayState })
        await active.synchronize(preferences(currentStore))
    }
}
export async function stopAndroidPush() {
    stopping = true
    try { await active?.stop(true) }
    finally { active = null; stopping = false }
}
export function disposeAndroidPush() {
    stopWatching?.()
    stopWatching = null
    active?.stop()
    active = null
    currentStore = null
}
