// Text invariants of the gliding indicators (visual refresh step 4c,
// docs/plans/2026-09-28-gliding-indicators-design.md). Same approach as details-motion.test.js:
// the stylesheet and components are read as text and checked for what the design relies on.
// The mechanism itself is tested in utils/glideInk.test.js and composables/useGlideInk.test.js.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** Split on commas at parenthesis depth 0. */
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
 *  { selectors, decls, order, ancestors } (ancestors = the enclosing heads). */
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
        const body = css.slice(open + 1, j - 1)
        const entry = { head, selectors: head.startsWith('@') ? [] : splitTopLevel(head), ancestors, decls: {}, order: out.length }
        out.push(entry)
        entry.decls = declarationsOf(rulesOf(body, [...ancestors, head], out))
        i = j
    }
    // The part outside nested blocks: the caller reads its own declarations from it.
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
    return out
}

const styleOf = (sfc) => [...sfc.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
const scriptOf = (sfc) => sfc.match(/<script setup>([\s\S]*?)<\/script>/)[1]
const templateOf = (sfc) => sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '')

/** The unique rule whose selector list is exactly `selectors` (any order), optionally filtered. */
function rule(rules, selectors, where = () => true) {
    const wanted = selectors.map(collapse)
    const found = rules.filter((r) => r.selectors.length === wanted.length && wanted.every((s) => r.selectors.includes(s)) && where(r))
    assert.equal(found.length, 1, `expected exactly one rule "${wanted.join(', ')}", found ${found.length}`)
    return found[0]
}

// ---------------------------------------------------------------------------
// motion.css
// ---------------------------------------------------------------------------

const motion = parseCss(read('motion.css'))
const isReduced = (r) => r.ancestors.some((a) => /^@media \(prefers-reduced-motion: ?reduce\)$/.test(a))

test('motion.css: glide tokens, no glide under reduced motion', () => {
    const tokens = rule(motion, [':root'], (r) => r.ancestors.length === 0 && '--motion-amount' in r.decls)
    assert.deepEqual(splitTopLevel(tokens.decls['--glide-transition']), [
        'translate var(--motion-dur-3) var(--motion-ease-spring)',
        'width var(--motion-dur-3) var(--motion-ease-spring)',
        'height var(--motion-dur-3) var(--motion-ease-spring)',
    ])
    assert.equal(tokens.decls['--glide-fade'], 'opacity var(--motion-dur-1) var(--motion-ease)')
    const reduced = rule(motion, [':root'], isReduced)
    assert.equal(reduced.decls['--glide-transition'], 'none')
    assert.equal(reduced.decls['--glide-fade'], undefined, 'the fade stays')
})

test('motion.css: the list ink, base → ready → instant', () => {
    const base = rule(motion, ['.glide-ink'])
    assert.deepEqual(base.decls, {
        position: 'absolute',
        top: '0',
        left: '0',
        width: 'var(--glide-w, 0px)',
        height: 'var(--glide-h, 0px)',
        translate: 'var(--glide-x, 0px) var(--glide-y, 0px)',
        'border-radius': 'var(--glide-ink-radius, 0)',
        background: 'var(--glide-ink-bg, transparent)',
        'pointer-events': 'none',
        opacity: '0',
        transition: 'var(--glide-fade)',
    })
    const ready = rule(motion, ['[data-glide-ready] > .glide-ink'])
    assert.deepEqual(ready.decls, { opacity: '1', transition: 'var(--glide-transition)' })
    const instant = rule(motion, ['[data-glide-instant] > .glide-ink'])
    assert.deepEqual(instant.decls, { transition: 'none' })
    assert.ok(base.order < ready.order && ready.order < instant.order, 'base → ready → instant')
    for (const r of [base, ready, instant]) assert.equal(r.ancestors.length, 0, 'top level')
})

// ---------------------------------------------------------------------------
// TabBar
// ---------------------------------------------------------------------------

