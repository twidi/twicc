import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { ref, reactive, computed, watch, nextTick, effectScope, onScopeDispose } from 'vue'
import { createMarkdownRenderCoordinator, MARKDOWN_RENDER_CANCELLED } from '../../utils/markdownRenderCoordinator.js'
import { markdownReferenceContextKey, markdownBlockCacheKey } from '../../utils/markdownRenderCache.js'

const source = readFileSync(process.env.TWICC_MARKDOWN_TEST_SOURCE ?? new URL('./MarkdownContent.vue', import.meta.url), 'utf8')
const start = source.indexOf('const blocks = ref([])')
const end = source.indexOf('const showRaw = ref(false)', start)
assert.ok(start >= 0 && end > start, 'MarkdownContent setup extraction boundaries must exist')
const extracted = source.slice(start, end)
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); await nextTick() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }

function harness(t, options = {}) {
    const props = reactive({ source: '', tagSlashCommand: false, showToc: false })
    const settingsStore = reactive({ _effectiveColorScheme: 'light' })
    const eligible = ref(true)
    const calls = [], errors = [], emitted = [], reports = []
    const scope = effectScope()
    const document = options.document ?? { createElement: () => ({ innerHTML: '', querySelectorAll: () => [] }) }
    const dependencies = {
        ref, computed, watch, nextTick, onScopeDispose, props, settingsStore, document,
        inject: () => options.rowContext ?? null, STREAMING_ROW_CONTEXT: Symbol(),
        getCurrentInstance: () => ({ proxy: 'component-proxy', appContext: { config: { errorHandler: options.errorHandler ?? ((...args) => reports.push(args)) } } }),
        console: { error: (...args) => reports.push(args) },
        useMarkdownRenderEligibility: () => ({ eligible }),
        createMarkdownRenderCoordinator, MARKDOWN_RENDER_CANCELLED,
        markdownReferenceContextKey, markdownBlockCacheKey,
        extractHeadings: () => [], extractBlockquoteSources: () => [], hashString: x => x,
        fileLinks: null, rewriteContentMediaUrl: null,
        toast: { error: (...args) => errors.push(args) }, emit: (...args) => { emitted.push(args); options.onEmit?.(props) },
        splitMarkdownBlocks: options.split ?? (text => ({ blocks: text ? text.split('|').map(src => ({ src, hash: src })) : [], env: {} })),
        renderBlockToHtml: async (text, env) => { calls.push(text); return options.render ? options.render(text, env) : `<p>${text}</p>` },
        getMermaid: options.getMermaid ?? (() => { throw Error('unexpected Mermaid') }),
        applyMermaidTheme: text => text,
    }
    const setup = new Function(...Object.keys(dependencies), `${extracted}\nreturn { blocks, renderCache, coordinator, container, renderOneBlock, renderNestedMarkdown, renderMermaidIn, toolOwnership, applyCodeRendered, handleCodeToolsAction, restoreCodeToolsState, codeToolsState };`)
    const component = scope.run(() => setup(...Object.values(dependencies)))
    t.after(() => scope.stop())
    return { ...component, props, eligible, calls, errors, emitted, settingsStore, scope, reports }
}

test('superseded highlighter stops before another block and retains complete output', async t => {
    const held = deferred()
    const h = harness(t, { render: text => text === 'held' ? held.promise : `<p>${text}</p>` })
    h.props.source = 'old'; await flush()
    h.props.source = 'held|obsolete'; await flush()
    h.props.source = 'latest|final'; await flush()
    assert.deepEqual(h.blocks.value, [{ key: 'old:0', html: '<p>old</p>' }])
    held.resolve('<p>held</p>'); await flush()
    assert.deepEqual(h.calls, ['old', 'held', 'latest', 'final'])
    assert.deepEqual(h.blocks.value.map(x => x.html), ['<p>latest</p>', '<p>final</p>'])
    assert.equal([...h.renderCache.values()].includes('<p>held</p>'), false)
    assert.equal(h.emitted.length, 2) // old and latest; initial empty coalesces
})

