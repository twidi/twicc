import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { parse, babelParse } from '@vue/compiler-sfc'
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { createSelectionCommentSendController } from '../../utils/selectionCommentSend.js'
import { composerAttachmentsReady, sendComposerMessage, setAttachmentPayloadFields, snapshotAttachments } from '../../utils/composerAttachments.js'

// Execute the actual composer methods with real Vue reactivity and a stubbed transport.
// The rest of MessageInput's pickers and visual controls do not participate in this flow.
function fixture({ collapsed = false, text = 'draft ', connected = true, overrides = {} } = {}) {
    const source = parse(fs.readFileSync(new URL('./MessageInput.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content
    const ast = babelParse(source, { sourceType: 'module' }).program.body
    const names = new Set(['handleSend', 'sendMessage', 'insertTextAtCursor', 'updateTextareaContent',
        'startSelectionCommentSend', 'attemptSelectionCommentSend', 'observeSelectionScreenshot',
        'selectionCommentSendController', 'selectionCommentSendPending', 'selectionScreenshotId',
        'selectionScreenshotState', 'sendInProgress', 'isDisabled'])
    const selected = ast.filter(node => (node.type === 'FunctionDeclaration' && names.has(node.id.name))
        || (node.type === 'VariableDeclaration' && node.declarations.some(d => names.has(d.id.name))))
        .map(node => source.slice(node.start, node.end)).join('\n')
    const observerExpressions = ast.filter(node => node.type === 'ExpressionStatement'
        && node.expression.type === 'CallExpression' && node.expression.callee.name === 'watch'
        && /selectionCommentSendController|selectionScreenshotState/.test(source.slice(node.start, node.end)))
        .map(node => source.slice(node.start, node.end).replace(/;$/, ''))
    const values = {}, events = [], frames = [], toasts = [], records = ref([]), messageText = ref(text)
    const props = reactive({ sessionId: 's1', projectId: 'p1', sendingLocked: false })
    const store = {
        wsConnected: connected, isInitialSyncInProgress: false, projects: [],
        localState: reactive({ attachmentRuntime: {} }),
        getComposerAttachments: () => records.value,
        getAttachmentPreviewUrl: () => null,
        forgetAttachments: async () => {}, registerOutgoingSend: () => events.push('registered'),
        clearDraftMessage: () => events.push('cleared'), applyCreationSendMode: () => {},
        deleteDraftSession: () => {}, getDraftMessage: () => null,
        getPendingAsyncQuestionIds: () => [], reserveAsyncQuestionSend: () => true,
    }
    for (const name of ['isStarting', 'isDraft', 'isHybridStaged', 'isContextMaxForced', 'isComposerCommand']) values[name] = ref(false)
    for (const name of ['selectedModel', 'selectedPermissionMode', 'selectedEffort', 'selectedThinking', 'selectedClaudeInChrome', 'selectedFastMode', 'selectedContextMax',
        'activeModel', 'activePermissionMode', 'activeEffort', 'activeThinking', 'activeClaudeInChrome', 'activeFastMode', 'activeContextMax', 'asyncQuestionSnapshot', 'asyncQuestionDraft']) values[name] = ref(null)
    const inner = { selectionStart: 6, selectionEnd: 6, value: text, setSelectionRange() {} }
    const textareaRef = ref({ shadowRoot: { querySelector: () => inner }, focus: () => events.push('focus') })
    Object.assign(values, {
        ref, computed, watch, nextTick, props, store, createSelectionCommentSendController,
        composerAttachmentsReady, sendComposerMessage, setAttachmentPayloadFields, snapshotAttachments,
        messageText, collapsed: ref(collapsed), textareaRef,
        session: ref({ provider: 'codex', title: 'Test' }),
        getProviderHelpers: () => ({ canSendMessage: () => true }),
        composerRecords: records, legacyAttachmentCount: ref(0), canSendAttachmentsOnly: ref(false),
        attachmentsReady: computed(() => composerAttachmentsReady(records.value, store.localState.attachmentRuntime, 0)),
        attachmentsBlockSend: computed(() => !composerAttachmentsReady(records.value, store.localState.attachmentRuntime, 0)),
        asyncQuestionSendClassification: ref({ canSend: true, commandBlocked: false, settingsOnly: false }),
        adjustTextareaHeight: () => {}, emit: () => {}, generateUUID: () => 'request-1',
        prepareAsyncQuestionSend: () => ({}),
        settings: { providerStore: ref({}), resolvedDefaults: ref({}) },
        resolveProjectTrust: () => ({ state: true }), ensureProjectTrust: async () => ({ state: true }),
        sendWsMessage: payload => { frames.push(payload); return store.wsConnected },
        toast: { warning: msg => toasts.push(msg), error: msg => toasts.push(msg), info: msg => toasts.push(msg) },
    })
    Object.assign(values, overrides)
    const result = new Function(...Object.keys(values), `${selected}\nconst observerStops = [${observerExpressions.join(",")}];\nreturn { observerStops, handleSend, startSelectionCommentSend, selectionCommentSendPending, selectionScreenshotState, observeSelectionScreenshot, selectionCommentSendController, sendInProgress };`)(...Object.values(values))
    return { ...result, messageText, records, props, store, frames, toasts, events, values, cleanup: () => { result.observerStops.forEach(stop => stop()); result.selectionCommentSendController.dispose() } }
}

test('selection send uses existing composer and payload settings', async () => {
    const f = fixture()
    try {
        f.values.selectedModel.value = 'model'
        await f.startSelectionCommentSend('comment\n', { onPrepared: () => f.events.push('prepared') })
        assert.equal(f.frames.length, 1)
        assert.equal(f.frames[0].text, 'draft comment')
        assert.equal(f.frames[0].selected_model, 'model')
        assert.equal(f.frames[0].session_id, 's1')
        assert.equal(f.events[0], 'prepared')
        assert.equal(f.events.includes('focus'), false)
        assert.equal(f.messageText.value, '')
    } finally { f.cleanup() }
})

for (const block of ['sendingLocked', 'connection', 'initialSync', 'starting', 'attachments']) {
    test(`blocked ${block} keeps inserted draft and explains unavailable send`, async () => {
        const f = fixture({ collapsed: true })
        try {
            if (block === 'sendingLocked') f.props.sendingLocked = true
            if (block === 'connection') f.store.wsConnected = false
            if (block === 'initialSync') f.store.isInitialSyncInProgress = true
            if (block === 'starting') f.values.isStarting.value = true
            if (block === 'attachments') f.records.value = [{ id: 'other' }]
            await f.startSelectionCommentSend('comment', { onPrepared() {} })
            assert.equal(f.messageText.value, 'draft comment')
            assert.equal(f.frames.length, 0)
            assert.deepEqual(f.toasts, ['Cannot send right now. Your message is in the composer. Send it when sending becomes available.'])
        } finally { f.cleanup() }
    })
}

test('text clear followed by replacement cancels pending screenshot send synchronously', async () => {
    const f = fixture()
    try {
        const running = f.startSelectionCommentSend('comment', {
            attachScreenshot: async ({ onUploadStarted }) => {
                f.records.value = [{ id: 'shot' }]
                f.store.localState.attachmentRuntime.shot = { state: 'uploading' }
                onUploadStarted({ id: 'shot' })
                return { id: 'shot' }
            }, onPrepared() {},
        })
        await Promise.resolve()
        f.messageText.value = ''
        f.messageText.value = 'replacement'
        f.store.localState.attachmentRuntime.shot.state = 'ready'
        await running
        assert.equal(f.frames.length, 0)
        assert.equal(f.messageText.value, 'replacement')
    } finally { f.cleanup() }
})

test('normal send guard prevents concurrent dispatch while async trust gate waits', async () => {
    const f = fixture()
    try {
        // Guard must cover the entire asynchronous send, not only transport dispatch.
        f.sendInProgress.value = true
        await f.handleSend()
        assert.equal(f.frames.length, 0)
        assert.equal(f.messageText.value, 'draft ')
    } finally { f.cleanup() }
})

test('ready screenshot and manual send race across trust resolution dispatches once', async () => {
    let openTrust
    const gate = new Promise(resolve => { openTrust = resolve })
    const f = fixture({ overrides: { resolveProjectTrust: () => ({ state: null }), ensureProjectTrust: () => gate } })
    try {
        f.values.isDraft.value = true
        const running = f.startSelectionCommentSend('comment', {
            attachScreenshot: async ({ onUploadStarted }) => {
                f.records.value = [{ id: 'shot', bucket: 's1', name: 'shot.png' }]
                f.store.localState.attachmentRuntime.shot = { state: 'uploading' }
                onUploadStarted({ id: 'shot' })
                return { id: 'shot' }
            }, onPrepared() {},
        })
        await Promise.resolve()
        f.store.localState.attachmentRuntime.shot.state = 'ready'
        const manual = f.handleSend()
        assert.equal(f.sendInProgress.value, true)
        await Promise.resolve()
        openTrust({ state: true })
        await Promise.all([running, manual])
        assert.equal(f.frames.length, 1)
        assert.deepEqual(f.frames[0].attachments, [{ id: 'shot', bucket: 's1' }])
        assert.equal(f.selectionCommentSendPending.value, false)
        assert.equal(f.sendInProgress.value, false)
    } finally { f.cleanup() }
})

test('dispatch failure uses existing error handling and preserves composer', async () => {
    const f = fixture({ overrides: { sendWsMessage: () => false } })
    try {
        await f.startSelectionCommentSend('comment', { onPrepared() {} })
        assert.equal(f.messageText.value, 'draft comment')
        assert.deepEqual(f.toasts, ['Message not sent: the connection is unavailable. Your message is still in the composer.'])
        assert.equal(f.sendInProgress.value, false)
    } finally { f.cleanup() }
})

test('unavailable provider blocks automatic sending but keeps the insertion', async () => {
    const f = fixture({ overrides: { getProviderHelpers: () => ({ canSendMessage: () => false }) } })
    try {
        await f.startSelectionCommentSend('comment', { onPrepared() {} })
        assert.equal(f.frames.length, 0)
        assert.equal(f.messageText.value, 'draft comment')
        assert.equal(f.toasts.length, 1)
    } finally { f.cleanup() }
})

test('upload failure before staging resolves still inserts and prepares the composer', async () => {
    const f = fixture()
    try {
        await f.startSelectionCommentSend('comment', {
            attachScreenshot: async ({ onUploadStarted }) => {
                f.records.value = [{ id: 'shot' }]
                f.store.localState.attachmentRuntime.shot = { state: 'failed' }
                onUploadStarted({ id: 'shot' })
                return { id: 'shot' }
            }, onPrepared: () => f.events.push('prepared'),
        })
        assert.equal(f.messageText.value, 'draft comment')
        assert.deepEqual(f.events, ['prepared'])
        assert.equal(f.frames.length, 0)
    } finally { f.cleanup() }
})
