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

        // Play each frequency (single tone or chord/sequence)
        config.frequencies.forEach((freq, index) => {
            const oscillator = audioContext.createOscillator()
            const gainNode = audioContext.createGain()

            oscillator.connect(gainNode)
            gainNode.connect(audioContext.destination)

            oscillator.frequency.setValueAtTime(freq, audioContext.currentTime)
            oscillator.type = config.type

            // Slight delay between frequencies for sequences
            const startTime = audioContext.currentTime + index * 0.05
            gainNode.gain.setValueAtTime(config.volume, startTime)
            gainNode.gain.exponentialRampToValueAtTime(0.01, startTime + config.duration)

            oscillator.start(startTime)
            oscillator.stop(startTime + config.duration)
        })
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

/**
 * Tags for browser notifications.
 * Using tags means a new notification of the same type replaces the previous one
 * instead of stacking. Combined with renotify: true, the replacement still
 * triggers the system sound/vibration so the user notices.
 */
export const BROWSER_NOTIFICATION_TAGS = {
    USER_TURN: 'twicc-user-turn',
    PENDING_REQUEST: 'twicc-pending-request',
}

/**
 * Send a browser (desktop) notification.
 * Only sends if the Notification API is available and permission is granted.
 * @param {string} title - Notification title
 * @param {string} body - Notification body text
 * @param {Object} [options] - Additional options
 * @param {string} [options.tag] - Tag to group/replace notifications of the same type
 * @param {Function} [options.onClick] - Callback when notification is clicked
 * @returns {Notification|null}
 */
export function sendBrowserNotification(title, body, options = {}) {
    if (!('Notification' in window) || Notification.permission !== 'granted') {
        return null
    }

    try {
        const notifOptions = { body }
        if (options.tag) {
            notifOptions.tag = options.tag
            notifOptions.renotify = true
        }

        const notification = new Notification(title, notifOptions)

        if (options.onClick) {
            notification.onclick = () => {
                window.focus()
                options.onClick()
                notification.close()
            }
        }

        return notification
    } catch (error) {
        console.warn('Failed to send browser notification:', error)
        return null
    }
}
