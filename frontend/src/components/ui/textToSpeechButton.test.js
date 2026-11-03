import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import * as Vue from 'vue'
import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as rendering from '../../inline-artifacts/rendering.js'
import * as markdown from '../../utils/markdown.js'

const srcRoot = new URL('../../', import.meta.url)
const read = (path) => readFileSync(new URL(path, srcRoot), 'utf8')

const markdownSource = read('components/ui/MarkdownContent.vue')
const buttonSource = (() => {
    try {
        return read('components/ui/TextToSpeechButton.vue')
    } catch {
        return ''
    }
})()
const compactSummarySource = read('components/session/detail/items/CompactSummary.vue')

const OPT_IN_HOSTS = new Set([
    'components/session/detail/items/TextContent.vue',
    'components/session/detail/items/CompactSummary.vue',
])

test('MarkdownContent declares speakable off by default and renders the button only when speakable', () => {
    assert.match(markdownSource, /speakable:\s*\{\s*type:\s*Boolean,\s*default:\s*false/)
    assert.match(markdownSource, /<TextToSpeechButton[^>]*v-if="speakable/)
    assert.match(markdownSource, /<TextToSpeechButton[^>]*:text="source"/)
    // The button follows the Copy button.
    const copy = markdownSource.indexOf('title="Copy raw markdown"')
    const speech = markdownSource.indexOf('<TextToSpeechButton')
    assert.ok(copy !== -1 && speech > copy, 'the speech button follows the Copy button')
})

test('MarkdownContent never touches the speech store itself', () => {
    assert.doesNotMatch(markdownSource, /useTextToSpeechStore/)
    assert.doesNotMatch(markdownSource, /stores\/textToSpeech/)
})

test('only TextContent and CompactSummary opt in', () => {
    assert.match(compactSummarySource, /<MarkdownContent[^>]*\sspeakable/)
    const others = readdirSync(srcRoot, { recursive: true })
        .filter(path => path.endsWith('.vue') && !OPT_IN_HOSTS.has(path))
        .filter(path => /<MarkdownContent[^>]*\s:?speakable/.test(read(path)))
    assert.deepEqual(others, [])
})

test('chat text enables speech only for assistant messages and keeps user Markdown', async t => {
    const MarkdownContent = {
        props: { source: String, speakable: Boolean },
        setup: props => () => Vue.h('article', { 'data-speakable': props.speakable }, props.source),
    }
    const TextContent = compileComponent(new URL('../session/detail/items/TextContent.vue', import.meta.url), {
        '../../../ui/MarkdownContent.vue': MarkdownContent,
        '../../../../inline-artifacts/rendering.js': rendering,
        '../../../../utils/markdown': markdown,
    })
    const role = Vue.ref('assistant')
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ render: () => Vue.h(TextContent, { text: '**Message**', role: role.value }) })
    app.mount(root)
    t.after(() => app.unmount())
    for (const [value, expected] of [['assistant', true], ['user', false], [null, false], ['assistant', true]]) {
        role.value = value
        await flush()
        const [article] = descendants(root, node => node.type === 'article')
        assert.equal(article.props['data-speakable'], expected, `speech eligibility for ${value}`)
        assert.equal(article.text, '**Message**', 'the message still renders through MarkdownContent')
    }
})

test('a busy speech button stays visible with the toolbar closed', () => {
    assert.match(markdownSource, /:not\(\.markdown-toolbar-speech--busy\)/)
    const hover = markdownSource.indexOf('.markdown-content-wrapper:hover .markdown-toolbar')
    const busy = markdownSource.indexOf('.markdown-content-wrapper .markdown-toolbar:has(.markdown-toolbar-speech--busy)')
    assert.ok(hover !== -1 && busy > hover, 'the busy rule follows the hover rule')
    assert.match(markdownSource, /\.markdown-toolbar wa-button wa-spinner\s*\{[^}]*font-size:\s*\.6rem/s)
})

test('the button is a small filled wa-button shown when available or busy', () => {
    assert.match(buttonSource, /^\s*<wa-button\b/m)
    assert.match(buttonSource, /size="small"/)
    assert.match(buttonSource, /appearance="filled"/)
    assert.match(buttonSource, /v-if="store\.available \|\| status !== 'idle'"/)
    assert.match(buttonSource, /markdown-toolbar-speech--busy/)
})

test('the button maps each state to its look and title', () => {
    assert.match(buttonSource, /<wa-spinner v-if="status === 'loading'">/)
    assert.match(buttonSource, /<wa-icon v-else :name="status === 'playing' \? 'stop' : 'volume-up'">/)
    assert.match(buttonSource, /:variant="status === 'playing' \? 'brand' : 'neutral'"/)
    assert.match(buttonSource, /'Read aloud'/)
    assert.match(buttonSource, /'Cancel the request'/)
    assert.match(buttonSource, /'Stop reading'/)
})

test('a click plays when idle, stops otherwise, and toasts only real errors', () => {
    assert.match(buttonSource, /store\.play\(props\.text\)/)
    assert.match(buttonSource, /store\.stop\(\)/)
    assert.match(buttonSource, /!result\.ok && !result\.aborted/)
    assert.match(buttonSource, /toast\.error\(result\.error, \{ duration: 4000 \}\)/)
})

test('an error after the sound started still toasts, unless aborted', () => {
    // The start succeeded: the end result comes later, through `result.ended`.
    assert.match(buttonSource, /if \(!result\.ok\) return\s+[\s\S]*?result\.ended\.then\(end => \{/)
    assert.match(
        buttonSource,
        /result\.ended\.then\(end => \{\s*if \(!end\.ok && !end\.aborted\) toast\.error\(end\.error, \{ duration: 4000 \}\)/,
    )
})
