import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import * as Vue from 'vue'
import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import { messageSummaryModule } from '../../../tests/helpers/messageSummaryHarness.js'
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
    const copy = markdownSource.indexOf(':title="$t(\'markdown.tools.copyRaw\')"')
    assert.match(markdownSource, /<wa-button\b[^>]*:title="\$t\('markdown\.tools\.copyRaw'\)"[^>]*@click="copySource"[^>]*>\s*<wa-icon name="copy"/s)
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
        '../../../../composables/useMessageSummary.js': messageSummaryModule(),
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
    assert.match(buttonSource, /idle: 'tts\.controls\.readAloud'/)
    assert.match(buttonSource, /loading: 'tts\.controls\.cancelRequest'/)
    assert.match(buttonSource, /playing: 'tts\.controls\.stopReading'/)
    assert.match(buttonSource, /computed\(\(\) => t\(TITLES\[status\.value\]\)\)/)
})

test('a click plays when idle, stops otherwise, and toasts only real errors', () => {
    assert.match(buttonSource, /store\.play\(props\.text\)/)
    assert.match(buttonSource, /store\.stop\(\)/)
    assert.match(buttonSource, /!result\.ok && !result\.aborted/)
    assert.match(buttonSource, /toast\.error\(result\.uiMessage \|\| result\.error, \{ duration: 4000 \}\)/)
})

test('an error after the sound started still toasts, unless aborted', () => {
    // The start succeeded: the end result comes later, through `result.ended`.
    assert.match(buttonSource, /if \(!result\.ok\) return\s+[\s\S]*?result\.ended\.then\(end => \{/)
    assert.match(
        buttonSource,
        /result\.ended\.then\(end => \{\s*if \(!end\.ok && !end\.aborted\) toast\.error\(end\.uiMessage \|\| end\.error, \{ duration: 4000 \}\)/,
    )
})

for (const status of ['loading', 'playing']) {
    test(`the message ${status} stop calls public tts.stop synchronously and preserves its focused button`, async t => {
        const calls = []
        const tts = Vue.reactive({ available: true, status, statusForText: text => {
            assert.equal(text, 'Original message')
            return tts.status
        }, stop: (...args) => { calls.push(['stop', ...args]); tts.status = 'idle' }, play: () => assert.fail('a busy button cannot start speech') })
        const Button = compileComponent(new URL('./TextToSpeechButton.vue', import.meta.url), {
            '../../stores/textToSpeech': { useTextToSpeechStore: () => tts },
            '../../composables/useToast': { toast: { error: () => assert.fail('a stop cannot toast') } },
        })
        let focused = null
        const { renderer, root } = makeRenderer(40, element => { element.focus = () => { focused = element } })
        const app = renderer.createApp({ render: () => Vue.h(Button, { text: 'Original message' }) })
        app.mount(root)
        t.after(() => app.unmount())
        const [button] = descendants(root, node => node.type === 'wa-button')
        button.focus()
        const pending = button.props.onClick()
        assert.deepEqual(calls, [['stop']], 'no owner argument: the player handles automatic ownership')
        await pending
        await flush()
        assert.equal(button.props['aria-label'], 'Read aloud')
        assert.ok(descendants(root, node => node.type === 'wa-button')[0] === button)
        assert.ok(focused === button)
    })
}

test('summary button retains stop access after switching to original and reports changed saved results', async t => {
    const identity = { sourceKey: 'message:x', sourceHash: 'hash', variant: 'summary' }
    const selected = Vue.ref(true)
    const calls = []
    const tts = Vue.reactive({
        available: true,
        statusForText: () => 'idle',
        summaryStatus: 'idle',
        statusForSummary: value => { assert.deepEqual(value, identity); return tts.summaryStatus },
        playSummary: async (ref, options) => {
            calls.push(['play', ref, options])
            return { ok: false, summaryChanged: true, savedId: 7, error: 'Changed' }
        },
        stop: () => { calls.push(['stop']); tts.summaryStatus = 'idle' },
        play: () => assert.fail('Summary selection must not read the original'),
    })
    const Button = compileComponent(new URL('./TextToSpeechButton.vue', import.meta.url), {
        '../../stores/textToSpeech': { useTextToSpeechStore: () => tts },
        '../../composables/useToast': { toast: { error: () => {} } },
    })
    const reference = { saved_id: 7, result_hash: 'a'.repeat(64) }
    const changes = []
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp({ render: () => Vue.h(Button, {
        text: 'Original', summaryIdentity: identity, summaryRef: selected.value ? reference : null,
        onSummaryChanged: value => changes.push(value),
    }) })
    app.mount(root)
    t.after(() => app.unmount())
    const [button] = descendants(root, node => node.type === 'wa-button')
    await button.props.onClick()
    assert.equal(calls[0][0], 'play')
    assert.deepEqual(changes, [7])
    tts.summaryStatus = 'playing'
    selected.value = false
    await flush()
    assert.equal(button.props['aria-label'], 'Stop reading')
    await button.props.onClick()
    assert.deepEqual(calls.at(-1), ['stop'])
})
