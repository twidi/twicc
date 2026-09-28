// Run with: node --test src/composables/useChatReveal.test.js (from the frontend dir)
// Chat reveal (visual refresh step 5a, docs/plans/2026-09-28-chat-entrances-skeleton-design.md §7.2):
// the Vue wrapper, in an effect scope with a fake environment whose methods check `this`.
import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref } from 'vue'

import { useChatReveal } from './useChatReveal.js'

/** Timers and performance.now throw when called unbound, like the browser's ("Illegal invocation"). */
function makeEnv() {
    const env = { clock: 0, timers: new Map(), nextId: 1 }
    env.performance = {
        now() {
            if (this !== env.performance) throw new TypeError('Illegal invocation')
            return env.clock
        },
    }
    env.setTimeout = function (fn, ms) {
        if (this !== env) throw new TypeError('Illegal invocation')
        const id = env.nextId++
        env.timers.set(id, { fn, at: env.clock + ms })
        return id
    }
    env.clearTimeout = function (id) {
        if (this !== env) throw new TypeError('Illegal invocation')
        env.timers.delete(id)
    }
    env.advance = (ms = 0) => {
        const target = env.clock + ms
        for (;;) {
            const due = [...env.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
            if (!due) break
            env.timers.delete(due[0])
            env.clock = due[1].at
            due[1].fn()
        }
        env.clock = target
    }
    return env
}

function setup(initialBusy) {
    const env = makeEnv()
    const busy = ref(initialBusy)
    const scope = effectScope()
    const reveal = scope.run(() => useChatReveal(() => busy.value, env))
    return { env, busy, scope, reveal }
}

test('the setup call applies the initial busy value', () => {
    const idle = setup(false)
    assert.equal(idle.reveal.hidden.value, false)
    assert.equal(idle.reveal.phaseId.value, 0)
    idle.scope.stop()

    const loading = setup(true)
    assert.equal(loading.reveal.hidden.value, true)
    assert.equal(loading.reveal.phaseId.value, 1)
    assert.equal(loading.reveal.startedAt.value, 0)
    loading.scope.stop()
})

test('a change of the source reaches the controller synchronously; the refs mirror the state', () => {
    const { env, busy, scope, reveal } = setup(false)
    env.clock = 10
    busy.value = true
    assert.equal(reveal.hidden.value, true, 'no flush needed')
    assert.equal(reveal.startedAt.value, 10)
    env.advance(300)
    assert.equal(reveal.skeletonShown.value, true)
    busy.value = false
    env.advance(300)
    assert.equal(reveal.hidden.value, false)
    assert.equal(reveal.skeletonShown.value, false)
    assert.equal(reveal.startedAt.value, null)
    scope.stop()
})

test('restartClock is exposed', () => {
    const { env, scope, reveal } = setup(true)
    env.clock = 50
    reveal.restartClock()
    assert.equal(reveal.phaseId.value, 2)
    assert.equal(reveal.startedAt.value, 50)
    scope.stop()
})

test('scope dispose cancels the timers', () => {
    const { env, scope } = setup(true)
    assert.ok(env.timers.size > 0)
    scope.stop()
    assert.equal(env.timers.size, 0)
})
