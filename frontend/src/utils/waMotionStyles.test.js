// Run with: node --test src/utils/waMotionStyles.test.js (from the frontend dir)
// Overlay entrances and exits (visual refresh step 5b,
// docs/plans/2026-09-28-overlay-motion-design.md §4, §5, §14): the install into Lit's
// elementStyles, the CSS invariants of every sheet, and guards on the Web Awesome, Lit and
// Notivue internals the design relies on.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { installWaMotionStyles, WA_MOTION_STYLES } from './waMotionStyles.js'

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

function fakeRegistry(definitions) {
    return { get: (tag) => definitions[tag] }
}

const fakeSheet = (css) => ({ css })

test('install appends one sheet per defined tag, last in elementStyles, once', () => {
    const own = { own: true }
    const definitions = {}
    for (const tag of Object.keys(WA_MOTION_STYLES)) {
        definitions[tag] = class {}
        definitions[tag].elementStyles = [own]
    }
    const patched = installWaMotionStyles({ registry: fakeRegistry(definitions), createSheet: fakeSheet })
    assert.deepEqual(patched, Object.keys(WA_MOTION_STYLES))
    for (const [tag, css] of Object.entries(WA_MOTION_STYLES)) {
        const styles = definitions[tag].elementStyles
        assert.equal(styles.length, 2, tag)
        assert.equal(styles[0], own, `${tag}: Web Awesome's styles first`)
        assert.deepEqual(styles[1], { css }, `${tag}: our sheet last`)
    }

    assert.deepEqual(installWaMotionStyles({ registry: fakeRegistry(definitions), createSheet: fakeSheet }), [])
    for (const tag of Object.keys(WA_MOTION_STYLES)) assert.equal(definitions[tag].elementStyles.length, 2, `${tag}: idempotent`)
})

test('install skips an undefined tag and a non-array elementStyles', () => {
    const dialog = class {}
    dialog.elementStyles = []
    const popup = class {}
    popup.elementStyles = undefined
    const patched = installWaMotionStyles({ registry: fakeRegistry({ 'wa-dialog': dialog, 'wa-popup': popup }), createSheet: fakeSheet })
    assert.deepEqual(patched, ['wa-dialog'])
    assert.equal(dialog.elementStyles.length, 1)
    assert.equal(popup.elementStyles, undefined)
})

// ---------------------------------------------------------------------------
// CSS reader
// ---------------------------------------------------------------------------

const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

function declarations(body) {
    const list = []
    for (const part of body.split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) list.push([match[1], collapse(match[2])])
    }
    return list
}

/** { type: 'rule', selector, decls, index, ancestors } and { type: 'keyframes', name, decls } in source order. */
function parseSheet(css) {
    const rules = []
    const keyframes = []
    let index = 0
    const walk = (text, ancestors) => {
        let i = 0
        while (i < text.length) {
            const open = text.indexOf('{', i)
            if (open === -1) break
            const head = collapse(text.slice(i, open))
            let depth = 1
            let j = open + 1
            while (j < text.length && depth > 0) {
                if (text[j] === '{') depth++
                else if (text[j] === '}') depth--
                j++
            }
            const body = text.slice(open + 1, j - 1)
            if (head.startsWith('@keyframes')) {
                const steps = []
                walk2(body, steps)
                keyframes.push({ name: head.slice('@keyframes'.length).trim(), steps })
            } else if (head.startsWith('@')) walk(body, [...ancestors, head])
            else rules.push({ selector: head, decls: declarations(body), index: index++, ancestors })
            i = j
        }
    }
    const walk2 = (text, steps) => {
        for (const match of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) steps.push({ step: collapse(match[1]), decls: declarations(match[2]) })
    }
    walk(stripComments(css), [])
    return { rules, keyframes }
}

/** Split on commas at parenthesis depth 0. */
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

/** Tokens of one animation (whitespace at depth 0). */
const tokens = (text) => splitTopLevel(text, ' ')