test('TabBar: the ink rules on ::part(tabs)::after', () => {
    const sfc = read('../components/ui/TabBar.vue')
    const rules = parseCss(styleOf(sfc))
    const base = rule(rules, ['.tab-bar::part(tabs)::after'])
    assert.equal(base.decls.content, "''")
    assert.equal(base.decls.position, 'absolute')
    assert.equal(base.decls['box-sizing'], 'border-box')
    assert.equal(base.decls.width, 'var(--glide-w, 0px)')
    assert.equal(base.decls.height, 'var(--glide-h, 0px)')
    assert.equal(base.decls.translate, 'var(--glide-x, 0px) var(--glide-y, 0px)')
    // Accent glow (step 6a §8): a gradient strip with a glow instead of the border.
    assert.equal(base.decls['border-block-end'], undefined)
    assert.equal(base.decls.border, '0')
    assert.match(base.decls.background, /var\(--glow-ink-thickness\)/)
    assert.ok(base.decls.filter, 'the ink glows')
    assert.equal(base.decls.opacity, '0')
    assert.equal(base.decls.transition, 'var(--glide-fade)')
    const ready = rule(rules, ['.tab-bar[data-glide-ready]::part(tabs)::after'])
    assert.deepEqual(ready.decls, { opacity: '1', transition: 'var(--glide-transition)' })
    const instant = rule(rules, ['.tab-bar[data-glide-instant]::part(tabs)::after'])
    assert.deepEqual(instant.decls, { transition: 'none' })
    assert.ok(base.order < ready.order && ready.order < instant.order)
    const tab = rule(rules, ['.tab-bar[data-glide-ready] > :deep(wa-tab[active])'])
    assert.deepEqual(tab.decls, { 'border-block-end-color': 'transparent' })
})

test('TabBar: controller, active observer, unmount guard', () => {
    const script = scriptOf(read('../components/ui/TabBar.vue'))
    assert.match(script, /\.querySelector\('\[part~="tabs"\]'\)/)
    assert.match(script, /getActive:\s*\(\)\s*=>\s*\(host\.placement === 'top' \? host\.querySelector\(':scope > wa-tab\[active\]'\) : null\)/)
    assert.match(script, /getItems:\s*\(\)\s*=>\s*\[\.\.\.host\.querySelectorAll\(':scope > wa-tab'\)\]/)
    assert.match(script, /flushPseudo:\s*'::after'/)
    const observes = [...script.matchAll(/\w+\.observe\([^)]*\)/g)].map((m) => collapse(m[0]))
    assert.ok(observes.includes("activeObserver.observe(host, { attributes: true, attributeFilter: ['active'], subtree: true })"), 'active observer')
    for (const call of observes) {
        assert.ok(!(call.includes('childList: true') && call.includes('subtree: true')), `no childList + subtree observer: ${call}`)
    }
    assert.match(script, /m\.target\.tagName === 'WA-TAB' && m\.target\.closest\('wa-tab-group'\) === host/)
    // An active switch made just before a view transition glides after its update (overlay bar).
    assert.match(script, /=== host\)\) \{\s*afterViewTransitionUpdate\(\(\) => glideInk\?\.update\(\)\)/)
    // The list observer also re-places the ink (a reorder moves the active tab).
    assert.match(script, /listObserver = new MutationObserver\(\(\) => \{\s*scheduleEnsureActiveVisible\(\)\s*glideInk\?\.update\(\)/)
    // Unmounted during the await: nothing is created.
    const mounted = script.slice(script.indexOf('onMounted(async'))
    const awaitAt = mounted.indexOf('await el.value.updateComplete')
    const guardAt = mounted.indexOf('if (unmounted')
    assert.ok(awaitAt >= 0 && guardAt > awaitAt, 'unmounted guard after the await')
    assert.ok(guardAt < mounted.indexOf('new MutationObserver') && guardAt < mounted.indexOf('createTabGlide('), 'guard before any creation')
    const unmount = script.slice(script.indexOf('onBeforeUnmount('))
    for (const call of ['unmounted = true', 'activeObserver?.disconnect()', 'glideInk?.destroy()']) assert.ok(unmount.includes(call), call)
})

// ---------------------------------------------------------------------------
// Keyboard lists and the settings nav
// ---------------------------------------------------------------------------

const SITES = [
    { file: '../components/app/CommandPalette.vue', container: 'palette-list', ref: 'listRef', row: '.command-item', active: '.command-item.active' },
    { file: '../components/app/SessionSwitcher.vue', container: 'switcher-list', ref: 'listRef', row: '.switcher-row', active: '.switcher-row--active' },
    { file: '../components/app/SearchOverlay.vue', container: 'search-results', ref: 'resultsRef', row: '.search-result-card', active: '.search-result-card.selected' },
    { file: '../components/message/CommandPickerPopup.vue', container: 'picker-list', ref: 'listRef', row: '.picker-item', active: '.picker-item.active', hover: true },
    { file: '../components/message/MessageHistoryPickerPopup.vue', container: 'picker-list', ref: 'listRef', row: '.picker-item', active: '.picker-item.active', hover: true },
    { file: '../components/app/SettingsPopover.vue', container: 'settings-nav', ref: 'navRef', row: '.settings-nav-item', active: null },
]

