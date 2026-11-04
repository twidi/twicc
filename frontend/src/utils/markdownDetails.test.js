import test from 'node:test'
import assert from 'node:assert/strict'
import MarkdownIt from 'markdown-it'

import { installDetailsBlocks } from './markdownDetails.js'

function makeMd() {
    const md = MarkdownIt({ html: false, breaks: true })
    installDetailsBlocks(md)
    return md
}

test('renders summary and markdown body', () => {
    const html = makeMd().render([
        '<details>',
        '<summary>The **title**</summary>',
        '',
        '- one',
        '- two',
        '',
        '</details>',
    ].join('\n'))

    assert.match(html, /<wa-details class="md-details"><span slot="summary">The <strong>title<\/strong><\/span>/)
    assert.match(html, /<li>one<\/li>/)
    assert.ok(html.trim().endsWith('</wa-details>'))
})

test('summary on the opener line and the open attribute', () => {
    const html = makeMd().render('<details open><summary>Title</summary>\n\nbody\n\n</details>')
    assert.match(html, /<wa-details class="md-details" open><span slot="summary">Title<\/span>/)
    assert.match(html, /<p>body<\/p>/)
})

test('defaults the summary when absent', () => {
    const html = makeMd().render('<details>\n\nbody\n\n</details>')
    assert.match(html, /<span slot="summary">Details<\/span>/)
})

test('keeps a fenced block and nested details intact', () => {
    const html = makeMd().render([
        '<details>',
        '<summary>Outer</summary>',
        '',
        '```html',
        '</details>',
        '```',
        '',
        '<details>',
        '<summary>Inner</summary>',
        'text',
        '</details>',
        '',
        '</details>',
    ].join('\n'))

    assert.equal(html.match(/<wa-details /g).length, 2)
    assert.equal(html.match(/<\/wa-details>/g).length, 2)
    assert.match(html, /&lt;\/details&gt;/)
})

test('closes an unterminated block at the end of the scope', () => {
    const html = makeMd().render('<details>\n<summary>Open</summary>\n\nstreaming…')
    assert.match(html, /streaming…<\/p>\s*<\/wa-details>/)
})

test('is one top-level block for block splitting', () => {
    const md = makeMd()
    const source = 'intro\n\n<details>\n<summary>T</summary>\n\na\n\nb\n\n</details>\n\noutro'
    const lines = source.split('\n')
    const blocks = md.parse(source, {})
        .filter(t => t.level === 0 && t.map)
        .map(t => lines.slice(t.map[0], t.map[1]).join('\n'))
    assert.equal(blocks.length, 3)
    assert.equal(blocks[1], '<details>\n<summary>T</summary>\n\na\n\nb\n\n</details>')
})

test('leaves non-matching HTML escaped', () => {
    const md = makeMd()
    assert.match(md.render('<details>trailing</details>'), /&lt;details&gt;/)
    assert.match(md.render('<div>x</div>'), /&lt;div&gt;/)
    assert.match(md.render('    <details>\n    code'), /<pre>/)
})
