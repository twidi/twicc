import test from 'node:test'
import assert from 'node:assert/strict'
import { buildSessionLinkMarkdown, escapeLinkLabel, padInsertion } from './sessionLink.js'
import { parseMarkdownTokens } from './markdown.js'

const router = {
    resolve(location) {
        assert.equal(location.name, 'session')
        return { href: `/project/${location.params.projectId}/session/${location.params.sessionId}` }
    },
}

test('link labels escape the characters that change inline rendering', () => {
    assert.equal(escapeLinkLabel('Fix [bug] *now* `x` <b> ~a~ & c\\d'),
        'Fix \\[bug\\] \\*now\\* \\`x\\` \\<b\\> \\~a\\~ \\& c\\\\d')
    assert.equal(escapeLinkLabel('snake_case and _lead trail_'), 'snake_case and \\_lead trail\\_')
    assert.equal(escapeLinkLabel('one\r\ntwo\nthree'), 'one two three')
})

test('the link uses the canonical session route and the displayed title', () => {
    assert.equal(buildSessionLinkMarkdown({ id: 's1', project_id: '-home-p', title: 'T' }, router),
        '[T](/project/-home-p/session/s1)')
    assert.equal(buildSessionLinkMarkdown({ id: 's2', project_id: 'p', title: '' }, router),
        '[s2](/project/p/session/s2)')
})

test('the rendered link text is exactly the title', () => {
    for (const title of ['Fix [bug] *now*', 'a_b and _c_ snake_case', 'x `y` <z> ~w~ & \\']) {
        const markdown = buildSessionLinkMarkdown({ id: 's', project_id: 'p', title }, router)
        const inline = parseMarkdownTokens(markdown).find(token => token.type === 'inline')
        assert.deepEqual(inline.children.map(token => token.type), ['link_open', 'text', 'link_close'])
        assert.equal(inline.children[0].attrGet('href'), '/project/p/session/s')
        assert.equal(inline.children[1].content, title)
    }
})

test('insertion padding adds a space only where the link would touch text', () => {
    assert.equal(padInsertion('', 'L', ''), 'L')
    assert.equal(padInsertion('a', 'L', 'b'), ' L ')
    assert.equal(padInsertion('a ', 'L', '\nb'), 'L')
    assert.equal(padInsertion('a\n', 'L', ''), 'L')
})