/** The container's opening tag and what follows it, up to the first child element's tag. */
function firstChildOf(template, containerClass) {
    // Attribute values may hold `>` (v-else-if="results.length > 0"): quote-aware.
    const attrs = `(?:[^>"']|"[^"]*"|'[^']*')*`
    const open = template.match(new RegExp(`<[\\w-]+\\b${attrs}?\\sclass="${containerClass}"${attrs}>`))
    assert.ok(open, `container .${containerClass}`)
    const after = template.slice(open.index + open[0].length)
    return { open: open[0], child: after.match(/<[\w-]+\b[^>]*>/)[0] }
}

test('every list site: ink first child, composable with getItems, positioned rows', () => {
    for (const site of SITES) {
        const sfc = read(site.file)
        const { open, child } = firstChildOf(templateOf(sfc), site.container)
        assert.match(open, new RegExp(`ref="${site.ref}"`), `${site.file}: container ref`)
        assert.match(child, /^<span ref="\w+" class="glide-ink[^"]*" aria-hidden="true">$/, `${site.file}: ink first child`)
        const inkRef = child.match(/ref="(\w+)"/)[1]
        const script = scriptOf(sfc)
        const call = script.slice(script.indexOf('useGlideInk({'), script.indexOf('\n})', script.indexOf('useGlideInk({')))
        assert.ok(call.length > 0, `${site.file}: useGlideInk( call`)
        assert.match(call, new RegExp(`container: ${site.ref},`), `${site.file}: container`)
        assert.match(call, new RegExp(`flushTarget: ${inkRef},`), `${site.file}: flush target`)
        assert.match(call, /getItems: /, `${site.file}: getItems`)
        assert.ok(call.includes(`${site.ref}.value?.querySelectorAll('${site.row}')`), `${site.file}: getItems rows`)
        const activeSelector = site.active ?? '.settings-nav-item.active'
        assert.ok(call.includes(`${site.ref}.value?.querySelector('${activeSelector}')`), `${site.file}: getActive in the container`)
        assert.ok(!call.includes('document'), `${site.file}: never document`)

        const rules = parseCss(styleOf(sfc))
        const container = rule(rules, [`.${site.container}`], (r) => r.ancestors.length === 0)
        assert.equal(container.decls.position, 'relative', `${site.file}: container positioned`)
        assert.ok(container.decls['--glide-ink-bg'], `${site.file}: ink background`)
        assert.ok(container.decls['--glide-ink-radius'], `${site.file}: ink radius`)
        const row = rule(rules, [site.row], (r) => r.ancestors.length === 0)
        assert.equal(row.decls.position, 'relative', `${site.file}: rows positioned`)
        if (site.active) {
            const transparent = rules.filter((r) => r.selectors.includes(`.${site.container}[data-glide-ready] > ${site.active}`))
            assert.equal(transparent.length, 1, `${site.file}: transparent active row`)
            assert.equal(transparent[0].decls.background, 'transparent')
        }
        if (site.hover) {
            const hover = rule(rules, [`.${site.container}[data-glide-ready] > ${site.active}`, `.${site.container}[data-glide-ready] > .picker-item:hover`])
            assert.equal(hover.decls.background, 'transparent', `${site.file}: hover fill off`)
        }
    }
})

test('list sites: ink colors and radii, sources and reset keys', () => {
    const expect = {
        '../components/app/CommandPalette.vue': ['var(--glass-item-highlight)', 'var(--wa-border-radius-s)', 'sources: [activeKey, visibleItems, navEpoch]', 'resetKey: () => `${parentCommand.value?.id ?? \'\'}|${query.value}|${navEpoch.value}`'],
        '../components/app/SessionSwitcher.vue': ['var(--glass-item-highlight)', 'var(--wa-border-radius-m)', 'sources: [cursor, rows]', 'resetKey: () => mode.value'],
        '../components/app/SearchOverlay.vue': ['var(--glass-item-highlight)', 'var(--wa-border-radius-s)', 'sources: [selectedIndex, results]', 'resetKey: () => results.value'],
        '../components/message/CommandPickerPopup.vue': ['var(--glass-item-highlight)', '0', 'sources: [activeIndex, filteredCommands]', 'resetKey: () => searchQuery.value'],
        '../components/message/MessageHistoryPickerPopup.vue': ['var(--glass-item-highlight)', '0', 'sources: [activeIndex, filteredMessages]', 'resetKey: () => searchQuery.value'],
        '../components/app/SettingsPopover.vue': ['linear-gradient(100deg, var(--wa-color-brand-fill-normal), var(--wa-color-brand-fill-quiet))', 'var(--wa-border-radius-m)', 'sources: [activeSection, sections]', null],
    }
    for (const site of SITES) {
        const sfc = read(site.file)
        const [bg, radius, sources, resetKey] = expect[site.file]
        const container = rule(parseCss(styleOf(sfc)), [`.${site.container}`], (r) => r.ancestors.length === 0)
        assert.equal(container.decls['--glide-ink-bg'], bg, site.file)
        assert.equal(container.decls['--glide-ink-radius'], radius, site.file)
        const script = scriptOf(sfc)
        const call = script.slice(script.indexOf('useGlideInk({'), script.indexOf('\n})', script.indexOf('useGlideInk({')))
        // The trailing comma ends the value: a longer expression does not match.
        assert.ok(call.includes(`${sources},`), `${site.file}: ${sources}`)
        if (resetKey) assert.ok(call.includes(`${resetKey},`), `${site.file}: ${resetKey}`)
        else assert.ok(!call.includes('resetKey'), `${site.file}: no reset key`)
    }
})

