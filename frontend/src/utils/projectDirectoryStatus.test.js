import test from 'node:test'
import assert from 'node:assert/strict'
import { createProjectDirectoryStatus } from './projectDirectoryStatus.js'

const reply = (body, ok = true) => ({ ok, json: async () => body })

test('check publishes the status of the project directory', async () => {
    const urls = []
    const state = createProjectDirectoryStatus({
        request: async url => { urls.push(url); return reply({ status: 'missing' }) },
        onProjectUpdated: () => {},
    })
    await state.check('p1')
    assert.deepEqual(urls, ['/api/projects/p1/directory-status/'])
    assert.equal(state.status.value, 'missing')
})

test('a failed check leaves the composer available', async () => {
    for (const request of [async () => { throw new Error('offline') }, async () => reply({}, false)]) {
        const state = createProjectDirectoryStatus({ request, onProjectUpdated: () => {} })
        await state.check('p1')
        assert.equal(state.status.value, 'ok')
    }
})

test('only the latest check is published', async () => {
    const releases = []
    const state = createProjectDirectoryStatus({
        request: () => new Promise(resolve => releases.push(resolve)),
        onProjectUpdated: () => {},
    })
    const first = state.check('p1')
    const second = state.check('p2')
    releases[1](reply({ status: 'blocked' }))
    await second
    releases[0](reply({ status: 'missing' }))
    await first
    assert.equal(state.status.value, 'blocked')
})

test('clear abandons a pending check', async () => {
    let release
    const state = createProjectDirectoryStatus({
        request: () => new Promise(resolve => { release = resolve }),
        onProjectUpdated: () => {},
    })
    const pending = state.check('p1')
    state.clear()
    release(reply({ status: 'missing' }))
    await pending
    assert.equal(state.status.value, 'ok')
})

test('a successful recreation restores the composer and publishes the project', async () => {
    const calls = []
    const updates = []
    const state = createProjectDirectoryStatus({
        request: async (url, options) => {
            calls.push([url, options])
            return url.endsWith('directory-status/') ? reply({ status: 'missing' }) : reply({ id: 'p1', stale: false })
        },
        onProjectUpdated: project => updates.push(project),
    })
    await state.check('p1')
    await state.recreate('p1')
    assert.deepEqual(calls[1], ['/api/projects/p1/recreate-directory/', { method: 'POST' }])
    assert.equal(state.status.value, 'ok')
    assert.deepEqual(updates, [{ id: 'p1', stale: false }])
    assert.equal(state.recreating.value, false)
})

test('a refused recreation keeps the banner and exposes the error', async () => {
    const state = createProjectDirectoryStatus({
        request: async url => url.endsWith('directory-status/')
            ? reply({ status: 'missing' }) : reply({ error: 'denied' }, false),
        onProjectUpdated: () => { throw new Error('must not run') },
    })
    await state.check('p1')
    await state.recreate('p1')
    assert.equal(state.status.value, 'missing')
    assert.deepEqual(state.recreateError.value, { error: 'denied' })
    assert.equal(state.recreating.value, false)
})

test('a network failure during the recreation exposes an empty error', async () => {
    const state = createProjectDirectoryStatus({
        request: async url => {
            if (url.endsWith('directory-status/')) return reply({ status: 'missing' })
            throw new Error('offline')
        },
        onProjectUpdated: () => {},
    })
    await state.check('p1')
    await state.recreate('p1')
    assert.deepEqual(state.recreateError.value, {})
})
