// Exclusive fixture ownership. The baseline remains executable source without instrumentation.
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { createHash, randomUUID } from 'node:crypto'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
export const EXPECTED_ROOT = '/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows'
export const BASELINE_COMMIT = '56a1600a'
export const SOURCE_PATH = 'frontend/src/composables/useVirtualScroll.js'
export const ADAPTER_PATH = 'frontend/src/composables/useVirtualScrollGeometryBaseline.js'
export const MANIFEST_PATH = 'frontend/tests/browser/.scroller-geometry-generation.json'
const nativeOperations = {
    git: args => execFileSync('git', args, { cwd: EXPECTED_ROOT, encoding: 'utf8' }),
    read: path => readFileSync(path, 'utf8'), write: writeFileSync, remove: unlinkSync,
}
const digest = text => createHash('sha256').update(text).digest('hex')
function optionalRead(operations, path) {
    try { return operations.read(path) } catch (error) { if (error.code === 'ENOENT') return null; throw error }
}
function validateRoot(root) { if (root !== EXPECTED_ROOT) throw new Error(`Fixture requires ${EXPECTED_ROOT}`) }
export function prepareScrollerGeometryBaseline({ root = EXPECTED_ROOT, operations = nativeOperations } = {}) {
    validateRoot(root)
    for (const path of [ADAPTER_PATH, MANIFEST_PATH]) {
        if (optionalRead(operations, resolve(root, path)) !== null) throw new Error(`File already exists: ${path}`)
    }
    const source = operations.git(['show', `${BASELINE_COMMIT}:${SOURCE_PATH}`])
    const manifest = { version: 1, generationId: randomUUID(), baselineCommit: BASELINE_COMMIT,
        sourcePath: SOURCE_PATH, createdPaths: [ADAPTER_PATH], digest: digest(source) }
    const adapter = resolve(root, ADAPTER_PATH), record = resolve(root, MANIFEST_PATH)
    operations.write(adapter, source, { flag: 'wx' })
    try { operations.write(record, JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' }) }
    catch (error) {
        try {
            if (digest(operations.read(adapter)) !== manifest.digest) throw new Error('Refusing rollback: modified adapter')
            operations.remove(adapter)
        } catch (rollbackError) {
            const errors = [error, rollbackError]
            try { operations.write(record, JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' }) }
            catch (recoveryError) { errors.push(recoveryError) }
            throw new AggregateError(errors, 'Preparation rollback failed; preserve remaining files and inspect ownership')
        }
        throw error
    }
    return manifest
}
function validateManifest(m) {
    if (!m || Object.keys(m).sort().join(',') !== 'baselineCommit,createdPaths,digest,generationId,sourcePath,version'
        || m.version !== 1 || m.baselineCommit !== BASELINE_COMMIT || m.sourcePath !== SOURCE_PATH
        || typeof m.generationId !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(m.generationId)
        || !Array.isArray(m.createdPaths) || m.createdPaths.length !== 1 || m.createdPaths[0] !== ADAPTER_PATH
        || typeof m.digest !== 'string' || !/^[0-9a-f]{64}$/.test(m.digest)) throw new Error('Refusing removal: invalid manifest')
}
export function removeScrollerGeometryBaseline({ root = EXPECTED_ROOT, operations = nativeOperations, tabsClosed = false } = {}) {
    validateRoot(root)
    if (!tabsClosed) throw new Error('Refusing removal: close owned fixture tabs first and confirm tabsClosed')
    const record = optionalRead(operations, resolve(root, MANIFEST_PATH))
    if (record === null) throw new Error('Refusing removal: missing ownership manifest')
    let m
    try { m = JSON.parse(record) } catch { throw new Error('Refusing removal: invalid manifest') }
    validateManifest(m)
    const pinned = operations.git(['show', `${BASELINE_COMMIT}:${SOURCE_PATH}`])
    if (digest(pinned) !== m.digest) throw new Error('Refusing removal: manifest does not own pinned source')
    const adapter = resolve(root, ADAPTER_PATH), source = optionalRead(operations, adapter)
    if (source !== null && digest(source) !== m.digest) throw new Error('Refusing removal: modified adapter')
    if (source !== null) operations.remove(adapter)
    operations.remove(resolve(root, MANIFEST_PATH))
    return m
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim()
    if (process.argv.includes('--remove')) removeScrollerGeometryBaseline({ root, tabsClosed: process.argv.includes('--tabs-closed') })
    else prepareScrollerGeometryBaseline({ root })
    console.log(process.argv.includes('--remove') ? 'Owned scroller baseline removed' : `Scroller baseline prepared from ${BASELINE_COMMIT}`)
}
