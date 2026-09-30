// Footer blocks motion (visual refresh step 7d, docs/plans/2026-09-30-footer-blocks-motion-design.md
// §6). Same approach as tasks-motion.test.js: the components are read as text, each new CSS
// rule is pinned whole (selector + ordered declaration list, roadmap §6l.2) and the template and
// script facts the controller relies on are checked. Templates are read through the
// @vue/compiler-sfc AST (root nodes, comments, directives as written).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'
import { compileStyle, parse } from '@vue/compiler-sfc'

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

function parseAll(css) {
    const out = []
    rulesOf(stripComments(css), [], out)
    return out
}

/** A rule pinned whole: its ordered declaration list equals the spec block's exactly. */
function assertPinned(r, specBody, label = r.head) {
    assert.deepEqual(r.list, declarationList(stripComments(specBody)), `${label}: declaration list`)
}

const topLevel = (r) => r.ancestors.length === 0

/** The unique rule whose selector list is exactly `selectors` (any order), optionally filtered. */
function rule(rules, selectors, where = () => true) {
    const wanted = selectors.map(collapse)
    const found = rules.filter((r) => r.selectors.length === wanted.length && wanted.every((s) => r.selectors.includes(s)) && where(r))
    assert.equal(found.length, 1, `expected exactly one rule "${wanted.join(', ')}", found ${found.length}`)
    return found[0]
}

