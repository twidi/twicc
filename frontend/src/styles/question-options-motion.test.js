// Question options interior (visual refresh step 7e,
// docs/plans/2026-09-30-question-options-motion-design.md §6). Same approach as
// tasks-motion.test.js: the stylesheets and components are read as text, each rule of the
// spec is pinned whole (selector + ordered declaration list, roadmap §6l.2) and the template
// facts the rules rely on are checked on the @vue/compiler-sfc AST. The two bodies are also
// compiled, so a markup or scoped-style slip that Vue rejects fails here.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { compileStyle, compileTemplate, parse } from '@vue/compiler-sfc'

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

/** Every rule, at-rules included (@media). */
function parseAll(css) {
    const out = []
    rulesOf(stripComments(css), [], out)
    return out
}

/** A rule pinned whole: its ordered declaration list equals the spec block's exactly. */
function assertPinned(r, specBody, label = r.head) {
    assert.deepEqual(r.list, declarationList(stripComments(specBody)), `${label}: declaration list`)
}

const styleOf = (sfc) => [...sfc.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')

/** The unique rule whose selector list is exactly `selectors` (any order), optionally filtered. */
function rule(rules, selectors, where = () => true) {
    const wanted = selectors.map(collapse)
    const found = rules.filter((r) => r.selectors.length === wanted.length && wanted.every((s) => r.selectors.includes(s)) && where(r))
    assert.equal(found.length, 1, `expected exactly one rule "${wanted.join(', ')}", found ${found.length}`)
    return found[0]
}

const topLevel = (r) => r.ancestors.length === 0
const HOVER = '@media (hover: hover)'
const inHover = (r) => r.ancestors.length === 1 && r.ancestors[0] === HOVER

/** The template's AST root nodes (whitespace dropped, comments kept). */
const templateRoots = (sfc) => parse(sfc).descriptor.template.ast.children

/** A node's attributes and directives as written: { class: value, ':class': exp, ... }. */
function attrsOf(node) {
    const out = {}
    for (const p of node.props) {
        if (p.type === 6) out[p.name] = p.value?.content ?? ''
        else out[p.rawName] = p.exp?.content ?? ''
    }
    return out
}

/** Every element node of a subtree, depth first. */
function elements(nodes, out = []) {
    for (const node of nodes) {
        if (node.type !== 1) continue
        out.push(node)
        elements(node.children ?? [], out)
    }
    return out
}
const hasClass = (node, name) => (attrsOf(node).class ?? '').split(/\s+/).includes(name)
const elementChildren = (node) => node.children.filter((c) => c.type === 1)

const SHEET = 'option-cards.css'
const CLAUDE = '../components/session/detail/items/claude_code/PendingRequestBody.vue'
const CODEX = '../components/session/detail/items/codex/RequestUserInputBody.vue'
const FIELDS = '../components/message/QuestionFields.vue'
const sheetRules = () => parseAll(read(SHEET))

// The rules of §4.2 and §4.3, in source order: [enclosing at-rule or null, selectors, body].
const SPEC = [
    [null, [':where(.question-options)'], `
        display: flex;
        flex-wrap: wrap;
        gap: var(--wa-space-s);
        margin-top: var(--wa-space-2xs);`],
    [null, [':where(.option-card)'], `
        flex: 1 1 0;
        min-width: min-content;
        max-width: 20rem;
        cursor: pointer;
        transition: border-color var(--motion-dur-2) var(--motion-ease), background-color var(--motion-dur-2) var(--motion-ease), box-shadow var(--motion-dur-2) var(--motion-ease);
        --spacing: var(--wa-space-m);
        --border-color-base: var(--wa-color-surface-border);
        --background-color-base: var(--wa-color-surface-raised);
        --option-border-on: var(--glow-accent);
        --option-bg-on: color-mix(in oklab, var(--glow-accent) 9%, var(--wa-color-surface-raised));
        border-color: var(--border-color-base);
        background-color: var(--background-color-base);
        box-shadow: var(--depth-1);`],
    [null, [':where(.wa-dark .option-card)'], `
        --option-bg-on: color-mix(in oklab, var(--glow-accent) 16%, var(--wa-color-surface-raised));`],
    [null, [':where(.option-card.selected)'], `
        --border-color-base: var(--option-border-on);
        --background-color-base: var(--option-bg-on);
        box-shadow: var(--depth-1), 0 0 0 1px var(--glow-accent), 0 0 1rem -0.25rem color-mix(in oklab, var(--glow-accent) 55%, transparent);`],
    [HOVER, [':where(.option-card:not(.disabled):hover)'], `
        --border-color-base: color-mix(in oklab, var(--wa-color-surface-border) 66.667%, var(--option-border-on));
        --background-color-base: color-mix(in oklab, var(--wa-color-surface-raised) 66.667%, var(--option-bg-on));
        box-shadow: var(--depth-1), 0 0 0 1px color-mix(in oklab, var(--glow-accent) 33%, transparent), 0 0 1rem -0.25rem color-mix(in oklab, var(--glow-accent) 18%, transparent);`],
    [HOVER, [':where(.option-card.selected:not(.disabled):hover)'], `
        --border-color-base: color-mix(in oklab, var(--wa-color-surface-border) 33.333%, var(--option-border-on));
        --background-color-base: color-mix(in oklab, var(--wa-color-surface-raised) 33.333%, var(--option-bg-on));
        box-shadow: var(--depth-1), 0 0 0 1px color-mix(in oklab, var(--glow-accent) 67%, transparent), 0 0 1rem -0.25rem color-mix(in oklab, var(--glow-accent) 37%, transparent);`],
    [null, [':where(.option-card.disabled)'], `
        opacity: 0.6;
        cursor: not-allowed;`],
    [null, [':where(.option-card:focus-visible)', ':where(.option-card.auto-focused:focus-within)'], `
        outline: var(--wa-focus-ring);
        outline-offset: var(--wa-focus-ring-offset);`],
    [null, [':where(.option-card-content)'], `
        display: flex;
        align-items: flex-start;
        gap: var(--wa-space-s);`],
    [null, [':where(.option-card-text)'], `
        display: flex;
        flex-direction: column;
        gap: var(--wa-space-s);
        min-width: 0;`],
    [null, [':where(.option-card .option-label)'], `
        font-weight: 600;
        color: var(--wa-color-text);
        line-height: 1.4;`],
    [null, [':where(.option-indicator)'], `
        flex: none;
        position: relative;
        box-sizing: border-box;
        inline-size: 1.125rem;
        block-size: 1.125rem;
        margin-block-start: 0.125rem;
        border: 2px solid var(--wa-color-neutral-border-loud);
        background: var(--wa-color-surface-default);
        transition: border-color var(--motion-dur-2) var(--motion-ease), background-color var(--motion-dur-2) var(--motion-ease), box-shadow var(--motion-dur-2) var(--motion-ease);`],
    [null, [':where(.option-indicator--radio)'], 'border-radius: 50%;'],
    [null, [':where(.option-indicator--check)'], 'border-radius: 0.3125rem;'],
    [HOVER, [':where(.option-card:not(.selected):not(.disabled):hover .option-indicator)'], `
        border-color: color-mix(in oklab, var(--wa-color-neutral-border-loud) 66.667%, var(--glow-accent));`],
    [null, [':where(.option-card.selected .option-indicator)'], `
        border-color: var(--glow-accent);
        box-shadow: 0 0 0.5rem -0.125rem color-mix(in oklab, var(--glow-accent) 70%, transparent);`],
    [null, [':where(.option-indicator)::after'], `
        content: '';
        position: absolute;
        opacity: 0;
        scale: calc(1 - 0.6 * var(--motion-amount));
        transition: opacity var(--motion-dur-1) var(--motion-ease), scale var(--motion-dur-3) var(--motion-ease-spring);`],
    [null, [':where(.option-indicator--radio)::after'], `
        inset: 0.1875rem;
        border-radius: 50%;
        background: var(--glow-accent);`],
    [null, [':where(.option-indicator--check)::after'], `
        inset: 0.1875rem;
        background: var(--glow-accent);
        clip-path: polygon(14% 44%, 0 65%, 50% 100%, 100% 16%, 80% 0, 43% 62%);`],
    [null, [':where(.option-card.selected .option-indicator)::after'], `
        opacity: 1;
        scale: none;`],
]

test('1. option-cards.css is imported by main.js right after sidebar-rows.css, and nowhere else (§4.2)', () => {
    const lines = read('../main.js').split('\n').map((l) => l.trim()).filter((l) => l.startsWith('import '))
    const rowsAt = lines.indexOf("import './styles/sidebar-rows.css'")
    assert.ok(rowsAt >= 0, 'main.js imports sidebar-rows.css')
    assert.equal(lines[rowsAt + 1], "import './styles/option-cards.css'")
    const importers = readdirSync(srcDir, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile() && /\.(vue|css|js)$/.test(e.name) && !e.name.endsWith('.test.js'))
        .map((e) => join(e.parentPath ?? e.path, e.name))
        .filter((file) => /(?:import|@import)\s+(?:url\()?['"][^'"]*option-cards\.css['"]/.test(readFileSync(file, 'utf8')))
    assert.deepEqual(importers, [join(srcDir, 'main.js')])
})

test('2. the header: SPA only, the design doc, and why the colours are host declarations', () => {
    const css = read(SHEET)
    const header = css.slice(0, css.indexOf('*/'))
    assert.ok(css.trimStart().startsWith('/*'), 'the sheet opens with its header comment')
    assert.match(header, /docs\/plans\/2026-09-30-question-options-motion-design\.md/)
    assert.match(header, /SPA only/)
    assert.match(header, /:host/, 'wa-card paints its colours in :host: the host declarations carry them')
    assert.ok(!stripComments(css).includes('@layer'), 'unlayered')
    assert.ok(!stripComments(css).includes('!important'), 'no !important')
})

test('3. every rule of §4.2 and §4.3, pinned whole, in order, and nothing else', () => {
    const all = sheetRules()
    assert.deepEqual(
        all.map((r) => [r.ancestors[0] ?? null, r.head]),
        [
            ...SPEC.slice(0, 4).map(([, selectors]) => [null, selectors.join(', ')]),
            [null, HOVER], [HOVER, SPEC[4][1][0]], [HOVER, SPEC[5][1][0]],
            ...SPEC.slice(6, 14).map(([, selectors]) => [null, selectors.join(', ')]),
            [null, HOVER], [HOVER, SPEC[14][1][0]],
            ...SPEC.slice(15).map(([, selectors]) => [null, selectors.join(', ')]),
        ],
        'the rule heads, in source order',
    )
    for (const [at, selectors, body] of SPEC) {
        const r = rule(all, selectors, at ? inHover : topLevel)
        assertPinned(r, body, selectors.join(', '))
    }
    const hoverBlocks = all.filter((r) => r.head === HOVER)
    assert.equal(hoverBlocks.length, 2, 'two @media (hover: hover) wrappers')
    for (const block of hoverBlocks) assert.deepEqual(block.list, [], 'the wrapper holds rules only')
    // The first wrapper holds the two card hover rules, the second the indicator ring.
    const [cardHover, ringHover] = hoverBlocks
    assert.deepEqual(all.filter((r) => r.ancestors[0] === HOVER && r.order > cardHover.order && r.order < ringHover.order).map((r) => r.head),
        [SPEC[4][1][0], SPEC[5][1][0]], 'the card hover rules share the first wrapper')
})

test('4. the host declarations, the "chosen" variables, the dark override and the hover order (§4.2)', () => {
    const all = sheetRules()
    const card = rule(all, [':where(.option-card)'], topLevel)
    assert.equal(card.decls['border-color'], 'var(--border-color-base)')
    assert.equal(card.decls['background-color'], 'var(--background-color-base)')
    assert.ok(!('--border-color' in card.decls) && !('--background-color' in card.decls), 'no derived colour layer')
    assert.equal(card.decls['--option-border-on'], 'var(--glow-accent)')
    assert.equal(card.decls['--option-bg-on'], 'color-mix(in oklab, var(--glow-accent) 9%, var(--wa-color-surface-raised))')
    const chosen = rule(all, [':where(.option-card.selected)'], topLevel)
    assert.match(chosen.decls['box-shadow'], /0 0 1rem -0\.25rem color-mix\(in oklab, var\(--glow-accent\) 55%, transparent\)$/, 'the chosen glow')
    const dark = rule(all, [':where(.wa-dark .option-card)'], topLevel)
    assert.equal(dark.order, card.order + 1, 'the dark override comes right after :where(.option-card) (both set --option-bg-on at specificity 0)')
    const hover = rule(all, [':where(.option-card:not(.disabled):hover)'], inHover)
    const chosenHover = rule(all, [':where(.option-card.selected:not(.disabled):hover)'], inHover)
    assert.ok(chosenHover.order > chosen.order, 'the chosen-hover rule wins over .selected by source order')
    assert.ok(chosenHover.order > hover.order, 'the chosen-hover rule wins over the unchosen-hover rule by source order')
    for (const r of [hover, chosenHover]) {
        assert.match(r.decls['--border-color-base'], /var\(--option-border-on\)\)$/, `${r.head}: mixes toward the chosen border`)
        assert.match(r.decls['--background-color-base'], /var\(--option-bg-on\)\)$/, `${r.head}: mixes toward the chosen background`)
    }
})

