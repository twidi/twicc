import { i18n } from '../i18n/index.js'

import { isAndroidApp } from './androidEnvironment.js'

// frontend/src/utils/notificationSounds.js
// Audio notification sounds synthesized via Web Audio API
// Adapted from claude-code-viewer's notification system

import { NOTIFICATION_SOUNDS, SOUND_CONFIGS, scheduleNotificationSound } from './notificationSoundDefinitions.js'

export { NOTIFICATION_SOUNDS } from './notificationSoundDefinitions.js'

/**
 * Display labels for each sound type (used in settings UI).
 */
export const NOTIFICATION_SOUND_LABELS = {
    get [NOTIFICATION_SOUNDS.NONE]() { return i18n.global.t('settings.notifications.sounds.none') },
    get [NOTIFICATION_SOUNDS.BEEP]() { return i18n.global.t('settings.notifications.sounds.beep') },
    get [NOTIFICATION_SOUNDS.CHIME]() { return i18n.global.t('settings.notifications.sounds.chime') },
    get [NOTIFICATION_SOUNDS.PING]() { return i18n.global.t('settings.notifications.sounds.ping') },
    get [NOTIFICATION_SOUNDS.POP]() { return i18n.global.t('settings.notifications.sounds.pop') },
    get [NOTIFICATION_SOUNDS.BLOOP]() { return i18n.global.t('settings.notifications.sounds.bloop') },
    get [NOTIFICATION_SOUNDS.TINK]() { return i18n.global.t('settings.notifications.sounds.tink') },
    get [NOTIFICATION_SOUNDS.KNOCK]() { return i18n.global.t('settings.notifications.sounds.knock') },
    get [NOTIFICATION_SOUNDS.SWEEP]() { return i18n.global.t('settings.notifications.sounds.sweep') },
    get [NOTIFICATION_SOUNDS.SIGNAL]() { return i18n.global.t('settings.notifications.sounds.signal') },
    get [NOTIFICATION_SOUNDS.RIPPLE]() { return i18n.global.t('settings.notifications.sounds.ripple') },
}

/**
 * Play a notification sound using the Web Audio API.
 * @param {string} soundType - One of NOTIFICATION_SOUNDS values
 */
export function playNotificationSound(soundType) {
    if (isAndroidApp.value || soundType === NOTIFICATION_SOUNDS.NONE) {
        return
    }

    const config = SOUND_CONFIGS[soundType]
    if (!config) {
        console.warn(`Unknown notification sound type: ${soundType}`)
        return
    }

    try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext
        if (!AudioContextClass) {
            console.warn('Web Audio API not supported')
            return
        }

        const audioContext = new AudioContextClass()
        const now = audioContext.currentTime
        const latestStopTime = scheduleNotificationSound(audioContext, soundType, now)

        window.setTimeout(() => audioContext.close?.(), (latestStopTime - now + 0.05) * 1000)
    } catch (error) {
        console.warn('Failed to play notification sound:', error)
    }
}

/**
 * Get the list of available sound options for use in select menus.
 * @returns {Array<{value: string, label: string}>}
 */
export function getAvailableSoundOptions() {
    return Object.entries(NOTIFICATION_SOUND_LABELS).map(([value, label]) => ({
        value,
        label,
    }))
}

// --- Browser notification management ---

/** Single tag so only one notification is visible at a time (the latest replaces the previous). */
const BROWSER_NOTIFICATION_TAG = 'twicc'

/** Reference to the last browser notification, so we can close it on return. */
let activeNotification = null

/**
 * Whether the user is actively looking at the app.
 * True when the tab is in the foreground AND the browser window has OS-level focus.
 */
export function isPageActive() {
    return document.visibilityState === 'visible' && document.hasFocus()
}

/**
 * Close the active browser notification (if any).
 * Called when the user returns to the app.
 */
export function closeBrowserNotification() {
    if (activeNotification) {
        activeNotification.close()
        activeNotification = null
    }
}

// Auto-close browser notification when the user returns to the app.
// Both events are needed: visibilitychange catches tab switches,
// focus catches alt-tab back to the browser window.
document.addEventListener('visibilitychange', () => {
    if (isPageActive()) closeBrowserNotification()
})
window.addEventListener('focus', () => {
    if (isPageActive()) closeBrowserNotification()
})

/**
 * Send a browser (desktop) notification.
 * Only sends if the Notification API is available, permission is granted,
 * and the page is NOT currently active (user is away).
 * Uses a single tag ('twicc') so the latest notification always replaces the previous one,
 * with renotify: true so the system sound/vibration re-triggers.
 * @param {string} title - Notification title
 * @param {string} body - Notification body text
 * @param {Function} [onClick] - Optional callback when notification is clicked
 * @returns {Notification|null}
 */
export function sendBrowserNotification(title, body, onClick) {
    if (isAndroidApp.value) return null
    if (!('Notification' in window) || Notification.permission !== 'granted') {
        return null
    }

    // Don't send if the user is already looking at the app
    if (isPageActive()) {
        return null
    }

    try {
        const notification = new Notification(title, {
            body,
            tag: BROWSER_NOTIFICATION_TAG,
            renotify: true,
        })

        notification.onclick = () => {
            window.focus()
            onClick?.()
            notification.close()
            activeNotification = null
        }

        // Keep reference for auto-close on return
        activeNotification = notification

        return notification
    } catch (error) {
        console.warn('Failed to send browser notification:', error)
        return null
    }
}