/** `animation` shorthand → [{ name, duration, easing, reverse, forwards }]. */
function animations(value) {
    return splitTopLevel(value).map((part) => {
        const list = tokens(part)
        const easing = list.find((t) => /^(var\(--motion-ease[\w-]*\)|ease(-in|-out|-in-out)?|linear)$/.test(t)) ?? null
        return {
            name: list.find((t) => t.startsWith('twicc-')) ?? null,
            duration: list.find((t) => /^\d+ms$/.test(t)) ?? null,
            easing,
            reverse: list.includes('reverse'),
            forwards: list.includes('forwards'),
        }
    })
}

/** Specificity [ids, classes, elements] of the simple selectors used in these sheets. */
function specificity(selector) {
    let ids = 0
    let classes = 0
    let elements = 0
    let rest = selector
    // :host(X): one pseudo-class plus its argument.
    rest = rest.replace(/:host\(([^)]*)\)/g, (_, inner) => {
        const [a, b, c] = specificity(inner)
        ids += a
        classes += b + 1
        elements += c
        return ' '
    })
    rest = rest.replace(/::[\w-]+(\([^)]*\))?/g, () => {
        elements++
        return ' '
    })
    rest = rest.replace(/\[[^\]]*\]/g, () => {
        classes++
        return ' '
    })
    rest = rest.replace(/#[\w-]+/g, () => {
        ids++
        return ' '
    })
    rest = rest.replace(/[.:][\w-]+/g, () => {
        classes++
        return ' '
    })
    for (const word of rest.split(/[\s>+~]+/)) if (/^[a-z][\w-]*$/i.test(word)) elements++
    return [ids, classes, elements]
}
const compareSpecificity = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]

const SHEETS = Object.fromEntries(Object.entries(WA_MOTION_STYLES).map(([tag, css]) => [tag, parseSheet(css)]))
const ALL = Object.entries(SHEETS).flatMap(([tag, sheet]) => sheet.rules.map((rule) => ({ tag, rule, sheet })))
const animated = ALL.filter(({ rule }) => rule.decls.some(([p]) => p.startsWith('animation')))
const decl = (rule, property) => rule.decls.find(([p]) => p === property)?.[1]
const isHide = (selector) => /\.hide(-with-scale)?\b/.test(selector)
const isShow = (selector) => /\.show(-with-scale)?\b/.test(selector)
const inReducedMedia = (rule) => rule.ancestors.some((a) => /^@media \(prefers-reduced-motion: ?reduce\)$/.test(a))
const showSelectorOf = (hide) => hide.replace(/\.hide-with-scale\b/, '.show-with-scale').replace(/\.hide\b/, '.show')

function rule(tag, selector) {
    const found = SHEETS[tag].rules.filter((r) => r.selector === selector && !inReducedMedia(r))
    assert.equal(found.length, 1, `${tag}: exactly one rule "${selector}"`)
    return found[0]
}
const keyframeOf = (sheet, name) => sheet.keyframes.find((k) => k.name === name)
const keyframeProperties = (keyframe) => new Set(keyframe.steps.flatMap((s) => s.decls.map(([p]) => p)))

// ---------------------------------------------------------------------------
// CSS invariants
// ---------------------------------------------------------------------------

test('the sheets cover the six Web Awesome elements', () => {
    assert.deepEqual(Object.keys(WA_MOTION_STYLES).sort(), ['wa-dialog', 'wa-dropdown', 'wa-dropdown-item', 'wa-popover', 'wa-popup', 'wa-select'])
})

test('keyframes: twicc-* names, individual properties and --twicc-reveal, movement scaled by --motion-amount', () => {
    for (const [tag, sheet] of Object.entries(SHEETS)) {
        for (const keyframe of sheet.keyframes) {
            assert.match(keyframe.name, /^twicc-/, `${tag}: ${keyframe.name}`)
            for (const step of keyframe.steps) {
                for (const [property, value] of step.decls) {
                    assert.ok(['opacity', 'translate', 'scale', '--twicc-reveal'].includes(property), `${tag} ${keyframe.name}: ${property}`)
                    if (!['opacity', '--twicc-reveal'].includes(property)) assert.ok(value.includes('var(--motion-amount)'), `${tag} ${keyframe.name}: ${property} ${value}`)
                }
            }
        }
    }
})

