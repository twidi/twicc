// CSS invariants of the motion tokens and micro-interactions (visual refresh step 4a,
// docs/plans/2026-09-27-motion-micro-design.md). Same approach as glass.test.js: the
// stylesheets and components are read as text and checked for what the design relies on.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const srcDir = join(here, '..')
const read = (rel) => readFileSync(join(here, rel), 'utf8')

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')

/** Split on commas at parenthesis depth 0 (selector lists, value lists). */
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

/** The part of a rule body outside its nested blocks (CSS nesting). */
function ownBody(body) {
    let out = ''
    let i = 0
    while (i < body.length) {
        const c = body[i]
        if (c === '{') {
            // Drop the nested block's head (back to the last declaration boundary), then the block.
            const boundary = Math.max(out.lastIndexOf(';'), out.lastIndexOf('}'))
            out = out.slice(0, boundary + 1)
            let depth = 1
            i++
            while (i < body.length && depth > 0) {
                if (body[i] === '{') depth++
                else if (body[i] === '}') depth--
                i++
            }
            out += ';'
            continue
        }
        out += c
        i++
    }
    return out
}

/** Every declaration of a rule body as [property, value] pairs, in order (nested blocks skipped). */
function declarationList(body) {
    const list = []
    for (const part of ownBody(body).split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) list.push([match[1], collapse(match[2])])
    }
    return list
}
const declarationMap = (body) => Object.fromEntries(declarationList(body))

/** Nesting-aware reader: a tree of { type: 'rule', selector, selectors, body, decls, children }
 *  and { type: 'at', prelude, children } nodes, in source order, comments stripped. */
function parseBlocks(text) {
    const nodes = []
    let i = 0
    while (i < text.length) {
        const open = text.indexOf('{', i)
        if (open === -1) break
        // A nested block's head starts after the parent's last declaration.
        const rawHead = text.slice(i, open)
        const head = collapse(rawHead.slice(Math.max(rawHead.lastIndexOf(';'), rawHead.lastIndexOf('}')) + 1))
        let depth = 1
        let j = open + 1
        while (j < text.length && depth > 0) {
            if (text[j] === '{') depth++
            else if (text[j] === '}') depth--
            j++
        }
        const body = text.slice(open + 1, j - 1)
        if (head.startsWith('@')) nodes.push({ type: 'at', prelude: head, children: parseBlocks(body) })
        else {
            nodes.push({
                type: 'rule', selector: head, selectors: splitTopLevel(head), body,
                decls: declarationMap(body), children: body.includes('{') ? parseBlocks(body) : [],
            })
        }
        i = j
    }
    return nodes
}

/** Every rule of the tree with its ancestors (at-rules and parent rules), in source order. */
function flatten(nodes, ancestors = []) {
    const out = []
    for (const node of nodes) {
        if (node.type === 'rule') out.push({ rule: node, ancestors })
        if (node.type === 'at' && node.prelude.startsWith('@keyframes')) continue
        out.push(...flatten(node.children, [...ancestors, node]))
    }
    return out
}
const inHoverMedia = (ancestors) => ancestors.some((a) => a.type === 'at' && /^@media \(hover: ?hover\)$/.test(a.prelude))

const sameSelectors = (rule, selectors) =>
    rule.selectors.length === selectors.length && selectors.every((s) => rule.selectors.includes(collapse(s)))

/** The unique rule (among `rules`) whose selector list is exactly `selectors` (any order). */
function findRule(rules, selectors, label = selectors.join(', ')) {
    const found = rules.filter((r) => sameSelectors(r, selectors))
    assert.equal(found.length, 1, `expected exactly one rule "${label}", found ${found.length}`)
    return found[0]
}

/** The CSS of every <style> block of a single-file component. */
function styleOf(sfc) {
    return [...sfc.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')
}
function templateOf(sfc) {
    return sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '')
}
const componentTree = (rel) => parseBlocks(stripComments(styleOf(read(rel))))

const motionCss = read('motion.css')
const motionStripped = stripComments(motionCss)
const motionTree = parseBlocks(motionStripped)
const motionFlat = flatten(motionTree)
const motionRules = motionFlat.map((e) => e.rule)
const topRules = motionTree.filter((n) => n.type === 'rule')
const reducedMedia = motionTree.filter((n) => n.type === 'at' && /^@media \(prefers-reduced-motion: ?reduce\)$/.test(n.prelude))