test('SettingsPopover: item positioned after all: unset, no fill made transparent, no ink on mobile', () => {
    const sfc = read('../components/app/SettingsPopover.vue')
    const rules = parseCss(styleOf(sfc))
    const item = stripComments(styleOf(sfc)).match(/\n\.settings-nav-item \{([\s\S]*?)\n\}/)[1]
    const unsetAt = item.indexOf('all: unset;')
    const positionAt = item.indexOf('position: relative;')
    assert.ok(unsetAt >= 0 && positionAt > unsetAt, 'position: relative after all: unset')
    assert.ok(!rules.some((r) => r.selectors.some((s) => s.includes('[data-glide-ready]'))), 'the active item has no background to hide')
    const mobile = rule(rules, ['.settings-nav-ink'], (r) => r.ancestors.includes('@media (width < 640px)'))
    assert.deepEqual(mobile.decls, { display: 'none' })
    assert.match(templateOf(sfc), /<span ref="navInkRef" class="glide-ink settings-nav-ink" aria-hidden="true">/)
})

test('CommandPalette: navEpoch bumped in goBack\'s nextTick, next to the re-highlight', () => {
    const script = scriptOf(read('../components/app/CommandPalette.vue'))
    assert.match(script, /const navEpoch = ref\(0\)/)
    const goBack = script.slice(script.indexOf('function goBack()'), script.indexOf('// ─── Keyboard navigation'))
    const tick = goBack.slice(goBack.indexOf('nextTick(() => {'))
    const bumpAt = tick.indexOf('navEpoch.value++')
    const activeAt = tick.indexOf('activeKey.value = parentId')
    assert.ok(bumpAt > 0 && activeAt > 0, 'both inside the nextTick callback')
    // Before the `if`: bumped whether or not the parent is found.
    assert.ok(bumpAt < tick.indexOf('if (visibleItems.value.some('), 'bumped unconditionally')
    assert.ok(!goBack.slice(0, goBack.indexOf('nextTick(')).includes('navEpoch'), 'not before the nextTick')
})

test('SearchOverlay: a visited selected card dims the ink', () => {
    const rules = parseCss(styleOf(read('../components/app/SearchOverlay.vue')))
    const dim = rule(rules, ['.search-results[data-glide-ready]:has(> .search-result-card.selected.visited) > .glide-ink'])
    assert.deepEqual(dim.decls, { opacity: '0.5' })
})

// ---------------------------------------------------------------------------
// SegmentedControl and its consumers
// ---------------------------------------------------------------------------

