// Run with: node --test src/utils/theme.test.js (from the frontend dir)
// The OS scheme listener delegates to a handler when one is set (visual refresh step 5c,
// docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §5.3). In its own file: the
// global stubs below must not reach the `env = globalThis` defaults of other tests.
import test from 'node:test'
import assert from 'node:assert/strict'

const counts = { toggle: 0, createElement: 0 }
let changeListener = null

globalThis.window = {
    matchMedia: () => ({
        matches: false,
        addEventListener: (type, listener) => {
            if (type === 'change') changeListener = listener
        },
    }),
}
globalThis.document = {
    documentElement: {
        classList: {
            add() {},
            remove() {},
            toggle() { counts.toggle++ },
        },
        dataset: {},
    },
    body: { appendChild() {} },
    createElement(tag) {
        counts.createElement++
        if (tag === 'canvas') {
            return {
                getContext: () => ({
                    clearRect() {},
                    fillStyle: '',
                    fillRect() {},
                    getImageData: () => ({ data: [0, 0, 0, 0] }),
                }),
            }
        }
        return { style: {}, remove() {} }
    },
}
globalThis.getComputedStyle = () => ({ color: '' })

const { initTheme, setSystemSchemeChangeHandler } = await import('./theme.js')

test('25. the prefers-color-scheme listener: fallback, handler, handler cleared', () => {
    initTheme()
    assert.equal(typeof changeListener, 'function', 'initTheme listens to the OS scheme')

    let toggles = counts.toggle
    changeListener()
    assert.ok(counts.toggle > toggles, 'no handler: re-applies the scheme')

    let handled = 0
    setSystemSchemeChangeHandler(() => { handled++ })
    toggles = counts.toggle
    const created = counts.createElement
    changeListener()
    assert.equal(handled, 1)
    assert.equal(counts.toggle, toggles, 'no class toggle')
    assert.equal(counts.createElement, created, 'no color recompute')

    setSystemSchemeChangeHandler(null)
    toggles = counts.toggle
    changeListener()
    assert.ok(counts.toggle > toggles, 'handler cleared: the fallback again')
    assert.equal(handled, 1)
})