test('5. the global rules are :where()-wrapped and name only the card classes (§5)', () => {
    const allowed = new Set([
        'question-options', 'option-card', 'option-card-content', 'option-card-text', 'option-label',
        'option-indicator', 'option-indicator--radio', 'option-indicator--check',
        'wa-dark', 'selected', 'disabled', 'auto-focused',
    ])
    for (const r of sheetRules().filter((x) => !x.head.startsWith('@'))) {
        for (const selector of r.selectors) {
            assert.match(selector, /^:where\([^]*\)(::after)?$/, `${selector}: wrapped in :where()`)
            for (const [, name] of selector.matchAll(/\.([\w-]+)/g)) assert.ok(allowed.has(name), `${selector}: .${name}`)
            if (selector.includes('.option-label')) assert.match(selector, /\.option-card \.option-label/, selector)
            // A state class is compounded on .option-card (a :not() of it counts too).
            const compound = selector.replace(/:not\((\.[\w-]+)\)/g, '$1')
            for (const state of ['selected', 'disabled', 'auto-focused']) {
                if (selector.includes(`.${state}`)) assert.match(compound, new RegExp(`\\.option-card(?:[.:][\\w-]+)*\\.${state}\\b`), `${selector}: .${state} on .option-card`)
            }
        }
    }
})

