<script setup>
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

// NotificationSettings.vue - Notification settings section for the settings popover
// Four notification types: User Turn, Pending Request, Extra Usage and Peer.
// Each has a sound selector (with test button) and a browser notification toggle;
// User Turn also has its own in-app toast toggle.
// Plus the external notification targets (Apprise URLs, pushed by the backend).

import { computed, ref, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { generateUUID } from '../../utils/crypto'
import { isAndroidApp, androidPushState } from '../../utils/androidEnvironment.js'
import { requestAndroidPushPermission, retryAndroidPush } from '../../utils/androidPush.js'
import { playNotificationSound, getAvailableSoundOptions, NOTIFICATION_SOUNDS } from '../../utils/notificationSounds'

const store = useSettingsStore()

const emit = defineEmits(['go-to-public-base-url'])

// The External address setting (stored as `publicBaseUrl`) lives in the Global
// section; here we only surface a callout inviting the user to set it when it's
// missing (so notification links aren't silently omitted).
const hasPublicBaseUrl = computed(() => !!store.getUsablePublicBaseUrl)

// Sound options for selects
const soundOptions = computed(() => getAvailableSoundOptions())

// Computed from store
const userTurnSound = computed(() => store.getNotifUserTurnSound)
const userTurnToast = computed(() => store.isNotifUserTurnToast)
const userTurnBrowser = computed(() => store.isNotifUserTurnBrowser)
const pendingRequestSound = computed(() => store.getNotifPendingRequestSound)
const pendingRequestBrowser = computed(() => store.isNotifPendingRequestBrowser)
// Extra-usage alert: a master switch (synced) plus per-device sound/browser.
const notifyOnExtraUsageStart = computed(() => store.shouldNotifyOnExtraUsageStart)
const extraUsageStartSound = computed(() => store.getNotifExtraUsageStartSound)
const extraUsageStartBrowser = computed(() => store.isNotifExtraUsageStartBrowser)
// One event for the whole peer system: an incoming message and an incoming
// pairing request both mean someone else waits on you.
const peerSound = computed(() => store.getNotifPeerSound)
const peerBrowser = computed(() => store.isNotifPeerBrowser)

// External notification targets (synced settings, consumed by the backend).
// Each target carries a required ``id`` (a stable handle for a future
// per-project scoping; generated at row creation) and an optional human
// ``name``. The UI edits a local working list (``externalRows``) so that rows
// with an empty URL (just added, or cleared) exist on screen without ever being
// persisted: ``persistExternalRows()`` writes only the non-empty ones to the
// store. Remote updates (another device) reset the working list.
const externalRows = ref((store.getExternalNotificationTargets || []).map(t => ({ ...t })))

// Transient per-row state (keyed by row index; cleared on list mutations):
// in-flight tests, last test error, and the URL as currently typed.
// ``liveUrls`` exists because rows only update on the input's change event
// (blur/Enter) — the Test button must react to the value being typed or
// pasted before it is committed.
const testingTargets = ref({})
const targetTestErrors = ref({})
const liveUrls = ref({})

function persistExternalRows() {
    store.setExternalNotificationTargets(externalRows.value.filter(r => r.url).map(r => ({ ...r })))
}

watch(() => store.getExternalNotificationTargets, (remote) => {
    // Ignore the echo of our own persist; reset the working list (and the
    // index-keyed transient maps) on genuine remote changes.
    const persisted = externalRows.value.filter(r => r.url)
    if (JSON.stringify(remote || []) !== JSON.stringify(persisted)) {
        externalRows.value = (remote || []).map(t => ({ ...t }))
        testingTargets.value = {}
        targetTestErrors.value = {}
        liveUrls.value = {}
    }
})

function effectiveTargetUrl(index) {
    return liveUrls.value[index] ?? externalRows.value[index]?.url ?? ''
}

function addExternalTarget() {
    // Local draft row only — persisted once it gets a non-empty URL.
    externalRows.value.push({ id: generateUUID(), name: '', url: '', enabled: true, tested: null, notifyUserTurn: true, notifyPendingRequest: true, notifyExtraUsageStart: true, notifyPeer: true, awayOnly: true })
}

function removeExternalTarget(index) {
    externalRows.value.splice(index, 1)
    persistExternalRows()
    // The transient maps are keyed by row index, which just shifted: drop
    // them wholesale rather than showing state on the wrong rows. An
    // in-flight test still lands correctly (it re-checks the URL on return).
    targetTestErrors.value = {}
    testingTargets.value = {}
    liveUrls.value = {}
}

function onExternalTargetUrlInput(index, event) {
    liveUrls.value[index] = event.target.value.trim()
}

function onExternalTargetUrlChange(index, event) {
    // Editing the URL invalidates the last test result: a stale checkmark
    // must never vouch for a URL it didn't test.
    externalRows.value[index] = { ...externalRows.value[index], url: event.target.value.trim(), tested: null }
    persistExternalRows()
    delete liveUrls.value[index]
    delete targetTestErrors.value[index]
}

function onExternalTargetNameChange(index, event) {
    externalRows.value[index] = { ...externalRows.value[index], name: event.target.value.trim() }
    persistExternalRows()
}

function onExternalTargetEnabledChange(index, event) {
    externalRows.value[index] = { ...externalRows.value[index], enabled: event.target.checked }
    persistExternalRows()
}

function onExternalTargetEventChange(index, key, event) {
    externalRows.value[index] = { ...externalRows.value[index], [key]: event.target.checked }
    persistExternalRows()
    // Opting a target into the extra-usage push implies wanting the alert, so
    // turn the master switch on (the single kill switch governs every channel).
    if (key === 'notifyExtraUsageStart' && event.target.checked) {
        store.setNotifyOnExtraUsageStart(true)
    }
}

async function testExternalTarget(index) {
    const url = effectiveTargetUrl(index)
    if (!url || testingTargets.value[index]) return
    // Commit the typed-but-not-blurred URL first, so the tested outcome can
    // be persisted on the row it actually vouches for.
    if (url !== externalRows.value[index]?.url) {
        externalRows.value[index] = { ...externalRows.value[index], url, tested: null }
        persistExternalRows()
    }
    delete liveUrls.value[index]
    testingTargets.value[index] = true
    delete targetTestErrors.value[index]
    let ok = false
    let error = null
    try {
        const response = await fetch('/api/external-notifications/test/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls: [url] }),
        })
        const data = await response.json()
        if (!response.ok) {
            error = data?.error || localUiMessage('settings.notifications.status.requestFailed', { status: response.status })
        } else {
            const result = data.results?.[0]
            ok = !!result?.ok
            error = result?.error || null
        }
    } catch (e) {
        error = e?.uiMessage || e?.message || localUiMessage('settings.notifications.status.networkError')
    } finally {
        delete testingTargets.value[index]
    }
    // Persist the outcome on the target (synced across devices) — unless the
    // URL changed while the test was in flight.
    if (externalRows.value[index]?.url === url) {
        externalRows.value[index] = { ...externalRows.value[index], tested: ok }
        persistExternalRows()
    }
    if (error) targetTestErrors.value[index] = error
}