test('operation staging reuses repeated blocks and complete cache entries', async t => {
    const h = harness(t)
    h.props.source = 'same|same'; await flush()
    assert.deepEqual(h.calls, ['same'])
    assert.deepEqual(h.blocks.value.map(x => x.key), ['same:0', 'same:1'])
    h.props.source = 'same|next'; await flush()
    assert.deepEqual(h.calls, ['same', 'next'])
    h.props.source = ''; await flush()
    assert.deepEqual(h.blocks.value, [])
    assert.equal(h.renderCache.size, 0)
})

test('reference definitions and slash changes cannot reuse old HTML', async t => {
    const h = harness(t, {
        split: text => ({ blocks: [{ src: '/go [x][a]', hash: 'constant' }], env: { references: { A: { href: text, title: text } } } }),
        render: (text, env) => `${env.references.A.href}:${env.references.A.title}:${Boolean(env.tagLeadingSlashCommand)}`,
    })
    h.props.source = '/first'; await flush()
    assert.equal(h.blocks.value[0].html, '/first:/first:false')
    h.props.source = '/second'; await flush()
    assert.equal(h.blocks.value[0].html, '/second:/second:false')
    h.props.tagSlashCommand = true; await flush()
    assert.equal(h.blocks.value[0].html, '/second:/second:true')
})

test('hidden work consumes rejection and resumes only latest input', async t => {
    const held = deferred()
    const h = harness(t, { render: text => text === 'held' ? held.promise : text })
    h.props.source = 'old'; await flush()
    h.props.source = 'held|never'; await flush()
    h.eligible.value = false
    held.reject(Error('obsolete')); await flush()
    assert.equal(h.errors.length, 0)
    assert.equal(h.reports.length, 0)
    assert.equal(h.blocks.value[0].html, 'old')
    h.props.source = 'new'; h.eligible.value = true; await flush()
    assert.equal(h.blocks.value[0].html, 'new')
})

test('scope disposal consumes library rejection without commit or error', async t => {
    const held = deferred()
    const h = harness(t, { render: () => held.promise })
    h.props.source = 'held'; await flush()
    h.scope.stop(); held.reject(Error('disposed')); await flush()
    assert.equal(h.errors.length, 0)
    assert.equal(h.reports.length, 0)
    assert.equal(h.renderCache.size, 0)
})

test('Mermaid loading and diagram awaits stop obsolete diagrams', async t => {
    const loading = deferred(), diagram = deferred()
    const h = harness(t, { getMermaid: () => loading.promise })
    await flush()
    let current = true, renders = 0, replacements = 0
    const code = { textContent: 'a-->b', closest: () => ({ replaceWith: () => replacements++, classList: { add() {} } }) }
    const root = { querySelectorAll: () => [code, code] }
    const pending = h.renderMermaidIn(root, 'default', () => current)
    current = false; loading.resolve({ render: () => { renders++; return diagram.promise } })
    assert.equal(await pending, MARKDOWN_RENDER_CANCELLED)
    assert.equal(renders, 0)
    current = true
    const next = h.renderMermaidIn(root, 'default', () => current)
    await flush(); current = false; diagram.resolve({ svg: '<svg/>' })
    assert.equal(await next, MARKDOWN_RENDER_CANCELLED)
    assert.equal(renders, 1); assert.equal(replacements, 0)
})

function wrapperFixture() {
    const attributes = [], appended = []
    const pre = { textContent: 'nested' }
    const button = { setAttribute: (...args) => attributes.push(args), querySelector: () => null }
    const wrapper = {
        dataset: { codeKey: 'nested' }, isConnected: true,
        querySelector: selector => selector === ':scope > pre' ? pre : selector.includes('data-block-action="view"') ? button : null,
        classList: { contains: () => false, toggle: (...args) => attributes.push(args) },
        append: x => appended.push(x),
    }
    return { wrapper, attributes, appended }
}

