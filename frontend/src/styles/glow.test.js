// CSS invariants of the accent glow (visual refresh step 6a,
// docs/plans/2026-09-29-accent-glow-design.md §12). Same approach as depth.test.js and
// motion.test.js: the stylesheets and components are read as text and checked for what the
// design relies on.
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

/** Split on commas at parenthesis depth 0 (selector lists, value lists). */
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

/** Every rule of a stylesheet, nesting-aware, in source order:
 *  { head, selectors, decls, order, ancestors } (ancestors = the enclosing heads). */
function rulesOf(css, ancestors = [], out = []) {
    let i = 0
    let own = ''
    while (i < css.length) {
        const open = css.indexOf('{', i)
        if (open === -1) break
        const rawHead = css.slice(i, open)
        const cut = Math.max(rawHead.lastIndexOf(';'), rawHead.lastIndexOf('}'))
        own += rawHead.slice(0, cut + 1)
        const head = collapse(rawHead.slice(cut + 1))
        let depth = 1
        let j = open + 1
        while (j < css.length && depth > 0) {
            if (css[j] === '{') depth++
            else if (css[j] === '}') depth--
            j++
        }
        const entry = { head, selectors: head.startsWith('@') ? [] : splitTopLevel(head), ancestors, decls: {}, order: out.length }
        out.push(entry)
        entry.decls = declarationsOf(rulesOf(css.slice(open + 1, j - 1), [...ancestors, head], out))
        i = j
    }
    return own + css.slice(i)
}

function declarationsOf(body) {
    const out = {}
    for (const part of body.split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) out[match[1]] = collapse(match[2])
    }
    return out
}

function parseCss(css) {
    const out = []
    rulesOf(stripComments(css), [], out)
    return out.filter((r) => !r.head.startsWith('@'))
}

const styleOf = (sfc) => [...sfc.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
const templateOf = (sfc) => sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '')

/** The unique rule whose selector list is exactly `selectors` (any order), optionally filtered. */
function rule(rules, selectors, where = () => true) {
    const wanted = selectors.map(collapse)
    const found = rules.filter((r) => r.selectors.length === wanted.length && wanted.every((s) => r.selectors.includes(s)) && where(r))
    assert.equal(found.length, 1, `expected exactly one rule "${wanted.join(', ')}", found ${found.length}`)
    return found[0]
}

const topLevel = (r) => r.ancestors.length === 0
const inHover = (r) => r.ancestors.length === 1 && /^@media \(hover: ?hover\)$/.test(r.ancestors[0])
const inReduced = (r) => r.ancestors.length === 1 && /^@media \(prefers-reduced-motion: ?reduce\)$/.test(r.ancestors[0])

const glowCss = read('glow.css')
const glowStripped = stripComments(glowCss)
const glow = parseCss(glowCss)

const BRAND = "wa-button[variant='brand'][appearance='accent']"
const EXCLUDED = ".wa-invert wa-button, .layout-winbtn, .dock-winbtn, .detail-toggle, [disabled], .fake-disabled"
const LIT = `${BRAND}:not(${EXCLUDED})`
const LIVE = `${BRAND}:not(${EXCLUDED}, [loading])`
const FILL_LOUD_ACTIVE = 'color-mix(in oklab, var(--wa-color-fill-loud), var(--wa-color-mix-active))'

test('1. glow.css is imported right after motion.css in the three entry files', () => {
    for (const [file, prefix] of [['../main.js', './styles/'], ['../share-session/main.js', '../styles/'], ['../artifact-shell/main.js', '../styles/']]) {
        const lines = read(file).split('\n').map((l) => l.trim()).filter((l) => l.startsWith('import '))
        const motionAt = lines.indexOf(`import '${prefix}motion.css'`)
        assert.ok(motionAt >= 0, `${file}: no motion.css import`)
        assert.equal(lines[motionAt + 1], `import '${prefix}glow.css'`, `${file}: glow.css must follow motion.css`)
        assert.equal(lines[motionAt - 1], `import '${prefix}glass.css'`, `${file}: motion.css still follows glass.css`)
    }
})

