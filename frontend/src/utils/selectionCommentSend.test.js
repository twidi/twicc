import test from 'node:test'
import assert from 'node:assert/strict'
import { createSelectionCommentSendController } from './selectionCommentSend.js'

function deferred() {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
}
function fixture() {
    const events = []
    let timer
    const controller = createSelectionCommentSendController({
        insert: text => events.push(`insert:${text}`),
        attemptSend: async () => events.push('send'),
        onPendingChange: () => {},
        notifyUploadWait: () => events.push('toast'),
        schedule: (fn, delay) => {
            assert.equal(delay, 3000)
            timer = fn
            return () => { timer = null }
        },
    })
    const onPrepared = () => events.push('prepared')
    return { controller, events, onPrepared, fire: () => timer?.() }
}

test('inserts before preparation and sends once without a screenshot', async () => {
    const f = fixture()
    await f.controller.start('comment', { onPrepared: f.onPrepared })
    assert.deepEqual(f.events, ['insert:comment', 'prepared', 'send'])
    assert.equal(f.controller.pending, false)
})

test('wait starts before attachment promise resolves, but sending waits for insertion', async () => {
    const f = fixture(), gate = deferred()
    const running = f.controller.start('comment', {
        onPrepared: f.onPrepared,
        attachScreenshot: ({ onUploadStarted }) => { onUploadStarted({ id: 'shot' }); return gate.promise },
    })
    f.fire(); f.fire()
    assert.deepEqual(f.events, ['toast'])
    f.controller.observeScreenshot({ id: 'shot', present: true, state: 'ready' })
    assert.deepEqual(f.events, ['toast'])
    gate.resolve({ id: 'shot' })
    await running
    assert.deepEqual(f.events, ['toast', 'insert:comment', 'prepared', 'send'])
})

test('ready screenshot sends once after preparation and cancels late toast', async () => {
    const f = fixture()
    const running = f.controller.start('comment', {
        onPrepared: f.onPrepared,
        attachScreenshot: async ({ onUploadStarted }) => { onUploadStarted({ id: 'shot' }); return { id: 'shot' } },
    })
    await Promise.resolve()
    assert.deepEqual(f.events, ['insert:comment', 'prepared'])
    f.controller.observeScreenshot({ id: 'other', present: true, state: 'ready' })
    f.controller.observeScreenshot({ id: 'shot', present: true, state: undefined })
    assert.equal(f.controller.pending, true)
    f.controller.observeScreenshot({ id: 'shot', present: true, state: 'ready' })
    f.controller.observeScreenshot({ id: 'shot', present: true, state: 'ready' })
    await running
    f.fire()
    assert.deepEqual(f.events, ['insert:comment', 'prepared', 'send'])
})

for (const state of ['failed', 'missing', 'removed']) {
    test(`${state} during staging preserves insertion, without sending`, async () => {
        const f = fixture(), gate = deferred()
        const running = f.controller.start('comment', {
            onPrepared: f.onPrepared,
            attachScreenshot: ({ onUploadStarted }) => { onUploadStarted({ id: 'shot' }); return gate.promise },
        })
        f.controller.observeScreenshot({ id: 'shot', present: state !== 'removed', state })
        f.fire()
        gate.resolve({ id: 'shot' })
        await running
        assert.deepEqual(f.events, ['insert:comment', 'prepared'])
        assert.equal(f.controller.pending, false)
    })
}

test('attachment rejection preserves the form and releases ownership', async () => {
    const f = fixture()
    await assert.rejects(f.controller.start('comment', {
        attachScreenshot: async () => { throw Error('storage') }, onPrepared: f.onPrepared,
    }), /storage/)
    assert.deepEqual(f.events, [])
    assert.equal(f.controller.pending, false)
})

test('duplicate start during staging cannot duplicate attachments or insertion', async () => {
    const f = fixture(), gate = deferred()
    let attachments = 0
    const running = f.controller.start('first', {
        attachScreenshot: ({ onUploadStarted }) => { attachments++; onUploadStarted({ id: 'shot' }); return gate.promise },
        onPrepared: f.onPrepared,
    })
    await f.controller.start('duplicate', { onPrepared: f.onPrepared })
    gate.resolve({ id: 'shot' })
    await Promise.resolve()
    f.controller.cancel()
    await running
    assert.equal(attachments, 1)
    assert.deepEqual(f.events, ['insert:first', 'prepared'])
})

test('cancelled wait cannot send a replacement operation or emit a late toast', async () => {
    const f = fixture()
    const running = f.controller.start('first', {
        attachScreenshot: async ({ onUploadStarted }) => { onUploadStarted({ id: 'shot' }); return { id: 'shot' } },
        onPrepared: f.onPrepared,
    })
    await Promise.resolve()
    f.controller.cancel()
    await running
    await f.controller.start('replacement', { onPrepared: f.onPrepared })
    f.controller.observeScreenshot({ id: 'shot', present: true, state: 'ready' })
    f.fire()
    assert.deepEqual(f.events, ['insert:first', 'prepared', 'insert:replacement', 'prepared', 'send'])
})

test('disposal before staging finishes prevents insertion and preparation', async () => {
    const f = fixture(), gate = deferred()
    const running = f.controller.start('comment', { attachScreenshot: () => gate.promise, onPrepared: f.onPrepared })
    f.controller.dispose()
    gate.resolve({ id: 'shot' })
    await running
    assert.deepEqual(f.events, [])
})
