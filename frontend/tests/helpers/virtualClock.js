import { setTimeout as realSetTimeout, clearTimeout as realClearTimeout } from 'node:timers'

// A clock for the timers a test would otherwise wait out for real (retry
// delays, debounces). Timers of at least `minDelayMs` are virtual; shorter ones
// (zero-delay ones, with the default of 1 ms) stay real. Virtual time flows with
// real time, so a virtual timer still fires on its own when a test waits for
// real; `elapse(ms)` only skips idle time.
//
// For the virtual timers, `elapse(ms)` has the outcome of a real wait of `ms`,
// in the same order:
//   1. it first lets the work already queued run (Vue watchers, promise
//      chains, zero-delay timers): `drainMs` of real time, then a flush;
//   2. if a virtual timer is due by the end of the wait, it jumps to it, fires
//      it, and goes back to 1 — so a timer scheduled by that work still fires
//      within the same wait if it is due in time;
//   3. once nothing is due, it jumps to the end. If a timer fired, the real
//      time the former wait left after it (up to `settleMs`) is still given,
//      so its async work settles before the test looks.
// A real timer longer than `drainMs` is NOT advanced by `elapse`: keep
// `minDelayMs` low enough that every timer the code under test sets is virtual.
// The one assumption: queued work schedules its timers within `drainMs`.
//
// Hand `setTimeout` / `clearTimeout` to the code under test (when it takes
// them as dependencies), or `install()` them as globals.
export function createVirtualClock({ minDelayMs = 1, drainMs = 10, settleMs = 100, flush = async () => {} }) {
    const pending = new Set()
    let offset = 0
    const now = () => performance.now() + offset
    const sleep = ms => new Promise(resolve => realSetTimeout(resolve, ms))

    function arm(timer) {
        timer.real = realSetTimeout(() => fire(timer), Math.max(0, timer.at - now()))
    }
    function fire(timer) {
        if (!pending.delete(timer)) return
        realClearTimeout(timer.real)
        timer.run()
    }
    function jumpTo(time) {
        if (time <= now()) return
        offset += time - now()
        // The real timers backing the pending ones were armed for the old time.
        for (const timer of pending) {
            realClearTimeout(timer.real)
            arm(timer)
        }
    }

    function setTimeout(callback, delay = 0, ...args) {
        if (!(delay >= minDelayMs)) return realSetTimeout(callback, delay, ...args)
        const timer = { at: now() + delay, run: () => callback(...args) }
        pending.add(timer)
        arm(timer)
        return timer
    }
    function clearTimeout(timer) {
        if (pending.delete(timer)) realClearTimeout(timer.real)
        else realClearTimeout(timer)
    }

    async function elapse(ms) {
        const target = now() + ms
        let lastFiredAt = null
        for (;;) {
            await sleep(drainMs)
            await flush()
            const due = [...pending].filter(timer => timer.at <= target).sort((a, b) => a.at - b.at)[0]
            if (!due) break
            jumpTo(due.at)
            lastFiredAt = due.at
            fire(due)
            await flush()
        }
        if (lastFiredAt !== null) {
            const settle = Math.min(target, lastFiredAt + settleMs) - now()
            if (settle > 0) await sleep(settle)
        }
        jumpTo(target)
    }

    function dispose() {
        for (const timer of pending) realClearTimeout(timer.real)
        pending.clear()
    }

    // Replace the global timers; returns the function that puts back the ones
    // it replaced.
    function install() {
        const previous = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout }
        globalThis.setTimeout = setTimeout
        globalThis.clearTimeout = clearTimeout
        return () => {
            Object.assign(globalThis, previous)
            dispose()
        }
    }

    return { setTimeout, clearTimeout, elapse, install, dispose }
}
