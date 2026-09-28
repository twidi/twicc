// CSS invariants of the glass overlays (visual refresh step 3,
// docs/plans/2026-09-27-glass-design.md). Same approach as depth.test.js: the stylesheets
// and components are read as text and checked for what the design relies on.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative, basename } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const srcDir = join(here, '..')
const read = (rel) => readFileSync(join(here, rel), 'utf8')

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')

/** Split on commas at parenthesis depth 0 (selector lists, shadow lists). */
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

/** Every declaration of a flat rule body as [property, value] pairs, in order. */
function declarationList(body) {
    const list = []
    for (const part of body.split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) list.push([match[1], collapse(match[2])])
    }
    return list
}
const declarationMap = (body) => Object.fromEntries(declarationList(body))

/** At-rule-aware reader: a tree of { type: 'rule', selector, selectors, body, decls }
 *  and { type: 'at', prelude, children } nodes, in source order, comments stripped. */
function parseBlocks(text) {
    const nodes = []
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
        if (head.startsWith('@')) nodes.push({ type: 'at', prelude: head, children: parseBlocks(body) })
        else nodes.push({ type: 'rule', selector: head, selectors: splitTopLevel(head), body, decls: declarationMap(body) })
        i = j
    }
    return nodes
}

/** Every rule of the tree, at-rule children included. */
function allRules(nodes) {
    return nodes.flatMap((n) => (n.type === 'rule' ? [n] : allRules(n.children)))
}

const sameSelectors = (rule, selectors) =>
    rule.selectors.length === selectors.length && selectors.every((s) => rule.selectors.includes(collapse(s)))

/** The unique rule (among `rules`) whose selector list is exactly `selectors` (any order). */
function findRule(rules, selectors, label = selectors.join(', ')) {
    const found = rules.filter((r) => sameSelectors(r, selectors))
    assert.equal(found.length, 1, `expected exactly one rule "${label}", found ${found.length}`)
    return found[0]
}

/** Merge of the custom-property declarations of every top-level block with this selector. */
function mergedTopLevel(nodes, selector) {
    const blocks = nodes.filter((n) => n.type === 'rule' && n.selector === selector)
    assert.ok(blocks.length, `no top-level block "${selector}"`)
    return Object.assign({}, ...blocks.map((b) => b.decls))
}

const glassCss = read('glass.css')
const glassTree = parseBlocks(stripComments(glassCss))
const glassRules = allRules(glassTree)
const topRules = glassTree.filter((n) => n.type === 'rule')
const topRulesOutsideAt = topRules // top-level rules only (at-rule children excluded)

const LIGHT_TOKENS = [
    '--glass-tint', '--glass-bg', '--glass-border', '--glass-highlight', '--glass-shadow',
    '--glass-filter', '--glass-sticky-bg', '--glass-field-bg', '--glass-item-highlight',
    '--glass-item-hover', '--glass-item-rest', '--glass-veil', '--glass-veil-filter',
    '--glass-tooltip-bg', '--glass-tooltip-filter',
]
const FILTER_TOKENS = ['--glass-filter', '--glass-veil-filter', '--glass-tooltip-filter']

const rootTokens = mergedTopLevel(glassTree, ':root')
const darkTokens = mergedTopLevel(glassTree, '.wa-dark')

test('1. glass tokens: light on :root, dark overrides, translucent, tint from palette steps', () => {
    for (const name of LIGHT_TOKENS) assert.ok(rootTokens[name], `:root ${name} missing`)
    for (const name of ['--glass-tint', '--glass-border', '--glass-highlight', '--glass-shadow', '--glass-veil']) {
        assert.ok(darkTokens[name], `.wa-dark ${name} missing`)
    }
    for (const name of ['--glass-bg', '--glass-sticky-bg', '--glass-field-bg', '--glass-item-highlight',
        '--glass-item-hover', '--glass-item-rest', '--glass-veil', '--glass-tooltip-bg']) {
        assert.match(rootTokens[name], /\btransparent\b/, `:root ${name} must be translucent`)
    }
    assert.match(darkTokens['--glass-veil'], /\btransparent\b/, '.wa-dark --glass-veil must be translucent')
    for (const [scheme, tokens] of [['light', rootTokens], ['dark', darkTokens]]) {
        const tint = tokens['--glass-tint']
        assert.ok(tint.includes('color-mix('), `${scheme} --glass-tint must use color-mix`)
        assert.ok(tint.includes('--wa-color-brand-'), `${scheme} --glass-tint must use a brand step`)
        assert.ok(!tint.includes('oklch('), `${scheme} --glass-tint must not force a chroma`)
    }
    assert.equal(rootTokens['--wa-color-overlay-modal'], 'var(--glass-veil)')
})

