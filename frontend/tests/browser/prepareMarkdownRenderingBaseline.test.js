import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, relative } from 'node:path'
import { execFileSync } from 'node:child_process'

const modulePath = './prepareMarkdownRenderingBaseline.mjs'
const root = '/home/twidi/dev/twicc-poc/.worktrees/bugfix-stable-streaming-rows'
const sourcePath = 'frontend/src/components/ui/MarkdownContent.vue'
const baseline = execFileSync('git', ['show', `f5d52630:${sourcePath}`], { encoding: 'utf8' })
const current = readFileSync(join(root, sourcePath), 'utf8')
async function fixture(t) {
    const api = await import(modulePath)
    const dir = mkdtempSync(join(tmpdir(), 'markdown-preparation-'))
    t.after(() => rmSync(dir, { recursive: true, force: true }))
    const map = path => join(dir, relative(root, path))
    mkdirSync(join(dir, 'frontend/src/components/ui'), { recursive: true })
    mkdirSync(join(dir, 'frontend/tests/browser'), { recursive: true })
    writeFileSync(join(dir, sourcePath), current)
    const operations = {
        git(args) { assert.deepEqual(args, ['show', `f5d52630:${sourcePath}`]); return baseline },
        read(path) { return readFileSync(map(path), 'utf8') },
        write(path, text, options) { writeFileSync(map(path), text, options) },
        remove(path) { rmSync(map(path)) },
    }
    return { api, dir, map, operations }
}
test('saved current ownership survives changes to production source', async t => {
    const { api, dir, operations } = await fixture(t)
    const manifest = api.prepareMarkdownRenderingBaseline({ root, operations })
    assert.equal(manifest.baselineCommit, 'f5d52630')
    assert.equal(Object.keys(manifest.adapters).length, 2)
    writeFileSync(join(dir, sourcePath), 'changed production source')
    api.removeMarkdownRenderingBaseline({ root, operations })
    for (const path of Object.keys(manifest.adapters)) assert.equal(existsSync(join(dir, path)), false)
    assert.equal(existsSync(join(dir, api.MANIFEST_PATH)), false)
})
test('modified adapter refuses removal before any partial deletion', async t => {
    const { api, dir, operations } = await fixture(t)
    const manifest = api.prepareMarkdownRenderingBaseline({ root, operations })
    const paths = Object.keys(manifest.adapters)
    writeFileSync(join(dir, paths[1]), 'owner modified adapter')
    assert.throws(() => api.removeMarkdownRenderingBaseline({ root, operations }), /Refusing removal/)
    for (const path of paths) assert.equal(existsSync(join(dir, path)), true)
    assert.equal(existsSync(join(dir, api.MANIFEST_PATH)), true)
})
test('pre-existing files refuse preparation and remain untouched', async t => {
    const { api, dir, operations } = await fixture(t)
    writeFileSync(join(dir, api.ADAPTER_PATHS[1]), 'unrelated file')
    assert.throws(() => api.prepareMarkdownRenderingBaseline({ root, operations }), /already exists/)
    assert.equal(readFileSync(join(dir, api.ADAPTER_PATHS[1]), 'utf8'), 'unrelated file')
    assert.equal(existsSync(join(dir, api.ADAPTER_PATHS[0])), false)
})
test('exclusive mid-transaction failure rolls back only created files', async t => {
    const { api, dir, operations } = await fixture(t)
    const write = operations.write
    operations.write = (path, text, options) => {
        if (path.endsWith('MarkdownRenderingCurrent.vue')) {
            write(path, 'racing unrelated file', options)
            const error = new Error('exclusive collision'); error.code = 'EEXIST'; throw error
        }
        write(path, text, options)
    }
    assert.throws(() => api.prepareMarkdownRenderingBaseline({ root, operations }), /collision/)
    assert.equal(existsSync(join(dir, api.ADAPTER_PATHS[0])), false)
    assert.equal(readFileSync(join(dir, api.ADAPTER_PATHS[1]), 'utf8'), 'racing unrelated file')
})
test('unknown ownership and malicious manifest paths cannot delete adapters', async t => {
    const { api, dir, operations } = await fixture(t)
    const manifest = api.prepareMarkdownRenderingBaseline({ root, operations })
    rmSync(join(dir, api.MANIFEST_PATH))
    assert.throws(() => api.removeMarkdownRenderingBaseline({ root, operations }), /ownership/)
    manifest.adapters['../../outside'] = 'a'.repeat(64)
    writeFileSync(join(dir, api.MANIFEST_PATH), JSON.stringify(manifest))
    assert.throws(() => api.removeMarkdownRenderingBaseline({ root, operations }), /manifest/)
    assert.equal(existsSync(join(dir, api.ADAPTER_PATHS[0])), true)
})
test('cleanup failure retains manifest and supports guarded retry', async t => {
    const { api, dir, operations } = await fixture(t)
    api.prepareMarkdownRenderingBaseline({ root, operations })
    const remove = operations.remove
    operations.remove = path => {
        if (path.endsWith('MarkdownRenderingCurrent.vue')) throw new Error('unlink denied')
        remove(path)
    }
    assert.throws(() => api.removeMarkdownRenderingBaseline({ root, operations }), /unlink denied/)
    assert.equal(existsSync(join(dir, api.MANIFEST_PATH)), true)
    operations.remove = remove
    api.removeMarkdownRenderingBaseline({ root, operations })
    assert.equal(existsSync(join(dir, api.MANIFEST_PATH)), false)
})
test('instrumentation preserves original executable statements for both revisions', async t => {
    const { api } = await fixture(t)
    for (const [kind, source] of [['baseline', baseline], ['current', current]]) {
        const adapter = api.instrumentMarkdownComponent(source, kind, 'test-generation')
        assert.equal(api.stripMarkdownInstrumentation(adapter), source)
        assert.throws(() => api.instrumentMarkdownComponent(source + '\n' + source, kind, 'test'), /exactly once/)
    }
})
test('wrong root refuses preparation without filesystem changes', async t => {
    const { api, operations } = await fixture(t)
    assert.throws(() => api.prepareMarkdownRenderingBaseline({ root: '/wrong', operations }), /require/)
})
test('document counters release actual wrapped operations on cancellation and throw', async t => {
    const { api } = await fixture(t)
    const adapter = api.instrumentMarkdownComponent(current, 'current', 'test-generation')
    const body = adapter.slice(adapter.indexOf('async function renderDocument('), adapter.indexOf('\nconst coordinator ='))
    const events = []
    const cancelled = Symbol('cancelled')
    const factory = new Function('markdownFixtureEvent', 'MARKDOWN_RENDER_CANCELLED', 'splitMarkdownBlocks',
        `${body}; return renderDocument`)
    const render = factory((kind, value) => { events.push(kind); return value }, cancelled,
        () => { throw new Error('parser failed') })
    assert.equal(await render({ source: 'obsolete' }, { isCurrent: () => false }), cancelled)
    await assert.rejects(render({ source: 'throw' }, { isCurrent: () => true }), /parser failed/)
    assert.deepEqual(events, ['document-start', 'document-finish', 'document-start', 'parse', 'document-finish'])
    const oldAdapter = api.instrumentMarkdownComponent(baseline, 'baseline', 'test-generation')
    const oldBody = oldAdapter.slice(oldAdapter.indexOf('async function render()'), oldAdapter.indexOf('\n// Re-render on source changes'))
    const baselineEvents = []
    const oldFactory = new Function('markdownFixtureEvent', 'splitMarkdownBlocks', `
        let renderSeq = 0; const rendering = { value: false };
        const props = { source: 'baseline throw', tagSlashCommand: false };
        const mermaidTheme = () => 'default'; const emit = () => {};
        ${oldBody}; return render`)
    const oldRender = oldFactory((kind, value) => { baselineEvents.push(kind); return value },
        () => { throw new Error('baseline parser failed') })
    await assert.rejects(oldRender(), /baseline parser failed/)
    assert.deepEqual(baselineEvents, ['document-start', 'parse', 'emit', 'document-finish'])
})
test('failed preparation cleanup preserves ownership for guarded retry', async t => {
    const { api, dir, operations } = await fixture(t)
    const write = operations.write, remove = operations.remove
    operations.write = (path, text, options) => {
        if (path.endsWith('MarkdownRenderingCurrent.vue')) throw new Error('write denied')
        write(path, text, options)
    }
    operations.remove = () => { throw new Error('unlink denied') }
    assert.throws(() => api.prepareMarkdownRenderingBaseline({ root, operations }), /rollback failed/)
    assert.equal(existsSync(join(dir, api.MANIFEST_PATH)), true)
    operations.remove = remove
    api.removeMarkdownRenderingBaseline({ root, operations })
    assert.equal(existsSync(join(dir, api.ADAPTER_PATHS[0])), false)
})
test('partial recovery record preserves an unowned racing adapter', async t => {
    const { api, dir, operations } = await fixture(t)
    const write = operations.write, remove = operations.remove
    operations.write = (path, text, options) => {
        if (path.endsWith('MarkdownRenderingCurrent.vue')) {
            write(path, 'unowned racing current adapter', options)
            const error = new Error('exclusive collision'); error.code = 'EEXIST'; throw error
        }
        write(path, text, options)
    }
    operations.remove = () => { throw new Error('unlink denied') }
    assert.throws(() => api.prepareMarkdownRenderingBaseline({ root, operations }), /rollback failed/)
    const manifest = JSON.parse(readFileSync(join(dir, api.MANIFEST_PATH), 'utf8'))
    assert.deepEqual(manifest.createdPaths, [api.ADAPTER_PATHS[0]])
    operations.remove = remove
    api.removeMarkdownRenderingBaseline({ root, operations })
    assert.equal(existsSync(join(dir, api.ADAPTER_PATHS[0])), false)
    assert.equal(readFileSync(join(dir, api.ADAPTER_PATHS[1]), 'utf8'), 'unowned racing current adapter')
})
test('baseline validation rejects altered source even with a matching modified digest', async t => {
    const { api, dir, operations } = await fixture(t)
    const { createHash } = await import('node:crypto')
    const manifest = api.prepareMarkdownRenderingBaseline({ root, operations })
    const adapter = readFileSync(join(dir, api.ADAPTER_PATHS[0]), 'utf8') + '\n// modified\n'
    writeFileSync(join(dir, api.ADAPTER_PATHS[0]), adapter)
    manifest.adapters[api.ADAPTER_PATHS[0]] = createHash('sha256').update(adapter).digest('hex')
    writeFileSync(join(dir, api.MANIFEST_PATH), JSON.stringify(manifest))
    assert.throws(() => api.removeMarkdownRenderingBaseline({ root, operations }), /Refusing removal/)
    assert.equal(existsSync(join(dir, api.ADAPTER_PATHS[1])), true)
})
test('both instrumented SFC copies compile with the production Vue compiler', async t => {
    const { api } = await fixture(t)
    const { parse, compileScript, compileTemplate } = await import('@vue/compiler-sfc')
    for (const [kind, source] of [['baseline', baseline], ['current', current]]) {
        const { descriptor, errors } = parse(api.instrumentMarkdownComponent(source, kind, 'test-generation'))
        assert.deepEqual(errors, [])
        const script = compileScript(descriptor, { id: `fixture-${kind}` })
        const template = compileTemplate({ source: descriptor.template.content, filename: `${kind}.vue`, id: `fixture-${kind}`,
            compilerOptions: { bindingMetadata: script.bindings } })
        assert.deepEqual(template.errors, [])
    }
})
test('baseline commit and emit metrics retain the entry tuple across awaited prop changes', async t => {
    const { api } = await fixture(t)
    const adapter = api.instrumentMarkdownComponent(baseline, 'baseline', 'test-generation')
    const body = adapter.slice(adapter.indexOf('async function render()'), adapter.indexOf('\n// Re-render on source changes'))
    const props = { source: 'entry A', tagSlashCommand: false }
    const metrics = []
    let release
    const pending = new Promise(resolve => { release = resolve })
    const factory = new Function('props', 'markdownFixtureEvent', 'renderOneBlock', `
        let renderSeq = 0; const rendering = { value: false }, blocks = { value: [] };
        const mermaidTheme = () => props.theme || 'default';
        const splitMarkdownBlocks = () => ({ blocks: [{ src: 'entry A', hash: 'A' }], env: {} });
        const cacheKeyFor = () => 'key'; const renderCache = new Map(), codeToolsState = new Map();
        const emit = () => {}; ${body}; return render`)
    const render = factory(props, (kind, data) => { metrics.push({ kind, data: typeof data === 'object' ? { ...data } : data }); return data },
        () => pending)
    const operation = render()
    props.source = 'later B'; props.theme = 'dark'; props.tagSlashCommand = true
    release('<p>entry A</p>')
    await operation
    for (const kind of ['commit', 'emit']) {
        assert.deepEqual(metrics.find(event => event.kind === kind).data,
            { source: 'entry A', theme: 'default', slashTag: false })
    }
    assert.equal(api.stripMarkdownInstrumentation(adapter), baseline)
})