function externalTestIcon(target) {
    if (target.tested === true) return 'check'
    if (target.tested === false) return 'xmark'
    return 'paper-plane'
}

// Browser notification permission state
const browserNotifPermission = ref(getBrowserNotifPermission())
const notificationLabel = computed(() => isAndroidApp.value ? t('settings.notifications.status.androidLabel') : t('settings.notifications.status.browserLabel'))
const canChooseNotification = computed(() => isAndroidApp.value || browserNotifPermission.value === 'granted')
const androidStatus = computed(() => {
    const state = androidPushState.value
    if (state?.permission === 'denied') return t('settings.notifications.status.blocked')
    if (state?.permission === 'default') return t('settings.notifications.status.permission')
    if (state?.status === 'unavailable') return t('settings.notifications.status.unavailable')
    if (state?.status === 'login') return t('settings.notifications.status.login')
    if (state?.status === 'server') return t('settings.notifications.status.server')
    if (state?.status === 'error') return t('settings.notifications.status.error')
    if (['registering', 'retrying'].includes(state?.status)) return t('settings.notifications.status.registering')
    return t('settings.notifications.status.ready')
})

function getBrowserNotifPermission() {
    if (isAndroidApp.value || !('Notification' in window)) return 'unsupported'
    return Notification.permission
}