test('2. opaque fallbacks: reduced transparency and no backdrop-filter support', () => {
    const darkIndex = glassTree.findIndex((n) => n.type === 'rule' && n.selector === '.wa-dark')
    assert.ok(darkIndex >= 0)
    const fallbacks = [
        glassTree.findIndex((n) => n.type === 'at' && /^@media \(prefers-reduced-transparency: ?reduce\)$/.test(n.prelude)),
        glassTree.findIndex((n) => n.type === 'at' && /^@supports not \(.*backdrop-filter/.test(n.prelude)),
    ]
    for (const index of fallbacks) {
        assert.ok(index > darkIndex, 'fallback block missing or before .wa-dark')
        const root = glassTree[index].children.find((n) => n.type === 'rule' && n.selector === ':root')
        assert.ok(root, `${glassTree[index].prelude}: no :root block`)
        assert.equal(root.decls['--glass-bg'], 'var(--glass-tint)')
        assert.equal(root.decls['--glass-sticky-bg'], 'var(--glass-tint)')
        assert.equal(root.decls['--glass-field-bg'], 'var(--wa-color-surface-default)')
        assert.equal(root.decls['--glass-tooltip-bg'], 'var(--wa-color-text-normal)')
        for (const name of FILTER_TOKENS) assert.equal(root.decls[name], 'none', `${name} must be none`)
    }
})

function listSourceFiles(dir) {
    return readdirSync(dir, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile() && /\.(css|vue|js|ts)$/.test(e.name) && !e.name.endsWith('.test.js'))
        .map((e) => join(e.parentPath ?? e.path, e.name))
}

test('3. backdrop-filter only in glass.css, only through tokens, only on childless boxes', () => {
    const offenders = listSourceFiles(srcDir)
        .filter((file) => relative(srcDir, file) !== join('styles', 'glass.css'))
        .filter((file) => /backdrop-?filter/i.test(readFileSync(file, 'utf8')))
        .map((file) => relative(srcDir, file))
    assert.deepEqual(offenders, [], 'backdrop-filter outside styles/glass.css')

    const allowedValues = FILTER_TOKENS.map((t) => `var(${t})`)
    let prefixed = 0
    let unprefixed = 0
    for (const rule of glassRules) {
        const filters = declarationList(rule.body).filter(([p]) => p === 'backdrop-filter' || p === '-webkit-backdrop-filter')
        if (!filters.length) continue
        for (const [property, value] of filters) {
            assert.ok(allowedValues.includes(value), `${rule.selector}: ${property} ${value} is not a glass filter token`)
            if (property === 'backdrop-filter') unprefixed++
            else prefixed++
        }
        for (const selector of rule.selectors) {
            const childless = /(::before|::after|::backdrop|::part\(popup__arrow\)|::part\(base__arrow\))$/.test(selector)
                || selector === ':where(wa-select.glass-listbox-direct)::part(listbox)'
            assert.ok(childless, `backdrop-filter on "${selector}", which may have children`)
        }
    }
    assert.ok(unprefixed > 0)
    assert.equal(prefixed, unprefixed, 'each backdrop-filter needs its -webkit- twin')
})

/** Opening tags of a template as { name, attrs, start }, quote-aware (`=>` in a handler). */
function scanTags(source) {
    const tags = []
    for (let i = 0; i < source.length; i++) {
        if (source[i] !== '<' || !/[a-zA-Z]/.test(source[i + 1] ?? '')) continue
        let j = i + 1
        let quote = null
        while (j < source.length) {
            const c = source[j]
            if (quote) {
                if (c === quote) quote = null
            } else if (c === '"' || c === "'") quote = c
            else if (c === '>') break
            j++
        }
        const inner = source.slice(i + 1, j)
        const name = inner.match(/^[\w-]+/)[0]
        tags.push({ name, attrs: inner.slice(name.length), start: i })
        i = j
    }
    return tags
}

/** Class tokens of a tag, from class="…" (split on whitespace) and :class="…" (quoted literals). */
function classTokens(attrs) {
    const tokens = []
    for (const m of attrs.matchAll(/(?:^|\s)(:class|v-bind:class|class)\s*=\s*("([^"]*)"|'([^']*)')/g)) {
        const value = m[3] ?? m[4]
        if (m[1] === 'class') tokens.push(...value.split(/\s+/).filter(Boolean))
        else for (const lit of value.matchAll(/'([^']*)'|`([^`]*)`/g)) tokens.push(...(lit[1] ?? lit[2]).split(/\s+/).filter(Boolean))
    }
    return tokens
}

function templateOf(sfc) {
    return sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '')
}

