import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { createTerminalClipboardObserver } from './terminalClipboardDiagnostics.js'

test('the terminal requires an enabled Android capability before collecting failure context', () => {
    const source = readFileSync(new URL('../composables/useTerminal.js', import.meta.url), 'utf8')
    const start = source.indexOf('    const observeClipboard = createTerminalClipboardObserver({')
    const declaration = source.slice(start, source.indexOf('\n    /**', start))
    for (const android of [false, true]) {
        for (const enabled of [false, true]) {
            let clientReads = 0, contexts = 0
            const reports = []
            const observe = runInNewContext(`${declaration}; observeClipboard`, {
                createTerminalClipboardObserver,
                isAndroidApp: { value: android },
                getAndroidAppClient() {
                    clientReads++
                    return { supports: () => enabled, request: (_, { diagnostic }) => reports.push(diagnostic) }
                },
                document: { hasFocus() { contexts++; return true }, visibilityState: 'visible' },
                window: { isSecureContext: true }, navigator: { userAgent: 'test', clipboard: {} },
                ws: null, isConnected: { value: true },
            })
            assert.equal(observe({ operation: 'copy', method: 'execCommand' }, () => false), false)
            assert.equal(contexts, android && enabled ? 1 : 0)
            assert.equal(reports.length, android && enabled ? 1 : 0)
            if (!android) assert.equal(clientReads, 0)
        }
    }
})

test('web operations keep their result and never collect or report diagnostics', async () => {
    let contexts = 0, reports = 0
    const observe = createTerminalClipboardObserver({
        enabled: () => false,
        context: () => { contexts++; return {} },
        report: () => { reports++ },
    })
    const failure = new Error('Denied')
    const pending = Promise.reject(failure)
    assert.equal(observe({}, () => pending), pending)
    await assert.rejects(pending, error => error === failure)
    assert.equal(observe({}, () => false), false)
    assert.throws(() => observe({}, () => { throw failure }), error => error === failure)
    assert.equal(contexts, 0)
    assert.equal(reports, 0)
})

test('Android records rejection context at invocation and preserves the original promise', async () => {
    const reports = []
    let focused = true
    const observe = createTerminalClipboardObserver({
        enabled: () => true,
        context: () => ({ focused, websocketState: 1 }),
        report: record => reports.push(record),
    })
    const failure = new DOMException('Read permission denied', 'NotAllowedError')
    const pending = Promise.reject(failure)
    assert.equal(observe({ operation: 'paste', trigger: 'button', method: 'readText' }, () => pending), pending)
    focused = false
    await assert.rejects(pending, error => error === failure)
    assert.equal(reports.length, 1)
    assert.equal(reports[0].focused, true)
    assert.equal(reports[0].websocketState, 1)
    assert.equal(reports[0].errorName, 'NotAllowedError')
    assert.equal(reports[0].errorMessage, 'Read permission denied')
    assert.equal(reports[0].operation, 'paste')
})

test('Android records synchronous failures and execCommand false without recording clipboard text', () => {
    const reports = []
    const observe = createTerminalClipboardObserver({ enabled: () => true, context: () => ({}), report: record => reports.push(record) })
    const failure = new TypeError('Clipboard API unavailable')
    assert.throws(() => observe({ operation: 'copy', trigger: 'shortcut', method: 'writeText', text: 'private command' },
        () => { throw failure }), error => error === failure)
    assert.equal(observe({ operation: 'copy', trigger: 'button', method: 'execCommand' }, () => false), false)
    assert.equal(reports.length, 2)
    assert.equal(reports[0].textLength, 15)
    assert.doesNotMatch(JSON.stringify(reports), /private command/)
    assert.equal(reports[1].errorName, 'CopyCommandFailed')
})

test('diagnostic failures cannot prevent an operation or replace its error', async () => {
    const failure = new Error('Original failure')
    for (const report of [() => { throw new Error('Bridge closed') }, () => Promise.reject(new Error('Bridge closed'))]) {
        const observe = createTerminalClipboardObserver({ enabled: () => true, context: () => ({}), report })
        assert.throws(() => observe({}, () => { throw failure }), error => error === failure)
        assert.equal(await observe({}, () => Promise.resolve('success')), 'success')
    }
    const observe = createTerminalClipboardObserver({ enabled: () => true, context: () => { throw new Error('Context unavailable') }, report() {} })
    assert.equal(observe({}, () => 'success'), 'success')
})

test('the terminal paste handler reports the failed stage only on Android and keeps its existing error message', async () => {
    const source = readFileSync(new URL('../composables/useTerminal.js', import.meta.url), 'utf8')
    const start = source.indexOf('    async function handleExtraKeyPaste()')
    const handler = source.slice(start, source.indexOf('\n    /**', start))
    for (const android of [false, true]) {
        for (const stage of ['readText', 'terminalInput']) {
            const reports = [], notices = []
            const failure = new DOMException('Access denied', 'NotAllowedError')
            const paste = runInNewContext(`${handler}; handleExtraKeyPaste`, {
                observeClipboard: createTerminalClipboardObserver({
                    enabled: () => android, context: () => ({}), report: diagnostic => reports.push(diagnostic),
                }),
                navigator: { clipboard: { readText: () => stage === 'readText' ? Promise.reject(failure) : Promise.resolve('private input') } },
                wsSend() { throw failure },
                terminal: { focus() {} },
                localUiMessage: key => key,
                toast: { error: message => notices.push(message) },
            })
            await paste()
            assert.deepEqual(notices, ['session.terminal.clipboardDenied'])
            assert.equal(reports.length, android ? 1 : 0)
            if (android) {
                assert.equal(reports[0].method, stage)
                assert.doesNotMatch(JSON.stringify(reports), /private input/)
            }
        }
    }
})