// Event handlers
function onUserTurnSoundChange(event) {
    store.setNotifUserTurnSound(event.target.value)
}

function onPendingRequestSoundChange(event) {
    store.setNotifPendingRequestSound(event.target.value)
}

function onUserTurnToastChange(event) {
    store.setNotifUserTurnToast(event.target.checked)
}

function onUserTurnBrowserChange(event) {
    store.setNotifUserTurnBrowser(event.target.checked)
}

function onPendingRequestBrowserChange(event) {
    store.setNotifPendingRequestBrowser(event.target.checked)
}

function onNotifyOnExtraUsageStartChange(event) {
    store.setNotifyOnExtraUsageStart(event.target.checked)
}

function onExtraUsageStartSoundChange(event) {
    // Picking a sound forces the master switch on (handled in the store action).
    store.setNotifExtraUsageStartSound(event.target.value)
}

function onExtraUsageStartBrowserChange(event) {
    store.setNotifExtraUsageStartBrowser(event.target.checked)
}

function onPeerSoundChange(event) {
    store.setNotifPeerSound(event.target.value)
}

function onPeerBrowserChange(event) {
    store.setNotifPeerBrowser(event.target.checked)
}

function testUserTurnSound() {
    playNotificationSound(userTurnSound.value)
}

function testPendingRequestSound() {
    playNotificationSound(pendingRequestSound.value)
}

function testExtraUsageStartSound() {
    playNotificationSound(extraUsageStartSound.value)
}

function testPeerSound() {
    playNotificationSound(peerSound.value)
}

/**
 * Request browser notification permission.
 * Once granted, the switches become usable.
 */
async function requestPermission() {
    if (isAndroidApp.value || !('Notification' in window)) return
    try {
        const permission = await Notification.requestPermission()
        browserNotifPermission.value = permission
    } catch (e) {
        console.warn('Failed to request notification permission:', e)
    }
}

/**
 * Called when the parent popover opens — refresh browser permission state.
 */
function sync() {
    browserNotifPermission.value = getBrowserNotifPermission()
}

defineExpose({ sync })
</script>