const GLASS_CLASSES = ['glass-surface', 'glass-sticky', 'glass-veil']
const GLASS_FILES = ['CommandPickerPopup', 'MessageHistoryPickerPopup', 'FilePickerPopup', 'DirectoryPickerPopup',
    'SessionSwitcher', 'TextSelectionComment', 'HoverInfoPanel', 'UsageGraphDialog', 'ContributionSparklines',
    'SharedSubagentView', 'ProjectView', 'CommandPalette', 'SettingsPopover']

test('4. glass classes stay away from pane containers', () => {
    for (const name of ['main-content', 'session-layout', 'center-slot', 'dock-region', 'layout-overlay',
        'frame-host', 'file-pane-preview--fullscreen', 'browser-pane--fullscreen']) {
        assert.ok(!glassCss.includes(name), `glass.css names .${name}`)
    }
    const vueFiles = listSourceFiles(srcDir).filter((f) => f.endsWith('.vue'))
    const directCounts = {}
    for (const file of vueFiles) {
        const component = basename(file, '.vue')
        const tags = scanTags(templateOf(readFileSync(file, 'utf8')))
        const tokens = tags.flatMap((t) => classTokens(t.attrs))
        const glass = tokens.filter((t) => GLASS_CLASSES.includes(t))
        if (glass.length) assert.ok(GLASS_FILES.includes(component), `${component} uses a glass class: not allowed`)
        for (const tag of tags) {
            if (classTokens(tag.attrs).includes('glass-listbox-direct')) {
                assert.equal(tag.name, 'wa-select', `${component}: glass-listbox-direct on <${tag.name}>`)
                directCounts[component] = (directCounts[component] ?? 0) + 1
                if (component === 'PeerMessageReviewDialog') {
                    assert.ok(classTokens(tag.attrs).includes('pr-actions__select'))
                }
            }
        }
        if (component === 'ProjectView') {
            assert.equal(tokens.filter((t) => t === 'glass-surface' || t === 'glass-sticky').length, 0)
            const veils = tags.filter((t) => classTokens(t.attrs).includes('glass-veil'))
            assert.equal(veils.length, 1, 'ProjectView: glass-veil exactly once')
            assert.equal(tokens.filter((t) => t === 'glass-veil').length, 1)
            assert.equal(veils[0].name, 'label')
            assert.ok(classTokens(veils[0].attrs).includes('sidebar-backdrop'))
        }
    }
    assert.deepEqual(directCounts, { SearchOverlay: 4, PeerMessageReviewDialog: 1 })
})

const LAYERED = [
    ':where(wa-dialog)::part(dialog)',
    ':where(wa-dropdown)::part(menu)',
    ':where(wa-dropdown-item)::part(submenu)',
    ':where(wa-select:not(.glass-listbox-direct))::part(listbox)',
    ':where(wa-popover)::part(body)',
    ':where(.glass-surface)',
]
const WA_LAYERED = LAYERED.slice(0, 5)
const BOX = { content: '""', position: 'absolute', inset: '0', 'pointer-events': 'none' }

function assertBox(rule, label) {
    for (const [p, v] of Object.entries(BOX)) assert.equal(rule.decls[p], v, `${label}: ${p}`)
}

