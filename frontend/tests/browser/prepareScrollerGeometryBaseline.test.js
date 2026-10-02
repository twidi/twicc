import test from 'node:test'
import assert from 'node:assert/strict'
import { resolve } from 'node:path'

async function fixture() {
    const api = await import('./prepareScrollerGeometryBaseline.mjs')
    const files = new Map()
    const missing = () => Object.assign(new Error('missing'), { code: 'ENOENT' })
    const operations = {
        git: args => { assert.deepEqual(args, ['show', '56a1600a:frontend/src/composables/useVirtualScroll.js']); return 'export function useVirtualScroll() {}\n' },
        read: path => { if (!files.has(path)) throw missing(); return files.get(path) },
        write: (path, value, options) => {
            assert.equal(options.flag, 'wx')
            if (files.has(path)) throw Object.assign(new Error('collision'), { code: 'EEXIST' })
            files.set(path, value)
        },
        remove: path => { if (!files.delete(path)) throw missing() },
    }
    const adapter = resolve(api.EXPECTED_ROOT, api.ADAPTER_PATH)
    const manifest = resolve(api.EXPECTED_ROOT, api.MANIFEST_PATH)
    return { api, files, operations, adapter, manifest }
}
test('pinned unchanged adapter has exclusive ownership and guarded removal', async () => {
    const f = await fixture(); const m = f.api.prepareScrollerGeometryBaseline({ operations: f.operations })
    assert.equal(m.baselineCommit, '56a1600a'); assert.equal(m.sourcePath, 'frontend/src/composables/useVirtualScroll.js')
    assert.deepEqual(m.createdPaths, [f.api.ADAPTER_PATH]); assert.match(m.digest, /^[a-f0-9]{64}$/)
    assert.equal(f.files.get(f.adapter), f.operations.git(['show', '56a1600a:frontend/src/composables/useVirtualScroll.js']))
    assert.throws(() => f.api.removeScrollerGeometryBaseline({ operations: f.operations }), /tabs/)
    f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true }); assert.equal(f.files.size, 0)
})
test('existing and racing adapters remain unowned', async () => {
    for (const racing of [false, true]) {
        const f = await fixture(); const write = f.operations.write
        if (!racing) f.files.set(f.adapter, 'user')
        else f.operations.write = (path, value, options) => { if (path === f.adapter) f.files.set(path, 'user'); write(path, value, options) }
        assert.throws(() => f.api.prepareScrollerGeometryBaseline({ operations: f.operations }))
        assert.equal(f.files.get(f.adapter), 'user'); assert.equal(f.files.has(f.manifest), false)
    }
})
test('modified adapter and invalid manifest cannot be removed', async () => {
    const f = await fixture(); f.api.prepareScrollerGeometryBaseline({ operations: f.operations })
    f.files.set(f.adapter, 'modified')
    assert.throws(() => f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true }), /modified/)
    const m = JSON.parse(f.files.get(f.manifest)); m.createdPaths = ['../../outside']; f.files.set(f.manifest, JSON.stringify(m))
    assert.throws(() => f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true }), /manifest/)
    assert.equal(f.files.get(f.adapter), 'modified')
})
test('manifest failure plus rollback failure saves recovery ownership for cleanup retry', async () => {
    const f = await fixture(); const write = f.operations.write, remove = f.operations.remove; let writes = 0
    f.operations.write = (path, value, options) => { if (path === f.manifest && writes++ === 0) throw new Error('manifest denied'); write(path, value, options) }
    f.operations.remove = () => { throw new Error('rollback denied') }
    assert.throws(() => f.api.prepareScrollerGeometryBaseline({ operations: f.operations }), error => error instanceof AggregateError && error.errors.length === 2)
    assert.equal(f.files.has(f.manifest), true)
    assert.throws(() => f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true }), /rollback denied/)
    f.operations.remove = remove; f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true }); assert.equal(f.files.size, 0)
})
test('recovery-write failure reports all failures and preserves remaining files', async () => {
    const f = await fixture(); const write = f.operations.write
    f.operations.write = (path, value, options) => { if (path === f.manifest) throw new Error('manifest denied'); write(path, value, options) }
    f.operations.remove = () => { throw new Error('rollback denied') }
    assert.throws(() => f.api.prepareScrollerGeometryBaseline({ operations: f.operations }), error => error instanceof AggregateError && error.errors.length === 3)
    assert.equal(f.files.has(f.adapter), true); assert.equal(f.files.has(f.manifest), false)
})
test('racing manifest remains untouched after failed rollback and recovery write', async () => {
    const f = await fixture(); const write = f.operations.write
    f.operations.write = (path, value, options) => { if (path === f.manifest) f.files.set(path, 'racing owner'); write(path, value, options) }
    f.operations.remove = () => { throw new Error('rollback denied') }
    assert.throws(() => f.api.prepareScrollerGeometryBaseline({ operations: f.operations }), AggregateError)
    assert.equal(f.files.get(f.manifest), 'racing owner'); assert.equal(f.files.has(f.adapter), true)
})
test('modified rollback target is preserved and saved ownership refuses cleanup', async () => {
    const f = await fixture(); const write = f.operations.write; let count = 0
    f.operations.write = (path, value, options) => {
        if (path === f.manifest && count++ === 0) { f.files.set(f.adapter, 'racing edit'); throw new Error('manifest denied') }
        write(path, value, options)
    }
    assert.throws(() => f.api.prepareScrollerGeometryBaseline({ operations: f.operations }), AggregateError)
    assert.equal(f.files.get(f.adapter), 'racing edit')
    assert.throws(() => f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true }), /modified/)
})
test('manifest unlink failure retains ownership after adapter removal and permits retry', async () => {
    const f = await fixture(); const remove = f.operations.remove
    f.api.prepareScrollerGeometryBaseline({ operations: f.operations })
    f.operations.remove = path => { if (path === f.manifest) throw new Error('manifest unlink denied'); remove(path) }
    assert.throws(() => f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true }), /unlink denied/)
    assert.equal(f.files.has(f.adapter), false); assert.equal(f.files.has(f.manifest), true)
    f.operations.remove = remove; f.api.removeScrollerGeometryBaseline({ operations: f.operations, tabsClosed: true })
    assert.equal(f.files.size, 0)
})
test('existing manifest and wrong root refuse preparation without touching files', async () => {
    const f = await fixture(); f.files.set(f.manifest, 'other owner')
    assert.throws(() => f.api.prepareScrollerGeometryBaseline({ operations: f.operations }), /already exists/)
    assert.throws(() => f.api.prepareScrollerGeometryBaseline({ root: '/wrong', operations: f.operations }), /requires/)
    assert.equal(f.files.get(f.manifest), 'other owner'); assert.equal(f.files.has(f.adapter), false)
})
