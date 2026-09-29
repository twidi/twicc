// Run with: node --test src/utils/viewTransition.test.js (from the frontend dir)
// The single owner of document view transitions (visual refresh step 5c,
// docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §5.1 and §12.3), driven with a
// fake document and manual timers.
import test from 'node:test'
import assert from 'node:assert/strict'

import { isViewTransitionUpdating, runViewTransition, supportsViewTransitions } from './viewTransition.js'

const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve))

function deferred() {
    let resolve, reject
    let settled = false
    const promise = new Promise((res, rej) => {
        resolve = (value) => { settled = true; res(value) }
        reject = (reason) => { settled = true; rej(reason) }
    })
    return { promise, resolve, reject, get settled() { return settled } }
}

/** A fake transition: the test invokes the captured callback; skipTransition rejects a
 *  pending `ready`. */
function makeTransition(callback) {
    const ready = deferred()
    const updateCallbackDone = deferred()
    const finished = deferred()
    const transition = {
        callback,
        invoked: false,
        skips: 0,
        readyControl: ready,
        doneControl: updateCallbackDone,
        finishedControl: finished,
        ready: ready.promise,
        updateCallbackDone: updateCallbackDone.promise,
        finished: finished.promise,
        skipTransition() {
            transition.skips++
            if (!ready.settled) ready.reject(new Error('AbortError'))
        },
        /** Invoke the update callback; returns its promise. */
        invoke() {
            transition.invoked = true
            let result
            try {
                result = Promise.resolve(callback())
            } catch (error) {
                result = Promise.reject(error)
            }
            result.then(updateCallbackDone.resolve, updateCallbackDone.reject)
            return result
        },
    }
    return transition
}