<template>
    <section class="settings-section">
        <h3 class="settings-section-title">{{ $t('settings.sections.notifications') }}</h3>

        <div v-if="isAndroidApp" class="notif-permission-row">
            <p class="setting-group-hint">{{ androidStatus }}</p>
            <wa-button v-if="androidPushState?.permission === 'denied'" size="small" appearance="outlined"
                @click="requestAndroidPushPermission">{{ $t('settings.notifications.android.openSettings') }}</wa-button>
            <wa-button v-else-if="['error', 'server'].includes(androidPushState?.status)" size="small" appearance="outlined"
                @click="retryAndroidPush">{{ $t('common.actions.retry') }}</wa-button>
        </div>
        <!-- Browser notification permission (shown once for both) -->
        <div v-else-if="browserNotifPermission === 'unsupported'" class="notif-permission-row">
            <span class="setting-group-hint">{{ $t('settings.notifications.browser.unsupported') }}</span>
        </div>
        <div v-else-if="browserNotifPermission === 'denied'" class="notif-permission-row">
            <span class="setting-group-hint notif-denied">{{ $t('settings.notifications.browser.blocked') }}</span>
        </div>
        <div v-else-if="browserNotifPermission === 'default'" class="notif-permission-row">
            <wa-button
                variant="neutral"
                appearance="outlined"
                size="small"
                @click="requestPermission"
            >{{ $t('settings.notifications.browser.enable') }}</wa-button>
        </div>

        <!-- Agent finished working -->
        <div class="setting-group">
            <label class="setting-group-label">{{ $t('settings.notifications.finished.label') }}</label>
            <wa-select
                :value.prop="userTurnSound"
                @change="onUserTurnSoundChange"
                size="small"
            >
                <wa-option
                    v-for="opt in soundOptions"
                    :key="opt.value"
                    :value="opt.value"
                >{{ opt.label }}</wa-option>
            </wa-select>
            <a v-if="userTurnSound !== 'none'" href="#" class="notif-test-link" @click.prevent="testUserTurnSound">
                <wa-icon name="volume-up"></wa-icon> {{ $t('settings.notifications.testSound') }}
            </a>
            <div class="notif-toast-row">
                <wa-switch
                    :checked="userTurnToast"
                    @change="onUserTurnToastChange"
                    size="small"
                >{{ $t('settings.notifications.inAppToast') }}</wa-switch>
            </div>
            <div v-if="canChooseNotification" class="notif-browser-row">
                <wa-switch
                    :checked="userTurnBrowser"
                    @change="onUserTurnBrowserChange"
                    size="small"
                >{{ notificationLabel }}</wa-switch>
            </div>
        </div>

        <!-- Agent needs your attention -->
        <div class="setting-group">
            <label class="setting-group-label">{{ $t('settings.notifications.attention.label') }}</label>
            <wa-select
                :value.prop="pendingRequestSound"
                @change="onPendingRequestSoundChange"
                size="small"
            >
                <wa-option
                    v-for="opt in soundOptions"
                    :key="opt.value"
                    :value="opt.value"
                >{{ opt.label }}</wa-option>
            </wa-select>
            <a v-if="pendingRequestSound !== 'none'" href="#" class="notif-test-link" @click.prevent="testPendingRequestSound">
                <wa-icon name="volume-up"></wa-icon> {{ $t('settings.notifications.testSound') }}
            </a>
            <div v-if="canChooseNotification" class="notif-browser-row">
                <wa-switch
                    :checked="pendingRequestBrowser"
                    @change="onPendingRequestBrowserChange"
                    size="small"
                >{{ notificationLabel }}</wa-switch>
            </div>
        </div>

        <!-- When extra usage starts -->
        <div class="setting-group">
            <label class="setting-group-label">{{ $t('settings.usage.extraUsageNotification.label') }}</label>
            <wa-switch
                :checked="notifyOnExtraUsageStart"
                @change="onNotifyOnExtraUsageStartChange"
                size="small"
            >{{ $t('settings.usage.extraUsageNotification.notify') }}</wa-switch>
            <wa-select
                :value.prop="extraUsageStartSound"
                @change="onExtraUsageStartSoundChange"
                size="small"
            >
                <wa-option
                    v-for="opt in soundOptions"
                    :key="opt.value"
                    :value="opt.value"
                >{{ opt.label }}</wa-option>
            </wa-select>
            <a v-if="extraUsageStartSound !== 'none'" href="#" class="notif-test-link" @click.prevent="testExtraUsageStartSound">
                <wa-icon name="volume-up"></wa-icon> {{ $t('settings.notifications.testSound') }}
            </a>
            <div v-if="canChooseNotification" class="notif-browser-row">
                <wa-switch
                    :checked="extraUsageStartBrowser"
                    @change="onExtraUsageStartBrowserChange"
                    size="small"
                >{{ notificationLabel }}</wa-switch>
            </div>
            <p class="setting-group-hint">
                {{ $t('settings.notifications.extraUsage.hint') }}
            </p>
        </div>

        <!-- A peer needs you -->
        <div class="setting-group">
            <label class="setting-group-label">{{ $t('settings.notifications.peer.label') }}</label>
            <wa-select
                :value.prop="peerSound"
                @change="onPeerSoundChange"
                size="small"
            >
                <wa-option
                    v-for="opt in soundOptions"
                    :key="opt.value"
                    :value="opt.value"
                >{{ opt.label }}</wa-option>
            </wa-select>
            <a v-if="peerSound !== 'none'" href="#" class="notif-test-link" @click.prevent="testPeerSound">
                <wa-icon name="volume-up"></wa-icon> {{ $t('settings.notifications.testSound') }}
            </a>
            <div v-if="canChooseNotification" class="notif-browser-row">
                <wa-switch
                    :checked="peerBrowser"
                    @change="onPeerBrowserChange"
                    size="small"
                >{{ notificationLabel }}</wa-switch>
            </div>
            <p class="setting-group-hint">
                {{ $t('settings.notifications.peer.hint') }}
            </p>
        </div>

        <!-- External notifications (Apprise) -->
        <wa-divider></wa-divider>
        <div class="setting-group">
            <label class="setting-group-label">{{ $t('settings.notifications.push.label') }} <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
            <p class="setting-group-hint">
                <i18n-t keypath="settings.notifications.push.hint" scope="global">
                    <template #apprise><a href="https://appriseit.com" target="_blank" rel="noopener">{{ $t('settings.notifications.push.appriseUrls') }}</a></template>
                    <!-- eslint-disable-next-line @intlify/vue-i18n/no-raw-text -- This is an Apprise URL syntax example. -->
                    <template #example><code>ntfy://ntfy.sh/your-secret-topic</code></template>
                    <template #builder><a href="https://appriseit.com/url-builder/" target="_blank" rel="noopener">{{ $t('settings.notifications.push.urlBuilder') }}</a></template>
                </i18n-t>
            </p>
            <wa-callout v-if="!hasPublicBaseUrl" variant="neutral" size="small" class="public-base-url-callout">
                <wa-icon slot="icon" name="link"></wa-icon>
                <i18n-t keypath="settings.notifications.push.missingAddress" scope="global">
                    <template #address><strong>{{ $t('settings.general.externalAddress.label') }}</strong></template>
                    <template #settings><a href="#" @click.prevent="emit('go-to-public-base-url')">{{ $t('settings.notifications.push.setAddress') }}</a></template>
                </i18n-t>
            </wa-callout>
            <label class="setting-group-label">{{ $t('settings.notifications.targets.label') }}</label>
            <div class="apprise-targets">
                <div v-for="(target, index) in externalRows" :key="index" class="apprise-target">
                    <div class="apprise-target-row">
                        <wa-input
                            class="apprise-name-input"
                            size="small"
                            :placeholder="$t('settings.notifications.targets.namePlaceholder')"
                            :value.prop="target.name"
                            @change="onExternalTargetNameChange(index, $event)"
                        ></wa-input>
                        <div>
                            <wa-switch
                                class="apprise-enabled-switch"
                                size="small"
                                :checked="target.enabled"
                                :title="$t('common.status.enabled')"
                                @change="onExternalTargetEnabledChange(index, $event)"
                            ></wa-switch>
                            <wa-button
                                class="apprise-remove-button"
                                size="small"
                                appearance="plain"
                                variant="danger"
                                :title="$t('settings.notifications.targets.remove')"
                                @click="removeExternalTarget(index)"
                            >
                                <wa-icon name="trash"></wa-icon>
                            </wa-button>
                        </div>
                    </div>
                    <div class="apprise-target-row">
                        <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- The placeholder is an Apprise URL syntax example. -->
                        <wa-input
                            class="apprise-url-input"
                            size="small"
                            placeholder="ntfy://ntfy.sh/your-secret-topic"
                            :value.prop="target.url"
                            @input="onExternalTargetUrlInput(index, $event)"
                            @change="onExternalTargetUrlChange(index, $event)"
                        ></wa-input>
                        <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
                        <wa-button
                            class="apprise-test-button"
                            size="small"
                            appearance="outlined"
                            :class="{ 'apprise-test-ok': target.tested === true, 'apprise-test-fail': target.tested === false }"
                            :disabled="!effectiveTargetUrl(index) || !!testingTargets[index]"
                            :title="$t('settings.notifications.targets.testTooltip')"
                            @click="testExternalTarget(index)"
                        >
                            <wa-spinner v-if="testingTargets[index]" slot="start"></wa-spinner>
                            <wa-icon v-else slot="start" :name="externalTestIcon(target)"></wa-icon>
                            {{ $t('settings.notifications.targets.test') }}
                        </wa-button>
                    </div>
                    <div class="apprise-target-events">
                        <wa-switch
                            size="small"
                            :checked="target.notifyUserTurn !== false"
                            @change="onExternalTargetEventChange(index, 'notifyUserTurn', $event)"
                        >{{ $t('settings.notifications.targets.finished') }}</wa-switch>
                        <wa-switch
                            size="small"
                            :checked="target.notifyPendingRequest !== false"
                            @change="onExternalTargetEventChange(index, 'notifyPendingRequest', $event)"
                        >{{ $t('settings.notifications.targets.attention') }}</wa-switch>
                        <wa-switch
                            size="small"
                            :checked="target.notifyExtraUsageStart !== false"
                            @change="onExternalTargetEventChange(index, 'notifyExtraUsageStart', $event)"
                        >{{ $t('settings.notifications.targets.extraUsage') }}</wa-switch>
                        <wa-switch
                            size="small"
                            :checked="target.notifyPeer !== false"
                            @change="onExternalTargetEventChange(index, 'notifyPeer', $event)"
                        >{{ $t('settings.notifications.targets.peer') }}</wa-switch>
                        <wa-switch
                            size="small"
                            :checked="target.awayOnly === true"
                            @change="onExternalTargetEventChange(index, 'awayOnly', $event)"
                        >{{ $t('settings.notifications.targets.awayOnly') }}</wa-switch>
                    </div>
                    <div v-if="targetTestErrors[index]" class="apprise-test-error">{{ resolveUiMessage(targetTestErrors[index]) }}</div>
                </div>
            </div>
            <wa-button size="small" appearance="outlined" @click="addExternalTarget">
                <wa-icon slot="start" name="plus"></wa-icon> {{ $t('settings.notifications.targets.add') }}
            </wa-button>
            <p v-if="externalRows.length" class="setting-group-hint">
                {{ $t('settings.notifications.targets.testHint') }}
            </p>
            <p v-if="externalRows.length" class="setting-group-hint">
                {{ $t('settings.notifications.targets.awayHint') }}
            </p>
        </div>
    </section>
