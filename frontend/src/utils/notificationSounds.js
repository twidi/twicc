import { isAndroidApp } from './androidEnvironment.js'

// frontend/src/utils/notificationSounds.js
// Audio notification sounds synthesized via Web Audio API
// Adapted from claude-code-viewer's notification system

/**
 * Available notification sound types.
 * Each sound is synthesized using the Web Audio API — no external audio files needed.
 */
export const NOTIFICATION_SOUNDS = {
    NONE: 'none',
    BEEP: 'beep',
    CHIME: 'chime',
    PING: 'ping',
    POP: 'pop',
    BLOOP: 'bloop',
    TINK: 'tink',
    KNOCK: 'knock',
    SWEEP: 'sweep',
    SIGNAL: 'signal',
    RIPPLE: 'ripple',
}

/**
 * Display labels for each sound type (used in settings UI).
 */
export const NOTIFICATION_SOUND_LABELS = {
    [NOTIFICATION_SOUNDS.NONE]: 'No sound',
    [NOTIFICATION_SOUNDS.BEEP]: 'Beep',
    [NOTIFICATION_SOUNDS.CHIME]: 'Chime',
    [NOTIFICATION_SOUNDS.PING]: 'Ping',
    [NOTIFICATION_SOUNDS.POP]: 'Pop',
    [NOTIFICATION_SOUNDS.BLOOP]: 'Bloop',
    [NOTIFICATION_SOUNDS.TINK]: 'Tink',
    [NOTIFICATION_SOUNDS.KNOCK]: 'Knock',
    [NOTIFICATION_SOUNDS.SWEEP]: 'Sweep',
    [NOTIFICATION_SOUNDS.SIGNAL]: 'Signal',
    [NOTIFICATION_SOUNDS.RIPPLE]: 'Ripple',
}

/**
 * Sound configurations for synthesized audio.
 * Each defines the oscillator parameters for Web Audio API playback.
 */
const SOUND_CONFIGS = {
    [NOTIFICATION_SOUNDS.BEEP]: {
        frequencies: [800],
        duration: 0.15,
        type: 'sine',
        volume: 0.3,
    },
    [NOTIFICATION_SOUNDS.CHIME]: {
        frequencies: [523, 659, 784], // C, E, G notes (major chord)
        duration: 0.4,
        type: 'sine',
        volume: 0.2,
    },
    [NOTIFICATION_SOUNDS.PING]: {
        frequencies: [1000],
        duration: 0.1,
        type: 'triangle',
        volume: 0.4,
    },
    [NOTIFICATION_SOUNDS.POP]: {
        frequencies: [400, 600],
        duration: 0.08,
        type: 'square',
        volume: 0.2,
    },
    [NOTIFICATION_SOUNDS.BLOOP]: {
        tones: [
            { frequency: 320, endFrequency: 560, duration: 0.18, type: 'sine', volume: 0.28 },
        ],
    },
    [NOTIFICATION_SOUNDS.TINK]: {
        tones: [
            { frequency: 1320, duration: 0.07, type: 'triangle', volume: 0.18 },
            { frequency: 1760, delay: 0.06, duration: 0.12, type: 'sine', volume: 0.14 },
        ],
    },
    [NOTIFICATION_SOUNDS.KNOCK]: {
        tones: [
            { frequency: 170, duration: 0.05, type: 'square', volume: 0.22, attack: 0.002 },
            { frequency: 95, delay: 0.015, duration: 0.09, type: 'sine', volume: 0.18, attack: 0.002 },
        ],
    },
    [NOTIFICATION_SOUNDS.SWEEP]: {
        tones: [
            { frequency: 1400, endFrequency: 500, duration: 0.22, type: 'sawtooth', volume: 0.18 },
        ],
    },
    [NOTIFICATION_SOUNDS.SIGNAL]: {
        tones: [
            { frequency: 880, duration: 0.055, type: 'triangle', volume: 0.22 },
            { frequency: 660, delay: 0.105, duration: 0.055, type: 'triangle', volume: 0.2 },
            { frequency: 880, delay: 0.21, duration: 0.07, type: 'triangle', volume: 0.22 },
        ],
    },
    [NOTIFICATION_SOUNDS.RIPPLE]: {
        tones: [
            { frequency: 587, duration: 0.055, type: 'sine', volume: 0.15 },
            { frequency: 740, delay: 0.045, duration: 0.055, type: 'sine', volume: 0.15 },
            { frequency: 988, delay: 0.09, duration: 0.07, type: 'sine', volume: 0.14 },
            { frequency: 1175, delay: 0.145, duration: 0.08, type: 'sine', volume: 0.13 },
        ],
    },
}

const DEFAULT_SEQUENCE_GAP = 0.05
const DEFAULT_ATTACK = 0.005
const SILENCE_GAIN = 0.0001

function getTones(config) {
    if (config.tones) {
        return config.tones
    }

    return config.frequencies.map((frequency, index) => ({
        frequency,
        delay: index * (config.gap ?? DEFAULT_SEQUENCE_GAP),
        duration: config.duration,
        type: config.type,
        volume: config.volume,
    }))
}

/**
 * Play a notification sound using the Web Audio API.
 * @param {string} soundType - One of NOTIFICATION_SOUNDS values
 */
export function playNotificationSound(soundType) {
    if (soundType === NOTIFICATION_SOUNDS.NONE) {
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
        let latestStopTime = now

        getTones(config).forEach((tone) => {
            const oscillator = audioContext.createOscillator()
            const gainNode = audioContext.createGain()

            oscillator.connect(gainNode)
            gainNode.connect(audioContext.destination)

            const startTime = now + (tone.delay ?? 0)
            const duration = tone.duration ?? config.duration
            const stopTime = startTime + duration
            const attack = Math.min(tone.attack ?? DEFAULT_ATTACK, duration / 2)
            const volume = tone.volume ?? config.volume

            oscillator.frequency.setValueAtTime(tone.frequency, startTime)
            if (tone.endFrequency) {
                oscillator.frequency.exponentialRampToValueAtTime(tone.endFrequency, stopTime)
            }
            oscillator.type = tone.type ?? config.type

            gainNode.gain.setValueAtTime(SILENCE_GAIN, startTime)
            gainNode.gain.linearRampToValueAtTime(volume, startTime + attack)
            gainNode.gain.exponentialRampToValueAtTime(SILENCE_GAIN, stopTime)

            oscillator.start(startTime)
            oscillator.stop(stopTime)
            latestStopTime = Math.max(latestStopTime, stopTime)
        })

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
