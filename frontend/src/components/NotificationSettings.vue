<script setup>
// NotificationSettings.vue - Notification settings section for the settings popover
// Two notification types: User Turn and Pending Request
// Each has a sound selector (with test button) and a browser notification toggle

import { computed, ref, watch, nextTick } from 'vue'
import { useSettingsStore } from '../stores/settings'
import { playNotificationSound, getAvailableSoundOptions, NOTIFICATION_SOUNDS } from '../utils/notificationSounds'

const store = useSettingsStore()

// Sound options for selects
const soundOptions = getAvailableSoundOptions()

// Refs for Web Components sync
const userTurnSoundSelect = ref(null)
const userTurnBrowserSwitch = ref(null)
const pendingRequestSoundSelect = ref(null)
const pendingRequestBrowserSwitch = ref(null)

// Computed from store
const userTurnSound = computed(() => store.getNotifUserTurnSound)
const userTurnBrowser = computed(() => store.isNotifUserTurnBrowser)
const pendingRequestSound = computed(() => store.getNotifPendingRequestSound)
const pendingRequestBrowser = computed(() => store.isNotifPendingRequestBrowser)

// Browser notification permission state
const browserNotifPermission = ref(getBrowserNotifPermission())

function getBrowserNotifPermission() {
    if (!('Notification' in window)) return 'unsupported'
    return Notification.permission
}

// Sync Web Component states
function syncState() {
    nextTick(() => {
        if (userTurnSoundSelect.value && userTurnSoundSelect.value.value !== userTurnSound.value) {
            userTurnSoundSelect.value.value = userTurnSound.value
        }
        if (userTurnBrowserSwitch.value && userTurnBrowserSwitch.value.checked !== userTurnBrowser.value) {
            userTurnBrowserSwitch.value.checked = userTurnBrowser.value
        }
        if (pendingRequestSoundSelect.value && pendingRequestSoundSelect.value.value !== pendingRequestSound.value) {
            pendingRequestSoundSelect.value.value = pendingRequestSound.value
        }
        if (pendingRequestBrowserSwitch.value && pendingRequestBrowserSwitch.value.checked !== pendingRequestBrowser.value) {
            pendingRequestBrowserSwitch.value.checked = pendingRequestBrowser.value
        }
    })
}

watch([userTurnSound, userTurnBrowser, pendingRequestSound, pendingRequestBrowser], syncState, { immediate: true })

// Event handlers
function onUserTurnSoundChange(event) {
    store.setNotifUserTurnSound(event.target.value)
}

function onPendingRequestSoundChange(event) {
    store.setNotifPendingRequestSound(event.target.value)
}

function onUserTurnBrowserChange(event) {
    store.setNotifUserTurnBrowser(event.target.checked)
}

function onPendingRequestBrowserChange(event) {
    store.setNotifPendingRequestBrowser(event.target.checked)
}

function testUserTurnSound() {
    playNotificationSound(userTurnSound.value)
}

function testPendingRequestSound() {
    playNotificationSound(pendingRequestSound.value)
}

/**
 * Request browser notification permission.
 * Once granted, the switches become usable.
 */
async function requestPermission() {
    if (!('Notification' in window)) return
    try {
        const permission = await Notification.requestPermission()
        browserNotifPermission.value = permission
    } catch (e) {
        console.warn('Failed to request notification permission:', e)
    }
}

/**
 * Called when the parent popover opens — re-sync switch states.
 */
function sync() {
    browserNotifPermission.value = getBrowserNotifPermission()
    syncState()
}

defineExpose({ sync })
</script>

<template>
    <section class="settings-section">
        <h3 class="settings-section-title">Notifications</h3>

        <!-- Browser notification permission (shown once for both) -->
        <div v-if="browserNotifPermission === 'unsupported'" class="notif-permission-row">
            <span class="setting-group-hint">Browser notifications not supported</span>
        </div>
        <div v-else-if="browserNotifPermission === 'denied'" class="notif-permission-row">
            <span class="setting-group-hint notif-denied">Browser notifications blocked</span>
        </div>
        <div v-else-if="browserNotifPermission === 'default'" class="notif-permission-row">
            <wa-button
                variant="neutral"
                appearance="outlined"
                size="small"
                @click="requestPermission"
            >Enable browser notifications</wa-button>
        </div>

        <!-- Claude finished working -->
        <div class="setting-group">
            <label class="setting-group-label">Claude finished working</label>
            <wa-select
                ref="userTurnSoundSelect"
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
                <wa-icon name="volume-up"></wa-icon> Test sound
            </a>
            <div v-if="browserNotifPermission === 'granted'" class="notif-browser-row">
                <wa-switch
                    ref="userTurnBrowserSwitch"
                    @change="onUserTurnBrowserChange"
                    size="small"
                >Browser notification</wa-switch>
            </div>
        </div>

        <!-- Claude needs your attention -->
        <div class="setting-group">
            <label class="setting-group-label">Claude needs your attention</label>
            <wa-select
                ref="pendingRequestSoundSelect"
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
                <wa-icon name="volume-up"></wa-icon> Test sound
            </a>
            <div v-if="browserNotifPermission === 'granted'" class="notif-browser-row">
                <wa-switch
                    ref="pendingRequestBrowserSwitch"
                    @change="onPendingRequestBrowserChange"
                    size="small"
                >Browser notification</wa-switch>
            </div>
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

.notif-browser-row {
    padding-left: var(--wa-space-2xs);
}

.notif-denied {
    color: var(--wa-color-danger-60);
}
</style>