test('2. glow.css: no @layer, no !important', () => {
    assert.ok(!glowStripped.includes('@layer'), 'no @layer')
    assert.ok(!glowStripped.includes('!important'), 'no !important')
})

test('3. focus ring tokens and glow tokens', () => {
    const focus = rule(glow, [':root', '.wa-theme-default', '.wa-light', '.wa-dark', '.wa-invert'], topLevel)
    assert.equal(focus.decls['--wa-focus-ring-width'], '0.25rem')
    assert.equal(focus.decls['--wa-focus-ring-offset'], '0rem')
    assert.match(focus.decls['--wa-focus-ring-offset'], /^\d*\.?\d+rem$/, 'the offset is a length with a unit')
    assert.equal(focus.decls['--wa-focus-ring'], 'var(--wa-focus-ring-style) var(--wa-focus-ring-width) var(--glow-focus-halo)')
    assert.ok(focus.decls['--glow-focus-halo'], '--glow-focus-halo declared')
    // Same total thickness as the theme's ring: 0.1875rem + 0.0625rem.
    const total = parseFloat(focus.decls['--wa-focus-ring-width']) + parseFloat(focus.decls['--wa-focus-ring-offset'])
    assert.equal(total, 0.1875 + 0.0625)

    const tab = rule(glow, [':where(wa-tab)'], topLevel)
    assert.deepEqual(tab.decls, { '--wa-focus-ring-offset': '0.0625rem' })

    const tokens = rule(glow, [':root', '.wa-invert'], topLevel)
    for (const name of ['--glow-accent', '--glow-accent-shifted', '--glow-button', '--glow-button-hover', '--glow-button-shading']) {
        assert.ok(tokens.decls[name], `${name} declared`)
    }
    assert.ok(tokens.order < focus.order, 'the focus block comes after the token block')
})