for (const invalidation of ['source', 'hide-show', 'dispose']) {
    test(`nested render retains invalid ownership after ${invalidation}`, async t => {
        const held = deferred()
        const h = harness(t, { render: text => text === 'nested' ? held.promise : text })
        await flush()
        const fixture = wrapperFixture()
        h.container.value = { contains: () => true }
        const pending = h.handleCodeToolsAction({ closest: () => fixture.wrapper, dataset: { blockAction: 'view' } })
        await flush()
        if (invalidation === 'source') h.props.source = 'replacement'
        else if (invalidation === 'hide-show') { h.eligible.value = false; h.eligible.value = true }
        else h.scope.stop()
        held.resolve('<p>nested</p>'); await pending; await flush()
        assert.deepEqual(fixture.appended, [])
        assert.deepEqual(fixture.attributes, [])
        assert.equal(h.codeToolsState.size, 0)
        assert.equal(h.errors.length, 0)
    })
}

test('invalid nested rejection cannot toast or delete remembered state', async t => {
    const held = deferred()
    const h = harness(t, { render: text => text === 'nested' ? held.promise : text })
    await flush()
    const { wrapper } = wrapperFixture()
    h.container.value = { contains: () => true, querySelectorAll: () => [wrapper] }
    h.codeToolsState.set('nested', { rendered: true })
    const pending = h.restoreCodeToolsState(() => true)
    await flush(); h.eligible.value = false; held.reject(Error('obsolete'))
    await pending
    assert.deepEqual(h.codeToolsState.get('nested'), { rendered: true })
    assert.equal(h.errors.length, 0)
})


test('current failure retains old blocks and emits no success', async t => {
    const h = harness(t, { render: text => { if (text === 'bad') throw Error('current'); return text } })
    h.props.source = 'old'; await flush()
    h.props.source = 'bad'; await flush()
    assert.equal(h.reports.length, 1)
    assert.equal(h.blocks.value[0].html, 'old')
    assert.equal(h.emitted.length, 1)
})

test('initial eligible reveal renders canonical source', async t => {
    const h = harness(t)
    h.eligible.value = false; h.props.source = 'canonical'; await flush()
    assert.deepEqual(h.calls, [])
    h.eligible.value = true; await flush()
    assert.equal(h.blocks.value[0].html, '<p>canonical</p>')
    assert.equal(h.emitted.length, 1)
})

test('failed Mermaid output stays retryable and never enters completed cache', async t => {
    let attempts = 0
    const document = { createElement: () => ({
        innerHTML: '',
        querySelectorAll(selector) {
            if (selector !== 'code.language-mermaid') return []
            return [{ textContent: 'invalid', closest: () => ({ classList: { add() {} } }) }]
        },
    }) }
    const h = harness(t, { document, getMermaid: async () => ({ render: async () => { attempts++; throw Error('syntax') } }) })
    h.props.source = '```mermaid invalid'; await flush()
    assert.equal(h.blocks.value[0].html, '<p>```mermaid invalid</p>')
    assert.equal(h.renderCache.size, 0)
    h.eligible.value = false; h.eligible.value = true
    // A completed fallback does not rerender on visibility alone. Theme changes retry.
    h.settingsStore._effectiveColorScheme = 'dark'; await flush()
    assert.equal(attempts, 2)
    assert.equal(h.renderCache.size, 0)
})

test('queued nextTick restoration cannot apply after source changes', async t => {
    const held = deferred()
    const h = harness(t, {
        onEmit: props => { if (props.source === 'one') props.source = 'two' },
        render: text => text === 'two' ? held.promise : text,
    })
    await flush()
    let wraps = 0
    const wrapper = { dataset: { codeKey: 'saved' }, querySelector: () => { wraps++; return null } }
    h.container.value = { contains: () => true, querySelectorAll: () => [wrapper] }
    h.codeToolsState.set('saved', { wrap: true })
    h.props.source = 'one'; await flush()
    assert.equal(h.blocks.value[0].html, 'one')
    assert.equal(wraps, 0)
    h.scope.stop(); held.resolve('two'); await flush()
    assert.equal(wraps, 0)
})


