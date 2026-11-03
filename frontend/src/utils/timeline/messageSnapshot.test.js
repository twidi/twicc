import assert from 'node:assert/strict'
import test from 'node:test'
import { describeMessageSnapshot } from './messageSnapshot.js'

test('snapshot keeps full Markdown and bounds the collapsed excerpt', () => {
    const text = '**Reply**\n' + 'x'.repeat(10000)
    const vm = describeMessageSnapshot('assistant_message.sent', { text })
    assert.equal(vm.role, 'assistant')
    assert.equal(vm.text, text)
    assert.equal(vm.excerpt, 'Reply ' + 'x'.repeat(234) + '…')
    assert.ok(vm.excerpt.length <= 241)
    assert.equal(vm.expandable, true)
})

test('the event kind determines the role independently of payload fields', () => {
    assert.deepEqual(describeMessageSnapshot('user_message.sent', { text: 'Hello', role: 'assistant' }), {
        role: 'user', text: 'Hello', excerpt: 'Hello', expandable: true,
    })
    assert.deepEqual(describeMessageSnapshot('assistant_message.sent', { text: 'Done', role: 'user' }), {
        role: 'assistant', text: 'Done', excerpt: 'Done', expandable: true,
    })
})

test('the excerpt removes Markdown syntax and collapses whitespace without changing saved text', () => {
    const text = '# Title\n\n**Bold** [link](https://example.com) ![image](image.png)\n`code`\n\n```js\n  value()\n```\n'
    const vm = describeMessageSnapshot('user_message.sent', { text })
    assert.equal(vm.text, text)
    assert.equal(vm.excerpt, 'Title Bold link image code value()')
})

test('the excerpt boundary adds an ellipsis only when content exceeds 240 characters', () => {
    assert.equal(describeMessageSnapshot('user_message.sent', { text: 'x'.repeat(240) }).excerpt, 'x'.repeat(240))
    assert.equal(describeMessageSnapshot('user_message.sent', { text: 'x'.repeat(241) }).excerpt, 'x'.repeat(240) + '…')
})

test('unknown kinds and malformed payloads have safe empty fallbacks', () => {
    for (const kind of ['turn.ended', 'constructor', null]) {
        assert.deepEqual(describeMessageSnapshot(kind, { text: 'Not a snapshot' }), {
            role: null, text: '', excerpt: '', expandable: false,
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