const TOKENS = {
    '--motion-dur-1': '120ms',
    '--motion-dur-2': '200ms',
    '--motion-dur-3': '380ms',
    '--motion-dur-press': '60ms',
    '--motion-ease': 'cubic-bezier(.2, .8, .2, 1)',
    '--motion-ease-out': 'cubic-bezier(.22, 1, .36, 1)',
    '--motion-ease-out-height': 'cubic-bezier(.25, .46, .45, .94)',
    '--motion-ease-spring': 'linear(0, 0.006, 0.025 2.8%, 0.101 6.1%, 0.539 18.9%, 0.721 25.3%, 0.849 31.5%, 0.937 38.1%, 0.968 41.8%, 0.991 45.7%, 1.006 50.1%, 1.015 55%, 1.017 63.9%, 1.001 85.9%, 1)',
    '--motion-amount': '1',
}

test('1. motion tokens on :root, reduced-motion block after them', () => {
    const tokenBlocks = motionTree.filter((n) => n.type === 'rule' && n.selector === ':root')
    assert.equal(tokenBlocks.length, 1, 'one top-level :root token block')
    for (const [name, value] of Object.entries(TOKENS)) assert.equal(tokenBlocks[0].decls[name], value, name)
    // The :root block (step 4a) and the wa-details chevron block (step 4b).
    assert.equal(reducedMedia.length, 2, 'two top-level reduced-motion blocks')
    assert.ok(motionTree.indexOf(reducedMedia[0]) > motionTree.indexOf(tokenBlocks[0]), 'reduced-motion block must follow the tokens')
})

test('2. Web Awesome tokens: mapping on :root, .wa-invert', () => {
    const mapping = findRule(topRules, [':root', '.wa-invert'])
    assert.deepEqual(mapping.decls, {
        '--wa-transition-fast': 'var(--motion-dur-1)',
        '--wa-transition-normal': 'var(--motion-dur-2)',
        '--wa-transition-slow': 'var(--motion-dur-3)',
        '--wa-transition-easing': 'var(--motion-ease)',
    })
    // Top level (never inside @layer): it must beat the layered theme rule.
    assert.ok(!/@layer/.test(motionStripped), 'motion.css must stay unlayered')
})

