import { test } from 'node:test'
import assert from 'node:assert/strict'
import { extractThinkingTitle } from './thinkingTitle.js'

test('a markdown heading on the first line is the title', () => {
    assert.equal(extractThinkingTitle('# Planning the fix\n\nBody'), 'Planning the fix')
    assert.equal(extractThinkingTitle('### Level three'), 'Level three')
    assert.equal(extractThinkingTitle('###### Level six\nBody'), 'Level six')
})

test('closing hashes of a heading are dropped, inner ones kept', () => {
    assert.equal(extractThinkingTitle('## Title ##\nBody'), 'Title')
    assert.equal(extractThinkingTitle('# Learning C#'), 'Learning C#')
})

test('a first line made of one bold span is the title', () => {
    assert.equal(extractThinkingTitle('**Inspecting the store**\n\nBody'), 'Inspecting the store')
    assert.equal(extractThinkingTitle('**Trailing spaces**   \r\nBody'), 'Trailing spaces')
})

test('leading blank lines are skipped', () => {
    assert.equal(extractThinkingTitle('\n\n  \n**Title**\nBody'), 'Title')
    assert.equal(extractThinkingTitle('\r\n\t# Title'), 'Title')
})

test('a first line that is neither gives no title', () => {
    assert.equal(extractThinkingTitle('Plain text first\n# Heading later'), null)
    assert.equal(extractThinkingTitle('**Bold** then text'), null)
    assert.equal(extractThinkingTitle('**One** and **two**'), null)
    assert.equal(extractThinkingTitle('#No space is not a heading'), null)
    assert.equal(extractThinkingTitle('####### Seven hashes'), null)
})

test('empty or blank input gives no title', () => {
    assert.equal(extractThinkingTitle(''), null)
    assert.equal(extractThinkingTitle(undefined), null)
    assert.equal(extractThinkingTitle(' \n\t\n'), null)
    assert.equal(extractThinkingTitle('#\nBody'), null)
    assert.equal(extractThinkingTitle('## \nBody'), null)
    assert.equal(extractThinkingTitle('****\nBody'), null)
    assert.equal(extractThinkingTitle('** **\nBody'), null)
})

test('streaming: a heading grows, an unclosed bold span waits', () => {
    assert.equal(extractThinkingTitle('# Plan'), 'Plan')
    assert.equal(extractThinkingTitle('# Planning'), 'Planning')
    assert.equal(extractThinkingTitle('**Inspec'), null)
    assert.equal(extractThinkingTitle('**Inspecting*'), null)
    assert.equal(extractThinkingTitle('**Inspecting**'), 'Inspecting')
})

test('only the first line is read', () => {
    const body = '\n' + 'x'.repeat(100_000)
    assert.equal(extractThinkingTitle('**Title**' + body), 'Title')
    assert.equal(extractThinkingTitle('Plain' + body + '\n# Not a title'), null)
})
