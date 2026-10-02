// Toast entrances and exits (visual refresh step 5b,
// docs/plans/2026-09-28-overlay-motion-design.md §8, §14.5): toast-motion.css and its Notivue
// wiring in main.js, read as text.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

function declarations(body) {
    const out = {}
    for (const part of body.split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) out[match[1]] = collapse(match[2])
    }
    return out
}

/** Top-level blocks: { head, body } in source order (nested blocks kept in body). */
function topBlocks(css) {
    const blocks = []
    let i = 0
    while (i < css.length) {
        const open = css.indexOf('{', i)
        if (open === -1) break
        let depth = 1
        let j = open + 1
        while (j < css.length && depth > 0) {
            if (css[j] === '{') depth++
            else if (css[j] === '}') depth--
            j++
        }
        blocks.push({ head: collapse(css.slice(i, open)), body: css.slice(open + 1, j - 1) })
        i = j
    }
    return blocks
}

function splitTopLevel(text, separator = ',') {
    const parts = []
    let depth = 0
    let start = 0
    for (let i = 0; i < text.length; i++) {
        const c = text[i]
        if (c === '(') depth++
        else if (c === ')') depth--
        else if (c === separator && depth === 0) {
            parts.push(text.slice(start, i))
            start = i + 1
        }
    }
    parts.push(text.slice(start))
    return parts.map(collapse).filter(Boolean)
}

function animations(value) {
    return splitTopLevel(value).map((part) => {
        const list = splitTopLevel(part, ' ')
        return {
            name: list.find((t) => t.startsWith('twicc-')),
            duration: list.find((t) => /^\d+ms$/.test(t)),
            easing: list.find((t) => /^(var\(--motion-ease[\w-]*\)|ease(-in|-out|-in-out)?)$/.test(t)),
            forwards: list.includes('forwards'),
            reverse: list.includes('reverse'),
        }
    })
}

const blocks = topBlocks(stripComments(read('toast-motion.css')))
const block = (head) => {
    const found = blocks.filter((b) => b.head === head)
    assert.equal(found.length, 1, `exactly one "${head}" block`)
    return found[0]
}
const keyframes = Object.fromEntries(
    blocks.filter((b) => b.head.startsWith('@keyframes ')).map((b) => [b.head.slice('@keyframes '.length), topBlocks(b.body)]),
)
const keyframeProperties = (name) => {
    assert.ok(keyframes[name], `@keyframes ${name}`)
    return new Set(keyframes[name].flatMap((step) => Object.keys(declarations(step.body))))
}
const animationOf = (head) => animations(declarations(block(head).body).animation)

const MOVE = ['scale', 'translate']
const rules = blocks.filter((b) => !b.head.startsWith('@'))