test('SegmentedControl: button radios, ink, not-ready fill, change guard, value property', () => {
    const sfc = read('../components/ui/SegmentedControl.vue')
    const template = templateOf(sfc)
    const { child } = firstChildOf(template, 'segmented-control')
    assert.equal(child, '<span ref="inkRef" class="glide-ink" aria-hidden="true">')
    assert.match(template, /<wa-radio v-for="option in options" :key="option.value" appearance="button" :value="option.value">/)
    assert.match(template, /:value\.prop="modelValue"/)
    const script = scriptOf(sfc)
    assert.match(script, /if \(event\.target !== event\.currentTarget\) return/)
    assert.match(script, /emit\('update:modelValue', event\.target\.value\)/)
    assert.match(script, /\.find\(\(radio\) => radio\.value === props\.modelValue\)/)
    assert.ok(!script.includes("getAttribute('value')"), 'the property, not the attribute')
    assert.match(script, /container: frameRef,\s*flushTarget: inkRef,/)
    assert.match(script, /getItems: \(\) => \[\.\.\.\(frameRef\.value\?\.querySelectorAll\('wa-radio'\) \?\? \[\]\)\]/)
    assert.match(script, /sources: \[\(\) => props\.modelValue, \(\) => props\.options\]/)

    const rules = parseCss(styleOf(sfc))
    const frame = rule(rules, ['.segmented-control'])
    assert.equal(frame.decls.position, 'relative')
    assert.equal(frame.decls['--glide-ink-bg'], 'var(--wa-color-brand-fill-normal)')
    const button = rule(rules, ["wa-radio[appearance='button']"])
    assert.equal(button.decls.margin, '0')
    assert.equal(button.decls['background-color'], 'transparent')
    const notReady = rule(rules, [".segmented-control:not([data-glide-ready]) wa-radio[appearance='button']:state(checked)"])
    assert.deepEqual(notReady.decls, { 'background-color': 'var(--glide-ink-bg)' })
    assert.equal(rule(rules, ['wa-radio-group']).decls.position, 'relative')
})

test('the two segmented controls use SegmentedControl', () => {
    const task = read('../components/message/AgentSettingsBenchmarkTask.vue')
    assert.match(templateOf(task), /<SegmentedControl\s+class="task-control task-favor"\s+label="Favor"\s+:model-value="store\.favor"\s+:options="FAVOR_OPTIONS"\s+@update:model-value="store\.setFavor"\s*\/>/)
    assert.match(scriptOf(task), /import SegmentedControl from '\.\.\/ui\/SegmentedControl\.vue'/)
    assert.ok(!task.includes('wa-radio'), 'no wa-radio-group left')
    assert.ok(!task.includes('onFavorChange'))

    const orch = read('../components/orchestration/OrchestrationPanel.vue')
    assert.match(templateOf(orch), /<SegmentedControl\s+v-if="canSwitchView"\s+class="orch-view-switch"\s+label="Tree to show"\s+:model-value="view"\s+:options="VIEW_OPTIONS"\s+@update:model-value="selectedView = \$event"\s*\/>/)
    assert.match(scriptOf(orch), /import SegmentedControl from '\.\.\/ui\/SegmentedControl\.vue'/)
    assert.ok(!orch.includes('wa-button-group'), 'no wa-button-group left')
})

// ---------------------------------------------------------------------------
// Web Awesome contract guard (re-read design §6.1 and §7 when this fails)
// ---------------------------------------------------------------------------

test('Web Awesome guard: version, tab-group and radio chunks', () => {
    const require = createRequire(import.meta.url)
    const root = dirname(require.resolve('@awesome.me/webawesome/package.json'))
    const chunk = (name) => collapse(readFileSync(join(root, 'dist/chunks', name), 'utf8'))
    assert.equal(JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version, '3.3.1')

    const tabStyles = chunk('chunk.R3LBB5FI.js')
    assert.ok(tabStyles.includes('src/components/tab-group/tab-group.styles.ts'))
    assert.ok(tabStyles.includes('.tab-group-top ::slotted(wa-tab[active]) { border-block-end: solid var(--safe-track-width) var(--indicator-color);'))
    assert.ok(tabStyles.includes('.tabs { display: flex; position: relative; }'))
    assert.ok(chunk('chunk.PVB6QGII.js').includes('<div part="tabs" class="tabs" role="tablist">'))

    const group = chunk('chunk.PTCJO2KT.js')
    assert.ok(group.includes('// src/components/radio-group/radio-group.ts'))
    assert.ok(group.includes('this.dispatchEvent(new Event("change", { bubbles: true, composed: true }));'))
    const groupStyles = chunk('chunk.OJS3MSZI.js')
    assert.ok(groupStyles.includes("[part~='form-control-input'] { display: flex;"))
    const radioStyles = chunk('chunk.BELHQIBT.js')
    assert.ok(radioStyles.includes("src/components/radio/radio.styles.ts"))
    assert.ok(radioStyles.includes(":host([appearance='button']) { margin: 0;"))
    assert.ok(radioStyles.includes(":host([appearance='button'][data-wa-radio-horizontal][data-wa-radio-inner]) { border-radius: 0; }"))
    assert.ok(radioStyles.includes(":host([appearance='button']:state(checked)) { border-color: var(--wa-form-control-activated-color); background-color: var(--wa-color-brand-fill-quiet); }"))
})

// ---------------------------------------------------------------------------
// Sidebar lists (step 6a-bis, docs/plans/2026-09-29-sidebar-row-glide-design.md)
// ---------------------------------------------------------------------------