test('no transform property anywhere, no filter in a keyframe', () => {
    for (const [tag, css] of Object.entries(WA_MOTION_STYLES)) {
        const stripped = stripComments(css)
        assert.ok(!/(^|[;{\s])transform\s*:/.test(stripped), `${tag}: a transform declaration`)
        for (const keyframe of SHEETS[tag].keyframes) {
            assert.ok(!keyframeProperties(keyframe).has('filter'), `${tag} ${keyframe.name}: filter`)
            assert.ok(!keyframeProperties(keyframe).has('transform'), `${tag} ${keyframe.name}: transform`)
        }
    }
})

test('every animation rule declares the full animation shorthand only', () => {
    assert.ok(animated.length > 0)
    for (const { tag, rule: r } of animated) {
        const properties = r.decls.map(([p]) => p).filter((p) => p.startsWith('animation'))
        assert.deepEqual(properties, ['animation'], `${tag} ${r.selector}`)
    }
})

test('hide rules replay with ease-out, reverse, forwards; show rules move with the spring or ease-out', () => {
    for (const { tag, rule: r } of animated) {
        const list = animations(decl(r, 'animation'))
        if (isHide(r.selector)) {
            for (const a of list) {
                assert.equal(a.easing, 'ease-out', `${tag} ${r.selector}: ${a.name}`)
                assert.ok(a.reverse && a.forwards, `${tag} ${r.selector}: ${a.name} reverse forwards`)
            }
        } else if (isShow(r.selector)) {
            const [movement] = list
            assert.ok(['var(--motion-ease-spring)', 'var(--motion-ease-out)'].includes(movement.easing), `${tag} ${r.selector}: ${movement.easing}`)
            for (const a of list) assert.ok(!a.reverse && !a.forwards, `${tag} ${r.selector}: ${a.name} plays forward`)
        } else {
            assert.match(r.selector, /\.pulse$/, `${tag}: unexpected animation rule ${r.selector}`)
            for (const a of list) assert.equal(a.easing, 'var(--motion-ease)', `${tag} ${r.selector}`)
        }
    }
})

test('a spring keyframe holds no opacity and is paired with twicc-reveal of the same duration on --motion-ease', () => {
    let springs = 0
    for (const { tag, rule: r, sheet } of animated) {
        const list = animations(decl(r, 'animation'))
        for (const a of list.filter((x) => x.easing === 'var(--motion-ease-spring)')) {
            springs++
            assert.ok(!keyframeProperties(keyframeOf(sheet, a.name)).has('opacity'), `${tag} ${a.name}: opacity in a spring keyframe`)
            const reveal = list.find((x) => x.name === 'twicc-reveal')
            assert.ok(reveal, `${tag} ${r.selector}: no twicc-reveal next to the spring`)
            assert.equal(reveal.duration, a.duration, `${tag} ${r.selector}: reveal duration`)
            assert.equal(reveal.easing, 'var(--motion-ease)', `${tag} ${r.selector}: reveal easing`)
        }
    }
    assert.equal(springs, 3, 'dialog, drop-in dialog, popover')
})

test('each hide rule names its show rule\'s keyframes, comes after it, with at least its specificity', () => {
    const hides = animated.filter(({ rule: r }) => isHide(r.selector))
    assert.equal(hides.length, 10)
    for (const { tag, rule: hide } of hides) {
        const show = rule(tag, showSelectorOf(hide.selector))
        assert.deepEqual(
            animations(decl(hide, 'animation')).map((a) => a.name),
            animations(decl(show, 'animation')).map((a) => a.name),
            `${tag} ${hide.selector}: same keyframes, same order`,
        )
        assert.ok(hide.index > show.index, `${tag} ${hide.selector}: after its show rule`)
        assert.ok(compareSpecificity(specificity(hide.selector), specificity(show.selector)) >= 0, `${tag} ${hide.selector}: specificity`)
        // Every show rule of the sheet the hide rule must beat while both classes are present.
        for (const other of SHEETS[tag].rules.filter((r) => isShow(r.selector) && showSelectorOf(hide.selector) === r.selector)) {
            assert.ok(hide.index > other.index)
        }
    }
    // The drop-in dialog hide must beat the more specific drop-in show, not only .dialog.show.
    const dropHide = rule('wa-dialog', ':host(.motion-drop) .dialog.hide')
    const dropShow = rule('wa-dialog', ':host(.motion-drop) .dialog.show')
    assert.ok(dropHide.index > dropShow.index)
    assert.ok(compareSpecificity(specificity(dropHide.selector), specificity(dropShow.selector)) >= 0)
})

test('pulse: a scale nudge, and a --twicc-reveal dip that only shows when --motion-amount is 0', () => {
    const sheet = SHEETS['wa-dialog']
    assert.equal(animations(decl(rule('wa-dialog', '.dialog.pulse'), 'animation'))[0].name, 'twicc-pulse')
    assert.deepEqual([...keyframeProperties(keyframeOf(sheet, 'twicc-pulse'))], ['scale', '--twicc-reveal'])
    assert.deepEqual(keyframeOf(sheet, 'twicc-pulse').steps, [{ step: '50%', decls: [
        ['scale', 'calc(1 + 0.02 * var(--motion-amount))'],
        ['--twicc-reveal', 'calc(1 - 0.15 * (1 - var(--motion-amount)))'],
    ] }])
    assert.deepEqual(rule('wa-dialog', '.dialog.pulse > *').decls, [['filter', 'var(--twicc-no-fade, opacity(var(--twicc-reveal)))']])
    assert.ok(!sheet.rules.some((r) => inReducedMedia(r)), 'no media query: a shadow tree cannot see the html class, the property carries it')
    assert.ok(!keyframeOf(sheet, 'twicc-pulse-dim'), 'no separate dim keyframes')
})

test('submenus fade without scaling (the safe triangle keeps working)', () => {
    const sheet = SHEETS['wa-dropdown-item']
    for (const selector of ['#submenu.show', '#submenu.hide']) {
        assert.deepEqual(animations(decl(rule('wa-dropdown-item', selector), 'animation')).map((a) => a.name), ['twicc-reveal'])
    }
    for (const keyframe of sheet.keyframes) {
        const properties = keyframeProperties(keyframe)
        assert.ok(!properties.has('scale') && !properties.has('translate'), keyframe.name)
    }
})

test('durations: entrances and exits of §5.6', () => {
    const expected = [
        ['wa-dialog', '.dialog.show', '320ms'],
        ['wa-dialog', '.dialog.show::backdrop', '320ms'],
        ['wa-dialog', '.dialog.hide', '160ms'],
        ['wa-dialog', '.dialog.hide::backdrop', '160ms'],
        ['wa-dialog', ':host(.motion-drop) .dialog.show', '260ms'],
        ['wa-dialog', ':host(.motion-drop) .dialog.show::backdrop', '260ms'],
        ['wa-dialog', ':host(.motion-drop) .dialog.hide', '160ms'],
        ['wa-dialog', ':host(.motion-drop) .dialog.hide::backdrop', '160ms'],
        ['wa-dialog', '.dialog.pulse', '250ms'],
        ['wa-dropdown', '#menu.show', '180ms'],
        ['wa-dropdown', '#menu.hide', '50ms'],
        ['wa-dropdown-item', '#submenu.show', '180ms'],
        ['wa-dropdown-item', '#submenu.hide', '50ms'],
        ['wa-popup', ':host(.select) .popup.show', '180ms'],
        ['wa-popup', ':host(.select) .popup.hide', '100ms'],
        ['wa-popup', ':host(.color-popup) .popup.show-with-scale', '180ms'],
        ['wa-popup', ':host(.color-popup) .popup.hide-with-scale', '100ms'],
        ['wa-popup', ':host(.popover) .popup.show-with-scale', '260ms'],
        ['wa-popup', ':host(.popover) .popup.hide-with-scale', '100ms'],
        ['wa-popup', ':host(.tooltip) .popup.show-with-scale', '160ms'],
        ['wa-popup', ':host(.tooltip) .popup.hide-with-scale', '100ms'],
    ]
    for (const [tag, selector, duration] of expected) {
        for (const a of animations(decl(rule(tag, selector), 'animation'))) assert.equal(a.duration, duration, `${tag} ${selector}: ${a.name}`)
    }
    const listed = new Set(expected.map(([tag, selector]) => `${tag} ${selector}`))
    for (const { tag, rule: r } of animated.filter(({ rule: x }) => !inReducedMedia(x))) {
        assert.ok(listed.has(`${tag} ${r.selector}`), `unlisted animation rule ${tag} ${r.selector}`)
    }
})

test('dialogs: the backdrop lasts as long as the dialog in each state, and fades only', () => {
    for (const base of ['.dialog.show', '.dialog.hide', ':host(.motion-drop) .dialog.show', ':host(.motion-drop) .dialog.hide']) {
        const dialog = animations(decl(rule('wa-dialog', base), 'animation'))
        const backdrop = animations(decl(rule('wa-dialog', `${base}::backdrop`), 'animation'))
        assert.deepEqual(backdrop.map((a) => a.name), ['twicc-fade'], base)
        for (const a of dialog) assert.equal(a.duration, backdrop[0].duration, `${base}: ${a.name}`)
    }
    assert.equal(animations(decl(rule('wa-dialog', '.dialog.show::backdrop'), 'animation'))[0].easing, 'var(--motion-ease-out)')
})

test('the color picker panel animates like a menu, from its placement', () => {
    for (const selector of [':host(.color-popup) .popup.show-with-scale', ':host(.color-popup) .popup.hide-with-scale']) {
        assert.deepEqual(animations(decl(rule('wa-popup', selector), 'animation')).map((a) => a.name), ['twicc-pop'])
    }
    const origins = {
        'bottom-start': 'left top',
        'bottom-end': 'right top',
        'top-start': 'left bottom',
        'top-end': 'right bottom',
    }
    for (const [placement, origin] of Object.entries(origins)) {
        const r = rule('wa-popup', `:host(.color-popup[data-current-placement='${placement}']) .popup`)
        assert.deepEqual(r.decls, [['transform-origin', origin]])
    }
})

test('popovers grow from their arrow: four origin rules on data-glass-arrow', () => {
    const origins = {
        bottom: 'var(--popover-arrow-center, 50%) bottom',
        top: 'var(--popover-arrow-center, 50%) top',
        right: 'right var(--popover-arrow-center, 50%)',
        left: 'left var(--popover-arrow-center, 50%)',
    }
    const sheet = SHEETS['wa-popover']
    assert.equal(sheet.rules.length, 5, 'four origin rules and the content rule')
    assert.equal(sheet.rules.filter((r) => r.decls.some(([p]) => p === 'transform-origin')).length, 4)
    for (const [side, origin] of Object.entries(origins)) {
        const r = rule('wa-popover', `:host([data-glass-arrow='${side}']) .popover::part(popup)`)
        assert.deepEqual(r.decls, [['transform-origin', origin]])
    }
})

// ---------------------------------------------------------------------------
// The glass blur stays on while overlays move (§14)
// ---------------------------------------------------------------------------

/** The glass movers: [tag, show selector, hide selector]. */
const GLASS_MOVERS = [
    ['wa-dialog', '.dialog.show', '.dialog.hide'],
    ['wa-dialog', ':host(.motion-drop) .dialog.show', ':host(.motion-drop) .dialog.hide'],
    ['wa-dropdown', '#menu.show', '#menu.hide'],
    ['wa-dropdown-item', '#submenu.show', '#submenu.hide'],
    ['wa-popup', ':host(.select) .popup.show', ':host(.select) .popup.hide'],
    ['wa-popup', ':host(.popover) .popup.show-with-scale', ':host(.popover) .popup.hide-with-scale'],
]

test('glass movers only move: their movement is paired with twicc-reveal (§14.3 easing rule)', () => {
    for (const [tag, showSelector, hideSelector] of GLASS_MOVERS) {
        const sheet = SHEETS[tag]
        const show = animations(decl(rule(tag, showSelector), 'animation'))
        const reveal = show.find((a) => a.name === 'twicc-reveal')
        assert.ok(reveal, `${tag} ${showSelector}: twicc-reveal`)
        assert.equal(show[show.length - 1], reveal, `${tag} ${showSelector}: the reveal comes last`)
        const movement = show.filter((a) => a !== reveal)
        assert.ok(movement.length <= 1, `${tag} ${showSelector}: one movement at most`)
        if (movement.length) {
            assert.equal(reveal.duration, movement[0].duration, `${tag} ${showSelector}: same duration`)
            const spring = movement[0].easing === 'var(--motion-ease-spring)'
            assert.equal(reveal.easing, spring ? 'var(--motion-ease)' : 'var(--motion-ease-out)', `${tag} ${showSelector}: reveal easing`)
        } else {
            assert.equal(reveal.easing, 'var(--motion-ease-out)', `${tag} ${showSelector}: a lone reveal eases out`)
        }
        const hide = animations(decl(rule(tag, hideSelector), 'animation'))
        assert.deepEqual(hide.map((a) => a.name), show.map((a) => a.name), `${tag} ${hideSelector}`)
        for (const a of [...show, ...hide]) {
            assert.ok(!keyframeProperties(keyframeOf(sheet, a.name)).has('opacity'), `${tag} ${a.name}: no opacity on a glass mover`)
        }
    }
})

test('twicc-reveal and twicc-pop-move keyframes; the color picker keeps twicc-pop', () => {
    for (const [tag, sheet] of Object.entries(SHEETS)) {
        const reveal = keyframeOf(sheet, 'twicc-reveal')
        if (reveal) assert.deepEqual(reveal.steps, [{ step: 'from', decls: [['--twicc-reveal', '0']] }], tag)
        const popMove = keyframeOf(sheet, 'twicc-pop-move')
        if (popMove) assert.deepEqual(popMove.steps, [{ step: 'from', decls: [['scale', 'calc(1 - 0.06 * var(--motion-amount))']] }], tag)
    }
    for (const selector of ['#menu.show', '#menu.hide']) {
        assert.equal(animations(decl(rule('wa-dropdown', selector), 'animation'))[0].name, 'twicc-pop-move', selector)
    }
    for (const selector of [':host(.select) .popup.show', ':host(.select) .popup.hide']) {
        assert.equal(animations(decl(rule('wa-popup', selector), 'animation'))[0].name, 'twicc-pop-move', selector)
    }
    assert.deepEqual([...keyframeProperties(keyframeOf(SHEETS['wa-popup'], 'twicc-pop'))].sort(), ['opacity', 'scale'])
})

test('keyframe inventory per sheet (§14.4): opacity only in the tooltip, color picker and veil fades', () => {
    const inventory = {
        'wa-dialog': ['twicc-dialog', 'twicc-drop', 'twicc-fade', 'twicc-reveal', 'twicc-pulse'],
        'wa-dropdown': ['twicc-pop-move', 'twicc-reveal'],
        'wa-dropdown-item': ['twicc-reveal'],
        'wa-popover': [],
        'wa-popup': ['twicc-pop', 'twicc-pop-move', 'twicc-grow', 'twicc-reveal', 'twicc-tip'],
        'wa-select': [],
    }
    for (const [tag, names] of Object.entries(inventory)) {
        assert.deepEqual(SHEETS[tag].keyframes.map((k) => k.name).sort(), [...names].sort(), tag)
    }
    const withOpacity = Object.entries(SHEETS).flatMap(([tag, sheet]) =>
        sheet.keyframes.filter((k) => keyframeProperties(k).has('opacity')).map((k) => `${tag} ${k.name}`))
    assert.deepEqual(withOpacity.sort(), ['wa-dialog twicc-fade', 'wa-popup twicc-pop', 'wa-popup twicc-tip'])
    // twicc-fade only animates the dialog's ::backdrop (the veil).
    for (const { rule: r } of animated.filter(({ tag }) => tag === 'wa-dialog')) {
        if (animations(decl(r, 'animation')).some((a) => a.name === 'twicc-fade')) assert.match(r.selector, /::backdrop$/, r.selector)
    }
})

test('content fade: direct rules on dialog, menu and submenu', () => {
    const direct = [
        ['wa-dialog', '.dialog.show > *, .dialog.hide > *'],
        ['wa-dropdown', '#menu.show ::slotted(*), #menu.hide ::slotted(*)'],
        ['wa-dropdown-item', '#submenu.show ::slotted(*), #submenu.hide ::slotted(*)'],
    ]
    for (const [tag, selector] of direct) {
        assert.deepEqual(rule(tag, selector).decls, [['filter', 'var(--twicc-no-fade, opacity(var(--twicc-reveal)))']], `${tag} ${selector}`)
    }
})

test('content fade: inherited --twicc-reveal-filter, set by the popover and select motion states only', () => {
    assert.deepEqual(rule('wa-popover', '.body ::slotted(*)').decls, [['filter', 'var(--twicc-no-fade, var(--twicc-reveal-filter, none))']])
    const selectContent = SHEETS['wa-select'].rules
    assert.equal(selectContent.length, 1, 'wa-select: one rule')
    assert.equal(selectContent[0].selector, ':host(:not(.glass-listbox-direct)) .listbox ::slotted(*)', 'the direct list box is excluded')
    assert.deepEqual(selectContent[0].decls, [['filter', 'var(--twicc-no-fade, var(--twicc-reveal-filter, none))']])

    const states = [
        ':host(.popover) .popup.show-with-scale',
        ':host(.popover) .popup.hide-with-scale',
        ':host(.select) .popup.show',
        ':host(.select) .popup.hide',
    ]
    const carriers = SHEETS['wa-popup'].rules.filter((r) => r.decls.some(([p]) => p === '--twicc-reveal-filter'))
    assert.equal(carriers.length, 1, 'one motion-state rule')
    assert.deepEqual(splitTopLevel(carriers[0].selector), states)
    assert.deepEqual(carriers[0].decls, [['--twicc-reveal-filter', 'opacity(var(--twicc-reveal))']])
    for (const [tag, sheet] of Object.entries(SHEETS)) {
        if (tag === 'wa-popup') continue
        assert.ok(!sheet.rules.some((r) => r.decls.some(([p]) => p === '--twicc-reveal-filter')), `${tag}: no motion state`)
    }
})

test('tooltips keep their opacity animation (§14.4 exception)', () => {
    assert.deepEqual(rule('wa-popup', ':host(.tooltip) .popup.show-with-scale').decls, [['animation', 'twicc-tip 160ms var(--motion-ease-out)']])
    assert.deepEqual(rule('wa-popup', ':host(.tooltip) .popup.hide-with-scale').decls, [['animation', 'twicc-tip 100ms ease-out reverse forwards']])
    assert.deepEqual(keyframeOf(SHEETS['wa-popup'], 'twicc-tip').steps, [
        { step: 'from', decls: [['opacity', '0'], ['scale', 'calc(1 - 0.04 * var(--motion-amount))']] },
    ])
})

test('each sheet declares every keyframe it references, once', () => {
    for (const [tag, sheet] of Object.entries(SHEETS)) {
        const declared = sheet.keyframes.map((k) => k.name)
        assert.equal(new Set(declared).size, declared.length, `${tag}: a keyframe declared twice`)
        for (const r of sheet.rules) {
            const value = decl(r, 'animation')
            if (!value) continue
            for (const a of animations(value)) assert.ok(declared.includes(a.name), `${tag}: ${a.name} not declared`)
        }
    }
})

// ---------------------------------------------------------------------------
// Guards: Web Awesome, Lit, Notivue (re-read the design when one fails)
// ---------------------------------------------------------------------------

const require = createRequire(import.meta.url)
const waRoot = dirname(require.resolve('@awesome.me/webawesome/package.json'))
const nodeModules = join(dirname(fileURLToPath(import.meta.url)), '../../node_modules')
const chunksDir = join(waRoot, 'dist/chunks')
const chunks = readdirSync(chunksDir).filter((f) => f.endsWith('.js')).map((f) => readFileSync(join(chunksDir, f), 'utf8')).join('\n')

test('Web Awesome guard: version 3.3.1', () => {
    const pkg = JSON.parse(readFileSync(join(waRoot, 'package.json'), 'utf8'))
    assert.equal(pkg.version, '3.3.1', 'Web Awesome upgraded: re-check docs/plans/2026-09-28-overlay-motion-design.md §2')
})

test('Web Awesome guard: animateWithClass waits on animationend / animationcancel / getAnimations', () => {
    const start = chunks.indexOf('function animateWithClass(')
    assert.ok(start >= 0, 'animateWithClass moved')
    const body = chunks.slice(start, chunks.indexOf('\n}\n', start))
    for (const word of ['animationend', 'animationcancel', 'getAnimations']) assert.ok(body.includes(word), `animateWithClass: ${word}`)
})

test('Web Awesome guard: the animated elements and their classes', () => {
    const calls = [
        'animateWithClass(this.dialog, "show")',
        'animateWithClass(this.dialog, "hide")',
        'animateWithClass(this.menu, "show")',
        'animateWithClass(this.menu, "hide")',
        'animateWithClass(submenu, "show")',
        'animateWithClass(submenu, "hide")',
        'animateWithClass(this.popup.popup, "show-with-scale")',
        'animateWithClass(this.popup.popup, "hide-with-scale")',
        'animateWithClass(this.popup.popup, "show")',
        'animateWithClass(this.popup.popup, "hide")',
    ]
    for (const call of calls) assert.ok(chunks.includes(call), `moved: ${call}`)
    for (const hostClass of ['popover: true', 'tooltip: true', 'select: true']) assert.ok(chunks.includes(hostClass), `wa-popup host class moved: ${hostClass}`)
    assert.ok(chunks.includes('class="color-popup"'), 'the color picker popup class moved')
})

test('Web Awesome guard: the slots the content fade rules reach (§14.4)', () => {
    const structures = [
        ['wa-select: .listbox wraps the default slot', /class="listbox"[^>]*>\s*<slot(?![^>]*\bname=)[^>]*><\/slot>\s*<\/div>/],
        ['wa-popover: .body wraps the default slot', /<div part="body" class="body"[^>]*>\s*<slot><\/slot>\s*<\/div>\s*<\/wa-popup>/],
        ['wa-dropdown: #menu holds the default slot', /id="menu"[^>]*>\s*<slot(?![^>]*\bname=)[^>]*><\/slot>\s*<\/div>/],
        ['wa-dropdown-item: #submenu holds the submenu slot', /id="submenu"[^>]*>\s*<slot name="submenu"><\/slot>\s*<\/div>/],
    ]
    for (const [label, pattern] of structures) assert.match(chunks, pattern, `moved: ${label}`)
})

test('Lit guard: createRenderRoot adopts this.constructor.elementStyles', () => {
    for (const file of ['reactive-element.js', 'development/reactive-element.js']) {
        const source = readFileSync(join(nodeModules, '@lit/reactive-element', file), 'utf8')
        const match = source.match(/createRenderRoot\(\)\s*\{([\s\S]*?)\n?\s*\}/)
        assert.ok(match, `${file}: createRenderRoot moved`)
        assert.ok(match[1].includes('this.constructor.elementStyles'), `${file}: createRenderRoot no longer adopts elementStyles`)
    }
})

test('Notivue guard: version 2.4.5', () => {
    const pkg = JSON.parse(readFileSync(join(nodeModules, 'notivue/package.json'), 'utf8'))
    assert.equal(pkg.version, '2.4.5', 'Notivue upgraded: re-check docs/plans/2026-09-28-overlay-motion-design.md §2, §8')
})