test('5. Web Awesome mapping, layers, overlays and fallback block', () => {
    // Token rule (six surfaces).
    const tokenRule = findRule(topRulesOutsideAt, [...WA_LAYERED.slice(0, 3), ':where(wa-select)::part(listbox)', WA_LAYERED[4], ':where(.glass-surface)'])
    assert.deepEqual(
        Object.fromEntries(Object.entries(tokenRule.decls)),
        {
            '--wa-form-control-background-color': 'var(--glass-field-bg)',
            '--wa-color-surface-border': 'var(--glass-border)',
            '--row-bg': 'transparent',
            '--row-hover-bg': 'var(--glass-item-hover)',
            '--row-active-bg': 'var(--glass-item-highlight)',
            '--glass-edge-gap': 'calc(var(--wa-border-width-s) + 1px)',
        },
    )

    // Layered surfaces.
    const layered = findRule(topRulesOutsideAt, LAYERED)
    assert.equal(layered.decls['background-color'], 'transparent')
    assert.equal(layered.decls.border, '0')
    assert.equal(layered.decls['box-shadow'], 'var(--glass-shadow-live)')
    assert.equal(findRule(topRulesOutsideAt, [':where(wa-select:not(.glass-listbox-direct))::part(listbox)']).decls.position, 'static')

    // Layer anchors.
    const anchors = findRule(topRulesOutsideAt, [':where(wa-popover)::part(body)', ':where(.glass-surface)', ':where(.Notivue__notification)'])
    assert.equal(anchors.decls.position, 'relative')
    assert.equal(anchors.decls.isolation, 'isolate')

    // Layers, in two rules.
    const layerHalves = [
        findRule(topRulesOutsideAt, [
            ':where(wa-dialog)::part(dialog)::before',
            ':where(wa-dropdown)::part(menu)::before',
            ':where(wa-dropdown-item)::part(submenu)::after',
            ':where(wa-select:not(.glass-listbox-direct))::part(listbox)::before',
            ':where(wa-popover)::part(body)::before',
        ], 'WA layers'),
        findRule(topRulesOutsideAt, [':where(.glass-surface)::before', ':where(.Notivue__notification)::before'], 'own layers'),
    ]
    for (const rule of layerHalves) {
        assertBox(rule, rule.selector)
        assert.equal(rule.decls['background-color'], 'var(--glass-bg)')
        assert.equal(rule.decls['z-index'], '-1')
        assert.equal(rule.decls['border-radius'], 'inherit')
        assert.ok(rule.decls['backdrop-filter'] && rule.decls['-webkit-backdrop-filter'], `${rule.selector}: filter pair`)
        assert.equal(rule.decls.border, undefined)
        assert.equal(rule.decls['box-shadow'], undefined)
    }

    // Border overlays, split the same way.
    const overlayHalves = [
        findRule(topRulesOutsideAt, [
            ':where(wa-dialog)::part(dialog)::after',
            ':where(wa-dropdown)::part(menu)::after',
            ':where(wa-select:not(.glass-listbox-direct))::part(listbox)::after',
            ':where(wa-popover)::part(body)::after',
        ], 'WA overlays'),
        findRule(topRulesOutsideAt, [':where(.glass-surface)::after', ':where(.Notivue__notification)::after'], 'own overlays'),
    ]
    for (const rule of overlayHalves) {
        assertBox(rule, rule.selector)
        assert.equal(rule.decls['z-index'], '100')
        assert.match(rule.decls.border ?? '', /var\(--glass-border\)/)
        assert.equal(rule.decls['border-radius'], 'inherit')
        assert.equal(rule.decls['box-shadow'], 'var(--glass-highlight)')
    }

    // Submenu: outline and its layer's top edge.
    const submenu = findRule(topRulesOutsideAt, [':where(wa-dropdown-item)::part(submenu)'])
    assert.equal(submenu.decls.outline, 'var(--wa-border-width-s) solid color-mix(in oklab, var(--glass-border) calc(var(--twicc-reveal, 1) * 100%), transparent)')
    assert.equal(submenu.decls['outline-offset'], 'calc(-1 * var(--wa-border-width-s))')
    assert.equal(findRule(topRulesOutsideAt, [':where(wa-dropdown-item)::part(submenu)::after']).decls['box-shadow'], 'var(--glass-highlight)')

    // Direct list boxes.
    const direct = findRule(topRulesOutsideAt, [':where(wa-select.glass-listbox-direct)::part(listbox)'])
    assert.equal(direct.decls['background-color'], 'var(--glass-bg)')
    assert.equal(direct.decls['border-color'], 'var(--glass-border)')
    assert.equal(direct.decls['box-shadow'], 'var(--glass-shadow), var(--glass-highlight)')
    assert.equal(direct.decls['backdrop-filter'], 'var(--glass-filter)')
    assert.equal(direct.decls['-webkit-backdrop-filter'], 'var(--glass-filter)')

    // Every selector list is homogeneous; any other pseudo-element after ::part() stands alone.
    for (const rule of glassRules) {
        const kinds = rule.selectors.map((s) => {
            const m = s.match(/::part\([^)]*\)::?([\w-]+)/)
            if (!m) return 'plain'
            return m[1] === 'before' || m[1] === 'after' ? 'before-after' : `other:${m[1]}`
        })
        if (kinds.some((k) => k.startsWith('other:'))) {
            assert.equal(rule.selectors.length, 1, `"${rule.selector}": a ::part()::<pseudo> must stand alone`)
        } else {
            assert.equal(new Set(kinds).size, 1, `"${rule.selector}": mixed selector list`)
        }
    }

    // Arrows (§5.2).
    const clip = findRule(topRulesOutsideAt, [':where(wa-tooltip)::part(base__arrow)', ':where(wa-popover)::part(popup__arrow)'])
    assert.equal(clip.decls['clip-path'], 'polygon(calc(0% - 1px) 100%, 100% calc(0% - 1px), 100% 100%)')
    assert.equal(findRule(topRulesOutsideAt, [':where(wa-popover)::part(popup)']).decls['--popup-border-width'], '0px')
    const popoverArrow = findRule(topRulesOutsideAt, [':where(wa-popover)::part(popup__arrow)'])
    assert.equal(popoverArrow.decls['background-color'], 'var(--glass-bg)')
    assert.equal(popoverArrow.decls['border-color'], 'var(--glass-border)')
    assert.equal(popoverArrow.decls['backdrop-filter'], 'var(--glass-filter)')
    assert.equal(popoverArrow.decls['-webkit-backdrop-filter'], 'var(--glass-filter)')
    const tooltipArrow = findRule(topRulesOutsideAt, [':where(wa-tooltip)::part(base__arrow)'])
    assert.equal(tooltipArrow.decls['backdrop-filter'], 'var(--glass-tooltip-filter)')
    assert.equal(tooltipArrow.decls['-webkit-backdrop-filter'], 'var(--glass-tooltip-filter)')

    // Tooltip (§5.2).
    const tooltipHost = findRule(topRulesOutsideAt, [':where(wa-tooltip)'])
    assert.equal(tooltipHost.decls['--wa-tooltip-background-color'], 'var(--glass-tooltip-bg)')
    assert.equal(tooltipHost.decls['--wa-tooltip-border-width'], '0px')
    const tooltipBody = findRule(topRulesOutsideAt, [':where(wa-tooltip)::part(body)'])
    assert.equal(tooltipBody.decls['background-color'], 'transparent')
    assert.equal(tooltipBody.decls.position, 'relative')
    assert.equal(tooltipBody.decls.isolation, 'isolate')
    const tooltipLayer = findRule(topRulesOutsideAt, [':where(wa-tooltip)::part(body)::before'])
    assertBox(tooltipLayer, 'tooltip layer')
    assert.equal(tooltipLayer.decls['background-color'], 'var(--glass-tooltip-bg)')
    assert.equal(tooltipLayer.decls['z-index'], '-1')
    assert.equal(tooltipLayer.decls['border-radius'], 'inherit')
    assert.equal(tooltipLayer.decls['backdrop-filter'], 'var(--glass-tooltip-filter)')
    assert.equal(tooltipLayer.decls['-webkit-backdrop-filter'], 'var(--glass-tooltip-filter)')

    // Modal veil (§5.3).
    const backdrop = findRule(topRulesOutsideAt, [':where(wa-dialog)::part(dialog)::backdrop'])
    assert.equal(backdrop.decls['backdrop-filter'], 'var(--glass-veil-filter)')
    assert.equal(backdrop.decls['-webkit-backdrop-filter'], 'var(--glass-veil-filter)')

    // Highlighted rows (§5.4).
    const hoverMedia = glassTree.find((n) => n.type === 'at' && n.prelude === '@media (hover: hover)')
    assert.ok(hoverMedia, 'no @media (hover: hover) block')
    const hover = allRules(hoverMedia.children).find((r) => r.selectors.length === 2 && r.selector.includes('wa-dropdown-item') && r.selector.includes('wa-option'))
    assert.ok(hover, 'no row hover rule')
    const [itemSel, optionSel] = hover.selectors[0].includes('wa-dropdown-item') ? hover.selectors : [...hover.selectors].reverse()
    for (const part of ["[variant='danger']", '[disabled]', ':state(disabled)', ':hover']) assert.ok(itemSel.includes(part), `item hover: ${part}`)
    for (const part of ['[disabled]', ':state(current)', ':hover', ':state(hover)']) assert.ok(optionSel.includes(part), `option hover: ${part}`)
    assert.equal(hover.decls['background-color'], 'var(--glass-item-highlight)')
    const focus = findRule(topRulesOutsideAt, [":where(wa-dropdown-item:not([variant='danger']):focus-visible)"])
    assert.equal(focus.decls['background-color'], 'var(--glass-item-highlight)')

    // Collapsible sections (§5.5).
    const details = topRulesOutsideAt.find((r) => r.selector.includes('wa-details') && r.selector.endsWith('::part(base)'))
    assert.ok(details, 'no wa-details rule')
    for (const part of ['wa-dialog', 'wa-popover', '.glass-surface', "[appearance='filled']", "[appearance='filled-outlined']", "[appearance='plain']"]) {
        assert.ok(details.selector.includes(part), `wa-details rule: ${part}`)
    }
    assert.equal(details.decls['background-color'], 'var(--glass-item-rest)')

    // Sticky and veil classes (§6.1).
    assert.equal(findRule(topRulesOutsideAt, [':where(.glass-sticky)']).decls.isolation, 'isolate')
    const sticky = findRule(topRulesOutsideAt, [':where(.glass-sticky)::before'])
    assertBox(sticky, 'sticky layer')
    assert.equal(sticky.decls['z-index'], '-1')
    assert.equal(sticky.decls['background-color'], 'var(--glass-sticky-bg)')
    assert.ok(sticky.decls['backdrop-filter'] && sticky.decls['-webkit-backdrop-filter'])
    const veil = findRule(topRulesOutsideAt, [':where(.glass-veil)::before'])
    assertBox(veil, 'veil layer')
    assert.equal(veil.decls['z-index'], '-1')
    assert.equal(veil.decls['background-color'], 'var(--glass-veil)')
    assert.equal(veil.decls.opacity, 'var(--glass-veil-opacity, 1)')
    assert.ok(veil.decls['backdrop-filter'] && veil.decls['-webkit-backdrop-filter'])

    // Fallback without ::part()::before: the last top-level block.
    const last = glassTree[glassTree.length - 1]
    assert.equal(last.type, 'at', 'the fallback block must be the last top-level block')
    assert.ok(last.prelude.startsWith('@supports not'), last.prelude)
    assert.ok(last.prelude.includes('selector(:where(wa-dialog)::part(dialog)::before)'), 'fallback condition: ::before')
    assert.ok(last.prelude.includes('selector(:where(wa-dialog)::part(dialog):before)'), 'fallback condition: :before')
    const fallbackRules = allRules(last.children)
    const fbFill = findRule(fallbackRules, WA_LAYERED, 'fallback fills')
    assert.equal(fbFill.decls['background-color'], 'var(--glass-tint)')
    assert.equal(fbFill.decls['box-shadow'], 'var(--glass-shadow), var(--glass-highlight)')
    const fbBorder = findRule(fallbackRules, WA_LAYERED.filter((s) => !s.includes('submenu')), 'fallback borders')
    assert.match(fbBorder.decls.border ?? '', /var\(--glass-border\)/)
    assert.equal(findRule(fallbackRules, [':where(wa-tooltip)']).decls['--wa-tooltip-background-color'], 'var(--wa-color-text-normal)')
    assert.equal(findRule(fallbackRules, [':where(wa-tooltip)::part(body)']).decls['background-color'], 'var(--wa-color-text-normal)')
    assert.equal(findRule(fallbackRules, [':where(wa-popover)::part(popup__arrow)']).decls['background-color'], 'var(--glass-tint)')

    // Dark --glass-shadow = dark --depth-3 without its inset layer.
    const depthTree = parseBlocks(stripComments(read('depth.css')))
    const darkDepth3 = mergedTopLevel(depthTree, '.wa-dark')['--depth-3']
    const [firstLayer, ...outerLayers] = splitTopLevel(darkDepth3)
    assert.match(firstLayer, /^inset\s/)
    assert.deepEqual(splitTopLevel(darkTokens['--glass-shadow']), outerLayers)
    assert.equal(rootTokens['--glass-shadow'], 'var(--depth-3)')
})

