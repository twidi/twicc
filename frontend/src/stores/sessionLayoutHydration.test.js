import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive } from 'vue'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const helperStart = source.indexOf('function emptyLayoutIntention()')
const helperEnd = source.indexOf('/** The catalog template subset', helperStart)
const helpers = new Function(`${source.slice(helperStart, helperEnd)}; return { hydrateLayoutIntention, stripLayoutForPersist }`)()
const start = source.indexOf('        _hydrateSessionLayoutFromPersisted(')
const end = source.indexOf('        /**', start)

function fixture() {
    const pending = new Set(), snapshots = new WeakMap()
    let copies = 0
    const actions = new Function('layoutPersistPending', 'layoutHydrationSnapshots', 'hydrateLayoutIntention', 'stripLayoutForPersist',
        `return { ${source.slice(start, end)} }`)(pending, snapshots,
        value => { copies++; return helpers.hydrateLayoutIntention(value) }, helpers.stripLayoutForPersist)
    const store = reactive({ localState: { sessionLayout: {
        s: { assignment: { plan: 'left' }, collapsed: [], tabOrder: ['chat', 'plan'], maximized: 'left' },
    } } })
    const persisted = { assignment: { plan: 'left' }, collapsed: [], tabOrder: ['chat', 'plan'] }
    return { store, pending, persisted, hydrate: value => actions._hydrateSessionLayoutFromPersisted.call(store, 's', value),
        copies: () => copies }
}

test('unchanged persisted layout avoids repeated reconstruction and preserves maximized state', () => {
    const f = fixture(), before = f.store.localState.sessionLayout.s
    f.hydrate(f.persisted)
    const copies = f.copies()
    for (let i = 0; i < 100; i++) f.hydrate(f.persisted)
    assert.equal(f.copies(), copies)
    assert.strictEqual(f.store.localState.sessionLayout.s, before)
    assert.equal(before.maximized, 'left')
})

test('remote layout changes apply while pending local edits keep precedence', () => {
    const f = fixture()
    f.hydrate(f.persisted)
    const remote = { assignment: { plan: 'right' }, collapsed: ['right'], tabOrder: ['plan', 'chat'] }
    f.pending.add('s')
    f.hydrate(remote)
    assert.deepEqual(f.store.localState.sessionLayout.s.assignment, { plan: 'left' })
    f.pending.delete('s')
    f.hydrate(remote)
    assert.deepEqual(f.store.localState.sessionLayout.s.assignment, { plan: 'right' })
    assert.deepEqual(f.store.localState.sessionLayout.s.collapsed, ['right'])
    assert.equal(f.store.localState.sessionLayout.s.maximized, null)
    const copies = f.copies()
    f.hydrate(remote)
    assert.equal(f.copies(), copies)
})

test('a layout arriving before its working copy still hydrates after mount', () => {
    const f = fixture()
    delete f.store.localState.sessionLayout.s
    f.hydrate(f.persisted)
    assert.equal(f.copies(), 0)
    f.store.localState.sessionLayout.s = { assignment: {}, collapsed: [], tabOrder: [] }
    f.hydrate(f.persisted)
    assert.deepEqual(f.store.localState.sessionLayout.s.assignment, { plan: 'left' })
})