/** The useGlideInk({ … }) call of a script, up to its closing line. */
function glideCallOf(script) {
    const start = script.indexOf('useGlideInk({')
    assert.ok(start >= 0, 'useGlideInk( call')
    return script.slice(start, script.indexOf('\n})', start))
}

test('VirtualScroller: a `before` slot, first child of the scroller root', () => {
    const template = templateOf(read('../components/virtual-scroller/VirtualScroller.vue'))
    const slotAt = template.indexOf('<slot name="before" />')
    const spacerAt = template.indexOf('virtual-scroller-spacer-before')
    assert.ok(slotAt >= 0, 'the before slot')
    assert.ok(slotAt < spacerAt, 'before the first spacer')
    const root = template.indexOf('class="virtual-scroller"')
    const between = template.slice(template.indexOf('>', root) + 1, slotAt)
    assert.ok(!/<[\w-]+\b/.test(between.replace(/<!--[\s\S]*?-->/g, '')), 'no element before the slot')
})

test('SessionList: the ink in the scroller\'s before slot, controller on the open row\'s base part', () => {
    const sfc = read('../components/session/list/SessionList.vue')
    const template = templateOf(sfc)
    assert.match(template, /<template #before><span ref="inkRef" class="glide-ink" aria-hidden="true"><\/span><\/template>/)
    assert.match(template, /class="session-list sidebar-row-list"/)
    const script = scriptOf(sfc)
    assert.match(script, /import \{ activeRowBase, entranceOffset(, [\w, ]+)? \} from '\.\.\/\.\.\/\.\.\/utils\/sidebarRows'/)
    assert.match(script, /const scrollerEl = computed\(\(\) => scrollerRef\.value\?\.\$el \?\? null\)/)
    const call = glideCallOf(script)
    assert.ok(call.includes('container: scrollerEl,'), 'container')
    assert.ok(call.includes('flushTarget: inkRef,'), 'flush target')
    assert.ok(call.includes('getActive: () => activeRowBase(scrollerEl.value),'), 'getActive')
    assert.ok(call.includes('getActiveOffset: () => entranceOffset(scrollerEl.value),'), 'getActiveOffset')
    assert.ok(call.includes("getItems: () => [...(scrollerEl.value?.querySelectorAll('.sidebar-row') ?? [])],"), 'getItems on the row hosts')
    assert.match(collapse(call), /sources: \[\(\) => props\.sessionId, sessions, \(\) => props\.compactView, \(\) => scrollerRef\.value\?\.getVisibleRange\(\)\],/)
    assert.ok(call.includes('resetKey: () => props.projectId,'), 'reset key')
    assert.ok(!call.includes('document'), 'never document')
})

test('ArtifactBookmarkList: the ink first child of the positioned list, controller on the open row\'s base part', () => {
    const sfc = read('../components/artifacts/ArtifactBookmarkList.vue')
    const { open, child } = firstChildOf(templateOf(sfc), 'bookmark-list sidebar-row-list')
    assert.match(open, /ref="listRef"/)
    assert.equal(child, '<span ref="inkRef" class="glide-ink" aria-hidden="true">')
    const script = scriptOf(sfc)
    assert.match(script, /import \{ activeRowBase, entranceOffset(, [\w, ]+)? \} from '\.\.\/\.\.\/utils\/sidebarRows'/)
    const call = glideCallOf(script)
    assert.ok(call.includes('container: listRef,'), 'container')
    assert.ok(call.includes('flushTarget: inkRef,'), 'flush target')
    assert.ok(call.includes('getActive: () => activeRowBase(listRef.value),'), 'getActive')
    assert.ok(call.includes('getActiveOffset: () => entranceOffset(listRef.value),'), 'getActiveOffset')
    assert.ok(call.includes("getItems: () => [...(listRef.value?.querySelectorAll(':scope > .bookmark-entry') ?? [])],"), 'getItems on the entries')
    assert.ok(call.includes('sources: [() => props.activeBookmarkId, list, () => props.compactView],'), 'sources')
    assert.ok(call.includes("resetKey: () => (props.showAllArtifacts ? 'all' : (props.effectiveProjectId ?? '')),"), 'scope reset key')
    assert.ok(!call.includes('document'), 'never document')
    const list = rule(parseCss(styleOf(sfc)), ['.bookmark-list'], (r) => r.ancestors.length === 0)
    assert.equal(list.decls.position, 'relative', 'the ink\'s containing block')
})

test('SidebarListSeparator: positioned, so a gliding ink passes under the label', () => {
    const rules = parseCss(styleOf(read('../components/sidebar/SidebarListSeparator.vue')))
    const root = rule(rules, ['.sidebar-list-separator'], (r) => r.ancestors.length === 0)
    assert.equal(root.decls.position, 'relative')
})

// Reveal the open row only near an edge (step 6a-bis §10): no centring, the bands read from
// the list, the minimal scroll through nearestScrollTop.

/** The body of `function <name>(` in a script, up to its closing line. */
function functionOf(script, name) {
    const start = script.indexOf(`function ${name}(`)
    assert.ok(start >= 0, `function ${name}`)
    return script.slice(start, script.indexOf('\n}', start))
}

test('SessionList: the open session revealed with align nearest and the list\'s bands', () => {
    const sfc = read('../components/session/list/SessionList.vue')
    const script = scriptOf(sfc)
    assert.match(script, /import \{ activeRowBase, entranceOffset, revealBands, revealMargins \} from '\.\.\/\.\.\/\.\.\/utils\/sidebarRows'/)
    const body = functionOf(script, 'scrollToSession')
    const guard = body.indexOf('if (targetSessionId !== props.sessionId) return Promise.resolve()')
    assert.ok(guard >= 0, 'the stale-target guard')
    assert.ok(guard < body.indexOf('retry()'), 'at the start of each attempt, before any retry')
    assert.ok(body.includes('const pad = parseFloat(getComputedStyle(scrollerEl.value).paddingTop) || 0'), 'the list padding')
    assert.ok(body.includes('const { marginTop, marginBottom } = revealMargins(revealBands(scrollerEl.value), pad)'), 'the margins')
    const call = collapse(body.slice(body.indexOf('scrollToKey(')))
    assert.match(call, /^scrollToKey\(targetSessionId, \{ align: 'nearest', marginTop, marginBottom, isCurrent: \(\) => targetSessionId === props\.sessionId, allowSmooth: !cascade\.isArriving\(\), \}\)/)
    assert.ok(!body.includes("'center'"), 'no centring left')
    const list = rule(parseCss(styleOf(sfc)), ['.session-list'], (r) => r.ancestors.length === 0)
    assert.equal(list.decls['--sidebar-row-reveal-cover'], '3.125rem')
})

test('ArtifactBookmarkList: the reveal watchers use revealEntry, keyboard navigation scrollRowIntoView', () => {
    const script = scriptOf(read('../components/artifacts/ArtifactBookmarkList.vue'))
    assert.match(script, /import \{ activeRowBase, entranceOffset, entryReveal, revealBands \} from '\.\.\/\.\.\/utils\/sidebarRows'/)
    const reveal = functionOf(script, 'revealEntry')
    assert.ok(reveal.includes('return nextTick('), 'the promise the cascade hold waits on')
    assert.ok(reveal.includes("':scope > .bookmark-entry'"), 'the entries')
    assert.ok(reveal.includes('offsetTop: el.offsetTop'), 'offsetTop')
    assert.ok(reveal.includes('offsetHeight: el.offsetHeight'), 'offsetHeight')
    assert.ok(reveal.includes('revealBands(list)'), 'the bands of the list')
    // The smooth reveal (§10.7): judged from where a smooth reveal in flight is going.
    assert.ok(reveal.includes('const current = smoothTo ? smoothTo.target : list.scrollTop'), 'the current scroll')
    assert.ok(reveal.includes('scrollTop: current,'), 'entryReveal from the current scroll')
    assert.ok(reveal.includes('if (target === null) return'), 'written only when needed')
    assert.match(collapse(reveal), /revealBehavior\(\{ distance: Math\.abs\(target - current\), viewport: list\.clientHeight, reduced: globalThis\.matchMedia\?\.\('\(prefers-reduced-motion: reduce\)'\)\?\.matches === true, allowed: !cascade\.isArriving\(\), \}\)/)
    const autoWrite = reveal.indexOf('list.scrollTop = target')
    assert.ok(autoWrite >= 0, 'the auto write')
    assert.ok(reveal.lastIndexOf('endSmooth()', autoWrite) >= 0, 'endSmooth() before the auto write')
    assert.ok(reveal.includes('startSmoothScroll(list, target)'), 'the smooth scroll')
    // Its destination is cleared only by its own end (a newer smooth reveal owns it then).
    const tokenAt = reveal.indexOf('const token = ++smoothToken')
    assert.ok(tokenAt >= 0 && tokenAt < reveal.indexOf('startSmoothScroll(list, target)'), 'a token before the smooth scroll')
    assert.ok(reveal.includes('if (smoothTo?.token === token) smoothTo = null'), 'cleared only by its own end')
    assert.ok(!reveal.includes('getBoundingClientRect'), 'no rect: the entrance translate would skew it')
    assert.ok(!reveal.includes('scrollIntoView'), 'no native scroll-into-view')
    // The two reveal watchers: the open id, and the open row joining the scoped list.
    assert.equal(script.match(/cascade\.holdTarget\(list\.value\[i\]\.id, revealEntry\(i\)\)/g)?.length, 2)
    assert.ok(!script.includes('holdTarget(list.value[i].id, scrollRowIntoView'), 'no reveal through scrollRowIntoView')
    assert.ok(functionOf(script, 'handleKeyNavigation').includes('scrollRowIntoView(newIndex)'), 'keyboard navigation unchanged')
})

test('both lists bind sidebar-row-list--compact to compactView', () => {
    const session = templateOf(read('../components/session/list/SessionList.vue'))
    const scroller = session.slice(session.indexOf('<VirtualScroller'), session.indexOf('>', session.indexOf('class="session-list sidebar-row-list"')))
    assert.match(scroller, /:class="\{ 'sidebar-row-list--compact': compactView \}"/)
    const { open } = firstChildOf(templateOf(read('../components/artifacts/ArtifactBookmarkList.vue')), 'bookmark-list sidebar-row-list')
    assert.match(open, /:class="\{ 'sidebar-row-list--compact': compactView \}"/)
})

test('VirtualScroller: the JSDoc lists the nearest alignment and its options', () => {
    const script = read('../components/virtual-scroller/VirtualScroller.vue')
    const doc = (name) => {
        const at = script.indexOf(`\nfunction ${name}(`)
        return script.slice(script.lastIndexOf('/**', at), at)
    }
    assert.match(doc('scrollToIndex'), /'start' \| 'center' \| 'end' \| 'nearest'/)
    for (const option of ['marginTop', 'marginBottom', 'allowSmooth']) assert.ok(doc('scrollToIndex').includes(`options.${option}`), `scrollToIndex ${option}`)
    assert.match(doc('scrollToKey'), /'nearest'/)
    for (const option of ['marginTop', 'marginBottom', 'isCurrent', 'allowSmooth']) assert.ok(doc('scrollToKey').includes(`options.${option}`), `scrollToKey ${option}`)
})

test('useVirtualScroll: onUnmounted removes the input listeners from listenedEl, then clears it', () => {
    const source = read('../composables/useVirtualScroll.js')
    const start = source.indexOf('onUnmounted(() => {')
    assert.ok(start >= 0, 'onUnmounted callback')
    const body = source.slice(start, source.indexOf('\n    })', start))
    const removeAt = body.indexOf('if (listenedEl) removeUserScrollListeners(listenedEl)')
    assert.ok(removeAt >= 0, 'the listeners removed from listenedEl (containerRef is already null)')
    assert.ok(body.indexOf('listenedEl = null', removeAt) > removeAt, 'listenedEl cleared after the removal')
})

// The smooth reveal (step 6a-bis §10.7): the artifacts list ends a smooth reveal in flight on
// a scrolling gesture the browser performs itself, and before a keyboard scroll that moved it.

test('ArtifactBookmarkList: the gestures that end a smooth reveal, and scrollRowIntoView', () => {
    const sfc = read('../components/artifacts/ArtifactBookmarkList.vue')
    const { open } = firstChildOf(templateOf(sfc), 'bookmark-list sidebar-row-list')
    assert.match(open, /@wheel\.passive="endSmooth"/)
    assert.match(open, /@pointerdown\.self="endSmooth"/)
    assert.match(open, /@pointercancel="endSmoothOnTouchPan"/)
    assert.match(open, /@keydown="handleListKeydown"/)
    const script = scriptOf(sfc)
    assert.match(collapse(functionOf(script, 'endSmoothOnTouchPan')), /if \(event\.pointerType === 'touch'\) endSmooth\(\)/)
    assert.match(collapse(functionOf(script, 'handleListKeydown')), /if \(event\.key === ' ' && !event\.defaultPrevented\) endSmooth\(\)/)
    const row = collapse(functionOf(script, 'scrollRowIntoView'))
    assert.match(row, /const before = list\.scrollTop/)
    assert.match(row, /if \(list\.scrollTop !== before\) endSmooth\(\)/, 'only when it moved the list')
    const end = collapse(functionOf(script, 'endSmooth'))
    assert.match(end, /smoothTo = null/)
    assert.match(end, /smoothToken\+\+/)
    assert.match(end, /cancel\(\)/)
})
