// Create/remove temporary baseline adapters inside the explicitly selected worktree.
import { execFileSync } from 'node:child_process'
import { writeFileSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'
const expected = '/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows'
const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim()
if (root !== expected) throw new Error(`Baseline adapters require ${expected}`)
const dataPath = resolve(root, 'frontend/src/stores/invisibleStreamingBaselineData.js')
const bufferPath = resolve(root, 'frontend/src/utils/invisibleStreamingBaselineBuffer.js')
if (process.argv.includes('--remove')) {
    rmSync(dataPath, { force: true }); rmSync(bufferPath, { force: true })
} else {
    const data = execFileSync('git', ['show', 'aeaf1838:frontend/src/stores/data.js'], { encoding: 'utf8' })
    const originalImport = "from '../utils/streamingBuffer'"
    if (!data.includes(originalImport)) throw new Error('Baseline buffer import does not match the reviewed adapter')
    writeFileSync(dataPath, data.replace(originalImport, "from '../utils/invisibleStreamingBaselineBuffer'"), { flag: 'wx' })
    writeFileSync(bufferPath, execFileSync('git', ['show', 'aeaf1838:frontend/src/utils/streamingBuffer.js'], { encoding: 'utf8' }), { flag: 'wx' })
}
console.log(process.argv.includes('--remove') ? 'Baseline adapters removed' : 'Baseline adapters prepared from aeaf1838')