test('6. glass.css is imported right after depth.css in the three entry files', () => {
    for (const [file, prefix] of [['../main.js', './styles/'], ['../share-session/main.js', '../styles/'], ['../artifact-shell/main.js', '../styles/']]) {
        const lines = read(file).split('\n').map((l) => l.trim()).filter((l) => l.startsWith('import '))
        const depthAt = lines.indexOf(`import '${prefix}depth.css'`)
        assert.ok(depthAt >= 0, `${file}: no depth.css import`)
        assert.equal(lines[depthAt + 1], `import '${prefix}glass.css'`, `${file}: glass.css must follow depth.css`)
    }
    const spa = read('../main.js')
    assert.ok(spa.indexOf("import './styles/glass.css'") < spa.indexOf("import './styles/surfaces.css'"), 'SPA: glass before surfaces')
})

test('7. toasts take the page scheme and the Notivue list is not clipped', () => {
    const app = read('../App.vue')
    const start = app.indexOf('<Notivue')
    const end = app.indexOf('</Notivue>')
    assert.ok(start >= 0 && end > start, 'no <Notivue> element')
    assert.ok(!app.slice(start, end).includes('wa-invert'), 'toasts inside a .wa-invert box')
    const [notivueTag] = scanTags(app.slice(start))
    const styles = notivueTag.attrs.match(/:styles\s*=\s*"([^"]*)"/)
    assert.ok(styles, '<Notivue> must bind :styles')
    assert.match(styles[1], /list\s*:\s*\{\s*clipPath\s*:\s*'none'\s*\}/)
    assert.match(app, /'--nv-global-bg'\s*:\s*'transparent'/)
})

