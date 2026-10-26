import assert from 'node:assert/strict'
import test from 'node:test'

import { describeAuthor } from './authorLabel.js'

const sessions = {
    boss: { title: 'The boss', project_id: 'p', provider: 'claude_code', hidden: false, archived: false },
    secret: { title: 'Secret', project_id: 'p', provider: 'codex', hidden: true, archived: false },
}
const label = (fields) => describeAuthor(
    { kind: 'archived.started', precision: 'exact', actor: 'user', via: 'ui', actor_ref: null, actor_name: null, ...fields },
    { sessions },
)
const plainText = (author) => author.parts.map((part) => (part.type === 'text' ? part.text : `[${part.mention.title}]`)).join('')

test('the table of section 6.3, row by row', () => {
    const cases = [
        [{ actor: 'user', via: 'ui' }, null],
        [{ actor: 'user', via: null }, null],
        [{ actor: 'user', via: 'cli' }, 'by you (CLI)'],
        [{ actor: 'user', via: 'mcp_internal', actor_ref: 'tok_1' }, 'by you (MCP)'],
        [{ actor: 'user', via: 'rpc' }, 'by you (API)'],
        [{ actor: 'agent', via: 'cli', actor_ref: 'boss' }, 'by [The boss] (CLI)'],
        [{ actor: 'agent', via: 'mcp_internal', actor_ref: 'boss' }, 'by [The boss] (MCP)'],
        [{ actor: 'external', via: 'mcp_external', actor_name: 'ChatGPT' }, 'by ChatGPT (external MCP)'],
        [{ actor: 'external', via: 'mcp_external', actor_name: null }, 'by an external MCP client'],
        // No text: the row shows an "automatic" tag (describeEvent.test.js).
        [{ actor: 'system', via: null }, '<automatic>'],
        [{ actor: 'agent', via: null }, null],
    ]
    for (const [fields, expected] of cases) {
        const author = label(fields)
        const shown = author.kind === 'none' ? null : author.kind === 'automatic' ? '<automatic>' : plainText(author)
        assert.equal(shown, expected, JSON.stringify(fields))
        assert.equal(author.unknown, false, JSON.stringify(fields))
    }
})

test('an agent label names a session; the kind says so', () => {
    assert.equal(label({ actor: 'agent', via: 'cli', actor_ref: 'boss' }).kind, 'session')
    assert.equal(label({ actor: 'user', via: 'cli' }).kind, 'text')
})

test('the session of an agent label follows the link rules: missing and hidden sessions are plain text', () => {
    const mentionOf = (ref) => label({ actor: 'agent', via: 'cli', actor_ref: ref }).parts[1].mention
    assert.equal(mentionOf('boss').link, true)
    assert.equal(mentionOf('secret').link, false)
    assert.equal(mentionOf('unknown-session-id').link, false)
    assert.equal(mentionOf('unknown-session-id').title, 'unknown-')
})

test('an agent label without a session reference still reads', () => {
    assert.equal(plainText(label({ actor: 'agent', via: 'mcp_internal', actor_ref: null })), 'by an agent (MCP)')
})

test('"automatic" is not written on backend rows nor on session.created', () => {
    assert.equal(label({ actor: 'system', via: null, kind: 'backend.started' }).kind, 'none')
    assert.equal(label({ actor: 'system', via: null, kind: 'backend.ended' }).kind, 'none')
    assert.equal(label({ actor: 'system', via: null, kind: 'session.created' }).kind, 'none')
    assert.deepEqual(label({ actor: 'system', via: null, kind: 'process.ended' }), { kind: 'automatic', parts: [], unknown: false })
})

test('no label on a row whose precision is not exact, for every actor', () => {
    for (const precision of ['estimated', 'derived']) {
        for (const [actor, via] of [['system', null], ['user', 'cli'], ['agent', 'cli'], ['external', 'mcp_external']]) {
            const author = label({ actor, via, precision, actor_ref: 'boss', actor_name: 'x' })
            assert.equal(author.kind, 'none', `${actor}/${via}/${precision}`)
            assert.equal(author.unknown, false)
        }
    }
})

test('an unknown precision counts as exact', () => {
    assert.equal(label({ actor: 'user', via: 'cli', precision: 'approximate' }).kind, 'text')
})

test('any other actor/via combination has no label and is flagged for the tooltip', () => {
    for (const fields of [
        { actor: 'user', via: 'mcp_external' }, { actor: 'agent', via: 'rpc' }, { actor: 'system', via: 'cli' },
        { actor: 'external', via: 'cli' }, { actor: 'alien', via: null }, { actor: 'user', via: 'toString' },
    ]) {
        const author = label(fields)
        assert.deepEqual([author.kind, author.unknown], ['none', true], JSON.stringify(fields))
    }
})

test('an external client name is cut near 80 characters', () => {
    const author = label({ actor: 'external', via: 'mcp_external', actor_name: 'N'.repeat(200) })
    // The cut name counts 80 characters, ellipsis included.
    assert.ok(plainText(author).includes(`${'N'.repeat(79)}…`))
    assert.ok(!plainText(author).includes('N'.repeat(80)))
})