const scriptOf = (sfc) => [...sfc.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n')
const scopedStyleOf = (sfc) => {
    const blocks = [...sfc.matchAll(/<style scoped>([\s\S]*?)<\/style>/g)]
    assert.equal(blocks.length, 1, 'one <style scoped> block')
    return blocks[0][1]
}

/** The template's AST root nodes (whitespace dropped, comments kept). */
const templateRoots = (sfc) => parse(sfc).descriptor.template.ast.children

/** A node's attributes and directives as written: { 'v-if': exp, ':css': exp, ref: value, ... }. */
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

const GOAL = '../components/message/GoalBlock.vue'
const PENDING = '../components/message/PendingRequestForm.vue'
const TERMINAL = '../components/message/HybridTerminalBlock.vue'
const COMPOSER = '../components/message/MessageInput.vue'
const LIST = '../components/session/detail/SessionItemsList.vue'
const LEAVING_FORM = '.pending-request-form:not([data-footer-leaving] *)'

test('1. .footer-block { display: flow-root } pinned whole in each block\'s scoped style (§4.2)', () => {
    for (const file of [GOAL, PENDING, TERMINAL]) {
        const style = scopedStyleOf(read(file))
        assertPinned(rule(parseAll(style), ['.footer-block'], topLevel), 'display: flow-root;', `${file} .footer-block`)
        const id = 'data-v-7d7d7d7d'
        const { code, errors } = compileStyle({ source: style, filename: file, id, scoped: true })
        assert.deepEqual(errors, [], `${file}: no compile errors`)
        assert.ok(parseAll(code).some((r) => r.selectors.includes(`.footer-block[${id}]`)), `${file}: the rule compiles scoped`)
    }
})

test('2. one root div.footer-block holding the divider; the root comments moved inside (§4.2, §4.3)', () => {
    const expectations = [
        [GOAL, { 'v-if': 'goal', ref: 'wrapperRef', class: 'footer-block' }, 'goal-block'],
        [PENDING, { ref: 'wrapperRef', class: 'footer-block' }, 'pending-request-form'],
        [TERMINAL, { ref: 'wrapperRef', class: 'footer-block' }, 'hybrid-terminal-block'],
    ]
    for (const [file, rootAttrs, blockClass] of expectations) {
        const roots = templateRoots(read(file))
        assert.equal(roots.length, 1, `${file}: a single root node (no root-level comment)`)
        const [root] = roots
        assert.equal(root.type, 1)
        assert.equal(root.tag, 'div')
        assert.deepEqual(attrsOf(root), rootAttrs, `${file}: root attributes`)
        const children = root.children.filter((n) => n.type === 1)
        assert.equal(children[0].tag, 'wa-divider', `${file}: the divider comes first`)
        assert.equal(children.length, 2, `${file}: the divider and the block`)
        const block = attrsOf(children[1])
        assert.equal(block.class, blockClass)
        assert.equal(block.ref, file === PENDING ? 'rootRef' : 'blockRef', `${file}: the block's ref`)
    }
    // The dev-only comments that kept the root a fragment now sit inside the wrapper.
    for (const [file, text] of [[GOAL, 'Guarded together with the divider'], [PENDING, 'Shell-only component']]) {
        const [root] = templateRoots(read(file))
        assert.ok(root.children.some((n) => n.type === 3 && n.content.includes(text)), `${file}: the comment is inside the wrapper`)
    }
    const terminalDivider = templateRoots(read(TERMINAL))[0].children.find((n) => n.tag === 'wa-divider')
    assert.equal(attrsOf(terminalDivider)['v-if'], 'isVisible', 'the terminal divider keeps its v-if')
})

test('3. each block calls useFooterBlockMotion with its shape (§4.2 table)', () => {
    const calls = [
        [GOAL, "const footerShape = computed(() => isOpen.value ? 'open' : 'collapsed')",
            'useFooterBlockMotion({ wrapperRef, blockRef, shape: footerShape, maximized: isMaximized, })'],
        [PENDING, "const footerShape = computed(() => isMinimized.value ? 'minimized' : 'normal')",
            'useFooterBlockMotion({ wrapperRef, blockRef: rootRef, shape: footerShape, maximized: isMaximized, })'],
        [TERMINAL, "const footerShape = computed(() => isClosed.value ? (calloutShown.value ? 'callout' : 'closed') : 'open')",
            'useFooterBlockMotion({ wrapperRef, blockRef, shape: footerShape, maximized: isMaximized, })'],
        [COMPOSER, "const footerShape = computed(() => collapsed.value ? 'collapsed' : 'open')",
            'const { animating } = useFooterBlockMotion({ wrapperRef: rootRef, shape: footerShape, beforeMeasure: beforeFooterMeasure, onSettled: recomputeIsTall, })'],
    ]
    for (const [file, shape, call] of calls) {
        const script = collapse(scriptOf(read(file)))
        assert.ok(script.includes(shape), `${file}: ${shape}`)
        assert.ok(script.includes(call), `${file}: ${call}`)
        assert.ok(script.includes("import { useFooterBlockMotion } from '../../composables/useFooterMotion.js'"), `${file}: import`)
        assert.ok(script.indexOf(shape) < script.indexOf('useFooterBlockMotion({'), `${file}: the shape is declared before the call`)
    }
    for (const file of [GOAL, TERMINAL]) {
        assert.ok(collapse(scriptOf(read(file))).includes('const wrapperRef = ref(null) const blockRef = ref(null)'), `${file}: refs`)
    }
    assert.ok(collapse(scriptOf(read(PENDING))).includes('const wrapperRef = ref(null)'))
})

test('4. the composer: the isTall observer returns early while its root animates (§4.7)', () => {
    const script = collapse(scriptOf(read(COMPOSER)))
    assert.ok(
        script.includes('collapseResizeObserver = new ResizeObserver(() => { if (animating.value) return recomputeIsTall() })'),
        'the guarded observer callback',
    )
    assert.ok(!script.includes('new ResizeObserver(recomputeIsTall)'), 'no unguarded observer left')
    assert.ok(script.includes("window.addEventListener('resize', recomputeIsTall)"), 'the window listener stays unguarded')
    assert.match(script, /function recomputeIsTall\(\) \{ if \(collapsed\.value \|\| !rootRef\.value\) return/, 'recomputeIsTall itself unguarded')
})

test('4b. the composer: beforeMeasure sizes the textarea, then forces the container query to re-evaluate (§9.1)', () => {
    const sfc = read(COMPOSER)
    const script = collapse(scriptOf(sfc))
    assert.ok(
        script.includes(
            'function beforeFooterMeasure() { adjustTextareaHeight() const root = rootRef.value '
            + "root.style.containerType = 'normal' root.offsetWidth root.style.containerType = '' }",
        ),
        'the textarea height first, then the container reflow, in this exact sequence',
    )
    // The toggled element is the query container: the root (rootRef) holds `container: message-input`.
    const [root] = templateRoots(sfc).filter((n) => n.type === 1)
    assert.deepEqual([root.tag, attrsOf(root).class, attrsOf(root).ref], ['div', 'message-input', 'rootRef'])
    const messageInput = rule(parseAll(scopedStyleOf(sfc)), ['.message-input'], topLevel)
    assert.equal(messageInput.decls.container, 'message-input / inline-size')
})

test('5. SessionItemsList: Transition wrappers, explicit notice v-if, banners (§4.3)', () => {
    const all = elements(templateRoots(read(LIST)))
    const transitions = all.filter((n) => n.tag === 'Transition' && attrsOf(n)['@enter'] === 'footerEnter')
    assert.deepEqual(
        transitions.map((n) => n.children.filter((c) => c.type === 1).map((c) => c.tag)),
        [['PendingRequestForm'], ['GoalBlock'], ['PendingRequestForm'], ['HybridTerminalBlock']],
        'the four instances, each alone in its Transition',
    )
    for (const t of transitions) {
        assert.deepEqual(attrsOf(t), { ':css': 'false', '@enter': 'footerEnter', '@leave': 'footerLeave' })
        const [child] = t.children.filter((c) => c.type === 1)
        assert.ok('v-if' in attrsOf(child) && 'ref' in attrsOf(child), `${child.tag}: v-if and ref stay on the component`)
    }
    const terminal = attrsOf(all.find((n) => n.tag === 'HybridTerminalBlock'))
    assert.equal(terminal['v-if'], 'isHybridSession && !parentSessionId && settingsStore.isClaudeHybridEnabled')
    const notice = all.find((n) => attrsOf(n).class === 'hybrid-disabled-notice')
    assert.deepEqual(
        Object.keys(attrsOf(notice)),
        ['v-if', 'v-footer-enter', 'class'],
        'the notice: its own v-if, the enter directive',
    )
    assert.equal(attrsOf(notice)['v-if'], 'isHybridSession && !parentSessionId && !settingsStore.isClaudeHybridEnabled')
    for (const cls of ['stale-banner', 'provider-disabled-banner']) {
        const banner = attrsOf(all.find((n) => attrsOf(n).class === cls))
        assert.ok('v-else-if' in banner, `${cls} keeps its v-else-if`)
        assert.ok('v-footer-enter' in banner, `${cls} carries v-footer-enter`)
    }
})

test('6. SessionItemsList: the switch gate, the controller, the mount-source capture (§4.3, §4.5)', () => {
    const script = collapse(scriptOf(read(LIST)))
    assert.ok(script.includes("import { createSwitchGate, provideFooterMotion } from '../../../composables/useFooterMotion.js'"))
    assert.ok(script.includes('const gate = createSwitchGate()'))
    assert.ok(script.includes('watch(() => props.sessionId, () => gate.arm(), { immediate: true })'), 'armed at mount and on each switch')
    assert.ok(script.includes(
        'const { capturePin, footerEnter, footerLeave, vFooterEnter } = provideFooterMotion({ '
        + 'enabled: computed(() => sessionActive.value && gate.open.value), '
        + 'pinEnabled: computed(() => !props.parentSessionId), '
        + 'getScrollEl: getScrollerElement, '
        + 'isAtBottom: () => !!scrollerRef.value?.isAtBottom(), })',
    ), 'the controller options')
    assert.ok(script.includes(
        'watch( [ hasAnswerablePendingRequest, () => !!currentGoal.value, '
        + '() => isHybridSession.value && !props.parentSessionId && settingsStore.isClaudeHybridEnabled, '
        + '() => isHybridSession.value && !props.parentSessionId && !settingsStore.isClaudeHybridEnabled, ], '
        + "() => capturePin(), { flush: 'pre' }, )",
    ), 'the pre watcher on the four mount sources')
    assert.match(script, /function getScrollerElement\(\) \{ return scrollerRef\.value\?\.\$el \?\? null \}/)
})

test('7. SessionItemsList: the restoring rule, pinned whole, right after the maximized static rule (§4.4)', () => {
    const style = read(LIST).match(/<style scoped>([\s\S]*?)<\/style>/)[1]
    const all = parseAll(style)
    const maximized = rule(all, [
        '.session-footer:has(.pending-request-form.maximized)',
        '.session-footer:has(.hybrid-terminal-block.maximized)',
        '.session-footer:has(.goal-block.maximized)',
    ], topLevel)
    const restoring = rule(all, ['.session-footer:has([data-footer-restoring])'], topLevel)
    assertPinned(restoring, 'position: static;')
    assert.equal(restoring.order, maximized.order + 1, 'next to the existing static rule')
    const id = 'data-v-7d7d7d7d'
    const { code, errors } = compileStyle({ source: style, filename: 'SessionItemsList.vue', id, scoped: true })
    assert.deepEqual(errors, [])
    const compiled = parseAll(code).filter((r) => r.selectors.some((s) => s.includes('[data-footer-restoring]')))
    assert.equal(compiled.length, 1)
    assert.ok(compiled[0].selectors[0].includes(`[${id}]`), 'scoped to the footer')
})

test('8. the seven document-wide pending-form lookups skip a leaving form (§4.3)', () => {
    const sites = [
        ['../composables/usePendingRequestSubmitShortcut.js', [`document.querySelector('${LEAVING_FORM}')`]],
        ['../utils/focusChat.js', [`document.querySelector('${LEAVING_FORM}')`]],
        ['../components/session/detail/items/codex/PendingRequestBody.vue', [`document.querySelector('${LEAVING_FORM}')`]],
        ['../components/session/detail/items/claude_code/PendingRequestBody.vue', [
            `document.querySelector('${LEAVING_FORM} .option-card.auto-focused')`,
            `document.querySelector('${LEAVING_FORM} .auto-focused')`,
            `document.querySelector('${LEAVING_FORM} .pending-request-details')`,
            `document.querySelector('${LEAVING_FORM}')`,
        ]],
    ]
    for (const [file, lookups] of sites) {
        const source = read(file)
        for (const lookup of lookups) assert.ok(source.includes(lookup), `${file}: ${lookup}`)
    }
    // No other document-wide lookup of the form anywhere in the sources.
    const walk = (dir) => readdirSync(dir).flatMap((name) => {
        const path = join(dir, name)
        return statSync(path).isDirectory() ? walk(path) : [path]
    })
    const offenders = []
    for (const path of walk(srcDir)) {
        if (!/\.(js|vue)$/.test(path) || path.endsWith('.test.js')) continue
        const text = readFileSync(path, 'utf8')
        for (const m of text.matchAll(/querySelector(?:All)?\(\s*['"`]([^'"`]*\.pending-request-form[^'"`]*)['"`]/g)) {
            if (!m[1].startsWith(LEAVING_FORM)) offenders.push(`${relative(srcDir, path)}: ${m[1]}`)
        }
    }
    assert.deepEqual(offenders, [])
    const count = sites.reduce((n, [file]) => n + (read(file).split(LEAVING_FORM).length - 1), 0)
    assert.equal(count, 7, 'seven lookups')
})

test('9. no transform in the composable and the helper keyframes (§5)', () => {
    for (const file of ['../composables/useFooterMotion.js', '../utils/footerMotion.js']) {
        const code = read(file).replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')
        assert.ok(!/transform|translate|scale|rotate/i.test(code), `${file}: no transform`)
    }
})
