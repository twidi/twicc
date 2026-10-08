import test from 'node:test'
import assert from 'node:assert/strict'
import { createSelectionCommentBridge } from './selectionCommentBridge.js'

test('bridge captures composer and initiates Chat navigation at preparation only', async () => {
    const events = []
    let prepare, finish
    const composer = {
        selectionCommentSendAvailable: true, selectionCommentSendPending: false,
        startSelectionCommentSend: (text, options) => {
            events.push(`start:${text}`)
            prepare = options.onPrepared
            return new Promise(resolve => { finish = resolve })
        },
    }
    let owner = composer
    const bridge = createSelectionCommentBridge({ getComposer: () => owner, showChat: () => events.push('chat') })
    assert.equal(bridge.available, true)
    const running = bridge.send('comment', { onPrepared: () => events.push('close') })
    owner = null
    prepare()
    finish()
    await running
    assert.deepEqual(events, ['start:comment', 'close', 'chat'])
    assert.equal(bridge.available, false)
})

test('availability is tied to actual composer, not current send controls', async () => {
    let owner = null
    const bridge = createSelectionCommentBridge({ getComposer: () => owner, showChat() {} })
    assert.equal(bridge.available, false)
    await assert.rejects(bridge.send('comment', {}), /composer/)
    owner = { selectionCommentSendAvailable: true, selectionCommentSendPending: true }
    assert.equal(bridge.available, true)
    assert.equal(bridge.pending, true)
})

test('navigation rejection does not stop the composer operation', async () => {
    const bridge = createSelectionCommentBridge({
        getComposer: () => ({ selectionCommentSendAvailable: true, startSelectionCommentSend: async (text, { onPrepared }) => { onPrepared(); return 'sent' } }),
        showChat: async () => { throw Error('navigation') },
        onNavigationError: () => {},
    })
    assert.equal(await bridge.send('comment', { onPrepared() {} }), 'sent')
})
