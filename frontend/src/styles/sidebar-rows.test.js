// CSS invariants of the shared sidebar rows (visual refresh step 6a,
// docs/plans/2026-09-29-accent-glow-design.md §17.3): the session list and the artifacts
// list style their rows through styles/sidebar-rows.css, not through copies in each
// component. Same approach as glow.test.js: the files are read as text.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const srcDir = join(here, '..')
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** Split on commas at parenthesis depth 0 (selector lists). */
function splitTopLevel(text) {
    const parts = []
    let depth = 0
    let start = 0
    for (let i = 0; i < text.length; i++) {
        if (text[i] === '(') depth++
        else if (text[i] === ')') depth--
        else if (text[i] === ',' && depth === 0) {
            parts.push(text.slice(start, i))
            start = i + 1
        }
    }
    parts.push(text.slice(start))
    return parts.map(collapse).filter(Boolean)
}

/** The flat rules of a stylesheet without nesting, in source order: { selectors, decls, order, nested }. */
function parseFlat(css) {
    const rules = []
    const re = /([^{}]+)\{([^{}]*)\}/g
    let match
    while ((match = re.exec(stripComments(css)))) {
        const decls = {}
        for (const part of match[2].split(';')) {
            const d = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
            if (d) decls[d[1]] = collapse(d[2])
        }
        rules.push({ selectors: splitTopLevel(match[1]), decls, order: rules.length })
    }
    return rules
}

/** The unique rule whose selector list is exactly `selectors` (any order). */
function rule(rules, selectors) {
    const wanted = selectors.map(collapse)
    const found = rules.filter((r) => r.selectors.length === wanted.length && wanted.every((s) => r.selectors.includes(s)))
    assert.equal(found.length, 1, `expected exactly one rule "${wanted.join(', ')}", found ${found.length}`)
    return found[0]
}

