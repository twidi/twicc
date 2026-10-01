import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createDirRefresher, mergeListingIntoNode } from './tree.js'

function dir(name, children, loaded = true) {
    return loaded ? { name, type: 'directory', loaded: true, children } : { name, type: 'directory', loaded: false }
}
const file = name => ({ name, type: 'file' })

test('merge keeps existing node objects (loaded subtree) and follows the listing order', () => {
    const sub = dir('sub', [file('deep.txt')])
    const node = dir('d', [file('b.txt'), sub, file('gone.txt')])
    mergeListingIntoNode(node, { children: [file('a.txt'), file('b.txt'), dir('sub', null, false)] })
    assert.deepEqual(node.children.map(c => c.name), ['a.txt', 'b.txt', 'sub'])
    assert.equal(node.children[2], sub)
    assert.equal(sub.children[0].name, 'deep.txt')
})

test('merge does not reuse a node whose type changed', () => {
    const node = dir('d', [file('x')])
    const listed = dir('x', null, false)
    mergeListingIntoNode(node, { children: [listed] })
    assert.equal(node.children[0], listed)
})

function deferred() {
    let resolve
    const promise = new Promise(r => { resolve = r })
    return { promise, resolve }
}

function setup({ rootPath = '/p', tree } = {}) {
    const state = { tree: tree ?? dir('p', [dir('d', [file('old.txt')]), dir('stub', null, false)]), rootPath }
    const fetches = []
    const refresher = createDirRefresher({
        fetchListing: path => {
            const d = deferred()
            fetches.push({ path, ...d })
            return d.promise
        },
        getTree: () => state,
    })
    return { state, fetches, refresher }
}

const completed = (targetDir, name) => ({ target_dir: targetDir, final_path: `${targetDir}/${name}` })
const flush = () => new Promise(r => setImmediate(r))

test('a burst of completions into one directory gives at most two fetches', async () => {
    const { state, fetches, refresher } = setup()
    const done = []
    for (let i = 0; i < 30; i++) done.push(refresher.completed(completed('/p/d', `f${i}.txt`)))
    assert.equal(fetches.length, 1)
    fetches[0].resolve({ children: [file('old.txt'), file('f0.txt')] })
    await flush()
    assert.equal(fetches.length, 2)
    fetches[1].resolve({ children: [file('old.txt'), file('f0.txt'), file('f29.txt')] })
    await Promise.all(done)
    assert.equal(fetches.length, 2)
    assert.deepEqual(state.tree.children[0].children.map(c => c.name), ['old.txt', 'f0.txt', 'f29.txt'])
})

test('stub, absent node, or a path outside the root: no fetch', async () => {
    const { fetches, refresher } = setup()
    await refresher.completed(completed('/p/stub', 'a.txt'))
    await refresher.completed(completed('/p/missing', 'a.txt'))
    await refresher.completed(completed('/elsewhere', 'a.txt'))
    assert.equal(fetches.length, 0)
})

test('panel not started (no tree): nothing', async () => {
    const fetches = []
    const refresher = createDirRefresher({ fetchListing: p => { fetches.push(p); return null }, getTree: () => null })
    await refresher.completed(completed('/p/d', 'a.txt'))
    assert.equal(fetches.length, 0)
})

test('root /: every absolute path is contained', async () => {
    const tree = dir('/', [dir('home', [dir('u', [])])])
    const { fetches, refresher } = setup({ rootPath: '/', tree })
    const done = refresher.completed(completed('/home/u', 'a.txt'))
    fetches[0].resolve({ children: [file('a.txt')] })
    await done
    assert.deepEqual(tree.children[0].children[0].children.map(c => c.name), ['a.txt'])
})

test('tree replaced during the fetch: merge into the new node, never the detached one', async () => {
    const { state, fetches, refresher } = setup()
    const detached = state.tree.children[0]
    const done = refresher.completed(completed('/p/d', 'new.txt'))
    const fresh = dir('p', [dir('d', [file('old.txt')])])
    state.tree = fresh
    fetches[0].resolve({ children: [file('old.txt'), file('new.txt')] })
    await done
    assert.deepEqual(detached.children.map(c => c.name), ['old.txt'])
    assert.deepEqual(fresh.children[0].children.map(c => c.name), ['old.txt', 'new.txt'])
})

test('node became a stub during the fetch: stop', async () => {
    const { state, fetches, refresher } = setup()
    const done = refresher.completed(completed('/p/d', 'new.txt'))
    state.tree = dir('p', [dir('d', null, false)])
    fetches[0].resolve({ children: [file('new.txt')] })
    await done
    assert.equal(state.tree.children[0].children, undefined)
})

test('a failed listing fetch changes nothing', async () => {
    const { state, fetches, refresher } = setup()
    const done = refresher.completed(completed('/p/d', 'new.txt'))
    fetches[0].resolve(null)
    await done
    assert.deepEqual(state.tree.children[0].children.map(c => c.name), ['old.txt'])
})
