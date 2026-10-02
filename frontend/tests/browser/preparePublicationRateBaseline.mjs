// Optional, isolated browser adapters. Never write production modules.
import { execFileSync } from 'node:child_process'
import { writeFileSync, readFileSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const EXPECTED_ROOT = '/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows'
export const BASELINE_COMMIT = '1f7d16ef'
const nativeOperations = {
    git(args) { return execFileSync('git', args, { encoding: 'utf8' }) },
    write: writeFileSync,
    read(path) { return readFileSync(path, 'utf8') },
    remove(path) { rmSync(path) },
}
export function prepareAdapters({ operations = nativeOperations, remove = false } = {}) {
    const root = operations.git(['rev-parse', '--show-toplevel']).trim()
    if (root !== EXPECTED_ROOT) throw new Error(`Baseline adapters require ${EXPECTED_ROOT}`)
    const originalImport = "from '../utils/streamingBuffer'"
    const data = operations.git(['show', `${BASELINE_COMMIT}:frontend/src/stores/data.js`])
    if (data.split(originalImport).length !== 2) throw new Error('Baseline buffer import does not match the reviewed adapter')
    const expected = [
        [resolve(root, 'frontend/src/stores/publicationRateBaselineData.js'),
            data.replace(originalImport, "from '../utils/publicationRateBaselineBuffer'")],
        [resolve(root, 'frontend/src/utils/publicationRateBaselineBuffer.js'),
            operations.git(['show', `${BASELINE_COMMIT}:frontend/src/utils/streamingBuffer.js`])],
    ]
    if (remove) {
        const present = []
        // Validate the whole transaction before removing any known file.
        for (const [path, contents] of expected) {
            let actual
            try { actual = operations.read(path) } catch (error) {
                if (error.code === 'ENOENT') continue
                throw error
            }
            if (actual !== contents) throw new Error(`Refusing removal: unexpected contents at ${path}`)
            present.push(path)
        }
        for (const path of present) operations.remove(path)
    } else {
        const created = []
        try {
            for (const [path, contents] of expected) {
                operations.write(path, contents, { flag: 'wx' })
                created.push(path)
            }
        } catch (error) {
            for (const path of created.reverse()) operations.remove(path)
            throw error
        }
    }
    return expected.map(([path]) => path)
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const remove = process.argv.includes('--remove')
    prepareAdapters({ remove })
    console.log(remove ? 'Publication rate adapters removed' : `Publication rate adapters prepared from ${BASELINE_COMMIT}`)
}