test('3. reduced motion is reduced, not none', () => {
    const rules = reducedMedia[0].children
    assert.equal(rules.length, 1)
    assert.equal(rules[0].selector, ':root')
    // Gliding indicators (step 4c): no glide, the ink goes to its place at once.
    assert.deepEqual(rules[0].decls, {
        '--motion-amount': '0',
        '--motion-ease-spring': 'var(--motion-ease)',
        '--glide-transition': 'none',
    })
    // The second block only makes the wa-details chevron turn at once (step 4b).
    const chevron = reducedMedia[1].children
    assert.equal(chevron.length, 1)
    assert.equal(chevron[0].selector, ':where(wa-details)::part(icon)')
    assert.deepEqual(chevron[0].decls, { 'transition-duration': '0s' })
    for (const rule of motionRules) {
        for (const selector of rule.selectors) assert.ok(!/(^|[\s>+~(,])\*/.test(selector), `"${selector}": no * selector`)
    }
    assert.ok(!motionStripped.includes('!important'), 'no !important in motion.css')
})

test('4. status indicators pulse in opacity under reduced motion', () => {
    const pulse = motionTree.filter((n) => n.type === 'at' && n.prelude === '@keyframes motion-status-pulse')
    assert.equal(pulse.length, 1, 'one @keyframes motion-status-pulse')
    const frames = pulse[0].children
    assert.ok(frames.length > 0)
    for (const frame of frames) {
        assert.deepEqual(Object.keys(frame.decls), ['opacity'], `frame "${frame.selector}": opacity only`)
    }

    const localRules = [
        ['robot-working.css', parseBlocks(stripComments(read('robot-working.css'))), '.robot-working', 'motion-status-pulse 1.4s ease-in-out infinite'],
        ['WorkflowStateBadge.vue', componentTree('../components/workflows/WorkflowStateBadge.vue'), '.wf-state-pending', 'motion-status-pulse 1s ease-in-out infinite'],
        ['WorkflowRunDetail.vue', componentTree('../components/workflows/WorkflowRunDetail.vue'), '.wf-row .wf-status-pending', 'motion-status-pulse 1s ease-in-out infinite'],
    ]
    for (const [file, tree, selector, animation] of localRules) {
        const media = tree.filter((n) => n.type === 'at' && /prefers-reduced-motion: ?reduce/.test(n.prelude))
        assert.equal(media.length, 1, `${file}: one reduced-motion block`)
        const rule = findRule(media[0].children, [selector], `${file} ${selector}`)
        assert.equal(rule.decls.animation, animation, `${file}: pulse`)
        const text = JSON.stringify(media[0])
        assert.ok(!text.includes('animation: none') && !/"animation":"none"/.test(text), `${file}: animation: none left`)
    }

    const logo = read('../components/ui/BrandLogo.vue')
    assert.match(logo, /busy:\s*\{\s*type:\s*Boolean,\s*default:\s*false\s*\}/, 'BrandLogo: busy prop')
    assert.match(logo, /'brand-logo--busy':\s*busy/, 'BrandLogo: brand-logo--busy class')
    const logoMedia = componentTree('../components/ui/BrandLogo.vue').filter((n) => n.type === 'at' && /prefers-reduced-motion: ?reduce/.test(n.prelude))
    assert.equal(logoMedia.length, 1)
    assert.equal(findRule(logoMedia[0].children, ['.brand-logo--busy']).decls.animation, 'motion-status-pulse 1.4s ease-in-out infinite')
    assert.ok(
        logoMedia[0].children.some((r) => r.selector.startsWith('.brand-logo--animated') && r.decls.animation === 'none'),
        'BrandLogo: the parts still stop under reduced motion',
    )

    const app = templateOf(read('../App.vue'))
    const logos = [...app.matchAll(/<BrandLogo\b[^>]*>/g)].map((m) => m[0])
    assert.equal(logos.filter((tag) => /\sbusy[\s/>]/.test(tag)).length, 2, 'App.vue: two busy logos')
    const busyContext = [...app.matchAll(/<BrandLogo\b[^>]*\sbusy[\s/>]/g)].map((m) => app.slice(Math.max(0, m.index - 400), m.index))
    assert.ok(busyContext.some((ctx) => ctx.includes('versionMismatchDetected')), 'busy logo in the version-mismatch dialog')
    assert.ok(busyContext.some((ctx) => ctx.includes('isConnecting')), 'busy logo in the connecting overlay')
    for (const view of ['../views/HomeView.vue', '../views/LoginView.vue']) {
        assert.ok(!/<BrandLogo\b[^>]*\sbusy[\s/>]/.test(read(view)), `${view}: no busy logo`)
    }
})

const WA_PROPERTIES = 'background, border, box-shadow, color, opacity'
const FAST = 'var(--wa-transition-fast)'
const EASING = 'var(--wa-transition-easing)'
const BASE_DURATIONS = [FAST, FAST, FAST, FAST, FAST, 'var(--motion-dur-2)']
const PRESS_DURATIONS = [FAST, FAST, FAST, FAST, FAST, 'var(--motion-dur-press)']
const TIMINGS = [EASING, EASING, EASING, EASING, EASING, 'var(--motion-ease-spring)']
const INTERNAL_PARTS = [
    ':where(wa-dialog, wa-drawer)::part(close-button__base)',
    ':where(wa-tab-group)::part(scroll-button__base)',
    ':where(wa-tag)::part(remove-button__base)',
]
const PRESS_SCALE = 'calc(1 - 0.04 * var(--motion-amount))'

/** Rest rules carry the three lists; press rules only swap the duration list. */
function assertTransitionLists(rule, durations, label) {
    assert.deepEqual(splitTopLevel(rule.decls['transition-duration']), durations, `${label}: duration list`)
    if (durations === BASE_DURATIONS) {
        assert.equal(rule.decls['transition-property'], `${WA_PROPERTIES}, scale`, `${label}: property list`)
        assert.deepEqual(splitTopLevel(rule.decls['transition-timing-function']), TIMINGS, `${label}: timing list`)
    } else {
        assert.equal(rule.decls['transition-property'], undefined, `${label}: property list is the rest rule's`)
    }
}

test('5. button press: transition lists and press rules', () => {
    assertTransitionLists(findRule(topRules, [':where(wa-button)::part(base)']), BASE_DURATIONS, 'wa-button base')
    assertTransitionLists(findRule(topRules, INTERNAL_PARTS), BASE_DURATIONS, 'internal parts')

    const press = topRules.filter((r) => r.selector.startsWith(':where(') && r.selector.endsWith(')::part(base)') && r.selector.includes(':active'))
    assert.equal(press.length, 1, 'one wa-button press rule')
    assert.equal(press[0].decls.scale, PRESS_SCALE)
    assertTransitionLists(press[0], PRESS_DURATIONS, 'wa-button press')
    // The :where() list, compounds on one line each (a line break is a descendant combinator).
    const rawPress = motionStripped.slice(motionStripped.indexOf(':where(\n'), motionStripped.indexOf(')::part(base)', motionStripped.indexOf(':where(\n')))
    const branches = rawPress.split('\n').map((l) => l.trim()).filter((l) => l.includes('wa-button'))
    assert.deepEqual(branches, ['wa-button:not([disabled], [loading], wa-button-group wa-button, .session-item, .bookmark-item):active'], 'one press branch, on one line')

    const internalPress = findRule(topRules, INTERNAL_PARTS.map((s) => `${s}:active`))
    assert.equal(internalPress.decls.scale, PRESS_SCALE)
    assertTransitionLists(internalPress, PRESS_DURATIONS, 'internal parts press')
})

test('6. plain icon grow and go arrows', () => {
    const ICON = "wa-icon:only-child:not([slot])"
    const iconRest = findRule(topRules, [`:where(wa-button[appearance='plain']) > ${ICON}`])
    assert.equal(iconRest.decls.transition, 'scale var(--motion-dur-2) var(--motion-ease-spring)')
    const iconHover = motionFlat.filter((e) => e.rule.selector === `:where(wa-button[appearance='plain']:not([disabled]):hover) > ${ICON}`)
    assert.equal(iconHover.length, 1)
    assert.ok(inHoverMedia(iconHover[0].ancestors))
    assert.equal(iconHover[0].rule.decls.scale, 'calc(1 + 0.12 * var(--motion-amount))')

    const ARROW = "wa-icon[slot='end']:is([name='arrow-right'], [name='chevron-right'])"
    const arrowRest = findRule(topRules, [`:where(wa-button) > ${ARROW}`])
    assert.equal(arrowRest.decls.transition, 'translate var(--motion-dur-2) var(--motion-ease-spring)')
    const arrowHover = motionFlat.filter((e) => e.rule.selector === `:where(wa-button:not([disabled]):hover) > ${ARROW}`)
    assert.equal(arrowHover.length, 1)
    assert.ok(inHoverMedia(arrowHover[0].ancestors))
    assert.equal(arrowHover[0].rule.decls.translate, 'calc(0.1875rem * var(--motion-amount)) 0')
})

function listVueFiles(dir) {
    return readdirSync(dir, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile() && e.name.endsWith('.vue'))
        .map((e) => join(e.parentPath ?? e.path, e.name))
}

/** Top-level children of an element's inner HTML: elements ({ name, attrs }) and the text between them. */
function topLevelChildren(inner) {
    const source = inner.replace(/<!--[\s\S]*?-->/g, '')
    const elements = []
    let text = ''
    let depth = 0
    let i = 0
    while (i < source.length) {
        if (source[i] === '<') {
            const close = source[i + 1] === '/'
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
            const tag = source.slice(i + 1, j)
            const selfClosing = tag.endsWith('/')
            if (close) depth--
            else {
                if (depth === 0) {
                    const name = tag.match(/^[\w-]+/)[0]
                    elements.push({ name, attrs: tag.slice(name.length) })
                }
                if (!selfClosing) depth++
            }
            i = j + 1
            continue
        }
        if (depth === 0) text += source[i]
        i++
    }
    return { elements, text: text.trim() }
}

/** Plain wa-buttons whose content is one unslotted wa-icon plus bare text (interpolations count). */
function plainIconPlusBareText(template) {
    const offenders = []
    for (const m of template.matchAll(/<wa-button\b((?:[^>"']|"[^"]*"|'[^']*')*)>([\s\S]*?)<\/wa-button>/g)) {
        if (!/(^|\s)appearance="plain"/.test(m[1])) continue
        const { elements, text } = topLevelChildren(m[2])
        if (elements.length === 1 && elements[0].name === 'wa-icon' && !/(^|\s)(:|v-bind:)?slot\s*=/.test(elements[0].attrs) && text) {
            offenders.push(collapse(m[0]).slice(0, 120))
        }
    }
    return offenders
}

test('7. no plain icon button carries a bare-text label', () => {
    // Self-check of the guard on the pre-step-4a markup of JsonHumanView's diff toggle.
    const before = `<wa-button size="small" variant="neutral" appearance="plain" class="jhv-diff-toggle">
        <wa-icon :name="x ? 'a' : 'b'" variant="classic"></wa-icon>
        {{ x ? 'Diff mode' : 'Old/new mode' }}
    </wa-button>`
    assert.equal(plainIconPlusBareText(before).length, 1, 'the guard must catch bare text')
    assert.equal(plainIconPlusBareText(before.replace(/\{\{[^}]*\}\}/, '<span>$&</span>')).length, 0)

    const offenders = listVueFiles(srcDir).flatMap((file) =>
        plainIconPlusBareText(templateOf(readFileSync(file, 'utf8'))).map((o) => `${relative(srcDir, file)}: ${o}`))
    assert.deepEqual(offenders, [])
    assert.match(read('../components/json/JsonHumanView.vue'), /<span>\{\{ diffSplitMode\[pair\.baseName\] \? 'Diff mode' : 'Old\/new mode' \}\}<\/span>/)
})

/** §6 invariants over a set of { rule, ancestors } entries. `measured`: the rule's translate is a
 *  measured placement (the glide ink), not a movement distance: no --motion-amount check. */
function assertMotionInvariants(entries, label, { measured = false } = {}) {
    for (const { rule, ancestors } of entries) {
        for (const [property, value] of declarationList(rule.body)) {
            if (['translate', 'scale', 'rotate'].includes(property)) {
                if (value !== 'none' && !measured) {
                    assert.ok(value.includes('var(--motion-amount)'), `${label} "${rule.selector}": ${property} ${value} without --motion-amount`)
                }
                if (rule.selector.includes(':hover')) {
                    assert.ok(inHoverMedia(ancestors), `${label} "${rule.selector}": hover movement outside @media (hover: hover)`)
                }
            }
            if (property === 'transform') assert.equal(value, 'none', `${label} "${rule.selector}": transform ${value}`)
        }
    }
}

/** Keyframe blocks with the given name prefix, as flattened entries. */
function keyframeEntries(tree, name) {
    return tree.filter((n) => n.type === 'at' && n.prelude.startsWith(`@keyframes ${name}`))
        .flatMap((n) => n.children.map((rule) => ({ rule, ancestors: [n] })))
}

test('8. invariants: individual transforms, scaled by --motion-amount, hover inside @media (hover: hover)', () => {
    // Exempt from the --motion-amount check only: the .glide-ink translate, a measured
    // placement (step 4c; reduced motion drops its transition instead). Stated in the header.
    const header = motionCss.slice(0, motionCss.indexOf('*/'))
    const isInk = (e) => e.rule.selector === '.glide-ink'
    assert.equal(motionFlat.filter(isInk).length, 1, 'one .glide-ink rule')
    assert.match(header, /\.glide-ink/, 'header states the .glide-ink exception')
    assertMotionInvariants(motionFlat.filter((e) => !isInk(e)), 'motion.css')
    assertMotionInvariants(motionFlat.filter(isInk), 'motion.css', { measured: true })
    // Exempt: motion-spin, the wa-details loading spinner, a status indicator that keeps
    // turning under reduced motion (roadmap §4; stated in the motion.css header).
    assert.match(header, /@keyframes motion-spin/, 'header states the exception')
    const keyframes = keyframeEntries(motionTree, '').filter((e) => e.ancestors[0].prelude !== '@keyframes motion-spin')
    assert.ok(keyframes.length > 0)
    assertMotionInvariants(keyframes, 'motion.css keyframes')

    const changed = [
        ['../components/message/MessageSnippetsBar.vue', (s) => s.includes('.snippet-btn')],
        ['../components/message/MessageInput.vue', (s) => s.includes('.send-button')],
        ['../components/app/SettingsPopover.vue', (s) => s.includes('#settings-trigger')],
        ['../components/session/detail/items/TodoContent.vue', (s) => s.includes('todo-item-icon')],
    ]
    for (const [file, pick] of changed) {
        const tree = componentTree(file)
        const entries = flatten(tree).filter((e) => pick(e.rule.selector))
        assert.ok(entries.length > 0, `${file}: no rule selected`)
        assertMotionInvariants(entries, file)
    }
    assertMotionInvariants(keyframeEntries(componentTree('../components/session/detail/items/TodoContent.vue'), 'todo-check-pop'), 'todo-check-pop')
})

test('9. snippet chips: lift on hover, press as scale, clip room in the narrow composer', () => {
    const tree = componentTree('../components/message/MessageSnippetsBar.vue')
    const flat = flatten(tree)
    const rule = (selector, where = () => true) => {
        const found = flat.filter((e) => e.rule.selector === selector && where(e.ancestors))
        assert.equal(found.length, 1, `one "${selector}" rule`)
        return found[0]
    }
    const base = rule('.snippet-btn').rule
    assert.deepEqual(splitTopLevel(base.decls.transition), [
        'background-color 0.1s', 'border-color 0.1s',
        'scale var(--motion-dur-1) var(--motion-ease)', 'translate var(--motion-dur-2) var(--motion-ease-spring)',
    ])
    const hover = rule('.snippet-btn:hover')
    assert.ok(inHoverMedia(hover.ancestors))
    assert.equal(hover.rule.decls.translate, '0 calc(-1px * var(--motion-amount))')
    const press = rule('.snippet-btn:active').rule
    assert.equal(press.decls.scale, 'calc(1 - 0.05 * var(--motion-amount))')
    const disabledHover = rule('.snippet-btn.snippet-disabled:hover')
    assert.ok(inHoverMedia(disabledHover.ancestors))
    assert.equal(disabledHover.rule.decls.scale, 'none')
    assert.equal(disabledHover.rule.decls.translate, 'none')
    assert.equal(disabledHover.rule.decls.transform, undefined)
    const disabledPress = rule('.snippet-btn.snippet-disabled:active').rule
    assert.equal(disabledPress.decls.scale, 'none')
    const transforms = flat.filter((e) => 'transform' in e.rule.decls).map((e) => e.rule.selector)
    assert.deepEqual(transforms, [], 'no snippet rule declares transform')

    const narrow = tree.filter((n) => n.type === 'at' && n.prelude === '@container message-input (width < 40rem)')
    assert.equal(narrow.length, 1, 'one top-level narrow container query')
    const bar = findRule(narrow[0].children, ['.message-snippets-bar'])
    assert.equal(bar.decls['padding-block-start'], '1px')
    assert.equal(bar.decls['margin-block-start'], '-1px')
})

test('10. session rows, Send icon, settings gear', () => {
    // Session rows (§5.4): no hover movement — the row nudge and the "⋮" slide were tried
    // and removed by the user.
    const rows = flatten(componentTree('../components/session/list/SessionListItem.vue'))
    assert.deepEqual(rows.filter((e) => 'translate' in e.rule.decls).map((e) => e.rule.selector), [])
    const find = (entries, selectors) => findRule(entries.map((e) => e.rule), selectors)

    // Send icon (§5.5): directly in .message-input-actions of the narrow container query.
    const input = componentTree('../components/message/MessageInput.vue')
    const narrow = input.filter((n) => n.type === 'at' && n.prelude === '@container message-input (width < 25rem)')
    assert.equal(narrow.length, 1)
    const actions = findRule(narrow[0].children, ['.message-input-actions'])
    const sendRest = findRule(actions.children.filter((n) => n.type === 'rule'), ['.send-button > wa-icon'])
    assert.equal(sendRest.decls.transition, 'translate var(--motion-dur-2) var(--motion-ease-spring)')
    const hoverMedia = actions.children.filter((n) => n.type === 'at' && /^@media \(hover: ?hover\)$/.test(n.prelude))
    assert.equal(hoverMedia.length, 1)
    assert.equal(findRule(hoverMedia[0].children, ['.send-button:not([disabled]):hover > wa-icon']).decls.translate,
        '0 calc(-0.125rem * var(--motion-amount))')
    const group = findRule(actions.children.filter((n) => n.type === 'rule'), ['.cancel-button', '.reset-button', '.send-button'])
    assert.ok(!JSON.stringify(group.children).includes('translate'), 'no Send rule inside the three-button group')

    // Settings gear (§5.7).
    const gear = flatten(componentTree('../components/app/SettingsPopover.vue'))
    assert.equal(find(gear.filter((e) => !e.ancestors.length), ["#settings-trigger > wa-icon[name='gear']"]).decls.transition,
        'rotate 600ms var(--motion-ease-out)')
    assert.equal(find(gear.filter((e) => inHoverMedia(e.ancestors)), ["#settings-trigger:hover > wa-icon[name='gear']"]).decls.rotate,
        'calc(90deg * var(--motion-amount))')
})

test('11. completed-task check pop: animation, set logic, active → animate chain', () => {
    const sfc = read('../components/session/detail/items/TodoContent.vue')
    const tree = componentTree('../components/session/detail/items/TodoContent.vue')
    assert.equal(findRule(tree.filter((n) => n.type === 'rule'), ['.todo-item-icon--pop']).decls.animation,
        'todo-check-pop 420ms var(--motion-ease-spring) both')
    const [frame] = keyframeEntries(tree, 'todo-check-pop')
    assert.equal(frame.rule.selector, 'from')
    assert.deepEqual(frame.rule.decls, { scale: 'calc(1 - var(--motion-amount))', rotate: 'calc(-30deg * var(--motion-amount))' })

    assert.match(sfc, /animate:\s*\{\s*type:\s*Boolean,\s*default:\s*false,?\s*\}/, 'animate prop')
    assert.match(sfc, /'todo-item-icon--pop':/, 'pop class binding')
    assert.match(sfc, /@animationend=/)
    assert.match(sfc, /@animationcancel=/)
    const script = sfc.match(/<script setup>([\s\S]*?)<\/script>/)[1]
    const todosWatch = script.slice(script.indexOf('watch(() => props.todos'), script.indexOf('watch(() => props.animate'))
    assert.ok(todosWatch.includes('findNewlyCompleted('), 'todos watcher uses findNewlyCompleted')
    assert.ok(todosWatch.includes('if (props.animate)'), 'todos watcher only adds pops while the list is visible')
    assert.ok(todosWatch.includes('.add('), 'todos watcher adds to the set')
    assert.ok(todosWatch.includes('.delete('), 'todos watcher deletes indices no longer completed')
    assert.ok(!/popping\.value\s*=/.test(script), 'the set is never replaced')
    assert.ok(!/immediate/.test(todosWatch), 'no immediate watcher: a first render never pops')
    const animateWatch = script.slice(script.indexOf('watch(() => props.animate'))
    assert.ok(animateWatch.includes('.clear()'), 'animate watcher clears the set')

    const pane = read('../components/tasks/TaskPane.vue')
    assert.match(pane, /active:\s*\{\s*type:\s*Boolean,\s*default:\s*false\s*\}/, 'TaskPane: active prop')
    const todoTag = templateOf(pane).match(/<TodoContent\b[^>]*>/)[0]
    assert.match(todoTag, /:animate="active"/)
    assert.match(todoTag, /:key="sessionId"/)
    const view = templateOf(read('../views/SessionView.vue'))
    const paneTag = view.match(/<TaskPane\b[^>]*>/)[0]
    assert.match(paneTag, /:active="isActive && isToolTabShown\('tasks'\)"/)
})

test('12. motion.css is imported right after glass.css in the three entry files', () => {
    for (const [file, prefix] of [['../main.js', './styles/'], ['../share-session/main.js', '../styles/'], ['../artifact-shell/main.js', '../styles/']]) {
        const lines = read(file).split('\n').map((l) => l.trim()).filter((l) => l.startsWith('import '))
        const glassAt = lines.indexOf(`import '${prefix}glass.css'`)
        assert.ok(glassAt >= 0, `${file}: no glass.css import`)
        assert.equal(lines[glassAt + 1], `import '${prefix}motion.css'`, `${file}: motion.css must follow glass.css`)
    }
})
