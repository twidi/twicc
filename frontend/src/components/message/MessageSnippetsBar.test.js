import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

function fixture() {
    const source = readFileSync(new URL('./MessageSnippetsBar.vue', import.meta.url), 'utf8')
    const start = source.indexOf('function handleSnippetClick(')
    const end = source.indexOf('</script>', start)
    const emitted = [], timers = new Map()
    let closures = 0, timerId = 0
    const api = runInNewContext(`(function() { ${source.slice(start, end)}\nreturn { handleSnippetClick, onSnippetTouchStart, onSnippetTouchMove, onSnippetTouchEnd } })`, {
        groupedEntries: { value: { close: () => closures++ } },
        emit: (...args) => emitted.push(args), onBeforeUnmount: () => {},
        setTimeout: (callback, delay) => { assert.equal(delay, 500); timers.set(++timerId, callback); return timerId },
        clearTimeout: id => timers.delete(id),
    })()
    return { api, emitted, timers, get closures() { return closures } }
}

test('composer touch long press applies once and closes its group', () => {
    const f = fixture(), snippet = { label: 'Example', text: 'Text' }
    f.api.onSnippetTouchStart(snippet, { touches: [{}] })
    assert.equal(f.emitted.length, 0)
    assert.equal(f.timers.size, 1)
    f.timers.values().next().value()
    let prevented = false
    f.api.onSnippetTouchEnd({ preventDefault: () => { prevented = true } })
    assert.equal(prevented, true)
    assert.equal(f.closures, 1)
    assert.deepEqual(f.emitted, [['snippet-long-press', snippet]])
})

test('composer swipe cancels long press and disabled snippets never start it', () => {
    const f = fixture()
    f.api.onSnippetTouchStart({}, { touches: [{}] })
    f.api.onSnippetTouchMove()
    assert.equal(f.timers.size, 0)
    f.api.onSnippetTouchStart({ _disabled: true }, { touches: [{}] })
    assert.equal(f.timers.size, 0)
    assert.equal(f.emitted.length, 0)
})

test('composer normal snippet selection applies and closes its group', () => {
    const f = fixture(), snippet = { label: 'Example', text: 'Text' }
    f.api.handleSnippetClick(snippet)
    assert.equal(f.closures, 1)
    assert.deepEqual(f.emitted, [['snippet-press', snippet]])
})
