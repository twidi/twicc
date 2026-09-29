// Run with: node --test src/utils/colorSchemeTransition.test.js (from the frontend dir)
// Color-scheme reveal (visual refresh step 5c, docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §5):
// the helper with a fake environment, and the wiring of the store and the entry points
// (file scans).
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

import {
    SCHEME_REVEAL_MS,
    effectiveSchemeFor,
    originFromElement,
    revealRadius,
    runSchemeTransition,
    setNextSchemeOrigin,
    takeNextSchemeOrigin,
    viewportCenter,
} from './colorSchemeTransition.js'

const here = dirname(fileURLToPath(import.meta.url))
const srcDir = join(here, '..')
const read = (rel) => readFileSync(join(srcDir, rel), 'utf8')

/** Fake env: manual timers, a matchMedia answering the given queries, an optional document. */
function makeEnv({ dark = false, reduced = false, support = true } = {}) {
    const env = { now: 0, timers: new Map(), nextId: 1, starts: 0, innerWidth: 300, innerHeight: 400 }
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
    env.matchMedia = (query) => ({
        matches: query === '(prefers-color-scheme: dark)' ? dark : query === '(prefers-reduced-motion: reduce)' ? reduced : false,
    })
    const classes = new Set()
    const properties = new Map()
    env.root = {
        classes,
        properties,
        classList: {
            add: (...n) => n.forEach((c) => classes.add(c)),
            remove: (...n) => n.forEach((c) => classes.delete(c)),
            [Symbol.iterator]: () => [...classes][Symbol.iterator](),
        },
        style: { setProperty: (n, v) => properties.set(n, v), removeProperty: (n) => properties.delete(n) },
    }
    env.document = { documentElement: env.root }
    if (support) {
        env.document.startViewTransition = (callback) => {
            env.starts++
            const never = new Promise(() => {})
            env.callback = callback
            return { ready: never, updateCallbackDone: never, finished: never, skipTransition() {} }
        }
    }
    return env
}

test('19. revealRadius covers the viewport plus 10% of its larger side', () => {
    assert.equal(revealRadius(0, 0, 300, 400), 540)
    assert.equal(revealRadius(150, 200, 300, 400), 290)
    assert.equal(SCHEME_REVEAL_MS, 650)
})

test('20. originFromElement, viewportCenter, effectiveSchemeFor', () => {
    const el = { getBoundingClientRect: () => ({ left: 10, top: 20, width: 100, height: 40 }) }
    assert.deepEqual(originFromElement(el), { x: 60, y: 40 })
    assert.equal(originFromElement(null), null)
    assert.deepEqual(viewportCenter(makeEnv()), { x: 150, y: 200 })
    assert.equal(effectiveSchemeFor('dark', makeEnv()), 'dark')
    assert.equal(effectiveSchemeFor('light', makeEnv({ dark: true })), 'light')
    assert.equal(effectiveSchemeFor('system', makeEnv({ dark: true })), 'dark')
    assert.equal(effectiveSchemeFor('system', makeEnv({ dark: false })), 'light')
})

test('21. runSchemeTransition: circle with an origin, fade otherwise, nothing when not animated', () => {
    {
        const env = makeEnv()
        let applied = 0
        runSchemeTransition(() => { applied++ }, { origin: { x: 0, y: 0 }, env })
        assert.equal(env.starts, 1)
        assert.ok(env.root.classes.has('twicc-vt-circle'))
        assert.equal(env.root.properties.get('--twicc-scheme-x'), '0px')
        assert.equal(env.root.properties.get('--twicc-scheme-y'), '0px')
        assert.equal(env.root.properties.get('--twicc-scheme-r'), '540px')
        assert.equal(applied, 0)
        env.callback()
        assert.equal(applied, 1)
    }
    for (const [label, options, envOptions] of [
        ['no origin', {}, {}],
        ['reduced motion', { origin: { x: 5, y: 5 } }, { reduced: true }],
    ]) {
        const env = makeEnv(envOptions)
        runSchemeTransition(() => {}, { ...options, env })
        assert.ok(env.root.classes.has('twicc-vt-fade'), label)
        assert.ok(!env.root.classes.has('twicc-vt-circle'), label)
        assert.equal(env.root.properties.size, 0, `${label}: no properties`)
    }
    {
        const env = makeEnv()
        let applied = 0
        runSchemeTransition(() => { applied++ }, { origin: { x: 1, y: 1 }, animate: false, env })
        assert.equal(applied, 1, 'applied synchronously')
        assert.equal(env.starts, 0)
    }
})

