import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { isCompactHeight, COMPACT_HEIGHT_REM } from './compactHeight.js'

const src = join(dirname(fileURLToPath(import.meta.url)), '..')

// Visual refresh retouches: the compact header threshold follows the font size setting.
test('1. the threshold is 60 root rem (960px at 16px), measured against the real root font size', () => {
    assert.equal(COMPACT_HEIGHT_REM, 60)
    assert.equal(isCompactHeight(960, 16), true)
    assert.equal(isCompactHeight(961, 16), false)
    assert.equal(isCompactHeight(960, 20), true, 'a bigger font makes the same viewport compact')
    assert.equal(isCompactHeight(700, 11), false, 'a smaller font makes it roomy: 11 x 60 = 660')
})

test('2. no height media query remains: the four compact styles read the class on <html>', () => {
    for (const f of ['views/SessionView.vue', 'components/project/ProjectDetailPanel.vue', 'components/project/ProjectDetailHeader.vue', 'components/session/detail/SessionHeader.vue']) {
        const s = readFileSync(join(src, f), 'utf8')
        assert.doesNotMatch(s, /@media \(max-height/, f)
        assert.match(s, /:where\(html\.compact-height\)/, f)
    }
})

test('3. the class is refreshed at startup and when the font size changes', () => {
    const s = readFileSync(join(src, 'stores/settings.js'), 'utf8')
    assert.match(s, /fontSize = `\$\{store\.fontSize\}px`\n\s+watchCompactHeight\(\)/)
    assert.match(s, /fontSize = `\$\{size\}px`\n\s+updateCompactHeight\(\)/)
})