function makeEnv({ support = true, throwOnStart = false } = {}) {
    const env = { now: 0, timers: new Map(), nextId: 1, transitions: [], animations: [] }
    env.setTimeout = (fn, ms) => {
        const id = env.nextId++
        env.timers.set(id, { fn, at: env.now + ms })
        return id
    }
    env.clearTimeout = (id) => { env.timers.delete(id) }
    env.advance = (ms) => {
        const target = env.now + ms
        for (;;) {
            const due = [...env.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
            if (!due) break
            env.timers.delete(due[0])
            env.now = due[1].at
            due[1].fn()
        }
        env.now = target
    }
    const classes = new Set(['wa-dark'])
    const properties = new Map()
    const root = {
        classes,
        properties,
        classList: {
            add: (...names) => names.forEach((n) => classes.add(n)),
            remove: (...names) => names.forEach((n) => classes.delete(n)),
            contains: (name) => classes.has(name),
            [Symbol.iterator]: () => [...classes][Symbol.iterator](),
        },
        style: {
            setProperty: (name, value) => properties.set(name, value),
            removeProperty: (name) => properties.delete(name),
        },
        getAnimations: () => env.animations,
    }
    env.document = { documentElement: root }
    if (support) {
        env.document.startViewTransition = (callback) => {
            if (throwOnStart) throw new Error('boom')
            const transition = makeTransition(callback)
            env.transitions.push(transition)
            return transition
        }
    }
    env.root = root
    return env
}

const vtClasses = (env) => [...env.root.classes].filter((c) => c.startsWith('twicc-vt-'))

function pseudoAnimation(endTime, pseudoElement = '::view-transition-new(root)') {
    return { effect: { pseudoElement, getComputedTiming: () => ({ endTime }) } }
}

test('15. no startViewTransition: update runs synchronously once, no class', () => {
    const env = makeEnv({ support: false })
    let calls = 0
    const result = runViewTransition(() => { calls++ }, { kind: 'fade', properties: { '--x': '1px' }, env })
    assert.equal(calls, 1)
    assert.equal(result, undefined)
    assert.deepEqual(vtClasses(env), [])
    assert.equal(env.root.properties.size, 0)
    assert.equal(supportsViewTransitions(env), false)
})

test('16. the class and properties are set before the call, update runs inside the callback, cleanup at finished', async () => {
    const env = makeEnv()
    let calls = 0
    runViewTransition(() => { calls++ }, { kind: 'circle', properties: { '--twicc-scheme-x': '10px' }, env })
    assert.equal(env.transitions.length, 1)
    assert.deepEqual(vtClasses(env), ['twicc-vt-circle'])
    assert.equal(env.root.properties.get('--twicc-scheme-x'), '10px')
    assert.equal(calls, 0, 'not before the callback')
    const [transition] = env.transitions
    transition.invoke()
    assert.equal(calls, 1)
    transition.readyControl.resolve()
    transition.finishedControl.resolve()
    await flushMicrotasks()
    assert.deepEqual(vtClasses(env), [])
    assert.equal(env.root.properties.size, 0)
    assert.ok(env.root.classes.has('wa-dark'), 'other classes stay')
})

test('17. two runs: the second clears the first; the first finishing late leaves the second alone', async () => {
    const env = makeEnv()
    runViewTransition(() => {}, { kind: 'fade', properties: { '--a': '1' }, env })
    runViewTransition(() => {}, { kind: 'circle', properties: { '--b': '2' }, env })
    assert.deepEqual(vtClasses(env), ['twicc-vt-circle'])
    assert.equal(env.root.properties.has('--a'), false)
    assert.equal(env.root.properties.get('--b'), '2')
    const [first, second] = env.transitions
    first.finishedControl.resolve()
    await flushMicrotasks()
    assert.deepEqual(vtClasses(env), ['twicc-vt-circle'])
    assert.equal(env.root.properties.get('--b'), '2')
    second.finishedControl.resolve()
    await flushMicrotasks()
    assert.deepEqual(vtClasses(env), [])
    assert.equal(env.root.properties.size, 0)
})

test('18. a skipped transition leaves no unhandled rejection; a throwing start still updates once', async () => {
    const unhandled = []
    const onUnhandled = (reason) => unhandled.push(reason)
    process.on('unhandledRejection', onUnhandled)
    try {
        const env = makeEnv()
        runViewTransition(() => {}, { kind: 'fade', env })
        const [transition] = env.transitions
        transition.readyControl.reject(new Error('skipped'))
        transition.doneControl.reject(new Error('skipped'))
        transition.finishedControl.reject(new Error('skipped'))
        await flushMicrotasks()
        await flushMicrotasks()
        assert.deepEqual(unhandled, [])
        assert.deepEqual(vtClasses(env), [])
    } finally {
        process.off('unhandledRejection', onUnhandled)
    }

    const env = makeEnv({ throwOnStart: true })
    let calls = 0
    const result = runViewTransition(() => { calls++ }, { kind: 'fade', properties: { '--a': '1' }, env })
    assert.equal(calls, 1)
    assert.equal(result, undefined)
    assert.deepEqual(vtClasses(env), [])
    assert.equal(env.root.properties.size, 0)
})

test('29. settle: the callback waits for the update and a macrotask; the depth counter', async () => {
    const env = makeEnv()
    const update = deferred()
    let insideUpdating = null
    const result = runViewTransition(() => {
        insideUpdating = isViewTransitionUpdating()
        return update.promise
    }, { kind: 'tab', settle: true, env })
    assert.equal(result, undefined)
    const [transition] = env.transitions
    let done = false
    transition.invoke().then(() => { done = true })
    assert.equal(insideUpdating, true)
    await flushMicrotasks()
    assert.equal(done, false, 'waits for the update')
    update.resolve()
    await flushMicrotasks()
    assert.equal(done, false, 'waits for the 0ms timer')
    assert.equal(isViewTransitionUpdating(), true)
    env.advance(0)
    await flushMicrotasks()
    assert.equal(done, true)
    assert.equal(isViewTransitionUpdating(), false)

    // An update that throws.
    runViewTransition(() => { throw new Error('nav failed') }, { kind: 'tab', settle: true, env })
    const failing = env.transitions[1]
    let rejected = false
    failing.invoke().catch(() => { rejected = true })
    await flushMicrotasks()
    assert.equal(rejected, true)
    assert.equal(isViewTransitionUpdating(), false)

    // Two overlapping callbacks.
    const one = deferred()
    const two = deferred()
    runViewTransition(() => one.promise, { kind: 'tab', settle: true, env })
    runViewTransition(() => two.promise, { kind: 'tab', settle: true, env })
    const [, , third, fourth] = env.transitions
    third.invoke()
    fourth.invoke()
    assert.equal(isViewTransitionUpdating(), true)
    one.resolve()
    await flushMicrotasks()
    env.advance(0)
    await flushMicrotasks()
    assert.equal(isViewTransitionUpdating(), true, 'the second callback still runs')
    two.resolve()
    await flushMicrotasks()
    env.advance(0)
    await flushMicrotasks()
    assert.equal(isViewTransitionUpdating(), false)
})

test('30. a run requested inside an update runs directly', () => {
    const env = makeEnv()
    let inner = 0
    let nestedResult = 'unset'
    runViewTransition(() => {
        nestedResult = runViewTransition(() => { inner++ }, { kind: 'tab', env })
    }, { kind: 'fade', env })
    env.transitions[0].invoke()
    assert.equal(inner, 1)
    assert.equal(nestedResult, undefined)
    assert.equal(env.transitions.length, 1, 'no second startViewTransition')
})

test('31. start watchdog: a callback not started after 150ms skips; a started one does not', () => {
    {
        const env = makeEnv()
        runViewTransition(() => {}, { kind: 'fade', env })
        const [transition] = env.transitions
        env.advance(149)
        assert.equal(transition.skips, 0)
        env.advance(1)
        assert.equal(transition.skips, 1)
    }
    {
        const env = makeEnv()
        runViewTransition(() => {}, { kind: 'fade', env })
        const [transition] = env.transitions
        env.advance(100)
        transition.invoke()
        env.advance(100)
        assert.equal(transition.skips, 0)
    }
})

test('32. overrun watchdog: longest pseudo animation + 150ms, 1000ms when none', async () => {
    const start = async (env) => {
        runViewTransition(() => {}, { kind: 'fade', env })
        const transition = env.transitions.at(-1)
        transition.invoke()
        transition.readyControl.resolve()
        await flushMicrotasks()
        return transition
    }
    {
        const env = makeEnv()
        env.animations = [pseudoAnimation(250), pseudoAnimation(200, '::view-transition-old(root)'), pseudoAnimation(5000, null)]
        const transition = await start(env)
        env.advance(399)
        assert.equal(transition.skips, 0)
        env.advance(1)
        assert.equal(transition.skips, 1)
    }
    {
        const env = makeEnv()
        env.animations = [pseudoAnimation(250)]
        const transition = await start(env)
        env.advance(260)
        transition.finishedControl.resolve()
        await flushMicrotasks()
        assert.equal(env.timers.size, 0, 'the timer is cleared')
        env.advance(2000)
        assert.equal(transition.skips, 0)
    }
    for (const removeGetAnimations of [false, true]) {
        const env = makeEnv()
        if (removeGetAnimations) delete env.root.getAnimations
        const transition = await start(env)
        env.advance(1149)
        assert.equal(transition.skips, 0)
        env.advance(1)
        assert.equal(transition.skips, 1)
    }
})

test('32b. update watchdog and update cap', async () => {
    {
        const env = makeEnv()
        runViewTransition(() => {}, { kind: 'fade', env })
        const [transition] = env.transitions
        env.advance(50)
        transition.invoke()
        env.advance(399)
        assert.equal(transition.skips, 0)
        env.advance(1)
        assert.equal(transition.skips, 1, 'ready never settled: skip 400ms after the callback start')
    }
    {
        const env = makeEnv()
        runViewTransition(() => {}, { kind: 'fade', env })
        const [transition] = env.transitions
        transition.invoke()
        env.advance(300)
        transition.readyControl.resolve()
        await flushMicrotasks()
        env.advance(500)
        assert.equal(transition.skips, 0)
    }
    {
        // Update cap: a navigation that never settles.
        const env = makeEnv()
        runViewTransition(() => new Promise(() => {}), { kind: 'tab', settle: true, env })
        const [transition] = env.transitions
        let done = false
        transition.invoke().then(() => { done = true })
        assert.equal(isViewTransitionUpdating(), true)
        env.advance(2999)
        await flushMicrotasks()
        assert.equal(done, false)
        env.advance(1)
        await flushMicrotasks()
        env.advance(0)
        await flushMicrotasks()
        assert.equal(done, true)
        assert.equal(isViewTransitionUpdating(), false)
    }
    {
        // A start-watchdog skip before the callback: no update watchdog when it starts.
        const env = makeEnv()
        runViewTransition(() => new Promise(() => {}), { kind: 'tab', settle: true, env })
        const [transition] = env.transitions
        env.advance(150)
        assert.equal(transition.skips, 1)
        await flushMicrotasks()
        transition.invoke()
        const delays = [...env.timers.values()].map((t) => t.at - env.now)
        assert.ok(!delays.includes(400), `no 400ms timer (queued: ${delays})`)
        // Let the capped update end, so the depth counter is back to 0 for the other tests.
        env.advance(3000)
        await flushMicrotasks()
        env.advance(0)
        await flushMicrotasks()
        assert.equal(isViewTransitionUpdating(), false)
    }
})

test('32c. supportsViewTransitions', () => {
    assert.equal(supportsViewTransitions(makeEnv()), true)
    assert.equal(supportsViewTransitions(makeEnv({ support: false })), false)
    assert.equal(supportsViewTransitions({}), false)
})
