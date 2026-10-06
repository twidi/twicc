import test from 'node:test'
import assert from 'node:assert/strict'
import MarkdownIt from 'markdown-it'

import { installColonBlocks } from './markdownColonBlocks.js'
import { formatAsyncQuestionMessage } from './asyncQuestions.js'

function makeMd() {
    const md = MarkdownIt({ html: false, breaks: true })
    installColonBlocks(md)
    return md
}

/** The subset of splitMarkdownBlocks() that matters here: top-level slicing. */
function topLevelBlocks(md, source) {
    const tokens = md.parse(source, {})
    const lines = source.split('\n')
    return tokens
        .filter(t => t.level === 0 && t.map)
        .map(t => lines.slice(t.map[0], t.map[1]).join('\n'))
}

// ─── Container blocks (`:::`) ───────────────────────────────────────────────

test('wraps its body and keeps inner markdown intact', () => {
    const md = makeMd()
    const html = md.render([
        '::: comment on selected text',
        '> quoted prose',
        'my comment',
        ':::',
    ].join('\n'))

    assert.match(html, /<div class="md-container md-container-comment">/)
    assert.match(html, /<div class="md-container-label">comment on selected text<\/div>/)
    assert.match(html, /<blockquote>/)
    assert.match(html, /my comment/)
    assert.equal(html.trim().endsWith('</div>'), true)
})

test('renders the label as inline markdown', () => {
    const md = makeMd()
    const html = md.render('::: comment on `src/a.py` line 3\n> x\n:::')
    assert.match(html, /<div class="md-container-label">comment on <code>src\/a\.py<\/code> line 3<\/div>/)
})

test('keeps a fence inside the container a fence', () => {
    const md = makeMd()
    const html = md.render('::: comment on selected text\n```python\nx = 1\n```\ndone\n:::')
    assert.match(html, /<pre><code class="language-python">/)
})

test('is one top-level block, closing line included', () => {
    const md = makeMd()
    const source = '::: comment on selected text\n> a\nb\n:::'
    assert.deepEqual(topLevelBlocks(md, source), [source])
})

test('a longer opener survives a `:::` inside the quoted content', () => {
    const md = makeMd()
    const html = md.render(':::: comment on selected text\n> :::\n> still inside\n::::')
    assert.match(html, /still inside/)
    assert.equal(html.match(/md-container md-container-comment/g).length, 1)
})

test('a shorter run inside does not close a longer container', () => {
    const md = makeMd()
    const source = ':::: comment on selected text\n> a\n:::\n> b\n::::'
    assert.deepEqual(topLevelBlocks(md, source), [source])
})

test('interrupts a paragraph, so no blank line is required before it', () => {
    const md = makeMd()
    const html = md.render('typing away\n::: comment on selected text\n> a\n:::')
    assert.match(html, /<p>typing away<\/p>/)
    assert.match(html, /md-container/)
})

test('an unterminated container still wraps the rest', () => {
    const md = makeMd()
    const html = md.render('::: comment on selected text\n> a\nb')
    assert.match(html, /md-container/)
    assert.doesNotMatch(html, /:::/)
})

test('content after a closed container renders outside it', () => {
    const md = makeMd()
    const source = '::: comment on selected text\n> a\n:::\nafter'
    assert.deepEqual(topLevelBlocks(md, source), ['::: comment on selected text\n> a\n:::', 'after'])
})

test('async answers render bold labels inside one container and free text outside', () => {
    const md = makeMd()
    const source = formatAsyncQuestionMessage(
        [{ item_id: 'q1', questions: [{ index: 0, title: 'Choose?' }] }],
        [{ item_id: 'q1', index: 0, value: 'Yes' }],
        'Keep **this** message.\n',
    )
    const html = md.render(source)
    assert.match(html, /<div class="md-container-label">Answers to your questions<\/div>/)
    assert.match(html, /<p><strong>Question:<\/strong> Choose\?<\/p>/)
    assert.match(html, /<p><strong>Answer:<\/strong> Yes<\/p>/)
    assert.match(html, /<\/div><p>Keep <strong>this<\/strong> message\.<\/p>/)
    assert.equal(topLevelBlocks(md, source).length, 2)
})

