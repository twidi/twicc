// CSS invariants of the depth tokens (visual refresh step 2,
// docs/plans/2026-09-26-depth-design.md). CSS has no other test harness here:
// these tests read the stylesheets as text and check what the design relies on.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative) => readFileSync(join(here, relative), 'utf8')

/** Top-level rules (at-rules skipped) as { selector, body }, comments stripped,
 *  selector whitespace collapsed. */
function parseTopLevelBlocks(css) {
    const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
    const blocks = []
    let i = 0
    while (i < text.length) {
        const open = text.indexOf('{', i)
        if (open === -1) break
        const selector = text.slice(i, open).trim().replace(/\s+/g, ' ')
        let depth = 1
        let j = open + 1
        while (j < text.length && depth > 0) {
            if (text[j] === '{') depth++
            else if (text[j] === '}') depth--
            j++
        }
        if (!selector.startsWith('@')) blocks.push({ selector, body: text.slice(open + 1, j - 1) })
        i = j
    }
    return blocks
}

/** Custom-property declarations of the first top-level block with this exact selector. */
function declarations(css, selector) {
    const block = parseTopLevelBlocks(css).find((b) => b.selector === selector)
    assert.ok(block, `no top-level block "${selector}"`)
    const result = {}
    for (const part of block.body.split(';')) {
        const match = part.match(/^\s*(--[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) result[match[1]] = match[2].replace(/\s+/g, ' ')
    }
    return result
}

const REM = 16 // browser default root size; reach checks are relative anyway
function toPx(length) {
    if (length.endsWith('rem')) return parseFloat(length) * REM
    return parseFloat(length) // px or unitless 0
}

/** A box-shadow list as layers { inset, x, y, blur, spread, lengthCount, color }. The
 *  colors used here (oklch(... / a), transparent) contain no comma, so a plain split is
 *  safe. */
function shadowLayers(value) {
    return value.split(',').map((raw) => {
        const layer = raw.trim()
        const inset = /^inset\s/.test(layer)
        const tokens = layer.replace(/^inset\s+/, '').split(/\s+/)
        const lengths = []
        let i = 0
        while (i < tokens.length && /^-?[\d.]+(px|rem)?$/.test(tokens[i])) lengths.push(toPx(tokens[i++]))
        const [x = 0, y = 0, blur = 0, spread = 0] = lengths
        return { inset, x, y, blur, spread, lengthCount: lengths.length, color: tokens.slice(i).join(' ') }
    })
}

/** How far the outer layers of a shadow reach beyond the box, in px at 16px root. */
function reach(value) {
    const outer = shadowLayers(value).filter((l) => !l.inset)
    const max = (fn) => Math.max(0, ...outer.map(fn))
    return {
        below: max((l) => l.y + l.spread + l.blur),
        above: max((l) => -l.y + l.spread + l.blur),
        side: max((l) => Math.abs(l.x) + l.spread + l.blur),
    }
}

const depthCss = read('depth.css')
const lightTokens = declarations(depthCss, ':root')
const darkTokens = declarations(depthCss, '.wa-dark')
const SHADOW_TOKENS = ['--depth-1', '--depth-2', '--depth-3', '--depth-card', '--depth-button', '--depth-highlight', '--depth-edge', '--depth-inset']

test('every depth token is a non-empty box-shadow list without none', () => {
    for (const [scheme, tokens] of [['light', lightTokens], ['dark', darkTokens]]) {
        for (const name of SHADOW_TOKENS) {
            const value = tokens[name]
            assert.ok(value, `${scheme} ${name} missing`)
            assert.doesNotMatch(value, /\bnone\b/, `${scheme} ${name} contains none`)
            for (const layer of shadowLayers(value)) {
                assert.ok(layer.lengthCount >= 2 && layer.lengthCount <= 4,
                    `${scheme} ${name}: a layer needs 2 to 4 lengths`)
                assert.match(layer.color, /^(oklch\([^)]*\)|transparent)$/,
                    `${scheme} ${name}: a layer must end with one color`)
            }
        }
    }
})

test('dark levels 1-3 put their inset layer first, outer layers after (interpolable hover)', () => {
    for (const name of ['--depth-1', '--depth-2', '--depth-3', '--depth-button']) {
        const [first, ...rest] = shadowLayers(darkTokens[name])
        assert.equal(first.inset, true, `dark ${name}: first layer must be inset`)
        assert.ok(rest.every((l) => !l.inset), `dark ${name}: only the first layer may be inset`)
        assert.ok(shadowLayers(lightTokens[name]).every((l) => !l.inset), `light ${name}: no inset layer`)
    }
})

test('--depth-card reaches exactly --depth-card-reach below, nothing above, ≤ 1px sideways', () => {
    const declaredReach = toPx(lightTokens['--depth-card-reach'])
    assert.equal(declaredReach, 3)
    for (const [scheme, tokens] of [['light', lightTokens], ['dark', darkTokens]]) {
        const r = reach(tokens['--depth-card'])
        assert.equal(r.below, declaredReach, `${scheme} --depth-card below`)
        assert.equal(r.above, 0, `${scheme} --depth-card above`)
        assert.ok(r.side <= 1, `${scheme} --depth-card side ${r.side}`)
    }
})

test('Web Awesome shadow tokens are mapped on :root and .wa-invert', () => {
    const mapping = declarations(depthCss, ':root, .wa-invert')
    assert.equal(mapping['--wa-shadow-s'], 'var(--depth-1)')
    assert.equal(mapping['--wa-shadow-m'], 'var(--depth-3)')
    assert.equal(mapping['--wa-shadow-l'], 'var(--depth-3)')
})

test('panel shadows keep the step-1 horizontal budget (≤ 4px)', () => {
    const surfacesCss = read('surfaces.css')
    for (const selector of [':root', '.wa-dark']) {
        const tokens = declarations(surfacesCss, selector)
        for (const name of ['--panel-shadow', '--panel-overlay-shadow']) {
            assert.ok(tokens[name], `${selector} ${name} missing`)
            const r = reach(tokens[name])
            assert.ok(r.side <= 4, `${selector} ${name} side reach ${r.side}px`)
        }
    }
})

test('depth.css is imported by the SPA, the share viewer and the artifact shell', () => {
    const spa = read('../main.js')
    const tokensAt = spa.indexOf("import './styles/transcript-tokens.css'")
    const depthAt = spa.indexOf("import './styles/depth.css'")
    const surfacesAt = spa.indexOf("import './styles/surfaces.css'")
    assert.ok(tokensAt >= 0 && depthAt > tokensAt && surfacesAt > depthAt, 'SPA import order')
    assert.match(read('../share-session/main.js'), /import '\.\.\/styles\/depth\.css'/)
    assert.match(read('../artifact-shell/main.js'), /import '\.\.\/styles\/depth\.css'/)
})