// ---------------------------------------------------------------------------
// The glass blur stays on while overlays move (overlay motion design §14)
// ---------------------------------------------------------------------------

const REVEAL_OPACITY = 'var(--twicc-reveal, 1)'

test('8. every glass layer fades through its own opacity, read from --twicc-reveal', () => {
    const layers = [
        [':where(wa-dialog)::part(dialog)::before', ':where(wa-dropdown)::part(menu)::before', ':where(wa-dropdown-item)::part(submenu)::after',
            ':where(wa-select:not(.glass-listbox-direct))::part(listbox)::before', ':where(wa-popover)::part(body)::before'],
        [':where(.glass-surface)::before', ':where(.Notivue__notification)::before'],
        [':where(wa-dialog)::part(dialog)::after', ':where(wa-dropdown)::part(menu)::after',
            ':where(wa-select:not(.glass-listbox-direct))::part(listbox)::after', ':where(wa-popover)::part(body)::after'],
        [':where(.glass-surface)::after', ':where(.Notivue__notification)::after'],
        [':where(wa-select.glass-listbox-direct)::part(listbox)'],
        [':where(wa-popover)::part(popup__arrow)'],
    ]
    for (const selectors of layers) assert.equal(findRule(topRulesOutsideAt, selectors).decls.opacity, REVEAL_OPACITY, selectors.join(', '))
    // Tooltips keep their opacity animation (§14.4): their layers do not read the reveal.
    for (const selectors of [[':where(wa-tooltip)::part(body)::before'], [':where(wa-tooltip)::part(base__arrow)']]) {
        assert.equal(findRule(topRulesOutsideAt, selectors).decls.opacity, undefined, selectors[0])
    }
})

