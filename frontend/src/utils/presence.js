// frontend/src/utils/presence.js
// Human-presence heartbeat: tells the backend whether a human is actively at
// this TwiCC client, so it can hold "away-only" external (Apprise) notifications
// while the user is here and send them only once the user is away.
//
// A page can only observe its own tab, so presence is approximated per device
// class and the backend applies a per-class grace window (see twicc/presence.py):
//   - desktop: report genuine input activity (mouse/keyboard/scroll). Input
//     events only reach the focused tab, so they double as a "TwiCC is
//     foregrounded and the user is interacting" signal; when the user switches
//     tab/app or walks away (even leaving the tab focused), the pings stop and
//     the backend's grace eventually expires.
//   - mobile/touch: report a foreground heartbeat on a timer (being in the
//     foreground counts as present, even without touching) and stop when the
//     tab is backgrounded — the OS suspends it anyway.
//
// All pings are fire-and-forget; the send function no-ops when the WS is down,
// so pings simply resume after a reconnect.

import { readonly, ref } from 'vue'
import { isAndroidApp } from './androidEnvironment.js'

// Cadence well under both backend grace windows (mobile = 30s, desktop = 5min),
// so a single dropped ping never prematurely expires presence.
const PING_INTERVAL_MS = 10000

// How long after the last desktop input we still consider a human present here.
// Mirrors the backend desktop grace window (twicc/presence.py). Used by
// isLocallyPresent() to gate auto "mark as read" so a desktop left open+focused
// but unattended stops marking the session read (otherwise it would steal the
// global last_viewed_at from a device where the user actually is, e.g. a phone).
const DESKTOP_PRESENCE_WINDOW_MS = 5 * 60 * 1000

// Touch-primary devices (phones, tablets) are classified as "mobile": their OS
// suspends background tabs and shows no desktop notification banner, so the
// backend treats backgrounding as "the user left" (short grace). A desktop with
// a touchscreen still exposes a fine pointer and stays "desktop".
function detectDeviceClass() {
    try {
        return window.matchMedia('(pointer: coarse)').matches ? 'mobile' : 'desktop'
    } catch {
        return 'desktop'
    }
}

let installed = false
let deviceClass = null
// Timer that flips presence back to "away" once the desktop grace window
// elapses with no input; re-armed on each activity.
let desktopAwayTimer = null

// Reactive single source of truth for local presence — "a human appears to be
// actively at THIS client right now". Drives BOTH the auto "mark as read" gate
// (isLocallyPresent) and the UI WebSocket dot, so they can never disagree.
//   - desktop: true while genuine input (mouse/keyboard/scroll) happened within
//     the grace window. Starts false (no input yet) — a freshly loaded but
//     untouched desktop reads as "not present", which is exactly what we want:
//     it must not steal the global last_viewed_at from the device the user is
//     actually on. Flips true on the first input, false again grace-ms later.
//   - mobile: mirrors tab visibility. A visible phone is being looked at; it
//     locks/suspends otherwise. So on mobile this matches isPageActive() and
//     introduces no behavior change.
const present = ref(false)

/**
 * Readonly reactive presence flag for UI consumers (e.g. the connection dot
 * turns blue when the WS is connected but the user is away). Same value as
 * isLocallyPresent().
 * @type {import('vue').DeepReadonly<import('vue').Ref<boolean>>}
 */
export const localPresence = readonly(present)

/**
 * Imperative point-in-time read of {@link localPresence}, for non-reactive
 * callers (the mark-as-read gate). See the `present` ref above for semantics.
 * @returns {boolean}
 */
export function isLocallyPresent() {
    return present.value
}

/**
 * Install the presence heartbeat once at module load. Idempotent.
 * @param {(data: object) => boolean} send - WS send function (no-ops when disconnected)
 * @param {() => void} [onActive] - Called whenever local activity is observed
 *   (same throttled cadence as pings). Lets callers refresh "viewed" state when
 *   the user resumes activity without a focus/visibility change.
 */
export function installPresenceHeartbeat(send, onActive) {
    if (installed) return
    installed = true

    deviceClass = detectDeviceClass()
    const ping = () => send({ type: 'presence', device_class: deviceClass, client_kind: isAndroidApp.value ? 'android' : 'browser' })
    window.addEventListener('twicc:android-ready', ping)
    const active = () => { if (onActive) onActive() }

    if (deviceClass === 'mobile') {
        // Foreground heartbeat: ping on a timer while the tab is visible. When
        // hidden we skip (and the OS throttles/suspends background timers
        // anyway), so presence expires shortly after backgrounding.
        const syncVisibility = () => { present.value = document.visibilityState === 'visible' }
        const tick = () => {
            if (document.visibilityState === 'visible') {
                ping()
                active()
            }
        }
        syncVisibility() // seed from the current state at install
        tick() // immediate, so returning to the app re-establishes presence at once
        setInterval(tick, PING_INTERVAL_MS)
        // Ping immediately on foreground so a quick flick back to the app
        // cancels a deferred push without waiting for the next interval.
        document.addEventListener('visibilitychange', () => {
            syncVisibility()
            if (document.visibilityState === 'visible') {
                ping()
                active()
            }
        })
    } else {
        // Desktop: input-driven. Input events only fire on the focused tab, so
        // they signal "TwiCC foregrounded + user interacting". Throttle so a
        // burst of pointer moves sends at most one ping per interval.
        let lastSent = 0
        const onInput = () => {
            // Mark present and (re)arm the away timer: presence drops back to
            // false only after a full grace window with no further input.
            present.value = true
            if (desktopAwayTimer) clearTimeout(desktopAwayTimer)
            desktopAwayTimer = setTimeout(() => { present.value = false }, DESKTOP_PRESENCE_WINDOW_MS)
            const now = Date.now()
            if (now - lastSent >= PING_INTERVAL_MS) {
                lastSent = now
                ping()
                active()
            }
        }
        for (const ev of ['pointerdown', 'pointermove', 'keydown', 'scroll', 'wheel', 'touchstart']) {
            window.addEventListener(ev, onInput, { passive: true })
        }
    }
}
