import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolveRailItems } from '../utils/sidebarRail.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const css = () => read('./rail-button.css')
const body = (source, selector) => {
    const start = source.indexOf(`${selector} {`)
    assert.ok(start >= 0, selector)
    return source.slice(start + selector.length + 2, source.indexOf('}', start))
}
const tokens = source => Object.fromEntries([...source.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]))

test('unlayered rail tokens and native button overrides match the design', () => {
    const source = css()
    assert.doesNotMatch(source, /@layer|outline|opacity|cursor/)
    assert.deepEqual(tokens(body(source, ':root')), {
        '--rail-button-size': '2.25rem', '--rail-icon-size': '1.125rem',
        '--rail-button-color': 'var(--wa-color-text-quiet)',
        '--rail-button-fill-hover': 'var(--wa-color-neutral-fill-quiet)',
        '--rail-button-fill-active': 'var(--wa-color-brand-fill-quiet)',
        '--rail-button-color-active': 'var(--wa-color-brand-on-quiet)',
        '--rail-card-padding': '0.25rem', '--rail-gap': '0.25rem',
        '--rail-card-width': 'calc(var(--rail-button-size) + 2 * var(--rail-card-padding) + 2 * var(--divider-size))',
        '--rail-width': 'calc(var(--rail-card-width) + var(--panel-gap))',
    })
    const declarations = Object.fromEntries(body(source, '.rail-button').split(';').filter(s => s.trim()).map(s => s.trim().split(/:\s*/)))
    assert.deepEqual(declarations, {
        height: 'var(--rail-button-size)', width: 'var(--rail-button-size)', display: 'grid', 'place-items': 'center',
        padding: '0', border: '0', 'line-height': '1', 'border-radius': 'var(--wa-form-control-border-radius)',
        color: 'var(--rail-button-color)', transition: 'none', position: 'relative', flex: 'none',
        'font-size': 'var(--rail-icon-size)', 'background-color': 'var(--rail-button-bg, transparent)', 'background-image': 'none',
    })
    assert.match(body(source, '.rail-button > wa-icon'), /margin: 0;/)
})

test('hover is guarded, press gives touch feedback, and active mode wins last', () => {
    const source = css()
    assert.match(source, /@media \(hover: hover\) \{\s*\.rail-button:hover:not\(:disabled\) \{\s*background-image: linear-gradient\(var\(--rail-button-fill-hover\), var\(--rail-button-fill-hover\)\);\s*\}\s*\}/)
    assert.match(body(source, '.rail-button:active:not(:disabled)'), /background-image: linear-gradient\(var\(--rail-button-fill-hover\), var\(--rail-button-fill-hover\)\);/)
    const active = source.slice(source.indexOf('.rail-button[aria-pressed="true"]'))
    assert.match(active, /^\.rail-button\[aria-pressed="true"\],\s*\.rail-button\[aria-pressed="true"\]:hover:not\(:disabled\),\s*\.rail-button\[aria-pressed="true"\]:active:not\(:disabled\) \{/)
    assert.match(active, /background-image: linear-gradient\(var\(--rail-button-fill-active\), var\(--rail-button-fill-active\)\);/)
    assert.match(active, /color: var\(--rail-button-color-active\);/)
    assert.ok(source.indexOf('.rail-button[aria-pressed="true"]') > source.indexOf('.rail-button:active:not(:disabled)'))
})

test('main imports rail styles between tool cards and scrollbars', () => {
    const source = read('../main.js')
    const rail = source.indexOf("import './styles/rail-button.css'")
    assert.ok(rail > source.indexOf("import './styles/tool-cards.css'"))
    assert.ok(rail < source.indexOf("import './styles/scrollbars.css'"))
    assert.match(source, /import '\.\/styles\/surfaces.css'\s*\/\/[^\n]*\nimport '\.\/styles\/sidebar-rows.css'\s*\/\/[^\n]*\nimport '\.\/styles\/option-cards.css'/)
})

test('short-height fallback fits nine controls and session dividers while preserving the focus ring', () => {
    const rail = tokens(body(css(), ':root'))
    // WA theme spacing map. These values are documented because node_modules is absent.
    const spacing = { '--wa-space-xs': '0.5rem', '--wa-space-2xs': '0.25rem' }
    const panelGaps = [...read('./surfaces.css').matchAll(/--panel-gap:\s*var\((--wa-space-[\w-]+)\);/g)].map(m => spacing[m[1]])
    assert.equal(panelGaps.length, 2)
    const divider = read('../App.vue').match(/--divider-size:\s*([^;]+);/)[1]
    const glow = tokens(read('./glow.css').slice(0, read('./glow.css').indexOf('/* Tabs draw')))
    const threshold = read('../components/sidebar/SidebarRail.vue').match(/@container rail \(height < ([\d.]+rem)\)/)[1]
    const count = Math.max(...[true, false].map(sidebarOpen => resolveRailItems({ mode: 'sessions', sidebarOpen,
        peerConfigured: true, inboxCount: 1, isMac: false }).length))
    assert.equal(count, 9, 'maximum fixed controls includes New session')
    const px = (length, root) => {
        assert.match(length, /^[\d.]+(?:rem|px)$/)
        return parseFloat(length) * (length.endsWith('rem') ? root : 1)
    }
    for (const root of [12, 16, 32]) {
        const padding = px(rail['--rail-card-padding'], root)
        assert.ok(padding >= px(glow['--wa-focus-ring-width'], root) + px(glow['--wa-focus-ring-offset'], root))
        for (const gap of panelGaps) {
            // Two divider rows add two gaps and two one-pixel borders around active sessions.
            const required = count * px(rail['--rail-button-size'], root) + (count + 2) * px(rail['--rail-gap'], root)
                + 2 * padding + 4 * px(divider, root) + 2 * px(gap, root)
            assert.ok(required < px(threshold, root), `${count} items at ${root}px root with ${gap} panel gap`)
            if (root === 12 && gap === '0.5rem') {
                assert.equal(required, 298, 'nine controls plus session dividers need 298px')
                assert.ok(288 < px(threshold, root), '288px viewport enables scrolling before controls overflow')
            }
        }
    }
})