test('9. the content of our own glass surfaces and toasts fades through --twicc-reveal-filter', () => {
    const content = findRule(topRulesOutsideAt, [':where(.glass-surface, .Notivue__notification) > *'])
    assert.deepEqual(content.decls, { filter: 'var(--twicc-reveal-filter, none)' })
})

const LIVE_SHADOW_HOSTS = [
    ':where(wa-dialog)::part(dialog)',
    ':where(wa-dropdown)::part(menu)',
    ':where(wa-dropdown-item)::part(submenu)',
    ':where(wa-select:not(.glass-listbox-direct))::part(listbox)',
    ':where(wa-popover)::part(body)',
    ':where(.glass-surface)',
    ':where(.Notivue__notification)',
]
const LIGHT_LIVE_SHADOW = '0 2px 4px oklch(0.2 0.02 275 / calc(0.06 * var(--twicc-reveal, 1))), '
    + '0 12px 28px -4px oklch(0.2 0.02 275 / calc(0.16 * var(--twicc-reveal, 1))), '
    + '0 32px 64px -16px oklch(0.2 0.02 275 / calc(0.24 * var(--twicc-reveal, 1)))'
const DARK_LIVE_SHADOW = '0 2px 6px oklch(0 0 0 / calc(0.45 * var(--twicc-reveal, 1))), '
    + '0 24px 56px -12px oklch(0 0 0 / calc(0.65 * var(--twicc-reveal, 1)))'

