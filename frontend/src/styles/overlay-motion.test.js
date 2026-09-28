// Overlay entrances and exits (visual refresh step 5b,
// docs/plans/2026-09-28-overlay-motion-design.md): the wiring in the entry files and the
// components, read as text. The sheets are tested in utils/waMotionStyles.test.js, the
// picker entrance in composables/usePopupMotion.test.js, the toasts in toast-motion.test.js.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
const collapse = (text) => text.trim().replace(/\s+/g, ' ')
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

const scriptOf = (sfc) => sfc.match(/<script setup>([\s\S]*?)<\/script>/)[1]
const templateOf = (sfc) =>
    sfc.replace(/<style[\s\S]*?<\/style>/g, '').replace(/<script[\s\S]*?<\/script>/g, '').replace(/<!--[\s\S]*?-->/g, '')
const styleOf = (sfc) => [...sfc.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n')

/** The body of `function name(` in a script (up to the closing brace at column 0). */
function functionBody(script, name) {
    const start = script.indexOf(`function ${name}(`)
    assert.ok(start >= 0, `function ${name}`)
    const end = script.indexOf('\n}\n', start)
    return script.slice(start, end + 2)
}

/** Opening tags named `tag` of a template, with their raw attribute text. */
function openingTags(template, tag) {
    return [...template.matchAll(new RegExp(`<${tag}\\b((?:[^>"']|"[^"]*"|'[^']*')*)>`, 'g'))].map((m) => m[1])
}

// ---------------------------------------------------------------------------
// Entry files
// ---------------------------------------------------------------------------

test('installWaMotionStyles() runs in the body of the three entry files, before mount', () => {
    const entries = [
        ['../main.js', './utils/', 'installDetailsMotion()'],
        ['../share-session/main.js', '../utils/', 'installDetailsMotion()'],
        ['../artifact-shell/main.js', '../utils/', null],
    ]
    for (const [file, prefix, after] of entries) {
        const lines = read(file).split('\n').map((l) => l.trim())
        assert.ok(lines.includes(`import { installWaMotionStyles } from '${prefix}waMotionStyles'`), `${file}: import`)
        const at = lines.indexOf('installWaMotionStyles()')
        assert.ok(at >= 0, `${file}: call`)
        assert.equal(lines.filter((l) => l === 'installWaMotionStyles()').length, 1, `${file}: one call`)
        const mountAt = lines.findIndex((l) => l.includes('.mount('))
        assert.ok(mountAt > at, `${file}: before mount`)
        if (after) {
            const previous = lines.slice(0, at).reverse().find((l) => l && !l.startsWith('import ') && !l.startsWith('//'))
            assert.equal(previous, after, `${file}: right after ${after}`)
        }
    }
})

// ---------------------------------------------------------------------------
// Command palette and search overlay (§5.1, §5.6)
// ---------------------------------------------------------------------------

const palette = read('../components/app/CommandPalette.vue')
const search = read('../components/app/SearchOverlay.vue')

test('the command palette and the search overlay are drop-in dialogs', () => {
    for (const [name, sfc] of [['CommandPalette', palette], ['SearchOverlay', search]]) {
        const [dialog] = openingTags(templateOf(sfc), 'wa-dialog')
        const classes = dialog.match(/\sclass="([^"]*)"/)?.[1].split(/\s+/) ?? []
        assert.ok(classes.includes('motion-drop'), `${name}: class motion-drop`)
    }
    const [searchDialog] = openingTags(templateOf(search), 'wa-dialog')
    assert.ok(searchDialog.match(/\sclass="([^"]*)"/)[1].split(/\s+/).includes('search-overlay'), 'SearchOverlay keeps its class')
})

test('their search fields carry the autofocus attribute', () => {
    const inputs = openingTags(templateOf(palette), 'input').filter((attrs) => attrs.includes('ref="searchInputRef"'))
    assert.equal(inputs.length, 1)
    assert.match(inputs[0], /\sautofocus(\s|$)/, 'CommandPalette <input autofocus>')

    const fields = openingTags(templateOf(search), 'wa-input').filter((attrs) => attrs.includes('ref="searchInputRef"'))
    assert.equal(fields.length, 1)
    assert.match(fields[0], /\s:autofocus\.attr="true"/, 'SearchOverlay <wa-input :autofocus.attr="true">')
})