test('4. solid brand buttons: target, exclusions, rest / hover / press', () => {
    // Every button rule targets the solid brand buttons with the full exclusion list, except
    // the reduced-motion rule, which hides the sheen on every one of them.
    for (const r of glow.filter((x) => x.head.includes('wa-button'))) {
        if (inReduced(r)) {
            assert.deepEqual(r.selectors, [`:where(${BRAND})::part(base)::after`])
            continue
        }
        for (const s of r.selectors) assert.ok(s.includes(`${BRAND}:not(${EXCLUDED}`), `exclusions: ${s}`)
    }

    const rest = rule(glow, [`:where(${LIT})::part(base)`], topLevel)
    assert.equal(rest.decls['background-origin'], 'border-box')
    assert.equal(rest.decls['background-image'], 'var(--glow-button-shading)')
    assert.equal(rest.decls.background, undefined, 'no background shorthand (it would reset background-color)')
    assert.match(rest.decls['box-shadow'], /^var\(--depth-button\), .*var\(--glow-button\)$/)

    const hover = rule(glow, [`:where(${LIVE}:hover)::part(base)`], inHover)
    assert.equal(hover.decls['background-color'], 'var(--wa-color-fill-loud)')
    assert.ok(hover.decls.filter, 'hover brightens')
    assert.match(hover.decls['box-shadow'], /var\(--glow-button-hover\)$/)

    const press = rule(glow, [`:where(${LIVE}:active)::part(base)`], topLevel)
    assert.equal(press.decls['background-color'], FILL_LOUD_ACTIVE)
    assert.equal(press.decls.filter, 'none')
    assert.ok(press.order > hover.order, 'the press rule comes after the hover block')

    // Each :is( / :not( compound on one line: a line break before it is a descendant combinator.
    assert.doesNotMatch(glowStripped, /\n\s*:(not|is)\(/, 'no line starting with :not( or :is(')
    for (const line of glowStripped.split('\n')) {
        for (const m of line.matchAll(/:(not|is)\(/g)) {
            let depth = 0
            let closed = false
            for (let k = m.index + m[0].length - 1; k < line.length; k++) {
                if (line[k] === '(') depth++
                else if (line[k] === ')' && --depth === 0) { closed = true; break }
            }
            assert.ok(closed, `compound split over lines: ${line.trim()}`)
        }
    }
})

test('5. the sheen: positioned band under the content, sweep on hover, off under reduced motion', () => {
    const rest = rule(glow, [`:where(${LIT})::part(base)`], topLevel)
    assert.equal(rest.decls.position, 'relative')
    assert.equal(rest.decls.isolation, 'isolate')

    const sheen = rule(glow, [`:where(${LIVE})::part(base)::after`], topLevel)
    assert.equal(sheen.decls['pointer-events'], 'none')
    assert.equal(sheen.decls.inset, '0')
    assert.equal(sheen.decls['z-index'], '-1')
    assert.equal(sheen.decls.content, "''")
    assert.match(sheen.decls.background, /no-repeat 100% 0 \/ 400% 100%$/)

    const sweep = rule(glow, [`:where(${LIVE}:hover)::part(base)::after`], inHover)
    assert.ok(sweep.decls['background-position'], 'the hover moves the band')

    const reduced = rule(glow, [`:where(${BRAND})::part(base)::after`], inReduced)
    assert.deepEqual(reduced.decls, { display: 'none' })
})

test('6. motion.css: filter joins the wa-button lists, internal parts keep six', () => {
    const motion = parseCss(read('motion.css'))
    const FAST = 'var(--wa-transition-fast)'
    const EASING = 'var(--wa-transition-easing)'
    const base = rule(motion, [':where(wa-button)::part(base)'], topLevel)
    const properties = splitTopLevel(base.decls['transition-property'])
    const durations = splitTopLevel(base.decls['transition-duration'])
    const timings = splitTopLevel(base.decls['transition-timing-function'])
    for (const list of [properties, durations, timings]) assert.equal(list.length, 7)
    assert.equal(properties[6], 'filter')
    assert.equal(durations[6], FAST)
    assert.equal(timings[6], EASING)

    const press = motion.filter((r) => topLevel(r) && r.selectors.length === 1 && /^:where\(\s*wa-button:not\(.*:active\s*\)::part\(base\)$/.test(r.selectors[0]))
    assert.equal(press.length, 1)
    assert.equal(press[0].decls['transition-property'], undefined)
    const pressDurations = splitTopLevel(press[0].decls['transition-duration'])
    assert.equal(pressDurations.length, 7)
    assert.equal(pressDurations[6], FAST)

    const internal = motion.filter((r) => topLevel(r) && r.selectors.some((s) => s.includes('close-button__base')))
    assert.equal(internal.length, 2, 'internal parts: rest and press')
    for (const r of internal) {
        assert.equal(splitTopLevel(r.decls['transition-duration']).length, 6)
        if (r.decls['transition-property']) assert.equal(splitTopLevel(r.decls['transition-property']).length, 6)
    }
})

test('7. context ring: a real colour at every percentage, glow on the base part', () => {
    for (const file of ['../components/session/detail/SessionHeader.vue', '../components/orchestration/AgentTreeNode.vue', '../components/orchestration/OrchestrationNode.vue']) {
        const sfc = read(file)
        const start = sfc.indexOf('const contextUsageColor = computed(')
        assert.ok(start >= 0, `${file}: contextUsageColor`)
        const body = sfc.slice(start, sfc.indexOf('\n})', start))
        assert.ok(!body.includes('--wa-color-primary'), `${file}: no --wa-color-primary`)
        assert.match(body, /return 'var\(--glow-context-ring\)'/, `${file}: default colour`)
    }
    const ring = rule(glow, [':where(wa-progress-ring:is(.context-usage-ring, .onode-context-ring))::part(base)'], topLevel)
    assert.match(ring.decls.filter, /^drop-shadow\(.*var\(--indicator-color\)/)
    assert.ok(!glowStripped.includes('::part(indicator)'), 'never on the indicator (the SVG clips it)')
    const light = rule(glow, [':root', '.wa-invert'], topLevel)
    assert.match(light.decls['--glow-context-ring'], /^color-mix\(in oklab, var\(--wa-color-brand-border-loud\), /)
    const dark = rule(glow, ['.wa-dark'], topLevel)
    assert.ok(dark.order > light.order, 'the dark value comes after the light one')
    assert.match(dark.decls['--glow-context-ring'], /^color-mix\(in oklab, var\(--wa-color-brand-50\), /)
    const track = rule(glow, ['.wa-dark :where(wa-progress-ring:is(.context-usage-ring, .onode-context-ring))'], topLevel)
    assert.equal(track.decls['--track-color'], 'var(--wa-color-neutral-border-normal)')
})

test('8. quota bars: the colour as --usage-fill, gradient and glow in CSS', () => {
    const sfc = read('../views/ProjectView.vue')
    const fills = [...templateOf(sfc).matchAll(/<div class="usage-lane-fill[^"]*"[^>]*>/g)].map((m) => m[0])
    assert.ok(fills.length >= 5, 'the usage lanes are found')
    for (const tag of fills) assert.doesNotMatch(tag, /background:/, `inline background left: ${tag}`)
    const severity = fills.filter((tag) => !tag.includes('usage-lane-time'))
    assert.equal(severity.length, 3)
    for (const tag of severity) assert.match(tag, /'--usage-fill': /, tag)
    // The balance dot is not a bar: it keeps its inline background.
    const dot = templateOf(sfc).match(/<span class="usage-balance-dot"[^>]*>/)[0]
    assert.match(dot, /background: quotaExtraUsageRingColor/, dot)

    const css = rule(parseCss(styleOf(sfc)), ['.usage-lane-fill:not(.usage-lane-time)'], topLevel)
    assert.match(css.decls.background, /^linear-gradient\(.*var\(--usage-fill\)/)
    assert.ok(css.decls['box-shadow'], 'a glow')
})

test('9. option cards: the focus ring token, no hard outline left', () => {
    const sites = [
        ['../components/session/detail/items/claude_code/PendingRequestBody.vue', ['.option-card:focus-visible', '.option-card.auto-focused:focus-within']],
        ['../components/session/detail/items/codex/RequestUserInputBody.vue', ['.option-card:focus-visible', '.option-card.auto-focused:focus-within']],
    ]
    for (const [file, selectors] of sites) {
        const rules = parseCss(styleOf(read(file)))
        for (const selector of selectors) {
            const r = rule(rules, [selector], topLevel)
            assert.equal(r.decls.outline, 'var(--wa-focus-ring)', `${file} ${selector}`)
            assert.equal(r.decls['outline-offset'], 'var(--wa-focus-ring-offset)', `${file} ${selector}`)
        }
    }
    const files = readdirSync(srcDir, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile() && /\.(vue|css|js)$/.test(e.name) && !e.name.endsWith('.test.js'))
        .map((e) => join(e.parentPath ?? e.path, e.name))
    for (const file of files) {
        assert.ok(!readFileSync(file, 'utf8').includes('outline: 2px solid var(--wa-color-brand-fill-loud)'), file)
    }
})

test('10. TabBar: a thicker glowing gradient strip', () => {
    const rules = parseCss(styleOf(read('../components/ui/TabBar.vue')))
    const ink = rule(rules, ['.tab-bar::part(tabs)::after'], topLevel)
    assert.equal(ink.decls['border-block-end'], undefined)
    assert.equal(ink.decls.border, '0')
    assert.match(ink.decls.background, /var\(--glow-ink-thickness\)/)
    assert.ok(ink.decls.filter, 'the glow')
    const bar = rule(rules, ['.tab-bar'], (r) => topLevel(r) && '--track-width' in r.decls)
    assert.equal(bar.decls['--glow-ink-thickness'], '2px')
})

test('11. the open session row: lit, and a thicker ring when multi-selected', () => {
    const rules = parseCss(styleOf(read('../components/session/list/SessionListItem.vue')))
    const ACTIVE = '.session-item--active::part(base)'
    const SELECTED_ACTIVE = '.session-item-wrapper--selected .session-item--active::part(base)'
    const lit = rule(rules, [ACTIVE, SELECTED_ACTIVE], topLevel)
    assert.ok(lit.decls['border-color'], 'border-color')
    assert.equal(lit.decls['background-origin'], 'border-box')
    assert.match(lit.decls['background-image'], /^linear-gradient\(100deg, var\(--wa-color-brand-fill-normal\), /)
    assert.equal(lit.decls.color, 'var(--wa-color-brand-on-quiet)')
    const multi = rule(rules, ['.session-item-wrapper--selected .session-item::part(base)'], topLevel)
    assert.ok(lit.order > multi.order, 'after the multi-select rule')
    const dark = rule(rules, [`html.wa-dark ${ACTIVE}`], topLevel)
    assert.match(dark.decls['background-image'], /^linear-gradient\(100deg, oklch\(from var\(--wa-color-brand-60\) /)
    for (const selector of [SELECTED_ACTIVE, `html.wa-dark ${SELECTED_ACTIVE}`]) {
        const r = rule(rules, [selector], topLevel)
        assert.equal(r.decls['border-color'], 'var(--wa-color-brand-60)', selector)
        assert.match(r.decls['box-shadow'], /^inset 0 0 0 1px var\(--wa-color-brand-60\), /, selector)
    }
})

test('12. focused fields glow; the composer keeps level 2', () => {
    const fields = rule(glow, [':where(:is(wa-input, wa-textarea):focus-within)::part(base)', ':where(wa-select:focus-within)::part(combobox)'], topLevel)
    assert.match(fields.decls['box-shadow'], /^var\(--depth-inset\), /)
    const composer = rule(parseCss(styleOf(read('../components/message/MessageInput.vue'))), ['.message-input wa-textarea:focus-within::part(base)'], topLevel)
    assert.match(composer.decls['box-shadow'], /^var\(--depth-2\), /)
})

test('12b. field rings keep the halo colour at rest, so the focus fade never starts dark', () => {
    const rest = rule(glow, [':where(wa-input)::part(base)', ':where(wa-select)::part(combobox)'], topLevel)
    assert.equal(rest.decls['outline-color'], 'var(--glow-focus-halo)')
    assert.equal(Object.keys(rest.decls).length, 1, 'only the colour: width and style stay Web Awesome\'s')
})

test('13. checked switches: gradient track with a glow', () => {
    const sw = rule(glow, [':where(wa-switch:state(checked):not([disabled]))::part(control)'], topLevel)
    assert.equal(sw.decls['border-color'], 'transparent')
    assert.equal(sw.decls['background-origin'], 'border-box')
    assert.match(sw.decls['background-image'], /^linear-gradient\(/)
    assert.ok(sw.decls['box-shadow'], 'a glow')
})

test('14. gliding inks: segmented control and settings nav', () => {
    const segmented = parseCss(styleOf(read('../components/ui/SegmentedControl.vue')))
    assert.ok(rule(segmented, ['.segmented-control > .glide-ink'], topLevel).decls['box-shadow'])

    const settings = parseCss(styleOf(read('../components/app/SettingsPopover.vue')))
    assert.ok(rule(settings, ['.settings-nav > .glide-ink'], topLevel).decls['box-shadow'])
    rule(settings, ['.settings-nav-item:not(.active):hover'], topLevel)
    assert.equal(settings.filter((r) => topLevel(r) && r.selectors.includes('.settings-nav-item:hover')).length, 0, 'no plain top-level hover rule')
    const mobile = rule(settings, ['.settings-nav-item:hover'], (r) => r.ancestors.length === 1 && r.ancestors[0] === '@media (width < 640px)')
    assert.deepEqual(mobile.decls, { background: 'var(--glass-item-hover)' })
})