const styleOf = (sfc) => [...sfc.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
const templateOf = (sfc) => sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '')
/** The opening tag of the first element whose static class list holds `className`. */
function tagWithClass(template, className) {
    const tags = template.match(/<[\w-]+\b(?:[^>"]|"[^"]*")*>/g) ?? []
    const found = tags.find((t) => (t.match(/\sclass="([^"]*)"/)?.[1] ?? '').split(/\s+/).includes(className))
    assert.ok(found, `an element with class "${className}"`)
    return collapse(found)
}

const SESSIONS = '../components/session/list/SessionListItem.vue'
const ARTIFACTS = '../components/artifacts/ArtifactBookmarkList.vue'
const css = read('sidebar-rows.css')
const cssStripped = stripComments(css)
const rules = parseFlat(css)

test('1. sidebar-rows.css is imported by main.js right after surfaces.css, and nowhere else', () => {
    const lines = read('../main.js').split('\n').map((l) => l.trim()).filter((l) => l.startsWith('import '))
    const surfacesAt = lines.indexOf("import './styles/surfaces.css'")
    assert.ok(surfacesAt >= 0, 'main.js imports surfaces.css')
    assert.equal(lines[surfacesAt + 1], "import './styles/sidebar-rows.css'")
    const importers = readdirSync(srcDir, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile() && /\.(vue|css|js)$/.test(e.name) && !e.name.endsWith('.test.js'))
        .map((e) => join(e.parentPath ?? e.path, e.name))
        .filter((file) => /(?:import|@import)\s+(?:url\()?['"][^'"]*sidebar-rows\.css['"]/.test(readFileSync(file, 'utf8')))
    assert.deepEqual(importers, [join(srcDir, 'main.js')])
})

test('2. sidebar-rows.css: no @layer, one !important (the menu trigger hover)', () => {
    assert.ok(!cssStripped.includes('@layer'), 'no @layer')
    assert.equal(cssStripped.match(/!important/g)?.length, 1)
    assert.equal(rule(rules, ['.sidebar-row-menu-trigger:hover']).decls.opacity, '1 !important')
})

test('3. the moved rules, with their values, in order', () => {
    const wrapper = rule(rules, ['.sidebar-row-wrapper'])
    assert.deepEqual(wrapper.decls, { position: 'relative', width: '100%' })
    assert.deepEqual(rule(rules, ['.sidebar-row']).decls, { width: '100%' })
    const base = rule(rules, ['.sidebar-row::part(base)'])
    assert.deepEqual(base.decls, { padding: 'var(--wa-space-xs)', height: 'auto', 'margin-bottom': 'var(--wa-shadow-offset-y-s)' })
    assert.deepEqual(rule(rules, ['.sidebar-row::part(label)']).decls, { width: '100%', 'text-align': 'left' })
    const compact = rule(rules, ['.sidebar-row-wrapper--compact .sidebar-row::part(base)'])
    assert.deepEqual(compact.decls, { 'padding-block': 'var(--wa-space-2xs)' })
    const highlight = rule(rules, ['.sidebar-row--highlighted::part(base)'])
    assert.deepEqual(highlight.decls, { outline: 'var(--wa-focus-ring)', 'outline-offset': 'var(--wa-focus-ring-offset)' })
    const multi = rule(rules, ['.sidebar-row-wrapper--selected .sidebar-row::part(base)'])
    assert.deepEqual(multi.decls, {
        'background-color': 'var(--wa-color-brand-fill-quiet)',
        'box-shadow': 'inset 0 0 0 1px var(--wa-color-brand-border-quiet)',
    })
    const lit = rule(rules, ['.sidebar-row--active::part(base)', '.sidebar-row-wrapper--selected .sidebar-row--active::part(base)'])
    assert.deepEqual(lit.decls, {
        'border-color': 'color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent)',
        'background-origin': 'border-box',
        'background-image': 'linear-gradient(100deg, var(--wa-color-brand-fill-normal), color-mix(in oklab, var(--wa-color-brand-fill-quiet) 70%, transparent))',
        'box-shadow': '0 2px 10px -4px color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent)',
        color: 'var(--wa-color-brand-on-quiet)',
    })
    assert.deepEqual(rule(rules, ['html.wa-dark .sidebar-row--active::part(base)']).decls, {
        'border-color': 'oklch(from var(--wa-color-brand-60) 0.6 0.11 h / 0.45)',
        'background-image': 'linear-gradient(100deg, oklch(from var(--wa-color-brand-60) 0.32 0.07 h), oklch(from var(--wa-color-brand-60) 0.25 0.05 h / 0.6))',
        'box-shadow': '0 2px 12px -4px oklch(from var(--wa-color-brand-60) 0.6 0.13 h / 0.5)',
        color: 'oklch(from var(--wa-color-brand-60) 0.88 0.08 h)',
    })
    const menu = rule(rules, ['.sidebar-row-menu'])
    assert.deepEqual(menu.decls, {
        display: 'block', position: 'absolute', top: 'var(--wa-space-2xs)', right: 'var(--wa-space-xs)', 'z-index': '1',
    })
    assert.deepEqual(rule(rules, ['.sidebar-row-wrapper--compact .sidebar-row-menu']).decls, { top: '0' })
    const trigger = rule(rules, ['.sidebar-row-menu-trigger'])
    assert.deepEqual(trigger.decls, { opacity: '0.4', transition: 'opacity 0.15s', 'font-size': 'var(--wa-font-size-2xs)' })
    const shown = rule(rules, [
        '.sidebar-row-wrapper:hover .sidebar-row-menu-trigger',
        '.sidebar-row-wrapper--active .sidebar-row-menu-trigger',
        '.sidebar-row-menu[open] .sidebar-row-menu-trigger',
    ])
    assert.deepEqual(shown.decls, { opacity: '0.6' })
    const order = [wrapper, base, compact, highlight, multi, lit, menu, trigger].map((r) => r.order)
    assert.deepEqual(order, [...order].sort((a, b) => a - b), 'wrapper, row, highlight, multi-select, open, menu')
    assert.ok(compact.order > base.order, 'the compact padding follows the base rule')
})

test('3b. depth.css: the sidebar rows are not raised (one shared class in the exclusion list)', () => {
    const depth = stripComments(read('depth.css'))
    assert.match(depth, /wa-button:is\(\[appearance\*='outlined'\], \[appearance\*='filled'\]\):not\([^)]*\.sidebar-row[,)]/)
    assert.ok(!/\.session-item|\.bookmark-item/.test(depth), 'no per-list row class left in depth.css')
})

test('4. both components put the shared classes on their row elements', () => {
    const sessions = templateOf(read(SESSIONS))
    const sWrapper = tagWithClass(sessions, 'sidebar-row-wrapper')
    assert.match(sWrapper, /class="session-item-wrapper sidebar-row-wrapper"/)
    assert.match(sWrapper, /'sidebar-row-wrapper--active': active,/)
    assert.match(sWrapper, /'sidebar-row-wrapper--compact': compactView,/)
    assert.match(sWrapper, /'sidebar-row-wrapper--selected': selected,/)
    const sRow = tagWithClass(sessions, 'sidebar-row')
    assert.match(sRow, /class="session-item sidebar-row"/)
    assert.match(sRow, /'sidebar-row--active': active,/)
    assert.match(sRow, /'sidebar-row--highlighted': highlighted/)
    assert.match(tagWithClass(sessions, 'sidebar-row-menu'), /class="session-menu sidebar-row-menu"/)
    assert.match(tagWithClass(sessions, 'sidebar-row-menu-trigger'), /class="session-menu-trigger sidebar-row-menu-trigger"/)

    const artifacts = templateOf(read(ARTIFACTS))
    const aWrapper = tagWithClass(artifacts, 'sidebar-row-wrapper')
    assert.match(aWrapper, /class="bookmark-item-wrapper sidebar-row-wrapper"/)
    assert.match(aWrapper, /'sidebar-row-wrapper--active': isActive\(b\),/)
    assert.match(aWrapper, /'sidebar-row-wrapper--compact': compactView,/)
    assert.ok(!aWrapper.includes('sidebar-row-wrapper--selected'), 'no multi-select on the artifacts list')
    const aRow = tagWithClass(artifacts, 'sidebar-row')
    assert.match(aRow, /class="bookmark-item sidebar-row"/)
    assert.match(aRow, /'sidebar-row--active': isActive\(b\),/)
    assert.match(aRow, /'sidebar-row--highlighted': index === highlightedIndex,/)
    assert.match(tagWithClass(artifacts, 'sidebar-row-menu'), /class="bookmark-menu sidebar-row-menu"/)
    assert.match(tagWithClass(artifacts, 'sidebar-row-menu-trigger'), /class="bookmark-menu-trigger sidebar-row-menu-trigger"/)
})

test('5. neither component keeps a copy of the moved rules', () => {
    for (const file of [SESSIONS, ARTIFACTS]) {
        const scoped = stripComments(styleOf(read(file)))
        for (const needle of ['.session-menu', '.bookmark-menu', '::part(base)', '::part(label)', 'var(--wa-focus-ring)']) {
            assert.ok(!scoped.includes(needle), `${file}: ${needle}`)
        }
        for (const r of parseFlat(scoped)) {
            assert.ok(!(r.decls.position === 'relative' && r.selectors.some((s) => /-wrapper$/.test(s))), `${file}: wrapper position`)
        }
    }
})

// Gliding open-row fill (step 6a-bis, docs/plans/2026-09-29-sidebar-row-glide-design.md §4.3):
// the lit look moves to the list's ink once it is placed.
test('6. lists with an ink: the ink carries the lit look, the open row hands it over', () => {
    const vars = rule(rules, ['.sidebar-row-list'])
    assert.deepEqual(vars.decls, {
        '--glide-ink-bg': 'linear-gradient(100deg, var(--wa-color-brand-fill-normal), color-mix(in oklab, var(--wa-color-brand-fill-quiet) 70%, transparent))',
        '--glide-ink-radius': 'var(--wa-form-control-border-radius)',
        // The reveal bands (§10): none outside compact mode.
        '--sidebar-row-reveal-top': '0rem',
        '--sidebar-row-reveal-bottom': '0rem',
    })
    const darkVars = rule(rules, ['html.wa-dark .sidebar-row-list'])
    assert.deepEqual(darkVars.decls, {
        '--glide-ink-bg': 'linear-gradient(100deg, oklch(from var(--wa-color-brand-60) 0.32 0.07 h), oklch(from var(--wa-color-brand-60) 0.25 0.05 h / 0.6))',
    })
    // The lit row's values (rule 3): its gradient, its ring as a 1px inset shadow, its shadow.
    const lit = rule(rules, ['.sidebar-row--active::part(base)', '.sidebar-row-wrapper--selected .sidebar-row--active::part(base)'])
    const darkLit = rule(rules, ['html.wa-dark .sidebar-row--active::part(base)'])
    assert.equal(vars.decls['--glide-ink-bg'], lit.decls['background-image'])
    assert.equal(darkVars.decls['--glide-ink-bg'], darkLit.decls['background-image'])
    const ink = rule(rules, ['.sidebar-row-list > .glide-ink'])
    assert.deepEqual(ink.decls, {
        'box-shadow': `inset 0 0 0 1px ${lit.decls['border-color']}, ${lit.decls['box-shadow']}`,
    })
    const darkInk = rule(rules, ['html.wa-dark .sidebar-row-list > .glide-ink'])
    assert.deepEqual(darkInk.decls, {
        'box-shadow': `inset 0 0 0 1px ${darkLit.decls['border-color']}, ${darkLit.decls['box-shadow']}`,
    })

    const handOff = rule(rules, ['.sidebar-row-list[data-glide-ready] .sidebar-row--active::part(base)'])
    assert.deepEqual(handOff.decls, {
        'background-color': 'transparent',
        'background-image': 'none',
        'border-color': 'transparent',
        'box-shadow': 'none',
    })
    const arriving = rule(rules, ['.sidebar-row-list:has(> .list-arriving) > .glide-ink'])
    assert.deepEqual(arriving.decls, { opacity: '0' })
    const selectedInk = rule(rules, ['.sidebar-row-list:has(.sidebar-row-wrapper--selected .sidebar-row--active) > .glide-ink'])
    assert.deepEqual(selectedInk.decls, { background: 'var(--glide-ink-bg), var(--wa-color-brand-fill-quiet)' })
    const selectedRing = rule(rules, ['.sidebar-row-list[data-glide-ready] .sidebar-row-wrapper--selected .sidebar-row--active::part(base)'])
    assert.deepEqual(selectedRing.decls, {
        'border-color': 'var(--wa-color-brand-60)',
        'box-shadow': 'inset 0 0 0 1px var(--wa-color-brand-60)',
    })
    const transition = rule(rules, ['.sidebar-row-list .sidebar-row--active::part(base)'])
    assert.deepEqual(transition.decls, { 'transition-property': 'color' })

    // After every lit rule (the dark open-and-selected one is the last of them).
    const lastLit = rule(rules, ['html.wa-dark .sidebar-row-wrapper--selected .sidebar-row--active::part(base)'])
    for (const r of [vars, darkVars, ink, darkInk, handOff, arriving, selectedInk, selectedRing, transition]) {
        assert.ok(r.order > lastLit.order, `${r.selectors[0]} after the lit rules`)
    }
    assert.ok(selectedRing.order > handOff.order, 'the selection ring comes back after the hand-off')
})

// Reveal the open row only near an edge (step 6a-bis §10): the bands are registered custom
// properties read by the lists' own code, never scroll-padding / scroll-margin.
test('7. the reveal bands: registered lengths, 5rem in compact mode, after the base rule', () => {
    for (const name of ['--sidebar-row-reveal-top', '--sidebar-row-reveal-bottom', '--sidebar-row-reveal-cover']) {
        const registered = rule(rules, [`@property ${name}`])
        assert.deepEqual(registered.decls, { syntax: "'<length>'", inherits: 'true', 'initial-value': '0px' }, name)
    }
    const properties = rules.filter((r) => r.selectors[0].startsWith('@property'))
    assert.deepEqual(properties.map((r) => r.order), [0, 1, 2], 'at the top of the file')
    const base = rule(rules, ['.sidebar-row-list'])
    const compact = rule(rules, ['.sidebar-row-list--compact'])
    assert.deepEqual(compact.decls, { '--sidebar-row-reveal-top': '5rem', '--sidebar-row-reveal-bottom': '5rem' })
    assert.ok(compact.order > base.order, 'the compact bands come after the .sidebar-row-list rule')
    assert.equal(compact.order, base.order + 1, 'right after it')
})

test('8. no scroll-padding / scroll-margin in the lists or the shared rows', () => {
    for (const file of ['sidebar-rows.css', '../components/session/list/SessionList.vue', ARTIFACTS]) {
        assert.ok(!/scroll-(padding|margin)/.test(stripComments(read(file))), file)
    }
})