test('keyframes: individual properties and --twicc-reveal, movement scaled by --motion-amount', () => {
    assert.deepEqual(Object.keys(keyframes).sort(), ['twicc-reveal', 'twicc-reveal-out', 'twicc-toast-in', 'twicc-toast-out'])
    for (const [name, steps] of Object.entries(keyframes)) {
        for (const step of steps) {
            for (const [property, value] of Object.entries(declarations(step.body))) {
                assert.ok(['opacity', 'translate', 'scale', '--twicc-reveal'].includes(property), `${name}: ${property}`)
                if (MOVE.includes(property)) assert.ok(value.includes('var(--motion-amount)'), `${name}: ${property} ${value}`)
            }
            assert.ok(!/(^|[;{\s])(transform|filter)\s*:/.test(step.body), `${name}: no transform, no filter`)
        }
    }
    assert.deepEqual([...keyframeProperties('twicc-toast-in')].sort(), MOVE, 'twicc-toast-in moves only')
    assert.deepEqual([...keyframeProperties('twicc-toast-out')].sort(), MOVE, 'twicc-toast-out moves only')
    assert.deepEqual(keyframes['twicc-reveal'].map((step) => [step.head, declarations(step.body)]), [['from', { '--twicc-reveal': '0' }]])
    assert.deepEqual(keyframes['twicc-reveal-out'].map((step) => [step.head, declarations(step.body)]), [['to', { '--twicc-reveal': '0' }]])
    assert.ok(!/(^|[;{\s])transform\s*:/.test(stripComments(read('toast-motion.css'))), 'no transform anywhere')
})

test('enter: the spring moves, twicc-reveal of the same duration on --motion-ease', () => {
    const [move, reveal] = animationOf('.twicc-toast-enter')
    assert.deepEqual([move.name, move.duration, move.easing], ['twicc-toast-in', '520ms', 'var(--motion-ease-spring)'])
    assert.deepEqual([reveal.name, reveal.duration, reveal.easing, reveal.forwards], ['twicc-reveal', '520ms', 'var(--motion-ease)', false])
})

test('leave and clear-all: ease-in, forwards; 220ms and 300ms', () => {
    const leave = animationOf('.twicc-toast-leave')
    assert.deepEqual(leave.map((a) => [a.name, a.duration, a.easing, a.forwards, a.reverse]), [
        ['twicc-toast-out', '220ms', 'ease-in', true, false],
        ['twicc-reveal-out', '220ms', 'ease-in', true, false],
    ])
    // Clear all: Notivue's class sits on the root list; the motion state goes on each container.
    assert.ok(!blocks.some((b) => b.head === '.twicc-toast-clear-all'), 'no animation on the root list')
    const clearAll = declarations(block('.twicc-toast-clear-all [data-notivue-container]').body)
    assert.deepEqual(animations(clearAll.animation).map((a) => [a.name, a.duration, a.easing, a.forwards]), [['twicc-reveal-out', '300ms', 'ease-in', true]])
    assert.equal(clearAll['--twicc-reveal-filter'], 'opacity(var(--twicc-reveal))')
})

test('the motion states declare --twicc-reveal-filter; the close button reads it', () => {
    const states = ['.twicc-toast-enter', '.twicc-toast-leave', '.twicc-toast-clear-all [data-notivue-container]']
    for (const b of rules) {
        const decls = declarations(b.body)
        if (!('--twicc-reveal-filter' in decls)) continue
        assert.equal(decls['--twicc-reveal-filter'], 'opacity(var(--twicc-reveal))', b.head)
        for (const selector of splitTopLevel(b.head)) assert.ok([...states, ':root.reduce-motion .Notivue__notification'].includes(selector), `${selector}: declares the motion state`)
    }
    for (const state of states) {
        const merged = Object.assign({}, ...rules.filter((b) => splitTopLevel(b.head).includes(state)).map((b) => declarations(b.body)))
        assert.equal(merged['--twicc-reveal-filter'], 'opacity(var(--twicc-reveal))', state)
    }
    assert.deepEqual(declarations(block('.Notivue__notification > .Notivue__close').body), { filter: 'var(--twicc-no-fade, var(--twicc-reveal-filter, none))' })
    const css = stripComments(read('toast-motion.css'))
    assert.ok(!/--glass-(bg|sticky-bg|tooltip-bg|settle)\s*:/.test(css), 'no opaque-while-moving state')
})

test('reduced motion: one rule, a 200ms reveal on .Notivue__notification', () => {
    const reduced = blocks.filter((b) => b.head.startsWith(':root.reduce-motion'))
    assert.equal(reduced.length, 1)
    assert.equal(reduced[0].head, ':root.reduce-motion .Notivue__notification')
    const decls = declarations(reduced[0].body)
    assert.deepEqual(animations(decls.animation).map((a) => [a.name, a.duration, a.easing]), [['twicc-reveal', '200ms', 'var(--motion-ease)']])
    assert.equal(decls['--twicc-reveal-filter'], 'opacity(var(--twicc-reveal))')
})

test('App.vue: toasts cast the live glass shadow', () => {
    assert.match(read('../App.vue'), /'--nv-shadow'\s*:\s*'var\(--glass-shadow-live\)'/)
})

test('main.js: Notivue gets the three classes, the CSS follows Notivue\'s, no animations.css', () => {
    const main = read('../main.js')
    const config = main.slice(main.indexOf('createNotivue({'), main.indexOf('app.use(notivue)'))
    assert.match(
        collapse(config),
        /animations: \{ enter: 'twicc-toast-enter', leave: 'twicc-toast-leave', clearAll: 'twicc-toast-clear-all',? \}/,
    )
    const imports = main.split('\n').map((l) => l.trim()).filter((l) => l.startsWith('import '))
    assert.ok(!imports.includes("import 'notivue/animations.css'"), 'notivue/animations.css is no longer imported')
    const notivueCss = imports.indexOf("import 'notivue/notification.css'")
    const toastCss = imports.indexOf("import './styles/toast-motion.css'")
    assert.ok(notivueCss >= 0 && toastCss > notivueCss, 'toast-motion.css after the Notivue CSS')
})