</template>

<style scoped>
.notif-test-link {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    text-decoration: none;
    cursor: pointer;

    &:hover {
        text-decoration: underline;
    }

    wa-icon {
        font-size: var(--wa-font-size-xs);
    }
}

.notif-permission-row {
    padding-bottom: var(--wa-space-2xs);
}

.notif-browser-row,
.notif-toast-row {
    padding-left: var(--wa-space-2xs);
}

.notif-denied {
    color: var(--wa-color-danger-60);
}

.synced-icon {
    color: var(--wa-color-brand);
}

.apprise-targets {
    /* Size container for the narrow-layout query below. */
    container-type: inline-size;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    margin-block: var(--wa-space-xs);
}

.apprise-target {
    margin-bottom: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

/* Two rows per target: (name + enable switch + remove) and (URL + Test). The
   leading input takes the free space so the trailing controls sit at the end. */
.apprise-target-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-s);
    row-gap: var(--wa-space-xs);
    & > :last-child {
        margin-left: auto;
        width: 5rem;
    }
}

.apprise-name-input,
.apprise-url-input {
    flex: 1;
    min-width: 10rem;
}

.apprise-url-input {
    font-family: var(--wa-font-family-code);
}

.apprise-remove-button::part(base) {
    xpadding-inline: var(--wa-space-2xs);
}

.apprise-test-ok wa-icon {
    color: var(--wa-color-success-60);
}

.apprise-test-fail wa-icon {
    color: var(--wa-color-danger-60);
}

.apprise-target-events {
    display: flex;
    gap: var(--wa-space-m);
    margin-block-start: var(--wa-space-2xs);
    padding-inline-start: var(--wa-space-s);
    flex-wrap: wrap;
    justify-content: flex-end;
}

.apprise-test-error {
    margin-top: var(--wa-space-3xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-60);
}
</style>