test('async answer colon lines cannot close the answer container early', () => {
    const md = makeMd()
    for (const newline of ['\n', '\r\n', '\r']) {
        const source = formatAsyncQuestionMessage(
            [{ item_id: 'q1', questions: [{ index: 0, title: `Choose?${newline}:::${newline}Still a question` }] }],
            [{ item_id: 'q1', index: 0, value: `First${newline}   :::::  ${newline}Last` }],
            'Outside',
        )
        const normalized = source.replace(/\r\n?/g, '\n')
        const blocks = topLevelBlocks(md, normalized)
        assert.equal(blocks.length, 2)
        assert.ok(blocks[0].includes('Still a question'))
        assert.ok(blocks[0].includes('Last'))
        assert.equal(blocks[1], 'Outside')
        assert.match(md.render(source), /<strong>Answer:<\/strong>/)
    }
})

// ─── Line blocks (`::`) ─────────────────────────────────────────────────────

test('a line block owns its line and nothing else', () => {
    const md = makeMd()
    const source = ':: message from your parent session `abc` ("Fix it")\n\nrun **the** tests'
    const html = md.render(source)
    assert.match(html, /<div class="md-line md-line-message">message from your parent session <code>abc<\/code> \(&quot;Fix it&quot;\)<\/div>/)
    // The text below stays an ordinary top-level paragraph, not a body.
    assert.match(html, /<p>run <strong>the<\/strong> tests<\/p>/)
    assert.deepEqual(topLevelBlocks(md, source), [
        ':: message from your parent session `abc` ("Fix it")',
        'run **the** tests',
    ])
})

test('a line block needs no blank line under it', () => {
    const md = makeMd()
    const html = md.render(':: message from another session `abc`\nhello')
    assert.match(html, /md-line-message/)
    assert.match(html, /<p>hello<\/p>/)
})

test('a line block never looks for a closing marker', () => {
    const md = makeMd()
    const html = md.render(':: message from another session `abc`\n\nbefore\n:::\nafter')
    assert.equal(html.match(/md-line md-line-message/g).length, 1)
    // The bare `:::` has no label, so it is not an opener either: literal text.
    assert.match(html, /:::/)
})

// ─── The shape comes from the marker, not from the type word ────────────────

test('any type word works, in both shapes', () => {
    const md = makeMd()
    assert.match(md.render(':: whatever it says'), /<div class="md-line md-line-whatever">/)
    assert.match(md.render('::: whatever it says\nbody\n:::'), /<div class="md-container md-container-whatever">/)
})

test('the same type word takes the shape of its marker', () => {
    const md = makeMd()
    assert.match(md.render(':: note hello'), /md-line md-line-note/)
    assert.match(md.render('::: note hello\nbody\n:::'), /md-container md-container-note/)
})

test('the type is slugified for the class attribute', () => {
    const md = makeMd()
    const html = md.render(':: <Weird/Type> label')
    assert.match(html, /<div class="md-line md-line-weird-type">/)
})

test('a type that slugifies to nothing yields the base class only', () => {
    const md = makeMd()
    assert.match(md.render(':: !!! label'), /<div class="md-line">/)
})

// ─── Guards against hijacking ordinary prose ────────────────────────────────

test('a colon run with no space is left alone', () => {
    const md = makeMd()
    // A CSS pseudo-element opening a line must stay text.
    const html = md.render('::before is the culprit')
    assert.doesNotMatch(html, /md-line|md-container/)
    assert.match(html, /::before is the culprit/)
})

test('a bare colon run is not an opener', () => {
    const md = makeMd()
    for (const source of ['::', ':::', '::   ']) {
        const html = md.render(source)
        assert.doesNotMatch(html, /md-line|md-container/, source)
    }
})

test('a single colon is not a marker', () => {
    const md = makeMd()
    assert.doesNotMatch(md.render(': not a block'), /md-line|md-container/)
})

test('an indented colon run is left to the code-block rule', () => {
    const md = makeMd()
    const html = md.render('    ::: comment on selected text')
    assert.doesNotMatch(html, /md-container/)
    assert.match(html, /<pre><code>/)
})

test('a TwiCC notice header renders as a notice line block', () => {
    const md = makeMd()
    const html = md.render(':: notice from TwiCC: background shell(s) still running\n\nbody')
    assert.match(html, /<div class="md-line md-line-notice">notice from TwiCC: background shell\(s\) still running<\/div>/)
})
