// Optional fixture copies. Production modules and Vite configuration remain unchanged.
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs'
import { createHash, randomUUID } from 'node:crypto'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const EXPECTED_ROOT = '/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows'
export const BASELINE_COMMIT = 'f5d52630'
export const SOURCE_PATH = 'frontend/src/components/ui/MarkdownContent.vue'
export const ADAPTER_PATHS = [
    'frontend/src/components/ui/MarkdownRenderingBaseline.vue',
    'frontend/src/components/ui/MarkdownRenderingCurrent.vue',
]
export const MANIFEST_PATH = 'frontend/tests/browser/.markdown-rendering-generation.json'
const nativeOperations = {
    git(args) { return execFileSync('git', args, { cwd: EXPECTED_ROOT, encoding: 'utf8' }) },
    read(path) { return readFileSync(path, 'utf8') },
    write: writeFileSync,
    remove: unlinkSync,
}
const digest = text => createHash('sha256').update(text).digest('hex')
const marked = line => `${line} // markdown-fixture-instrumentation\n`
function once(source, anchor, replacement) {
    if (source.split(anchor).length !== 2) throw new Error(`Insertion anchor must match exactly once: ${anchor}`)
    return source.replace(anchor, replacement)
}
export function stripMarkdownInstrumentation(source) {
    return source.replace(/^.*\/\/ markdown-fixture-instrumentation\n/gm, '')
}
export function instrumentMarkdownComponent(original, kind, generationId) {
    if (!['baseline', 'current'].includes(kind)) throw new Error('Unknown adapter kind')
    let source = original
    source = once(source, '<script setup>\n', '<script setup>\n' + marked(`// markdown-fixture-generation: ${generationId}`)
        + marked("const markdownFixtureSink = inject('markdownRenderingMetrics', null)")
        + marked('function markdownFixtureEvent(kind, data) { try { return markdownFixtureSink?.(kind, data) } catch { return undefined } }'))
    const start = kind === 'current' ? 'async function renderDocument(input, { isCurrent }) {\n' : 'async function render() {\n'
    const tuple = kind === 'current' ? 'input' : '{ source: props.source, theme: mermaidTheme(), slashTag: props.tagSlashCommand }'
    source = once(source, start, start
        + marked(`    const markdownFixtureInput = { ...${tuple} }`)
        + marked("    const markdownFixtureOperation = markdownFixtureEvent('document-start', markdownFixtureInput)")
        + marked('    try {'))
    const end = kind === 'current' ? '\n}\n\nconst coordinator =' : '\n}\n\n// Re-render on source changes'
    source = once(source, end, '\n' + marked("    } finally { markdownFixtureEvent('document-finish', markdownFixtureOperation) }") + end.slice(1))
    const split = kind === 'current'
        ? '    const { blocks: raw, env } = splitMarkdownBlocks(input.source)'
        : '        const { blocks: raw, env } = splitMarkdownBlocks(props.source)'
    source = once(source, split, marked(`    markdownFixtureEvent('parse', ${kind === 'current' ? 'input.source' : 'props.source'})`) + split)
    const block = kind === 'current'
        ? '    const renderedHtml = await renderBlockToHtml(src, slashTag ? { ...env, tagLeadingSlashCommand: true } : env)'
        : '    tmp.innerHTML = await renderBlockToHtml(src, slashTag ? { ...env, tagLeadingSlashCommand: true } : env)'
    source = once(source, block, marked("    markdownFixtureEvent('block', { source: src, theme, slashTag })") + block)
    const nested = kind === 'current'
        ? '    const renderedHtml = await renderBlockToHtml(source, {})'
        : '    tmp.innerHTML = await renderBlockToHtml(source, {})'
    source = once(source, nested, marked("    markdownFixtureEvent('block', { source, theme, nested: true })") + nested)
    const mermaid = '            const { svg } = await mermaid.render(id, source)'
    source = once(source, mermaid, marked("            markdownFixtureEvent('mermaid', { source, theme })") + mermaid)
    const commit = kind === 'current' ? '        blocks.value = result.blocks' : '        blocks.value = result'
    source = once(source, commit, marked(`        markdownFixtureEvent('commit', ${kind === 'current' ? 'input' : 'markdownFixtureInput'})`) + commit)
    const emit = kind === 'current' ? "        if (isCurrent()) emit('rendered')" : "            emit('rendered')"
    source = once(source, emit, marked(`${kind === 'current' ? '        if (isCurrent())' : '            '} markdownFixtureEvent('emit', ${kind === 'current' ? 'input' : 'markdownFixtureInput'})`) + emit)
    if (stripMarkdownInstrumentation(source) !== original) throw new Error('Instrumentation changes original statements')
    return source
}
function validateRoot(root) {
    if (root !== EXPECTED_ROOT) throw new Error(`Markdown adapters require ${EXPECTED_ROOT}`)
}
function optionalRead(operations, path) {
    try { return operations.read(path) } catch (error) {
        if (error.code === 'ENOENT') return null
        throw error
    }
}
export function prepareMarkdownRenderingBaseline({ root = EXPECTED_ROOT, operations = nativeOperations } = {}) {
    validateRoot(root)
    for (const relativePath of [...ADAPTER_PATHS, MANIFEST_PATH]) {
        if (optionalRead(operations, resolve(root, relativePath)) !== null) throw new Error(`File already exists: ${relativePath}`)
    }
    const generationId = randomUUID()
    const baseline = operations.git(['show', `${BASELINE_COMMIT}:${SOURCE_PATH}`])
    const current = operations.read(resolve(root, SOURCE_PATH))
    const contents = [baseline, current].map((source, index) => instrumentMarkdownComponent(source,
        index === 0 ? 'baseline' : 'current', generationId))
    const manifest = { version: 1, generationId, baselineCommit: BASELINE_COMMIT, createdPaths: [...ADAPTER_PATHS],
        adapters: Object.fromEntries(ADAPTER_PATHS.map((path, index) => [path, digest(contents[index])])) }
    const created = []
    try {
        for (const [index, path] of ADAPTER_PATHS.entries()) {
            operations.write(resolve(root, path), contents[index], { flag: 'wx' })
            created.push(path)
        }
        operations.write(resolve(root, MANIFEST_PATH), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' })
    } catch (error) {
        const failures = []
        for (const path of created.reverse()) {
            try { operations.remove(resolve(root, path)) } catch (failure) { failures.push(failure) }
        }
        if (failures.length) {
            // Save ownership exclusively. Never overwrite a racing manifest.
            try { operations.write(resolve(root, MANIFEST_PATH), JSON.stringify({ ...manifest, createdPaths: [...created] }, null, 2) + '\n', { flag: 'wx' }) }
            catch (failure) { failures.push(failure) }
            throw new AggregateError([error, ...failures], 'Preparation rollback failed; inspect saved ownership and adapters')
        }
        throw error
    }
    return manifest
}
function validateManifest(manifest) {
    const keys = Object.keys(manifest || {}).sort().join(',')
    if (keys !== 'adapters,baselineCommit,createdPaths,generationId,version' || manifest.version !== 1
        || manifest.baselineCommit !== BASELINE_COMMIT || typeof manifest.generationId !== 'string'
        || !/^[0-9a-f-]{36}$/.test(manifest.generationId)
        || !Array.isArray(manifest.createdPaths) || manifest.createdPaths.length === 0
        || new Set(manifest.createdPaths).size !== manifest.createdPaths.length
        || manifest.createdPaths.some(path => !ADAPTER_PATHS.includes(path))
        || !manifest.adapters || typeof manifest.adapters !== 'object' || Array.isArray(manifest.adapters)
        || Object.keys(manifest.adapters).sort().join(',') !== [...ADAPTER_PATHS].sort().join(',')
        || Object.values(manifest.adapters).some(value => typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value))) {
        throw new Error('Refusing removal: invalid generation manifest')
    }
}
export function removeMarkdownRenderingBaseline({ root = EXPECTED_ROOT, operations = nativeOperations } = {}) {
    validateRoot(root)
    const record = optionalRead(operations, resolve(root, MANIFEST_PATH))
    if (record === null) throw new Error('Refusing removal: missing ownership manifest')
    let manifest
    try { manifest = JSON.parse(record) } catch { throw new Error('Refusing removal: invalid generation manifest') }
    validateManifest(manifest)
    const baseline = instrumentMarkdownComponent(operations.git(['show', `${BASELINE_COMMIT}:${SOURCE_PATH}`]), 'baseline', manifest.generationId)
    const present = []
    for (const path of manifest.createdPaths) {
        const contents = optionalRead(operations, resolve(root, path))
        if (contents === null) continue
        if (digest(contents) !== manifest.adapters[path]
            || !contents.includes(marked(`// markdown-fixture-generation: ${manifest.generationId}`))
            || (path === ADAPTER_PATHS[0] && contents !== baseline)) {
            throw new Error(`Refusing removal: modified or unowned adapter ${path}`)
        }
        present.push(path)
    }
    for (const path of present) operations.remove(resolve(root, path))
    operations.remove(resolve(root, MANIFEST_PATH))
    return present
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim()
    const remove = process.argv.includes('--remove')
    if (remove) removeMarkdownRenderingBaseline({ root })
    else prepareMarkdownRenderingBaseline({ root })
    console.log(remove ? 'Owned Markdown adapters removed' : `Markdown adapters prepared from ${BASELINE_COMMIT}`)
}