test('22. the next-scheme origin is one-shot and expires after the task', () => {
    const env = makeEnv()
    setNextSchemeOrigin({ x: 1, y: 2 }, env)
    assert.deepEqual(takeNextSchemeOrigin(), { x: 1, y: 2 })
    assert.equal(takeNextSchemeOrigin(), null)
    env.advance(0)
    assert.equal(takeNextSchemeOrigin(), null)

    setNextSchemeOrigin({ x: 3, y: 4 }, env)
    env.advance(0)
    assert.equal(takeNextSchemeOrigin(), null, 'not taken: cleared by the timeout')
})

/** The text from `start` to its matching closing brace (brace count from the first `{`). */
function braceBlock(text, start) {
    const open = text.indexOf('{', start)
    let depth = 0
    for (let i = open; i < text.length; i++) {
        if (text[i] === '{') depth++
        else if (text[i] === '}' && --depth === 0) return text.slice(start, i + 1)
    }
    throw new Error('unbalanced braces')
}

const count = (text, needle) => text.split(needle).length - 1

test('23. stores/settings.js initSettings: one scheme watcher through runSchemeTransition, the OS handler', () => {
    const source = read('stores/settings.js')
    const lines = source.split('\n')
    const first = lines.findIndex((l) => l.startsWith('export function initSettings()'))
    assert.ok(first >= 0)
    const last = lines.findIndex((l, i) => i > first && l === '}')
    let body = lines.slice(first, last + 1).join('\n')

    const defAt = body.indexOf('const applyStoreColorScheme =')
    assert.ok(defAt >= 0, 'applyStoreColorScheme is defined in initSettings')
    const definition = braceBlock(body, defAt)
    assert.ok(definition.includes('setColorSchemeOnDom(store.colorScheme)'))
    assert.ok(definition.includes('store._updateEffectiveColorScheme()'))
    body = body.slice(0, defAt) + body.slice(defAt + definition.length)

    const watchAt = body.indexOf('watch(() => store.colorScheme')
    assert.ok(watchAt >= 0, 'the scheme watcher')
    const watcher = body.slice(watchAt, body.indexOf('})', watchAt) + 2)
    assert.ok(watcher.includes('runSchemeTransition(applyStoreColorScheme'))
    assert.ok(watcher.includes('takeNextSchemeOrigin()'))
    assert.ok(watcher.includes('effectiveSchemeFor(store.colorScheme) !== store._effectiveColorScheme'))
    assert.ok(body.includes('setSystemSchemeChangeHandler('))

    assert.equal(count(body, 'setColorSchemeOnDom('), 1, 'one bare DOM apply')
    assert.ok(body.indexOf('setColorSchemeOnDom(') < watchAt, 'before the watcher')
    assert.equal(count(body, 'store._updateEffectiveColorScheme()'), 1, 'one bare effective-scheme update')
    assert.ok(body.indexOf('store._updateEffectiveColorScheme()') > watchAt + watcher.length, 'after the watcher')
    assert.ok(!body.includes("addEventListener('change'"), 'no prefers-color-scheme listener left')
})

test('24. entry points: compare with the current scheme, set the origin before the change', () => {
    const popover = read('components/app/SettingsPopover.vue')
    const handler = braceBlock(popover, popover.indexOf('function onColorSchemeChange('))
    const commands = read('commands/staticCommands.js')
    const change = braceBlock(commands, commands.indexOf('function changeColorScheme('))
    for (const [label, body, current] of [
        ['SettingsPopover onColorSchemeChange', handler, 'store.colorScheme'],
        ['staticCommands changeColorScheme', change, 'settings.colorScheme'],
    ]) {
        assert.ok(body.includes(`!== ${current}`), `${label}: compares with the current scheme`)
        const originAt = body.indexOf('setNextSchemeOrigin(')
        assert.ok(originAt >= 0, `${label}: setNextSchemeOrigin(`)
        assert.ok(originAt < body.indexOf('setColorScheme('), `${label}: the origin comes first`)
    }
    assert.ok(handler.includes('originFromElement(event.target)'))
    assert.ok(change.includes('viewportCenter()'))
    const schemeItems = commands.slice(commands.indexOf("id: 'display.color-scheme'"), commands.indexOf("id: 'display.mode'"))
    assert.equal(count(schemeItems, 'changeColorScheme('), 3, 'the three palette items use it')
    assert.ok(!schemeItems.includes('settings.setColorScheme('))
})

test('26. only utils/viewTransition.js names startViewTransition', () => {
    const files = readdirSync(srcDir, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile() && /\.(js|vue)$/.test(e.name) && !e.name.endsWith('.test.js'))
        .map((e) => join(e.parentPath ?? e.path, e.name))
    const users = files.filter((file) => readFileSync(file, 'utf8').includes('startViewTransition'))
        .map((file) => relative(srcDir, file))
    assert.deepEqual(users, ['utils/viewTransition.js'])
})