test('CommandPalette: onAfterShow keeps a highlight moved during the entrance', () => {
    const body = collapse(functionBody(scriptOf(palette), 'onAfterShow'))
    assert.match(
        body,
        /if \(activeKey\.value === null \|\| !visibleItems\.value\.some\(\((\w+)\) => \1\.key === activeKey\.value\)\) \{? ?selectFirstItem\(\)/,
    )
    assert.equal(body.match(/selectFirstItem\(\)/g).length, 1, 'no unguarded call')
    assert.ok(body.includes('searchInputRef.value?.focus()'), 'keeps its focus() call')
})

test('SearchOverlay: the kept query is selected at focus time', () => {
    const script = scriptOf(search)
    // The handler is declared at the top level (a stable reference).
    const handler = functionBody(script, 'selectKeptQuery')
    assert.ok(script.includes('\nfunction selectKeptQuery('), 'top-level handler')
    assert.match(collapse(handler), /if \(query\.value\) searchInputRef\.value\?\.select\(\)/)

    const open = functionBody(script, 'open')
    const earlyReturn = open.indexOf('return')
    const listen = open.indexOf("addEventListener('focusin', selectKeptQuery, { once: true })")
    const opening = open.indexOf('dialogRef.value.open = true')
    assert.ok(earlyReturn >= 0 && listen > earlyReturn && opening > listen, 'registered on the opening branch, before open = true')

    const afterHide = functionBody(script, 'handleAfterHide')
    assert.ok(afterHide.includes("removeEventListener('focusin', selectKeptQuery)"), 'handleAfterHide removes it')
    assert.ok(afterHide.indexOf('removeEventListener') > afterHide.indexOf('if (e.target !== dialogRef.value) return'), 'after the target guard')

    const afterShow = collapse(functionBody(script, 'handleAfterShow'))
    assert.ok(!afterShow.includes('select()'), 'handleAfterShow no longer selects')
    assert.match(afterShow, /if \(input && !input\.contains\(document\.activeElement\)\) \{? ?input\.focus\(\)/)
})

// ---------------------------------------------------------------------------
// Pickers (§6)
// ---------------------------------------------------------------------------

test('the four pickers play their entrance and keep their active binding', () => {
    const pickers = [
        '../components/message/CommandPickerPopup.vue',
        '../components/message/MessageHistoryPickerPopup.vue',
        '../components/files/FilePickerPopup.vue',
        '../components/files/DirectoryPickerPopup.vue',
    ]
    for (const file of pickers) {
        const sfc = read(file)
        const script = scriptOf(sfc)
        assert.ok(script.includes("import { usePopupMotion } from '../../composables/usePopupMotion'"), `${file}: import`)
        assert.ok(script.includes('const panelRef = ref(null)'), `${file}: panelRef`)
        assert.equal(script.match(/usePopupMotion\(isOpen, panelRef, popupRef\)/g)?.length, 1, `${file}: one call`)
        const template = templateOf(sfc)
        const [popup] = openingTags(template, 'wa-popup')
        assert.match(popup, /\sref="popupRef"/, `${file}: popupRef`)
        assert.match(popup, /\s:active="isOpen"/, `${file}: active bound to isOpen`)
        const panels = openingTags(template, 'div').filter((attrs) => /class="picker-panel\b/.test(attrs))
        assert.equal(panels.length, 1, `${file}: one picker panel`)
        assert.match(panels[0], /\sref="panelRef"/, `${file}: panelRef on .picker-panel`)
    }
})

// ---------------------------------------------------------------------------
// Session switcher (§7)
// ---------------------------------------------------------------------------

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

function animations(value) {
    return splitTopLevel(value).map((part) => {
        const list = splitTopLevel(part, ' ')
        return {
            name: list.find((t) => t.startsWith('twicc-')),
            duration: list.find((t) => /^\d+ms$/.test(t)),
            easing: list.find((t) => /^(var\(--motion-ease[\w-]*\)|ease(-in|-out|-in-out)?)$/.test(t)),
            rest: list.filter((t) => ['reverse', 'forwards'].includes(t)).join(' '),
        }
    })
}

test('SessionSwitcher: the overlay sits in a Transition with explicit durations', () => {
    const template = collapse(templateOf(read('../components/app/SessionSwitcher.vue')))
    assert.match(
        template,
        /<Teleport to="body"> <Transition name="switcher" :duration="\{ enter: 260, leave: 160 \}"> <div v-if="visible" class="switcher-overlay glass-veil"/,
    )
    assert.match(template, /<\/div> <\/Transition> <\/Teleport>/)
})

test('SessionSwitcher: the veil and the panel move with one keyframe per part, forward then reversed', () => {
    const blocks = topBlocks(stripComments(styleOf(read('../components/app/SessionSwitcher.vue'))))
    const block = (head) => {
        const found = blocks.filter((b) => b.head === head)
        assert.equal(found.length, 1, `exactly one "${head}"`)
        return found[0]
    }
    const keyframe = (name) => topBlocks(block(`@keyframes ${name}`).body).map((step) => declarations(step.body))
    const drop = keyframe('twicc-switcher-drop')
    const fade = keyframe('twicc-switcher-fade')
    for (const step of drop) {
        assert.deepEqual(Object.keys(step).sort(), ['scale', 'translate'], 'movement only, no opacity')
        for (const value of Object.values(step)) assert.ok(value.includes('var(--motion-amount)'), value)
    }
    for (const step of fade) assert.deepEqual(Object.keys(step), ['opacity'])
    // The panel is a glass surface: it fades through the registered --twicc-reveal (§14).
    assert.deepEqual(keyframe('twicc-reveal'), [{ '--twicc-reveal': '0' }])

    const veilEnter = animations(declarations(block('.switcher-enter-active.switcher-overlay::before').body).animation)
    const veilLeave = animations(declarations(block('.switcher-leave-active.switcher-overlay::before').body).animation)
    const panelEnter = animations(declarations(block('.switcher-enter-active .switcher-panel').body).animation)
    const panelLeave = animations(declarations(block('.switcher-leave-active .switcher-panel').body).animation)

    assert.deepEqual(veilEnter, [{ name: 'twicc-switcher-fade', duration: '260ms', easing: 'var(--motion-ease-out)', rest: '' }])
    assert.deepEqual(panelEnter, [
        { name: 'twicc-switcher-drop', duration: '260ms', easing: 'var(--motion-ease-spring)', rest: '' },
        { name: 'twicc-reveal', duration: '260ms', easing: 'var(--motion-ease)', rest: '' },
    ])
    for (const [enter, leave] of [[veilEnter, veilLeave], [panelEnter, panelLeave]]) {
        assert.deepEqual(leave.map((a) => a.name), enter.map((a) => a.name), 'same keyframes, same order')
        for (const a of leave) assert.deepEqual([a.duration, a.easing, a.rest], ['160ms', 'ease-out', 'reverse forwards'])
    }

    assert.deepEqual(declarations(block('.switcher-leave-active').body), { 'pointer-events': 'none' })
    for (const b of blocks) {
        if (/\.switcher-overlay(?![\w-])(?!::)/.test(b.head) && !b.head.startsWith('@')) {
            assert.ok(!('opacity' in declarations(b.body)), `${b.head}: no opacity on the overlay itself`)
        }
    }
})

test('SessionSwitcher: the moving panel fades its content through --twicc-reveal-filter (§14.4)', () => {
    const blocks = topBlocks(stripComments(styleOf(read('../components/app/SessionSwitcher.vue'))))
    const moving = ['.switcher-enter-active .switcher-panel', '.switcher-leave-active .switcher-panel']
    const rules = blocks.filter((b) => !b.head.startsWith('@'))
    for (const selector of moving) {
        const merged = Object.assign({}, ...rules.filter((b) => splitTopLevel(b.head).includes(selector)).map((b) => declarations(b.body)))
        assert.equal(merged['--twicc-reveal-filter'], 'opacity(var(--twicc-reveal))', selector)
    }
    for (const b of rules) {
        const decls = declarations(b.body)
        if ('--twicc-reveal-filter' in decls) {
            for (const selector of splitTopLevel(b.head)) assert.ok(moving.includes(selector), `${selector}: declares the motion state`)
        }
        for (const property of ['--glass-bg', '--glass-sticky-bg', '--glass-tooltip-bg', '--glass-settle']) {
            assert.ok(!(property in decls), `${b.head}: no opaque-while-moving ${property}`)
        }
    }
})

// ---------------------------------------------------------------------------
// Tooltip delay (§5.4)
// ---------------------------------------------------------------------------

test('AppTooltip: a 250ms show delay, overridable by the caller', () => {
    const sfc = read('../components/ui/AppTooltip.vue')
    assert.match(scriptOf(sfc), /\nconst TOOLTIP_SHOW_DELAY_MS = 250\n/)
    const [tooltip] = openingTags(templateOf(sfc), 'wa-tooltip')
    const delayAt = tooltip.indexOf(':show-delay="TOOLTIP_SHOW_DELAY_MS"')
    assert.ok(delayAt >= 0, ':show-delay bound to the constant')
    assert.ok(tooltip.indexOf('v-bind="$attrs"') > delayAt, 'declared before v-bind="$attrs"')
})
