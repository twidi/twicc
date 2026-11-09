import test from 'node:test'
import assert from 'node:assert/strict'
import { NOTIFICATION_SOUNDS, SOUND_CONFIGS, getNotificationTones, scheduleNotificationSound } from './notificationSoundDefinitions.js'

// These literal schedules preserve the existing web sound design during extraction.
const schedules = {
    beep: [[800, 0, 0.15, 'sine', 0.3]],
    chime: [[523, 0, 0.4, 'sine', 0.2], [659, 0.05, 0.4, 'sine', 0.2], [784, 0.1, 0.4, 'sine', 0.2]],
    ping: [[1000, 0, 0.1, 'triangle', 0.4]],
    pop: [[400, 0, 0.08, 'square', 0.2], [600, 0.05, 0.08, 'square', 0.2]],
    bloop: [[320, 0, 0.18, 'sine', 0.28, 560]],
    tink: [[1320, 0, 0.07, 'triangle', 0.18], [1760, 0.06, 0.12, 'sine', 0.14]],
    knock: [[170, 0, 0.05, 'square', 0.22, undefined, 0.002], [95, 0.015, 0.09, 'sine', 0.18, undefined, 0.002]],
    sweep: [[1400, 0, 0.22, 'sawtooth', 0.18, 500]],
    signal: [[880, 0, 0.055, 'triangle', 0.22], [660, 0.105, 0.055, 'triangle', 0.2], [880, 0.21, 0.07, 'triangle', 0.22]],
    ripple: [[587, 0, 0.055, 'sine', 0.15], [740, 0.045, 0.055, 'sine', 0.15], [988, 0.09, 0.07, 'sine', 0.14], [1175, 0.145, 0.08, 'sine', 0.13]],
}

function recordingContext() {
    const oscillators = []
    const gains = []
    const parameter = () => ({
        events: [],
        setValueAtTime(...args) { this.events.push(['set', ...args]) },
        linearRampToValueAtTime(...args) { this.events.push(['linear', ...args]) },
        exponentialRampToValueAtTime(...args) { this.events.push(['exponential', ...args]) },
    })
    return {
        oscillators, gains, destination: {},
        createOscillator() {
            const oscillator = {
                frequency: parameter(),
                connect(target) { this.target = target },
                start(time) { this.startTime = time },
                stop(time) { this.stopTime = time },
            }
            oscillators.push(oscillator)
            return oscillator
        },
        createGain() {
            const gain = { gain: parameter(), connect(target) { this.target = target } }
            gains.push(gain)
            return gain
        },
    }
}

test('retains the ten sound identifiers and silent none identifier', () => {
    assert.deepEqual(Object.keys(SOUND_CONFIGS), ['beep', 'chime', 'ping', 'pop', 'bloop', 'tink', 'knock', 'sweep', 'signal', 'ripple'])
    assert.deepEqual(Object.values(NOTIFICATION_SOUNDS), ['none', ...Object.keys(schedules)])
    assert.deepEqual(getNotificationTones('none'), [])
    assert.deepEqual(getNotificationTones('unknown'), [])
})

for (const [id, tones] of Object.entries(schedules)) {
    test(`${id} preserves frequencies, delays, envelopes, types and sweep endpoints`, () => {
        assert.deepEqual(getNotificationTones(id).map(tone => [
            tone.frequency, tone.delay ?? 0, tone.duration, tone.type, tone.volume,
            tone.endFrequency, tone.attack,
        ]), tones.map(tone => [...tone.slice(0, 5), tone[5], tone[6]]))
        const context = recordingContext()
        const latestStop = scheduleNotificationSound(context, id, 2)
        assert.equal(context.oscillators.length, tones.length)
        for (const [index, [frequency, delay, duration, type, volume, endFrequency, attack = 0.005]] of tones.entries()) {
            const oscillator = context.oscillators[index]
            const gain = context.gains[index]
            const start = 2 + delay
            const stop = start + duration
            assert.equal(oscillator.startTime, start)
            assert.equal(oscillator.stopTime, stop)
            assert.equal(oscillator.type, type)
            assert.equal(oscillator.target, gain)
            assert.equal(gain.target, context.destination)
            assert.deepEqual(oscillator.frequency.events, [
                ['set', frequency, start],
                ...(endFrequency ? [['exponential', endFrequency, stop]] : []),
            ])
            assert.deepEqual(gain.gain.events, [
                ['set', 0.0001, start], ['linear', volume, start + attack], ['exponential', 0.0001, stop],
            ])
        }
        assert.equal(latestStop, Math.max(...context.oscillators.map(oscillator => oscillator.stopTime)))
    })
}

test('signal includes its final oscillator, while none schedules no audio', () => {
    const context = recordingContext()
    assert.equal(scheduleNotificationSound(context, 'signal', 0), 0.28)
    const silentContext = recordingContext()
    assert.equal(scheduleNotificationSound(silentContext, 'none', 4), 4)
    assert.equal(silentContext.oscillators.length, 0)
})
