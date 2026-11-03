import assert from 'node:assert/strict'
import test from 'node:test'
import { describeMessageSnapshot } from './messageSnapshot.js'

test('snapshot keeps full Markdown and previews only the first text line', () => {
    const text = '**Reply**\n' + 'x'.repeat(10000)
    const vm = describeMessageSnapshot('assistant_message.sent', { text })
    assert.equal(vm.role, 'assistant')
    assert.equal(vm.text, text)
    assert.equal(vm.excerpt, 'Reply…')
    assert.equal(vm.hasMoreLines, true)
    assert.equal(vm.expandable, true)
})

test('the event kind determines the role independently of payload fields', () => {
    assert.deepEqual(describeMessageSnapshot('user_message.sent', { text: 'Hello', role: 'assistant' }), {
        role: 'user', text: 'Hello', excerpt: 'Hello', expandable: true, hasMoreLines: false,
    })
    assert.deepEqual(describeMessageSnapshot('assistant_message.sent', { text: 'Done', role: 'user' }), {
        role: 'assistant', text: 'Done', excerpt: 'Done', expandable: true, hasMoreLines: false,
    })
})

test('the first line removes Markdown syntax without changing saved text', () => {
    const text = '# Title\n\n**Bold** [link](https://example.com) ![image](image.png)\n`code`\n\n```js\n  value()\n```\n'
    const vm = describeMessageSnapshot('user_message.sent', { text })
    assert.equal(vm.text, text)
    assert.equal(vm.excerpt, 'Title…')
    assert.equal(vm.hasMoreLines, true)
})

test('a long single line has no character cutoff; the layout clips it', () => {
    const text = 'x'.repeat(10000)
    const vm = describeMessageSnapshot('user_message.sent', { text })
    assert.equal(vm.excerpt, text)
    assert.equal(vm.hasMoreLines, false)
})

test('unknown kinds and malformed payloads have safe empty fallbacks', () => {
    for (const kind of ['turn.ended', 'constructor', null]) {
        assert.deepEqual(describeMessageSnapshot(kind, { text: 'Not a snapshot' }), {
            role: null, text: '', excerpt: '', expandable: false, hasMoreLines: false,
        })
    }
    for (const data of [null, undefined, [], 'text', {}, { text: 42 }, { text: ' \n\t' }]) {
        const vm = describeMessageSnapshot('assistant_message.sent', data)
        assert.equal(vm.role, 'assistant')
        assert.equal(vm.excerpt, '')
        assert.equal(vm.expandable, false)
        assert.equal(typeof vm.text, 'string')
    }
})