test('current failure reaches configured application handler with exact exception', async t => {
    const error = new Error('render failed', { cause: new Error('library cause') })
    const received = []
    const h = harness(t, { errorHandler: (...args) => received.push(args), render: () => { throw error } })
    h.props.source = 'bad'; await flush()
    assert.equal(received.length, 1)
    assert.equal(received[0][0], error)
    assert.equal(received[0][1], 'component-proxy')
    assert.equal(h.errors.length, 0)
})

test('current failure reaches console with exact exception when application handler is absent', async t => {
    const error = new Error('console failure')
    const h = harness(t, { errorHandler: false, render: () => { throw error } })
    h.props.source = 'bad'; await flush()
    assert.equal(h.reports.length, 1)
    assert.equal(h.reports[0][0], error)
    assert.equal(h.errors.length, 0)
})

test('throwing application error handler cannot strand the document slot', async t => {
    const errors = []
    const error = new Error('render failure')
    const h = harness(t, {
        errorHandler: caught => { errors.push(caught); throw Error('sink failure') },
        render: text => { if (text === 'bad') throw error; return text },
    })
    h.props.source = 'bad'; await flush()
    h.props.source = 'recovered'; await flush()
    assert.deepEqual(errors, [error])
    assert.equal(h.blocks.value[0].html, 'recovered')
    assert.equal(h.emitted.length, 1)
})


test('superseded rejection never reaches application handler', async t => {
    const held = deferred()
    const h = harness(t, { render: text => text === 'old' ? held.promise : text })
    h.props.source = 'old'; await flush()
    h.props.source = 'new'; held.reject(new Error('obsolete')); await flush()
    assert.deepEqual(h.reports, [])
    assert.equal(h.blocks.value[0].html, 'new')
})

for (const pipeline of ['renderOneBlock', 'renderNestedMarkdown']) {
    for (const invalidation of ['supersession', 'hide', 'disposal']) {
        test(`${pipeline} avoids detached HTML allocation after ${invalidation}`, async t => {
            const held = deferred()
            let allocations = 0, assignments = 0
            const document = { createElement: () => {
                allocations++
                return { set innerHTML(value) { assignments++ }, querySelectorAll: () => [] }
            } }
            const h = harness(t, { document, render: () => held.promise })
            await flush()
            h.container.value = { contains: () => true }
            const owned = h.toolOwnership({})
            assert.equal(owned(), true)
            const pending = pipeline === 'renderOneBlock'
                ? h.renderOneBlock('held', {}, 'default', false, '', new Map(), owned)
                : h.renderNestedMarkdown('held', 'default', owned)
            if (invalidation === 'supersession') h.settingsStore._effectiveColorScheme = 'dark'
            if (invalidation === 'hide') h.eligible.value = false
            if (invalidation === 'disposal') h.scope.stop()
            held.resolve('<p>obsolete</p>')
            assert.equal(await pending, MARKDOWN_RENDER_CANCELLED)
            // Observe this continuation before the coordinator can drain replacement work.
            assert.equal(allocations, 0)
            assert.equal(assignments, 0)
        })
    }
}


test('a row reserves its height until its first Markdown publication', async t => {
    const held = deferred(), reservations = []
    let reserved = 0
    const h = harness(t, { rowContext: { reserveInitialHeight() {
        reserved++; reservations.push(true)
        let released = false
        return () => { if (!released) { released = true; reserved-- } }
    } }, render: () => held.promise })
    h.eligible.value = false; h.props.source = 'pending'; await flush()
    assert.equal(reserved, 1, 'offscreen Markdown must retain a real row height')
    h.eligible.value = true; await flush()
    held.resolve('<p>ready</p>'); await flush()
    assert.equal(reserved, 0)
    h.props.source = 'updated'; await flush()
    assert.equal(reservations.length, 1, 'updates retain the existing DOM and need no new reservation')
    h.scope.stop(); assert.equal(reserved, 0)
})
test('disposed unpublished Markdown releases its height reservation', async t => {
    let reserved = 0
    const h = harness(t, { rowContext: { reserveInitialHeight() { reserved++; return () => reserved-- } } })
    h.eligible.value = false; h.props.source = 'pending'; await flush()
    assert.equal(reserved, 1)
    h.scope.stop(); assert.equal(reserved, 0)
})
