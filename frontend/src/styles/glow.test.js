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
 *  { head, selectors, decls, list, order, ancestors } (list = the [property, value] pairs in
 *  source order; ancestors = the enclosing heads). */
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
        const entry = { head, selectors: head.startsWith('@') ? [] : splitTopLevel(head), ancestors, decls: {}, list: [], order: out.length }
        out.push(entry)
        entry.list = declarationList(rulesOf(css.slice(open + 1, j - 1), [...ancestors, head], out))
        entry.decls = Object.fromEntries(entry.list)
        i = j
    }
    return own + css.slice(i)
}

/** The declarations of a rule body as [property, value] pairs, in source order. */
function declarationList(body) {
    const out = []
    for (const part of body.split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) out.push([match[1], collapse(match[2])])
    }
    return out
}

/** Every rule, at-rules included (@property, @keyframes, @media). */
function parseAll(css) {
    const out = []
    rulesOf(stripComments(css), [], out)
    return out
}

const parseCss = (css) => parseAll(css).filter((r) => !r.head.startsWith('@'))

/** A rule pinned whole: its ordered declaration list equals the spec block's exactly. */
function assertPinned(r, specBody, label = r.head) {
    assert.deepEqual(r.list, declarationList(stripComments(specBody)), `${label}: declaration list`)
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
    // No drop-shadow on the indicator: the SVG clips it (live states §7.2: an opacity pulse is safe).
    for (const r of glow.filter((x) => x.selectors.some((s) => s.includes('::part(indicator)')))) {
        assert.equal(r.decls.filter, undefined, `no filter on the indicator: ${r.head}`)
    }
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
    // One shared focus rule in option-cards.css (step 7e,
    // docs/plans/2026-09-30-question-options-motion-design.md §4.2), for both question bodies.
    const focus = rule(parseCss(read('option-cards.css')), [':where(.option-card:focus-visible)', ':where(.option-card.auto-focused:focus-within)'], topLevel)
    assert.equal(focus.decls.outline, 'var(--wa-focus-ring)')
    assert.equal(focus.decls['outline-offset'], 'var(--wa-focus-ring-offset)')
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

test('11. the open sidebar row: lit, and a thicker ring when multi-selected (§7, §17.3)', () => {
    const rules = parseCss(read('sidebar-rows.css'))
    const ACTIVE = '.sidebar-row--active::part(base)'
    const SELECTED_ACTIVE = '.sidebar-row-wrapper--selected .sidebar-row--active::part(base)'
    const lit = rule(rules, [ACTIVE, SELECTED_ACTIVE], topLevel)
    assert.ok(lit.decls['border-color'], 'border-color')
    assert.equal(lit.decls['background-origin'], 'border-box')
    assert.match(lit.decls['background-image'], /^linear-gradient\(100deg, var\(--wa-color-brand-fill-normal\), /)
    assert.equal(lit.decls.color, 'var(--wa-color-brand-on-quiet)')
    const multi = rule(rules, ['.sidebar-row-wrapper--selected .sidebar-row::part(base)'], topLevel)
    assert.ok(lit.order > multi.order, 'after the multi-select rule')
    const dark = rule(rules, [`html.wa-dark ${ACTIVE}`], topLevel)
    assert.match(dark.decls['background-image'], /^linear-gradient\(100deg, oklch\(from var\(--wa-color-brand-60\) /)
    for (const selector of [SELECTED_ACTIVE, `html.wa-dark ${SELECTED_ACTIVE}`]) {
        const r = rule(rules, [selector], topLevel)
        assert.equal(r.decls['border-color'], 'var(--wa-color-brand-60)', selector)
        assert.match(r.decls['box-shadow'], /^inset 0 0 0 1px var\(--wa-color-brand-60\), /, selector)
        assert.ok(r.order > lit.order, `${selector}: after the open rule`)
    }
    // No copy left in the components: the global rules are the only ones.
    for (const file of ['../components/session/list/SessionListItem.vue', '../components/artifacts/ArtifactBookmarkList.vue']) {
        const leftovers = parseCss(styleOf(read(file)))
            .flatMap((r) => r.selectors)
            .filter((s) => /--(active|selected)\b[^,]*::part\(base\)/.test(s))
        assert.deepEqual(leftovers, [], `${file}: no --active / --selected ::part(base) rule`)
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

// Live states (visual refresh step 6b, docs/plans/2026-09-30-live-states-design.md §10).
// New rules are pinned whole (ordered declaration list equal to the spec block); existing
// rules are asserted on the declaration they gain.

const inKeyframes = (name) => (r) => r.ancestors.length === 1 && r.ancestors[0] === `@keyframes ${name}`

/** The frames of a top-level @keyframes block, pinned whole: [[selectors, spec body], ...] in order. */
function assertKeyframes(name, frames) {
    const blocks = parseAll(glowCss).filter((r) => r.head === `@keyframes ${name}` && r.ancestors.length === 0)
    assert.equal(blocks.length, 1, `one top-level @keyframes ${name}`)
    const actual = glow.filter(inKeyframes(name))
    assert.deepEqual(actual.map((r) => r.selectors), frames.map(([selectors]) => selectors), `${name}: frames`)
    frames.forEach(([selectors, body], k) => assertPinned(actual[k], body, `${name} ${selectors.join(', ')}`))
}

test('15. live states: the live keyframes (§4; the comet was removed after the browser review)', () => {
    assert.ok(!glowCss.includes('glow-live-angle') && !glowCss.includes('glow-live-spin'), 'no comet left')

    assertKeyframes('glow-live-shimmer', [
        [['from'], 'background-position: 100% 0;'],
        [['to'], 'background-position: -66.667% 0;'],
    ])
    assertKeyframes('glow-live-dot', [
        [['0%', '60%', '100%'], 'translate: none; opacity: 0.35;'],
        [['30%'], 'translate: 0 calc(-0.25rem * var(--motion-amount)); opacity: 1;'],
    ])
    assertKeyframes('glow-live-ring-pulse', [
        [['0%', '100%'], 'opacity: 1;'],
        [['50%'], 'opacity: 0.65;'],
    ])
})

const WAM = '../components/session/detail/items/WorkingAssistantMessage.vue'

// §5.2 and §5.3, as written in the spec (comments are stripped before the comparison).
const PILL_RULES = [
    ['.working-assistant-message', `
        display: flex;
        width: fit-content;
        align-items: center;
        max-width: 100%;
        gap: var(--wa-space-s);
        font-style: italic;
        font-size: var(--wa-font-size-m);`],
    ['.working-assistant-message__phrase', `
        background: linear-gradient(90deg, var(--wa-color-text-quiet) 0%, var(--wa-color-text-quiet) 38%,
            var(--wa-color-text-normal) 50%, var(--wa-color-text-quiet) 62%, var(--wa-color-text-quiet) 100%);
        background-size: 250% 100%;
        background-clip: text;
        color: transparent;
        animation: glow-live-shimmer 1.47s linear infinite;
        min-width: 0;
        overflow-wrap: anywhere;`],
    ['.working-assistant-message__phrase code', 'color: var(--wa-color-text-normal);'],
    ['.working-assistant-message__dots', `
        display: inline-flex;
        gap: 0.1875rem;
        margin-inline-start: 0.3em;
        color: var(--wa-color-text-quiet);`],
    ['.working-assistant-message__dots i', `
        display: block;
        width: 0.25rem;
        height: 0.25rem;
        border-radius: 50%;
        background: currentColor;
        animation: glow-live-dot 1.4s var(--motion-ease-out) infinite;
        animation-fill-mode: backwards;`],
    ['.working-assistant-message__dots i:nth-child(2)', 'animation-delay: 0.15s;'],
    ['.working-assistant-message__dots i:nth-child(3)', 'animation-delay: 0.3s;'],
]
const CALM_PHRASE = `
    animation: none;
    background: none;
    color: var(--wa-color-text-quiet);`
const CALM_RULES = [
    ['.working-assistant-message--calm .working-assistant-message__phrase', CALM_PHRASE],
    ['.working-assistant-message--calm .working-assistant-message__dots i', 'animation: none; opacity: 1;'],
]
const REDUCED_RULES = [
    ['.working-assistant-message__phrase', CALM_PHRASE],
]

/** The inner HTML of the element opened at `start` (a <span>), matching nested spans. */
function spanInner(html, start) {
    const open = html.indexOf('>', start) + 1
    let depth = 1
    const tags = /<span\b|<\/span>/g
    tags.lastIndex = open
    for (let m = tags.exec(html); m; m = tags.exec(html)) {
        depth += m[0] === '</span>' ? -1 : 1
        if (depth === 0) return html.slice(open, m.index)
    }
    assert.fail('unclosed span')
}

test('16. the working pill: every rule pinned whole, in the stated order (§5.2, §5.3)', () => {
    const rules = parseCss(styleOf(read(WAM)))
    const base = PILL_RULES.map(([selector, body]) => {
        const r = rule(rules, [selector], topLevel)
        assertPinned(r, body, selector)
        return r
    })
    const calm = CALM_RULES.map(([selector, body]) => {
        const r = rule(rules, [selector], topLevel)
        assertPinned(r, body, selector)
        return r
    })
    const reduced = REDUCED_RULES.map(([selector, body]) => {
        const r = rule(rules, [selector], inReduced)
        assertPinned(r, body, `reduced motion ${selector}`)
        return r
    })
    const lastBase = Math.max(...base.map((r) => r.order))
    for (const r of calm) assert.ok(r.order > lastBase, `${r.head}: after the base rules`)
    const firstReduced = Math.min(...reduced.map((r) => r.order))
    for (const r of rules.filter((x) => !inReduced(x))) assert.ok(r.order < firstReduced, `${r.head}: before the reduced-motion block`)
    assert.equal(rules.filter(inReduced).length, 1, 'the reduced-motion block holds the phrase rule only')
    assert.ok(!rules.some((r) => r.head === '.working-assistant-message--calm'), 'no calm root rule: nothing to calm on the root')
})

test('17. the working pill template: phrase spans, dots, calm class (§5.1)', () => {
    const template = collapse(templateOf(read(WAM)))
    const DOTS = '<span class="working-assistant-message__dots" aria-hidden="true"><i></i><i></i><i></i></span>'
    const phrases = [...template.matchAll(/<span\b[^>]*class="working-assistant-message__phrase"[^>]*>/g)]
    assert.equal(phrases.length, 2, 'two phrase spans')
    assert.match(phrases[0][0], /\sv-if="plainPhrase !== null"/)
    assert.match(phrases[1][0], /\sv-else[\s>]/)
    for (const m of phrases) {
        const inner = spanInner(template, m.index)
        assert.ok(inner.endsWith(DOTS), `the dots end the phrase: ${m[0]}`)
        assert.equal(inner.split(DOTS).length, 2, 'one dots span per phrase')
        // No whitespace before the dots, in either branch: it renders a space before them.
        assert.ok(!inner.endsWith(` ${DOTS}`), `no space before the dots: ${m[0]}`)
    }
    // No whitespace between the text and the dots: it would render a space before them.
    assert.ok(spanInner(template, phrases[0].index).endsWith(`}}${DOTS}`), 'plain branch: }}<span class="working-assistant-message__dots"')
    assert.ok(template.includes(`:class="{ 'working-assistant-message--calm': isAwaiting }"`), 'calm class bound to isAwaiting')
    assert.ok(!template.includes('text-content'), 'no text-content class')
    assert.ok(!template.includes('...'), 'no literal "..."')
})

test('18. the breathing unread eye: five rules, six sites (§6)', () => {
    const PULSE = 'motion-status-pulse 2.4s ease-in-out infinite'
    for (const [file, selector] of [
        ['../components/ui/AggregatedProcessIndicator.vue', '.unread-indicator'],
        ['../components/session/list/SessionListItem.vue', '.unread-indicator'],
        ['../components/session/list/SessionListItem.vue', '.compact-unread-indicator'],
        ['../components/app/CommandPalette.vue', '.palette-unread-icon'],
        ['../components/app/SessionSwitcher.vue', '.switcher-unread'],
    ]) {
        assert.equal(rule(parseCss(styleOf(read(file))), [selector], topLevel).decls.animation, PULSE, `${file} ${selector}`)
    }
})

// After the merge of main (background shells): the terminal icon of a shell the agent left
// running breathes like the unread eye, wherever it shows (ProcessIndicator feeds the session
// list, the switcher, the palette and the aggregated indicator; the orchestration tree has its own).
test('18b. the breathing terminal icon of a background shell', () => {
    // Same 2.4s as the eye, a deeper breath (1 → 0.2 instead of 1 → 0.45): the icon is quiet already.
    const PULSE = 'motion-status-pulse-deep 2.4s ease-in-out infinite'
    const motion = parseCss(read('motion.css'))
    assert.deepEqual(rule(motion, ['0%', '100%'], inKeyframes('motion-status-pulse-deep')).decls, { opacity: '1' })
    assert.deepEqual(rule(motion, ['50%'], inKeyframes('motion-status-pulse-deep')).decls, { opacity: '0.2' })
    const indicator = read('../components/ui/ProcessIndicator.vue')
    assert.ok(collapse(templateOf(indicator)).includes(`'process-indicator__icon--shell': effectiveIconName === 'terminal'`), 'ProcessIndicator binds the class to the terminal icon')
    assert.equal(rule(parseCss(styleOf(indicator)), ['.process-indicator__icon--shell'], topLevel).decls.animation, PULSE)

    const node = read('../components/orchestration/OrchestrationNode.vue')
    assert.ok(collapse(node).includes(`icon: 'terminal', pulse: 'shell'`), 'OrchestrationNode flags the terminal status')
    assert.equal(rule(parseCss(read('../components/orchestration/treeNode.css')), ['.orch-status-icon--pulse-shell'], topLevel).decls.animation, PULSE)

    // The USER_TURN status line reads like the working line, without its movement: the phrase
    // keeps the resting (quiet) colour of the shimmer.
    const status = read('../components/session/detail/items/BackgroundWorkStatus.vue')
    assert.ok(collapse(templateOf(status)).includes('<span class="background-work-status__phrase">{{ line.text }}'), 'phrase span')
    assert.equal(rule(parseCss(styleOf(status)), ['.background-work-status__phrase'], topLevel).decls.color, 'var(--wa-color-text-quiet)')

    // A running shell shows the working line's three bouncing dots (same rules, own class names);
    // a cron line, which waits, does not.
    const statusTemplate = collapse(templateOf(status))
    assert.ok(statusTemplate.includes('{{ line.text }}<span v-if="line.kind === \'shells\'" class="background-work-status__dots" aria-hidden="true"><i></i><i></i><i></i></span></span>'), 'dots after the shells phrase, no space')
    const dots = parseCss(styleOf(status))
    assertPinned(rule(dots, ['.background-work-status__dots'], topLevel), `display: inline-flex;
        gap: 0.1875rem;
        margin-inline-start: 0.3em;
        color: var(--wa-color-text-quiet);`)
    assertPinned(rule(dots, ['.background-work-status__dots i'], topLevel), `display: block;
        width: 0.25rem;
        height: 0.25rem;
        border-radius: 50%;
        background: currentColor;
        animation: glow-live-dot 1.4s var(--motion-ease-out) infinite;
        animation-fill-mode: backwards;`)
    assert.equal(rule(dots, ['.background-work-status__dots i:nth-child(2)'], topLevel).decls['animation-delay'], '0.15s')
    assert.equal(rule(dots, ['.background-work-status__dots i:nth-child(3)'], topLevel).decls['animation-delay'], '0.3s')
})

// The upload strip in the Signature spirit: a band that unfolds (height + fade
// like the footer blocks), lines that rise in, a lit gradient bar with a glow, the percent in
// the accent colour.
test('18c. the upload strip: unfolding, upload icon, lit bar', () => {
    const sfc = read('../components/files/UploadStrip.vue')
    const tree = parseCss(styleOf(sfc))
    const template = collapse(templateOf(sfc))
    assert.ok(template.includes('<Transition name="upload-strip"> <div v-if="lines.length" class="upload-strip"> <div class="upload-strip-inner"> <div class="upload-lines">'), 'Transition > strip > inner > lines')

    assertPinned(rule(tree, ['.upload-strip'], topLevel), `--upload-line-height: 1.9rem;
        flex: 0 0 auto;
        display: grid;
        grid-template-rows: 1fr;
        border-bottom: 1px solid color-mix(in oklab, var(--glow-accent) 18%, var(--wa-color-surface-border));
        background: var(--wa-color-surface-lowered);
        font-size: var(--wa-font-size-s);`)
    assert.ok(!tree.some((r) => r.selectors.includes('html.wa-dark .upload-strip')), 'no dark tint: the grey backgrounds get their own pass')
    assertPinned(rule(tree, ['.upload-strip-inner'], topLevel), `min-height: 0;
        max-height: calc(4 * var(--upload-line-height) + 2 * var(--wa-space-2xs));
        overflow: auto;`)
    assertPinned(rule(tree, ['.upload-lines'], topLevel), 'padding: var(--wa-space-2xs) var(--wa-space-s);')

    // Unfolds and folds: the row track and the border go with the fade (no overflow flash).
    assertPinned(rule(tree, ['.upload-strip-enter-active', '.upload-strip-leave-active'], topLevel), `transition:
            grid-template-rows var(--motion-dur-3) var(--motion-ease-out-height),
            border-bottom-width var(--motion-dur-3) var(--motion-ease-out-height),
            opacity var(--motion-dur-2) ease-in-out;`)
    assertPinned(rule(tree, ['.upload-strip-enter-from', '.upload-strip-leave-to'], topLevel), `grid-template-rows: 0fr;
        border-bottom-width: 0;
        opacity: 0;`)
    assertPinned(rule(tree, ['.upload-strip-enter-active .upload-strip-inner', '.upload-strip-leave-active .upload-strip-inner'], topLevel), 'overflow: hidden;')

    // The upload icon of the tab status leads each line: it breathes while the file goes up, then
    // gives way to a green check (the colour of a finished turn) on the line kept for a moment.
    assert.ok(template.includes(`<wa-icon :name="line.done ? 'check' : 'upload'" class="upload-icon" :class="{ 'upload-icon--done': line.done }" aria-hidden="true"></wa-icon> <div class="upload-names">`), 'upload / check icon before the names')
    assertPinned(rule(tree, ['.upload-icon'], topLevel), `flex: 0 0 auto;
        font-size: var(--wa-font-size-s);
        animation: motion-status-pulse 2.4s ease-in-out infinite;`)
    assertPinned(rule(tree, ['.upload-icon--done'], topLevel), `color: var(--wa-color-success-60);
        animation: none;`)
    assertPinned(rule(tree, ['.upload-line--done'], topLevel), 'animation: none;')
    const script = collapse(sfc.slice(sfc.indexOf('<script'), sfc.indexOf('</script>')))
    assert.ok(script.includes('const DONE_VISIBLE_MS = 1800'), 'a finished line stays 1.8s')
    assert.ok(script.includes('uploads.onCompleted(record => {'), 'finished lines come from the completion event')
    assert.ok(script.includes('originKey(record.origin) !== originKey(props.origin)'), 'only the panel\'s own uploads')
    // Only a line that was live in this panel: the controller replays `completed` for the uploads
    // already finished on the server at each reload, which must not bring lines back.
    assert.ok(script.includes('uploads.entriesForOrigin(props.origin).find(e => e.key === record.id || (record.client_id && e.clientId === record.client_id))'), 'a live line only')
    // Removal by key, not by identity: the ref's array hands out proxies, never the raw object.
    assert.ok(script.includes('finished.value = finished.value.filter(l => l.entry.key !== line.entry.key)'), 'removed by key')
    assert.ok(!script.includes('l !== line'), 'no identity comparison')
    assert.ok(script.includes('unsubscribeCompleted?.()') && script.includes('clearTimeout(timer)'), 'cleanup on unmount')
    assert.ok(template.includes(`:class="{ 'upload-line--done': line.done }"`), 'done line class')

    // A line rises in (movement × --motion-amount: reduced motion keeps the fade).
    assert.equal(rule(tree, ['.upload-line'], topLevel).decls.animation, 'upload-line-in var(--motion-dur-2) var(--motion-ease-out) backwards')
    assertPinned(rule(tree, ['from'], (r) => r.ancestors.length === 1 && r.ancestors[0] === '@keyframes upload-line-in'), `opacity: 0;
        translate: 0 calc(0.375rem * var(--motion-amount));`)

    // The bar: the Tasks tab's look in the accent colour, plus a glow (the track no longer clips).
    assertPinned(rule(tree, ['.upload-progress'], topLevel), `flex: 0 0 5rem;
        --track-height: 0.375rem;
        --track-color: var(--wa-color-neutral-fill-normal);`)
    assertPinned(rule(tree, ['html.wa-dark .upload-progress'], topLevel), '--track-color: var(--wa-color-neutral-border-normal);')
    assertPinned(rule(tree, ['.upload-progress::part(base)'], topLevel), 'overflow: visible;')
    assertPinned(rule(tree, ['.upload-progress::part(indicator)'], topLevel), `background: linear-gradient(90deg, oklch(from var(--wa-color-brand-60) calc(l + 0.08) c h), var(--wa-color-brand-60));
        border-radius: var(--wa-border-radius-pill);
        box-shadow: 0 0 0.25rem color-mix(in oklab, var(--wa-color-brand-60) 40%, transparent);`)
    assertPinned(rule(tree, ['.upload-progress::part(indicator)'], inReduced), 'transition: none;')
    assert.equal(rule(tree, ['.upload-percent'], topLevel).decls.color, 'var(--wa-color-brand)')
})

test('19. the gap above the pill: on the card, after a text block (§5.4)', () => {
    const sfc = read('../components/session/detail/SessionItem.vue')
    const unscoped = [...sfc.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
    const SELECTOR = `.virtual-scroller-item:has( > .session-item[data-kind="assistant_message"] > .text-content:last-child)
        + .virtual-scroller-item > .session-item[data-kind="assistant_message"]:has(> .working-assistant-message:nth-child(2))`
    const r = rule(parseCss(unscoped), [SELECTOR], (x) => x.ancestors.length === 1 && x.ancestors[0] === '.session-items')
    assert.ok(r.head.endsWith(':has(> .working-assistant-message:nth-child(2))'))
    assertPinned(r, '--assistant-card-top-spacing: var(--wa-space-xl);')
})

test('20. the pulsing context ring: rule, keyframes, bindings (§7)', () => {
    const ring = rule(glow, [':where(wa-progress-ring.is-live:is(.context-usage-ring, .onode-context-ring))::part(indicator)'], topLevel)
    assertPinned(ring, 'animation: glow-live-ring-pulse 2.4s ease-in-out infinite;')
    assert.deepEqual(rule(glow, ['50%'], inKeyframes('glow-live-ring-pulse')).decls, { opacity: '0.65' })

    const ringTags = (file) => [...collapse(templateOf(read(file))).matchAll(/<wa-progress-ring\b[^>]*>/g)].map((m) => m[0])
    const header = read('../components/session/detail/SessionHeader.vue')
    assert.ok(collapse(header).includes('isContextRingLive(processState.value, store.getPendingRequests(props.sessionId))'), 'SessionHeader: the helper')
    const headerRings = ringTags('../components/session/detail/SessionHeader.vue')
    assert.equal(headerRings.length, 2, 'SessionHeader: two rings')
    for (const tag of headerRings) assert.ok(tag.includes(`:class="{ 'is-live': contextRingLive }"`), tag)
    for (const [file, binding] of [
        ['../components/orchestration/OrchestrationNode.vue', `:class="{ 'is-live': nodeData?.process?.state === 'assistant_turn' }"`],
        ['../components/orchestration/AgentTreeNode.vue', `:class="{ 'is-live': isRunning }"`],
    ]) {
        const tags = ringTags(file)
        assert.equal(tags.length, 1, `${file}: one ring`)
        assert.ok(tags[0].includes(binding), `${file}: ${binding}`)
    }
})

test('21. the pending request card and the accent fixes (§8)', () => {
    const form = parseCss(styleOf(read('../components/message/PendingRequestForm.vue')))
    const card = rule(form, ['.pending-request-form:not(.minimized)'], topLevel)
    assertPinned(card, `
        margin: var(--wa-space-xs);
        border: 1px solid transparent;
        border-radius: var(--wa-border-radius-l);
        background:
            linear-gradient(var(--wa-color-surface-default), var(--wa-color-surface-default)) padding-box,
            linear-gradient(120deg, var(--glow-accent), var(--glow-accent-shifted), var(--glow-accent)) border-box;
        box-shadow: 0 0 1rem -0.5rem color-mix(in oklab, var(--glow-accent) 45%, transparent);`)
    const maximized = rule(form, ['.pending-request-form.maximized'], topLevel)
    assertPinned(maximized, `
        margin: 0;
        border-radius: var(--pending-maximized-radius, 0);
        box-shadow: none;`)
    assert.ok(maximized.order > card.order, 'the maximized rule follows the card rule')

    const list = parseCss(styleOf(read('../components/session/detail/SessionItemsList.vue')))
    assert.equal(rule(list, ['.session-items-list'], topLevel).decls['--pending-maximized-radius'],
        '0 0 var(--panel-inner-radius) var(--panel-inner-radius)')
    assertPinned(rule(list, ['.session-items-list.panel-card'], topLevel), '--pending-maximized-radius: var(--panel-inner-radius);')

    for (const file of [
        '../components/message/PendingRequestForm.vue',
        '../components/session/detail/items/claude_code/PendingRequestBody.vue',
        '../components/session/detail/items/codex/RequestUserInputBody.vue',
    ]) {
        assert.ok(!read(file).includes('--wa-color-primary'), `${file}: no --wa-color-primary`)
    }
    assert.equal(rule(form, ['.question-icon'], topLevel).decls.color, 'var(--wa-color-brand-60)')
    for (const file of [
        '../components/session/detail/items/claude_code/PendingRequestBody.vue',
        '../components/session/detail/items/codex/RequestUserInputBody.vue',
    ]) {
        assert.equal(rule(parseCss(styleOf(read(file))), ['.other-toggle-link'], topLevel).decls.color,
            'var(--wa-color-brand-60)', `${file}: .other-toggle-link`)
    }
})
