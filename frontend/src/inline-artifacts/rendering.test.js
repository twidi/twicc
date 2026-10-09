import test from 'node:test'
import { readFileSync } from 'node:fs'
import { computed, unref } from 'vue'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { splitMarkdownBlocks } from '../utils/markdown.js'
import { parseInlineArtifactBlocks, publicationKey } from './publications.js'
import { artifactKey } from './context.js'
import * as canonical from '../providers/codex/canonical.js'
import { splitProposedPlan } from '../providers/codex/proposedPlan.js'
import { createInlineArtifactRuntime } from './runtime.js'
import { useFramePoolStore } from '../stores/framePool.js'
const rendering = await import('./rendering.js').catch(() => ({}))
const tag = '<twicc:inline-artifact id="a" src="inline-artifacts/a/index.html" />'
const cp = value => Array.from(value).length
const data = entries => ({ type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', content: entries } } })
const context = (text, index = 0) => rendering.createInlineTextContext({ sessionId: 's', lineNum: 42, finalized: true, publicationAllowed: true }, [{ textBlockIndex: index, text }])

test('typed splitting uses original recognized spans and keeps ordinary raw HTML escaped', () => {
    const source = '😀\r\n\r\n' + tag + '\r\n\r\nafter'
    const spans = parseInlineArtifactBlocks(source).map(span => ({ ...span, textBlockIndex: 3, tag_offset: span.start }))
    const blocks = splitMarkdownBlocks(source, { inlineArtifacts: true, sourceOffset: 0, recognizedSpans: spans }).blocks
    assert.equal(blocks[1].type, 'inline-artifact')
    assert.equal(blocks[1].span.tag_offset, 5)
    assert.equal(blocks[1].span.textBlockIndex, 3)
    assert.equal(splitMarkdownBlocks(source).blocks.every(block => block.type !== 'inline-artifact'), true)
    assert.equal(splitMarkdownBlocks(source, { inlineArtifacts: true, recognizedSpans: [] }).blocks.every(block => block.type !== 'inline-artifact'), true)
})

test('Codex text blocks retain canonical content indexes before joining', () => {
    assert.equal(typeof canonical.assistantTextBlocks, 'function')
    const entries = [{ type: 'Text', text: '😀\r\n\r\n' }, { type: 'Image', url: 'image' }, { type: 'Text', text: tag }]
    assert.deepEqual(canonical.assistantTextBlocks(data(entries)), [{ textBlockIndex: 0, text: '😀\r\n\r\n' }, { textBlockIndex: 2, text: tag }])
    const c = context('first')
    const joined = rendering.createInlineTextContext(c, canonical.assistantTextBlocks(data(entries)))
    assert.equal(joined.recognizedSpans[0].start, 5)
    assert.equal(joined.recognizedSpans[0].textBlockIndex, 2)
    assert.equal(joined.recognizedSpans[0].tag_offset, 0)
    assert.equal(canonical.agentMessageText(data(entries)), '😀\r\n\r\n' + tag)
})

test('tags split across canonical blocks never become widgets or errors', () => {
    assert.equal(typeof rendering.createInlineTextContext, 'function')
    const c = rendering.createInlineTextContext({ sessionId: 's', lineNum: 42, finalized: true, publicationAllowed: true }, [
        { textBlockIndex: 0, text: tag.slice(0, 23) }, { textBlockIndex: 1, text: tag.slice(23) },
    ])
    assert.deepEqual(c.recognizedSpans, [])
    assert.equal(splitMarkdownBlocks(tag, { inlineArtifacts: true, recognizedSpans: c.recognizedSpans }).blocks[0].type === 'inline-artifact', false)
})

test('trim keeps Unicode offsets and command transformations clear inline context', () => {
    assert.equal(typeof rendering.displayInlineText, 'function')
    const source = ' \r\n😀\r\n\r\n' + tag + '\r\n  '
    const c = context(source, 2)
    const display = rendering.displayInlineText(source, c)
    assert.equal(display.source, source.trim())
    assert.equal(display.inlineContext.sourceOffset, 3)
    const typed = splitMarkdownBlocks(display.source, { inlineArtifacts: true, ...display.inlineContext }).blocks.find(block => block.type === 'inline-artifact')
    assert.equal(typed.span.tag_offset, cp(source.slice(0, source.indexOf(tag))))
    const command = '<command-name>/go</command-name>\n<command-args>hello</command-args>'
    assert.deepEqual(rendering.displayInlineText(command, context(command)), { source: '/go hello', inlineContext: null })
})

test('normalized screenshot Markdown preserves the publication identity after it', () => {
    assert.equal(typeof rendering.createInlineTextContext, 'function')
    const text = '![Screenshot](/artifacts/s/screenshot.png)\n\n' + tag
    assert.equal(context(text).recognizedSpans[0].tag_offset, cp(text.slice(0, text.indexOf(tag))))
})

test('proposed-plan segments report original code-point starts', () => {
    const source = '😀 before\r\n\r\n<proposed_plan>\r\n\r\n' + tag + '\r\n\r\n</proposed_plan>\r\n\r\nafter'
    const segments = splitProposedPlan(source)
    assert.equal(segments.beforeOffset, 0)
    assert.equal(segments.planOffset, cp(source.slice(0, source.indexOf(tag))))
    assert.equal(segments.afterOffset, cp(source.slice(0, source.indexOf('after'))))
    const c = context(source)
    const planContext = rendering.segmentInlineTextContext(c, segments.planOffset)
    const blocks = splitMarkdownBlocks(segments.plan, { inlineArtifacts: true, ...planContext }).blocks
    assert.equal(blocks[0].span.tag_offset, segments.planOffset)
})

for (const wrap of [x => '```text\n' + x + '\n```', x => '<!--\n' + x + '\n-->', x => '::: note\n' + x + '\n:::']) {
    test('example proposed-plan wrappers stay ordinary Markdown', () => {
        const source = wrap('<proposed_plan>\n\n' + tag + '\n\n</proposed_plan>')
        assert.equal(splitProposedPlan(source), null)
        assert.equal(parseInlineArtifactBlocks(source).length, 0)
    })
}

test('unloaded latest publication suppresses the loaded older widget', () => {
    assert.equal(typeof rendering.inlineArtifactPlacement, 'function')
    setActivePinia(createPinia())
    const pool = useFramePoolStore()
    const runtime = createInlineArtifactRuntime({ viewId: 'v', pool, adapter: { probe: async () => ({ available: true }), documentUrl: () => '/doc', brokerConfig: () => ({}), dispose() {} } })
    const pub = { artifact_id: 'a', line_num: 87, text_block_index: 0, tag_offset: 0, src: 'inline-artifacts/a/index.html', title: 'a', height: 360 }
    runtime.reconcile({ revision: 1, descriptors: [{ sourceSessionId: 's', artifactId: 'a', publication: pub, publicationKey: publicationKey('s', pub), status: 'ready', title: 'a', height: 360 }] })
    const older = context(tag)
    const placement = rendering.inlineArtifactPlacement(older, older.recognizedSpans[0], runtime)
    assert.equal(placement.status, 'superseded')
    // The replaced notice links to the latest placement.
    assert.equal(placement.latestLineNum, 87)
    assert.equal(placement.latestPublicationKey, publicationKey('s', pub))
    assert.equal(Object.keys(pool.frames).length, 0)
    const latest = { ...older, lineNum: 87 }
    assert.equal(rendering.inlineArtifactPlacement(latest, latest.recognizedSpans[0], runtime).status, 'ready')
    assert.equal(runtime.entries.has(artifactKey('s', 'a')), true)
    runtime.dispose()
})

test('invalid newer tags cannot supersede valid placements; disabled placements show explicit state', () => {
    assert.equal(typeof rendering.inlineArtifactPlacement, 'function')
    const c = context(tag)
    const entry = { present: true, descriptor: { publicationKey: '["s",42,0,0]', status: 'not_included' } }
    const runtime = { entries: new Map([[artifactKey('s', 'a'), entry]]) }
    assert.equal(rendering.inlineArtifactPlacement(c, c.recognizedSpans[0], runtime).status, 'not_included')
    const invalid = context('<twicc:inline-artifact id="a" />')
    assert.equal(rendering.inlineArtifactPlacement(invalid, invalid.recognizedSpans[0], runtime).status, 'invalid')
    assert.equal(rendering.inlineArtifactPlacement(c, c.recognizedSpans[0], runtime).status, 'not_included')
})

test('ineligible user/tool/reasoning/native/synthetic text never receives recognized spans', () => {
    assert.equal(typeof rendering.createInlineTextContext, 'function')
    for (const c of [null, { publicationAllowed: false }, { publicationAllowed: true, finalized: false }]) {
        assert.equal(rendering.createInlineTextContext(c, [{ textBlockIndex: 0, text: tag }]), null)
    }
})

test('joined canonical blocks preserve adjacent prose and each independently eligible tag', () => {
    const textBlocks = [{ textBlockIndex: 0, text: 'intro' }, { textBlockIndex: 1, text: tag },
        { textBlockIndex: 2, text: tag }, { textBlockIndex: 3, text: 'after' }]
    const c = rendering.createInlineTextContext({ sessionId: 's', lineNum: 42, finalized: true, publicationAllowed: true }, textBlocks)
    const source = textBlocks.map(block => block.text).join('')
    const blocks = splitMarkdownBlocks(source, { inlineArtifacts: true, ...c }).blocks
    assert.deepEqual(blocks.map(block => block.type || 'markdown'), ['markdown', 'inline-artifact', 'inline-artifact', 'markdown'])
    assert.equal(blocks[0].src, 'intro')
    assert.equal(blocks[3].src, 'after')
})

test('example comment markers inside code do not suppress a later real plan', () => {
    const source = '```text\n<!--\n```\n\n<proposed_plan>\nplan\n</proposed_plan>'
    assert.equal(splitProposedPlan(source)?.plan, 'plan')
})

test('multiline native inline-code plan wrappers remain ordinary examples', () => {
    assert.equal(splitProposedPlan('`example\n<proposed_plan>\nplan\n</proposed_plan>\nend`'), null)
})


function setupExcerpt(path, start, end, dependencies) {
    const source = readFileSync(new URL(path, import.meta.url), 'utf8')
    const left = source.indexOf(start), right = source.indexOf(end, left)
    assert.ok(left >= 0 && right > left)
    return new Function(...Object.keys(dependencies), source.slice(left, right) + '\nreturn inlineContext;')(...Object.values(dependencies))
}

test('actual SessionItem context gates native, user, tool, reasoning and synthetic rows', () => {
    const props = { sessionId: 's', parentSessionId: null, kind: 'assistant_message', lineNum: 42, content: {}, syntheticKind: null }
    let sessionType = 'session', sourceSessionId = 's'
    const read = () => setupExcerpt('../components/session/detail/SessionItem.vue', 'const providedInlineContext =', '// Whether this item', {
        props, computed, unref, INLINE_ARTIFACT_CONTEXT: Symbol(),
        inject: () => ({ sourceSessionId, runtime: {} }), dataStore: { getSession: () => ({ type: sessionType }) },
    }).value
    assert.equal(read().lineNum, 42)
    for (const kind of ['user_message', 'tool_use', 'reasoning', 'content_items']) { props.kind = kind; assert.equal(read(), null) }
    props.kind = 'assistant_message'
    sessionType = 'subagent'; assert.equal(read(), null)
    sessionType = 'session'; props.parentSessionId = 'parent'; assert.equal(read(), null)
    props.parentSessionId = null; props.syntheticKind = 'streaming_block'; assert.equal(read(), null)
    props.syntheticKind = null; props.content.syntheticKind = 'streaming_block'; assert.equal(read(), null)
    props.content = {}; props.lineNum = -1; assert.equal(read(), null)
    props.lineNum = 42; sourceSessionId = 'other'; assert.equal(read(), null)
})

test('actual Claude ContentList preserves content-array indexes across text blocks', () => {
    const source = readFileSync(new URL('../components/session/detail/items/claude_code/ContentList.vue', import.meta.url), 'utf8')
    const start = source.indexOf('const textContexts ='), end = source.indexOf('const emit =', start)
    const props = { role: 'assistant', inlineContext: { sessionId: 's', lineNum: 42, finalized: true, publicationAllowed: true },
        items: [{ type: 'thinking', thinking: tag }, { type: 'text', text: '😀\r\n\r\n' + tag }, { type: 'tool_use' }, { type: 'text', text: tag }] }
    const contexts = new Function('props', 'computed', 'createInlineTextContext', source.slice(start, end) + '\nreturn textContexts.value;')(
        props, computed, rendering.createInlineTextContext)
    assert.equal(contexts[0], null)
    assert.equal(contexts[1].recognizedSpans[0].textBlockIndex, 1)
    assert.equal(contexts[1].recognizedSpans[0].tag_offset, 5)
    assert.equal(contexts[2], null)
    assert.equal(contexts[3].recognizedSpans[0].textBlockIndex, 3)
    assert.equal(contexts[3].recognizedSpans[0].tag_offset, 0)
})

test('actual Claude Message excludes meta, rewritten, error and synthetic assistant text', () => {
    const source = readFileSync(new URL('../components/session/detail/items/claude_code/Message.vue', import.meta.url), 'utf8')
    const start = source.indexOf('const publicationContext ='), end = source.indexOf('const dataStore =', start)
    const props = { role: 'assistant', parentSessionId: null, data: { type: 'assistant' }, inlineContext: { lineNum: 42 } }
    const read = () => new Function('props', 'computed', source.slice(start, end) + '\nreturn publicationContext.value;')(props, computed)
    assert.equal(read(), props.inlineContext)
    for (const field of ['isMeta', 'isApiErrorMessage', 'twiccOriginalContent', 'syntheticKind']) {
        props.data[field] = true; assert.equal(read(), null); delete props.data[field]
    }
    props.parentSessionId = 'parent'; assert.equal(read(), null)
    props.parentSessionId = null; props.role = 'user'; assert.equal(read(), null)
})

test('eligible artifact in a proposed plan uses full-source grammar and examples stay literal', () => {
    const source = '<proposed_plan>\n\n' + tag + '\n\n```text\n' + tag + '\n```\n\n</proposed_plan>'
    const segments = splitProposedPlan(source), c = context(source)
    assert.equal(c.recognizedSpans.length, 1)
    const blocks = splitMarkdownBlocks(segments.plan, { inlineArtifacts: true, ...rendering.segmentInlineTextContext(c, segments.planOffset) }).blocks
    assert.equal(blocks.filter(block => block.type === 'inline-artifact').length, 1)
    assert.ok(blocks.some(block => block.src?.includes('```text')))
})

test('valid missing document keeps new placement and reports a runtime error', async () => {
    setActivePinia(createPinia())
    const pool = useFramePoolStore()
    const runtime = createInlineArtifactRuntime({ viewId: 'missing', pool,
        adapter: { probe: async () => ({ available: false, error: 'document_unavailable' }), documentUrl: () => '/doc', brokerConfig: () => ({}), dispose() {} } })
    const c = { ...context(tag), lineNum: 87 }, span = c.recognizedSpans[0]
    const pub = { ...span.descriptor, line_num: 87, text_block_index: 0, tag_offset: 0 }
    const key = artifactKey('s', 'a'), pkey = publicationKey('s', pub)
    runtime.reconcile({ revision: 1, descriptors: [{ sourceSessionId: 's', artifactId: 'a', publication: pub, publicationKey: pkey, status: 'ready', title: 'a', height: 360 }] })
    runtime.attach(key, pkey, { placeholderEl: {}, isSuppressed: () => false, focusConversation() {} })
    runtime.setVisible(key, true)
    await Promise.resolve(); await Promise.resolve()
    assert.equal(rendering.inlineArtifactPlacement(c, span, runtime).status, 'ready')
    assert.equal(runtime.entries.get(key).loadState, 'error')
    assert.equal(runtime.entries.get(key).error, 'document_unavailable')
    assert.equal(Object.keys(pool.frames).length, 0)
    assert.equal(rendering.inlineArtifactPlacement(context(tag), span, runtime).status, 'superseded')
    runtime.dispose()
})

test('catalog removal hides a retained entry without an obsolete loading placeholder', () => {
    setActivePinia(createPinia())
    const pool = useFramePoolStore()
    const runtime = createInlineArtifactRuntime({ viewId: 'removed', pool,
        adapter: { probe: async () => ({ available: true }), documentUrl: () => '/doc', brokerConfig: () => ({}), dispose() {} } })
    const c = context(tag), span = c.recognizedSpans[0]
    const pub = { ...span.descriptor, line_num: 42, text_block_index: 0, tag_offset: 0 }
    runtime.reconcile({ revision: 1, descriptors: [{ sourceSessionId: 's', artifactId: 'a', publication: pub,
        publicationKey: publicationKey('s', pub), status: 'ready', title: 'a', height: 360 }] })
    assert.equal(rendering.inlineArtifactPlacement(c, span, runtime).status, 'ready')
    runtime.reconcile({ revision: 2, descriptors: [] })
    assert.equal(rendering.inlineArtifactPlacement(c, span, runtime).status, 'absent')
    assert.equal(runtime.entries.has(artifactKey('s', 'a')), true)
    runtime.dispose()
})

test('ordinary CRLF Markdown keeps existing exact block source and cache identities', () => {
    assert.deepEqual(splitMarkdownBlocks('first\r\n\r\nsecond').blocks.map(block => block.src), ['first\r', 'second'])
})

for (const invalid of [false, true]) {
    test(`eligible ${invalid ? 'invalid' : 'valid'} artifact title cannot become a plan delimiter`, () => {
        const title = 'Example\n<proposed_plan>\nTitle'
        const source = `<twicc:inline-artifact id="a" ${invalid ? '' : 'src="inline-artifacts/a/index.html" '}title="${title}" />`
        const c = context(source)
        assert.equal(c.recognizedSpans.length, 1)
        assert.equal(splitProposedPlan(source, c), null)
        const blocks = splitMarkdownBlocks(source, { inlineArtifacts: true, ...c }).blocks
        assert.equal(blocks.length, 1)
        assert.equal(blocks[0].type, 'inline-artifact')
        if (invalid) assert.equal(blocks[0].span.error, 'missing_src')
        else assert.equal(blocks[0].span.descriptor.title, title)
    })
}

test('an eligible artifact before a real plan remains a complete typed block', () => {
    const title = '😀 Example\r\n<proposed_plan>\r\n<!-- literal comment\r\nTitle'
    const artifact = `<twicc:inline-artifact id="a" src="inline-artifacts/a/index.html" title="${title}" />`
    const source = artifact + '\r\n\r\n<proposed_plan>\r\nreal plan\r\n</proposed_plan>'
    const c = context(source)
    const plan = splitProposedPlan(source, c)
    assert.equal(plan.before, artifact)
    assert.equal(plan.plan, 'real plan')
    assert.equal(plan.planOffset, cp(source.slice(0, source.indexOf('real plan'))))
    const blocks = splitMarkdownBlocks(plan.before, { inlineArtifacts: true, ...rendering.segmentInlineTextContext(c, plan.beforeOffset) }).blocks
    assert.equal(blocks.length, 1)
    assert.equal(blocks[0].type, 'inline-artifact')
    assert.equal(blocks[0].span.descriptor.title, title)
})

test('eligible plan body keeps an artifact title containing a closing delimiter', () => {
    const title = 'Example\n</proposed_plan>\nTitle'
    const artifact = `<twicc:inline-artifact id="a" src="inline-artifacts/a/index.html" title="${title}" />`
    const source = '<proposed_plan>\n\n' + artifact + '\n\nreal plan\n\n</proposed_plan>\n\nafter'
    const c = context(source), plan = splitProposedPlan(source, c)
    assert.equal(plan.plan, artifact + '\n\nreal plan')
    assert.equal(plan.after, 'after')
    const blocks = splitMarkdownBlocks(plan.plan, { inlineArtifacts: true, ...rendering.segmentInlineTextContext(c, plan.planOffset) }).blocks
    assert.equal(blocks.filter(block => block.type === 'inline-artifact').length, 1)
})

test('contextless and native proposed-plan calls keep their existing ordinary behavior', () => {
    const source = '<twicc:inline-artifact id="a" src="inline-artifacts/a/index.html" title="Example\n<proposed_plan>\nTitle" />'
    assert.equal(splitProposedPlan(source)?.plan, 'Title" />')
    assert.equal(splitProposedPlan(source, { recognizedSpans: [] })?.plan, 'Title" />')
})

test('actual AssistantMessage passes original eligible spans before proposed-plan segmentation', () => {
    const source = readFileSync(new URL('../components/session/detail/items/codex/AssistantMessage.vue', import.meta.url), 'utf8')
    const start = source.indexOf('const segments ='), end = source.indexOf('const dataStore =', start)
    const text = '<twicc:inline-artifact id="a" src="inline-artifacts/a/index.html" title="Example\n<proposed_plan>\nTitle" />'
    const props = { text, inlineContext: context(text) }
    const read = () => new Function('props', 'computed', 'splitProposedPlan', source.slice(start, end) + '\nreturn segments.value;')(props, computed, splitProposedPlan)
    assert.equal(read(), null)
    props.inlineContext = null
    assert.equal(read().plan, 'Title" />')
})

test('protected plan spans convert original code-point offsets after a source segment starts', () => {
    const prefix = '😀 before\r\n\r\n'
    const title = '😀'.repeat(20) + '\r\n<proposed_plan>\r\nTitle'
    const artifact = `<twicc:inline-artifact id="a" src="inline-artifacts/a/index.html" title="${title}" />`
    const c = context(prefix + artifact)
    const segmentContext = rendering.segmentInlineTextContext(c, cp(prefix))
    assert.equal(splitProposedPlan(artifact, segmentContext), null)
    const blocks = splitMarkdownBlocks(artifact, { inlineArtifacts: true, ...segmentContext }).blocks
    assert.equal(blocks.length, 1)
    assert.equal(blocks[0].span.descriptor.title, title)
})


test('missing runtime or descriptor never claims that an artifact has a later replacement', () => {
    const c = context(tag), span = c.recognizedSpans[0]
    for (const runtime of [null, { entries: new Map() }, { entries: new Map([[artifactKey('s', 'a'),
        { present: false, descriptor: { publicationKey: '["s",87,0,0]', status: 'ready' } }]]) }]) {
        assert.equal(rendering.inlineArtifactPlacement(c, span, runtime).status, 'absent')
    }
})


test('revealing a publication centers its own placeholder only', async () => {
    const { revealInlinePublication } = await import('./context.js')
    const calls = []
    const block = key => ({ dataset: { inlinePublication: key }, scrollIntoView: options => calls.push([key, options]) })
    const scroller = { querySelectorAll: () => [block('["s",3,0,0]'), block('["s",87,0,0]')] }
    assert.equal(revealInlinePublication(scroller, '["s",87,0,0]'), true)
    assert.deepEqual(calls, [['["s",87,0,0]', { block: 'center', behavior: 'instant' }]])
    assert.equal(revealInlinePublication(scroller, '["s",99,0,0]'), false)
    assert.equal(revealInlinePublication(null, '["s",87,0,0]'), false)
})