test('10. the cast shadow fades with the reveal: --glass-shadow-live on each host, light then dark', () => {
    const light = findRule(topRulesOutsideAt, LIVE_SHADOW_HOSTS, 'light --glass-shadow-live')
    const dark = findRule(topRulesOutsideAt, LIVE_SHADOW_HOSTS.map((s) => `:where(.wa-dark) ${s}`), 'dark --glass-shadow-live')
    assert.deepEqual(light.decls, { '--glass-shadow-live': LIGHT_LIVE_SHADOW })
    assert.deepEqual(dark.decls, { '--glass-shadow-live': DARK_LIVE_SHADOW })
    assert.ok(glassTree.indexOf(dark) > glassTree.indexOf(light), 'the dark rule comes after the light one')

    // Same layers as --depth-3 light and the dark --glass-shadow, alphas scaled by the reveal.
    const unscale = (value) => value.replace(/calc\(([\d.]+) \* var\(--twicc-reveal, 1\)\)/g, '$1')
    const depthTree = parseBlocks(stripComments(read('depth.css')))
    const lightDepth3 = mergedTopLevel(depthTree, ':root')['--depth-3']
    assert.deepEqual(splitTopLevel(unscale(LIGHT_LIVE_SHADOW)), splitTopLevel(lightDepth3))
    assert.deepEqual(splitTopLevel(unscale(DARK_LIVE_SHADOW)), splitTopLevel(darkTokens['--glass-shadow']))

    // The direct list box fades as a whole through its opacity: it keeps --glass-shadow.
    const direct = findRule(topRulesOutsideAt, [':where(wa-select.glass-listbox-direct)::part(listbox)'])
    assert.equal(direct.decls['box-shadow'], 'var(--glass-shadow), var(--glass-highlight)')
    assert.equal(findRule(topRulesOutsideAt, [':where(wa-popover)::part(popup__arrow)']).decls['box-shadow'], undefined, 'the arrow has no shadow')
    assert.ok(rootTokens['--glass-shadow'], '--glass-shadow stays for other readers')
})

test('11. fallback without ::part()::before: the hosts fade through their own opacity', () => {
    const last = glassTree[glassTree.length - 1]
    const fbFill = findRule(allRules(last.children), WA_LAYERED, 'fallback fills')
    assert.equal(fbFill.decls.opacity, REVEAL_OPACITY)
})

test('12. no trace of the opaque-while-moving attempt; header names motion.css', () => {
    const stripped = stripComments(glassCss)
    assert.ok(!stripped.includes('--glass-settle'), 'no --glass-settle')
    assert.ok(!/--glass-[\w-]*motion-bg/.test(stripped), 'no *-motion-bg token')
    assert.ok(!/\btransition\s*:/.test(stripped), 'no settle transition')
    assert.equal(findRule(topRulesOutsideAt, [':where(wa-tooltip)::part(base__arrow)']).decls['background-color'], undefined)
    const last = glassTree[glassTree.length - 1]
    assert.equal(allRules(last.children).filter((r) => r.selector === ':where(wa-tooltip)::part(base__arrow)').length, 0)
    const header = glassCss.slice(0, glassCss.indexOf('*/'))
    assert.match(collapse(header), /except depth\.css \(--depth-3\), motion\.css \(the registered `--twicc-reveal` that the glass layers and shadows read\) and the Web Awesome theme tokens/)
})

test('13. App.vue: the toasts cast the live shadow', () => {
    assert.match(read('../App.vue'), /'--nv-shadow'\s*:\s*'var\(--glass-shadow-live\)'/)
})
