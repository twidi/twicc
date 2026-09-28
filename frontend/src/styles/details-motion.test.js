// Text invariants of the wa-details open/close motion (visual refresh step 4b,
// docs/plans/2026-09-27-details-motion-design.md). Same approach as glass.test.js: the
// stylesheet, entry files and components are read as text and checked for what the design
// relies on. The motion itself is tested in utils/detailsMotion.test.js.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** Top-level blocks of a stylesheet: { head, body } in source order (nested blocks kept in body). */
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

function declarations(body) {
    const out = {}
    for (const part of body.split(';')) {
        const match = part.match(/^\s*(-?-?[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) out[match[1]] = collapse(match[2])
    }
    return out
}

const motion = topBlocks(stripComments(read('motion.css')))
const blockIndex = (head) => motion.findIndex((b) => b.head === head)
const block = (head) => {
    const found = motion.filter((b) => b.head === head)
    assert.equal(found.length, 1, `exactly one top-level "${head}" block`)
    return found[0]
}

const LOADING = 'wa-details[data-motion-loading]:not(:has(> :not([slot])))::part(content)'

test('motion.css: content part is a formatting root', () => {
    assert.deepEqual(declarations(block('wa-details::part(content)').body), { display: 'flow-root', 'padding-inline': '0' })
})

test('motion.css: loading line, guarded by :has(), with its spinner keyframes', () => {
    const before = declarations(block(`${LOADING}::before`).body)
    assert.equal(before.content, "''")
    assert.equal(before.display, 'inline-block')
    assert.equal(before['box-sizing'], 'border-box')
    assert.equal(before['border-radius'], '50%')
    // wa-spinner 3.3.1: --track-color, --indicator-color, --speed (2s).
    assert.equal(before.border, '0.1em solid var(--wa-color-neutral-fill-normal)')
    assert.equal(before['border-block-start-color'], 'var(--wa-color-brand-fill-loud)')
    assert.equal(before.animation, 'motion-spin 2s linear infinite')
    const after = declarations(block(`${LOADING}::after`).body)
    assert.equal(after.content, "'Loading...'")
    assert.equal(after.color, 'var(--wa-color-text-quiet)')

    const spin = block('@keyframes motion-spin')
    assert.deepEqual(topBlocks(spin.body).map((b) => [b.head, declarations(b.body)]), [['to', { rotate: '1turn' }]])
    // The spinner is a status indicator: nothing stops it under reduced motion.
    for (const media of motion.filter((b) => /prefers-reduced-motion/.test(b.head))) {
        assert.ok(!media.body.includes('motion-spin') && !media.body.includes('data-motion-loading'))
    }
})

test('motion.css: the chevron turns with the spring, at once under reduced motion', () => {
    const base = block(':where(wa-details)::part(icon)')
    assert.deepEqual(declarations(base.body), { transition: 'rotate var(--motion-dur-2) var(--motion-ease-spring)' })
    const reduced = motion.filter((b) => /^@media \(prefers-reduced-motion: ?reduce\)$/.test(b.head) && b.body.includes('::part(icon)'))
    assert.equal(reduced.length, 1)
    assert.ok(motion.indexOf(reduced[0]) > blockIndex(':where(wa-details)::part(icon)'), 'reduced block after the base rule')
    const inner = topBlocks(reduced[0].body)
    assert.deepEqual(inner.map((b) => [b.head, declarations(b.body)]), [[':where(wa-details)::part(icon)', { 'transition-duration': '0s' }]])
})

test('installDetailsMotion() runs right after installGlassArrowGap() in both entry files', () => {
    for (const [file, prefix] of [['../main.js', './utils/'], ['../share-session/main.js', '../utils/']]) {
        const lines = read(file).split('\n').map((l) => l.trim())
        assert.ok(lines.includes(`import { installDetailsMotion } from '${prefix}detailsMotion'`), `${file}: import`)
        const at = lines.indexOf('installGlassArrowGap()')
        assert.ok(at >= 0, `${file}: installGlassArrowGap()`)
        assert.equal(lines.slice(at + 1).find((l) => l && !l.startsWith('import ') && !l.startsWith('//')), 'installDetailsMotion()', file)
    }
    assert.ok(!read('../artifact-shell/main.js').includes('installDetailsMotion'), 'the artifact shell has no wa-details')
})

// ---------------------------------------------------------------------------
// Lazy components (§5, §6)
// ---------------------------------------------------------------------------

const scriptOf = (sfc) => sfc.match(/<script setup>([\s\S]*?)<\/script>/)[1]
const templateOf = (sfc) => sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '')

/** The body of `function name(` in a script (up to the closing brace at column 0). */
function functionBody(script, name) {
    const start = script.indexOf(`function ${name}(`)
    assert.ok(start >= 0, `function ${name}`)
    const end = script.indexOf('\n}\n', start)
    return script.slice(start, end + 2)
}

/** Opening tags of the wa-details of a template, attributes parsed ({ name → value }). */
function detailsTags(template) {
    return [...template.matchAll(/<wa-details\b((?:[^>"']|"[^"]*"|'[^']*')*)>/g)].map((m) => {
        const attrs = {}
        for (const a of m[1].matchAll(/([@:\w.-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = a[2] ?? ''
        return attrs
    })
}

/** The attribute of an event (with its modifiers), e.g. `@wa-hide.self`. */
const eventAttr = (attrs, event) => Object.keys(attrs).find((k) => k === `@${event}` || k.startsWith(`@${event}.`))

/** The script body a handler expression runs: `fn`, `fn(...)`, or an inline expression. */
function handlerSource(script, expression) {
    const name = expression.match(/^\s*([\w$]+)\s*(\(|$)/)?.[1]
    return name && script.includes(`function ${name}(`) ? functionBody(script, name) : expression
}

const LAZY = [
    // [file, number of lazy blocks, classes of non-lazy wa-details to skip]
    ['../components/session/detail/items/ToolUseContent.vue', 1, ['tool-result']],
    ['../components/session/detail/items/claude_code/ThinkingContent.vue', 1, []],
    ['../components/session/detail/items/codex/Reasoning.vue', 1, []],
    ['../components/session/detail/items/codex/AssistantMessage.vue', 1, []],
    ['../components/session/detail/items/codex/ImageGeneration.vue', 1, []],
    ['../components/session/detail/items/CompactSummary.vue', 1, []],
    ['../components/session/detail/items/UnknownEntry.vue', 1, []],
    ['../components/workflows/WorkflowRunDetail.vue', 6, []],
]

const GUARD = 'event.target !== event.currentTarget'

test('every lazy block stays rendered while its card folds', () => {
    for (const [file, lazyCount, skipClasses] of LAZY) {
        const sfc = read(file)
        const script = scriptOf(sfc)
        const template = templateOf(sfc)
        assert.match(script, /useDetailsClosing\(\)/, `${file}: useDetailsClosing()`)
        const rendered = [...template.matchAll(/v-if="([^"]*)"/g)].filter((m) => m[1].includes('|| isClosing('))
        assert.equal(rendered.length, lazyCount, `${file}: lazy blocks rendered with "|| isClosing("`)

        const tags = detailsTags(template).filter((a) => !skipClasses.some((c) => (a.class ?? '').split(' ').includes(c)))
        assert.equal(tags.length, lazyCount, `${file}: lazy wa-details`)
        for (const attrs of tags) {
            const hide = eventAttr(attrs, 'wa-hide')
            const afterHide = eventAttr(attrs, 'wa-after-hide')
            const show = eventAttr(attrs, 'wa-show')
            assert.ok(hide && afterHide && show, `${file}: wa-show, wa-hide and wa-after-hide handlers`)
            assert.equal(afterHide.slice('@wa-after-hide'.length), hide.slice('@wa-hide'.length), `${file}: same modifiers`)
            const hideSource = handlerSource(script, attrs[hide])
            const afterHideSource = handlerSource(script, attrs[afterHide])
            assert.ok(hideSource.includes('markClosing('), `${file}: ${attrs[hide]} marks closing`)
            assert.ok(afterHideSource.includes('clearClosing('), `${file}: ${attrs[afterHide]} clears closing`)
            assert.equal(afterHideSource.includes(GUARD), hideSource.includes(GUARD), `${file}: same target guard`)
            assert.ok(handlerSource(script, attrs[show]).includes('clearClosing('), `${file}: ${attrs[show]} clears closing`)
        }
    }
})

test('ToolUseContent: error close marks closing first; the Result folds with the card', () => {
    const script = scriptOf(read('../components/session/detail/items/ToolUseContent.vue'))
    const errorWatch = script.slice(script.indexOf('watch(isToolError'))
    const mark = errorWatch.indexOf('markClosing(')
    const close = errorWatch.indexOf('isOpen.value = false')
    assert.ok(mark >= 0 && close > mark, 'markClosing( before isOpen.value = false')

    // Every write of isOpen to false is preceded by markClosing( in the same function.
    const writes = [...script.matchAll(/isOpen\.value = false/g)].length
    assert.equal(writes, 2, 'onToolUseClose and the error watcher')

    const onClose = functionBody(script, 'onToolUseClose')
    assert.ok(!/isResultOpen\.value\s*=/.test(onClose), 'onToolUseClose no longer closes the Result')
    assert.ok(onClose.includes('setDetailOpen(props.sessionId, `result:${props.toolId}`, false)'), 'the store write stays')
    const afterHide = functionBody(script, 'onToolUseAfterHide')
    assert.match(afterHide, /if \(!isOpen\.value\) isResultOpen\.value = false/)
    const onOpen = functionBody(script, 'onToolUseOpen')
    assert.match(onOpen, /if \(isResultOpen\.value\) dataStore\.setDetailOpen\(props\.sessionId, `result:\$\{props\.toolId\}`, true\)/)
})

test('UnknownEntry restores its open state instantly, never through show()', () => {
    const sfc = read('../components/session/detail/items/UnknownEntry.vue')
    assert.ok(!sfc.includes('.show()'))
    const [tag] = detailsTags(templateOf(sfc))
    assert.equal(tag[':open'], 'isOpen')
    assert.equal(tag[':style'], "instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null")
    assert.match(scriptOf(sfc), /const instantOpen = ref\(isOpen\.value\)/)
    assert.match(scriptOf(sfc), /nextTick\(\(\) => \{ instantOpen\.value = false \}\)/)
})

test('dedicated tool results are capped at 20rem and scroll inside, like the JSON view and the diffs', () => {
    const rule = (sfc, selector) => {
        const style = sfc.slice(sfc.indexOf('<style'))
        const match = style.match(new RegExp(`\\n${selector.replace('.', '\\.')} \\{([^}]*)\\}`))
        assert.ok(match, `${selector} rule`)
        return match[1]
    }
    const body = rule(read('../components/session/detail/items/ToolUseContent.vue'), '.tool-result-dedicated')
    assert.match(body, /max-height: 20rem;/)
    assert.match(body, /overflow: auto;/)
    // Thinking and Reasoning are not capped (user, 2026-09-28): their markdown toolbar sits
    // outside the block, which a scroller or a paint containment would cut or scroll away.
    for (const [file, selector] of [
        ['../components/session/detail/items/claude_code/ThinkingContent.vue', '.thinking-body'],
        ['../components/session/detail/items/codex/Reasoning.vue', '.reasoning-body'],
    ]) {
        const own = rule(read(file), selector)
        assert.doesNotMatch(own, /max-height|overflow|contain/, `${file} ${selector}`)
    }
    // In the "Result" disclosure, the dedicated renderer sits inside the capped wrapper.
    // (The inline result above it only serves Codex's view_image: an image, capped at 75vh
    // by its own component.)
    const tool = read('../components/session/detail/items/ToolUseContent.vue')
    const data = tool.slice(tool.lastIndexOf('class="tool-result-data"'))
    assert.match(data, /^class="tool-result-data">\s*<div v-if="resultRendering" :class="\{ 'tool-result-dedicated': !resultRendering\.uncapped \}">\s*<component/)
    // Images size themselves (75vh): every Codex result rendering with ViewImageResult opts out.
    const codex = read('../providers/codex/toolHelpers.js')
    const imageReturns = codex.match(/component: ViewImageResult,\n.*\n.*/g)
    assert.equal(imageReturns.length, 2)
    for (const block of imageReturns) assert.match(block, /uncapped: true/)
})

test('the markdown raw / copy toolbar stays off inside tool cards', () => {
    // It belongs to messages, Thinking, Reasoning and the compact summary (placed outside
    // their block by SessionItem.vue); a tool card has the code block's wrap / copy buttons.
    const files = [
        '../components/session/detail/items/claude_code/BashResultContent.vue',
        '../components/session/detail/items/claude_code/MonitorResultContent.vue',
        '../components/session/detail/items/claude_code/ReadResultContent.vue',
        '../components/session/detail/items/claude_code/WebContentResult.vue',
        '../components/session/detail/items/codex/ExecResultContent.vue',
        '../components/session/detail/items/codex/ReadResultContent.vue',
        '../components/session/detail/items/codex/SpawnAgentResult.vue',
        '../components/session/detail/items/codex/ImageGeneration.vue',
        '../components/session/detail/items/ToolUseContent.vue',
    ]
    for (const file of files) {
        const tags = read(file).match(/<MarkdownContent\b[^>]*>/g)
        assert.ok(tags?.length, `${file}: MarkdownContent used`)
        for (const tag of tags) assert.match(tag, /:show-toolbar="false"/, `${file}: ${tag}`)
    }
    // Anything else inside a tool card (the JSON view of an input or a result) is covered by
    // the card's own rule; JsonHumanView itself keeps the toolbar for its other uses.
    const tool = read('../components/session/detail/items/ToolUseContent.vue')
    assert.match(tool, /\n\.tool-use :deep\(\.markdown-toolbar\) \{\s*display: none;\s*\}/)
    for (const tag of read('../components/json/JsonHumanView.vue').match(/<MarkdownContent\b[^>]*>/g)) {
        assert.doesNotMatch(tag, /show-toolbar/)
    }
    const items = read('../components/session/detail/SessionItem.vue')
    assert.match(items, /\.thinking-body, \.compact-summary-body \{\s*> \.markdown-content-wrapper > \.markdown-toolbar/)
    assert.match(items, /\.session-item\[data-kind="reasoning"\] \.reasoning-body > \.markdown-content-wrapper > \.markdown-toolbar \{/)
})

test('the side spacing of an open details lives in its children', () => {
    const motion = read('./motion.css')
    assert.match(motion, /\nwa-details \{\s*--details-spacing: var\(--spacing\);\s*\}/)
    assert.match(motion, /\nwa-details > :not\(\[slot\], wa-callout, wa-divider\) \{\s*padding-inline: var\(--details-spacing\);\s*\}/)
    assert.match(motion, /\nwa-details > :is\(wa-callout, wa-divider\):not\(\[slot\]\) \{\s*margin-inline: var\(--details-spacing\);\s*\}/)
    // The loading line is drawn in the content part: it keeps the spacing there.
    assert.match(motion, /\nwa-details\[data-motion-loading\]:not\(:has\(> :not\(\[slot\]\)\)\)::part\(content\) \{\s*padding-inline: var\(--details-spacing\);\s*\}/)
    // Children whose own rule sets their side padding take the details' spacing themselves
    // (and none outside a details, e.g. the Tasks tab's list).
    const own = [
        ['../components/session/detail/items/ToolUseContent.vue', '.tool-input', /padding: 0 var\(--spacing, 0\);/],
        ['../components/session/detail/items/ToolUseContent.vue', '.tool-no-input', /padding: var\(--wa-space-xs\) var\(--spacing, 0\);/],
        ['../components/session/detail/items/UnknownEntry.vue', '.unknown-data', /padding: var\(--wa-space-xs\) var\(--spacing, 0\);/],
        ['../components/session/detail/items/UnknownEntry.vue', '.unknown-no-data', /padding: var\(--wa-space-xs\) var\(--spacing, 0\);/],
        ['../components/session/detail/items/TodoContent.vue', '.todo-list', /padding: var\(--wa-space-xs\) var\(--spacing, 0\);/],
    ]
    for (const [file, selector, expected] of own) {
        const style = read(file).slice(read(file).indexOf('<style'))
        const body = style.match(new RegExp(`\\n${selector.replace('.', '\\.')} \\{([^}]*)\\}`))?.[1]
        assert.ok(body, `${file} ${selector}`)
        assert.match(body, expected, `${file} ${selector}`)
    }
    // In the Result, the spacing goes down to the scroller and the state lines.
    const tool = read('../components/session/detail/items/ToolUseContent.vue')
    assert.match(tool, /\.tool-result-content > :not\(\.tool-result-data\),\s*\.tool-result-content > \.tool-result-data > \* \{\s*padding-inline: var\(--spacing\);/)
})