test('6. the mark: scale only, × --motion-amount at rest, none when chosen (§4.3, §5)', () => {
    const css = stripComments(read(SHEET))
    assert.ok(!/\btransform\s*:/.test(css), 'no transform')
    assert.ok(!/\b(translate|rotate)\s*:/.test(css), 'no other movement')
    const mark = rule(sheetRules(), [':where(.option-indicator)::after'], topLevel)
    assert.equal(mark.decls.scale, 'calc(1 - 0.6 * var(--motion-amount))', 'reduced motion: --motion-amount 0 → scale 1, a fade')
    const chosen = rule(sheetRules(), [':where(.option-card.selected .option-indicator)::after'], topLevel)
    assert.equal(chosen.decls.scale, 'none')
    assert.equal(chosen.decls.opacity, '1')
})

/** The one option card of a body, and its content row. */
function cardOf(file) {
    const cards = elements(templateRoots(read(file))).filter((n) => n.tag === 'wa-card' && hasClass(n, 'option-card'))
    assert.equal(cards.length, 1, `${file}: one option-card template`)
    const [content] = elementChildren(cards[0])
    assert.ok(content && content.tag === 'div' && hasClass(content, 'option-card-content'), `${file}: .option-card-content`)
    assert.equal(elementChildren(cards[0]).length, 1, `${file}: the card holds the content row only`)
    return content
}

