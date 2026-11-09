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
 * Sound configurations for synthesized audio.
 * Each defines the oscillator parameters for Web Audio API playback.
 */
export const SOUND_CONFIGS = {
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

export function getNotificationTones(soundId) {
    const config = SOUND_CONFIGS[soundId]
    if (!config) return []
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

/** Schedule a sound without creating or closing the caller-owned context. */
export function scheduleNotificationSound(context, soundId, start) {
    const config = SOUND_CONFIGS[soundId]
    if (!config) return start
    let latestStopTime = start

    getNotificationTones(soundId).forEach((tone) => {
        const oscillator = context.createOscillator()
        const gainNode = context.createGain()

        oscillator.connect(gainNode)
        gainNode.connect(context.destination)

        const startTime = start + (tone.delay ?? 0)
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

    return latestStopTime
}