function assertCardMarkup(file, indicatorAttrs) {
    const content = cardOf(file)
    const [indicator, text] = elementChildren(content)
    assert.equal(elementChildren(content).length, 2, `${file}: indicator, then the text column`)
    assert.equal(indicator.tag, 'span', `${file}: the indicator is a span`)
    assert.deepEqual(attrsOf(indicator), indicatorAttrs, `${file}: the indicator`)
    assert.equal(indicator.children.length, 0, `${file}: the indicator is empty`)
    assert.equal(text.tag, 'div', `${file}: the text column is a div`)
    assert.deepEqual(attrsOf(text), { class: 'option-card-text' }, `${file}: .option-card-text`)
    const [label, description] = elementChildren(text)
    assert.equal(elementChildren(text).length, 2, `${file}: label and description`)
    assert.equal(label.tag, 'span')
    assert.deepEqual(attrsOf(label), { class: 'option-label' }, `${file}: the label`)
    assert.equal(description.tag, 'span')
    assert.deepEqual(attrsOf(description), { 'v-if': 'option.description', class: 'option-description' }, `${file}: the description`)
}

test('7. Claude body: the indicator follows question.multiSelect (§4.1)', () => {
    assertCardMarkup(CLAUDE, {
        class: 'option-indicator',
        ':class': "question.multiSelect ? 'option-indicator--check' : 'option-indicator--radio'",
        'aria-hidden': 'true',
    })
})

test('8. shared Codex fields: always a radio, a literal class (§4.1)', () => {
    assert.ok(elements(templateRoots(read(CODEX))).some(node => node.tag === 'QuestionFields'), 'Codex uses the shared fields')
    assertCardMarkup(FIELDS, { class: 'option-indicator option-indicator--radio', 'aria-hidden': 'true' })
})

test('9. no scoped copy of the card rules left; .option-description stays with the fields (§4.2)', () => {
    for (const file of [CLAUDE, CODEX, FIELDS]) {
        const style = styleOf(read(file))
        const rules = parseAll(style)
        for (const r of rules) {
            assert.ok(!/\.(question-options|option-card|option-label|option-indicator)\b/.test(r.head), `${file}: scoped copy "${r.head}"`)
        }
        // The false "10% lighter" comment went with its rule; the old hover clause is replaced.
        assert.ok(!style.includes('10% lighter'), `${file}: stale hover comment`)
        assert.ok(!style.includes('keeps its selected colors'), `${file}: stale two-layer comment`)
        if (file === CODEX) continue // The host delegates description markup and style to QuestionFields.
        const description = rule(rules, ['.option-description'], topLevel)
        assert.deepEqual(description.list, [
            ['display', 'block'],
            ['font-size', 'var(--wa-font-size-s)'],
            ['color', 'var(--wa-color-text-quiet)'],
            ['line-height', '1.3'],
        ], `${file}: .option-description`)
    }
    const sheet = read(SHEET)
    assert.ok(!sheet.includes('10% lighter') && !sheet.includes('keeps its selected colors'), 'no stale comment in the sheet')
    // The derived colour layer and the +0.025 lightness hover are gone, with their comments.
    for (const stale of ['Two-layer', 'derived layer', '0.025']) assert.ok(!sheet.includes(stale), `the sheet still mentions "${stale}"`)
})

/** The comment directly above `selector` in `text` (only whitespace between them), collapsed. */
function commentAbove(text, selector, label) {
    const at = text.indexOf(selector)
    assert.ok(at >= 0, `${label}: "${selector}" found`)
    const before = text.slice(0, at)
    const end = before.lastIndexOf('*/')
    assert.ok(end >= 0 && before.slice(end + 2).trim() === '', `${label}: a comment directly above "${selector}"`)
    return collapse(before.slice(before.lastIndexOf('/*', end), end + 2))
}

test('10. the stale comments moved with their rules (§6)', () => {
    const sheet = read(SHEET)
    // The §4.2 hover comment, directly above the first @media (hover: hover).
    assert.equal(commentAbove(sheet, HOVER, SHEET), collapse(`/* Hover (pointer devices): the colours go ONE THIRD of the way from "not chosen" to "chosen" on an
   unchosen card, TWO THIRDS on a chosen one (a click always shows, the pointer leaving completes it).
   The chosen-hover rule comes after \`.selected\` and after the unchosen-hover rule: same specificity
   (0 under :where), source order decides, and it must win on a chosen card under the pointer. */`))
    // The focus rule explains the plain-tabindex :focus-within case.
    const focus = commentAbove(sheet, ':where(.option-card:focus-visible)', SHEET)
    assert.match(focus, /plain tabindex element/, 'the .option-card focus reasoning sits above the focus rule')

    const claude = commentAbove(styleOf(read(CLAUDE)), 'wa-button.auto-focused:focus-within::part(base)', CLAUDE)
    assert.match(claude, /delegate focus/, 'Claude: the wa-button / wa-textarea reasoning stays')
    assert.ok(!/\.option-card|option-card\)|plain tabindex/.test(claude), 'Claude: the option-card part moved')
    const codex = commentAbove(styleOf(read(FIELDS)), 'wa-textarea.auto-focused:focus-within::part(base)', FIELDS)
    assert.match(codex, /lone text input/, 'Codex: the options-less text input comment stays above its rule')
    assert.ok(!/\.option-card|first option card/.test(codex), 'Codex: the option-card part moved')
})

test('11. both bodies still compile: template and scoped style', () => {
    for (const [file, name] of [[CLAUDE, 'PendingRequestBody.vue'], [CODEX, 'RequestUserInputBody.vue'], [FIELDS, 'QuestionFields.vue']]) {
        const sfc = read(file)
        const { descriptor, errors } = parse(sfc, { filename: name })
        assert.deepEqual(errors, [], `${file}: parse`)
        const id = 'data-v-7e7e7e7e'
        const template = compileTemplate({ source: descriptor.template.content, filename: name, id })
        assert.deepEqual(template.errors, [], `${file}: template`)
        for (const style of descriptor.styles) {
            const compiled = compileStyle({ source: style.content, filename: name, id, scoped: style.scoped })
            assert.deepEqual(compiled.errors, [], `${file}: style`)
        }
    }
})
