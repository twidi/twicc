import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { reactive, watch, nextTick } from 'vue'
import { getSessionCutoffMs } from './sessions.js'
import { jsonValuesEqual } from './jsonValuesEqual.js'

const title = await import('./sessionTitle.js').catch(() => ({}))
const enabled = { titleAutoApply: true, titleGenerationEnabled: true }
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const view = read('../views/SessionView.vue')
const dialog = read('../components/session/detail/SessionRenameDialog.vue')
const watcher = read('../composables/useAutoApplyTitle.js')
const data = read('../stores/data.js')

test('requests automatic suggestions only for ephemeral sessions with both settings enabled', () => {
    for (const [session, settings, expected] of [
        [{ ephemeral: true }, enabled, true],
        [{ draft: true }, enabled, false],
        [{ draft: false }, enabled, false],
        [{ ephemeral: true }, { ...enabled, titleAutoApply: false }, false],
        [{ ephemeral: true }, { ...enabled, titleGenerationEnabled: false }, false],
        [null, enabled, false],
    ]) {
        assert.equal(title.shouldRequestAutomaticTitle?.(session, settings), expected)
    }
})

test('shows the automatic hint only for automatic titles without a pending user title', () => {
    for (const [session, expected] of [
        [{ title_origin: 'auto' }, true],
        [{ title_origin: 'auto', has_pending_title: true }, false],
        [{ title_origin: 'user' }, false],
        [{ title_origin: '' }, false],
        [null, false],
    ]) {
        assert.equal(title.showAutomaticTitleHint?.(session), expected)
    }
})

test('builds a trimmed title PATCH without filtering unchanged text', () => {
    assert.deepEqual(title.buildSessionTitlePatch?.(' Current '), { title: 'Current' })
    assert.deepEqual(title.buildSessionTitlePatch?.('Current'), { title: 'Current' })
})

function runNeedsTitle(session, settings = enabled, sent = true) {
    const calls = []
    const code = view.slice(view.indexOf('function handleNeedsTitle()'), view.indexOf('// ═', view.indexOf('function handleNeedsTitle()')))
    new Function('session', 'sessionId', 'projectId', 'settingsStore', 'store', 'sessionHeaderRef',
        'requestTitleSuggestion', 'shouldRequestAutomaticTitle', `${code}; handleNeedsTitle()`)(
        { value: session }, { value: 's' }, { value: 'p' },
        { isTitleAutoApply: settings.titleAutoApply, isTitleGenerationEnabled: settings.titleGenerationEnabled, getTitleSystemPrompt: 'Summarize {text}' },
        {
            getDraftMessage: () => ({ message: '  Prompt  ' }),
            registerPendingTitleAutoApply: (...args) => calls.push(['register', ...args]),
            clearPendingTitleAutoApply: (...args) => calls.push(['clear', ...args]),
        },
        { value: { openRenameDialog: options => calls.push(['dialog', options]) } },
        (...args) => { calls.push(['request', ...args]); return sent },
        title.shouldRequestAutomaticTitle,
    )
    return calls
}

test('real sessions and regular drafts leave automatic title generation to the backend', () => {
    assert.deepEqual(runNeedsTitle({ draft: false }), [])
    assert.deepEqual(runNeedsTitle({ draft: true }), [])
    assert.match(view, /shouldRequestAutomaticTitle\(session\.value,/)
})

test('ephemeral suggestions register before the request and clear an unsent request', () => {
    assert.deepEqual(runNeedsTitle({ ephemeral: true }, enabled, false), [
        ['register', 's', 'p'], ['request', 's', 'Prompt', 'Summarize {text}'], ['clear', 's'],
    ])
})

test('either disabled setting keeps the contextual rename dialog', () => {
    for (const setting of ['titleAutoApply', 'titleGenerationEnabled']) {
        assert.deepEqual(runNeedsTitle({ draft: false }, { ...enabled, [setting]: false }), [
            ['dialog', { showHint: true }],
        ])
    }
})

function runAutoApply(session, entry) {
    const pending = { s: { projectId: 'p' } }
    const saved = []
    const patches = []
    const store = {
        localState: { pendingTitleAutoApply: pending },
        getSession: () => session,
        getTitleSuggestion: () => entry?.suggestion || null,
        getTitleSuggestionEntry: () => entry,
        clearPendingTitleAutoApply: sid => { delete pending[sid] },
        setDraftTitle: (...args) => saved.push(args),
        renameSession: (...args) => patches.push(args),
    }
    const code = watcher.slice(watcher.indexOf('let started')).replace('export function', 'function')
    new Function('watchEffect', 'useDataStore', `${code}; startAutoApplyTitleWatcher()`)(effect => effect(), () => store)
    return { pending, saved, patches }
}

test('auto-apply persists ephemeral titles locally', () => {
    const session = { ephemeral: true, title: null }
    assert.deepEqual(runAutoApply(session, { suggestion: 'Suggested' }), {
        pending: {}, saved: [['s', 'Suggested']], patches: [],
    })
    assert.equal(session.title, 'Suggested')
})

test('auto-apply keeps a manual ephemeral title', () => {
    const session = { ephemeral: true, title: 'Manual' }
    assert.deepEqual(runAutoApply(session, { suggestion: 'Suggested' }).saved, [['s', 'Manual']])
    assert.equal(session.title, 'Manual')
})

test('a failed suggestion clears its pending intent without changing the title', () => {
    const session = { ephemeral: true, title: null }
    assert.deepEqual(runAutoApply(session, { suggestion: null }), { pending: {}, saved: [], patches: [] })
    assert.equal(session.title, null)
})

test('auto-apply clears accidental non-ephemeral intents before any title mutation', () => {
    for (const session of [{ draft: false, title: null }, { draft: true, title: 'Manual' }]) {
        for (const entry of [null, { suggestion: 'Suggested' }]) {
            const oldTitle = session.title
            assert.deepEqual(runAutoApply(session, entry), { pending: {}, saved: [], patches: [] })
            assert.equal(session.title, oldTitle)
        }
    }
    assert.doesNotMatch(watcher, /\brenameSession\s*\(/)
})

test('an absent session or a pending suggestion waits without changing local state', () => {
    assert.deepEqual(runAutoApply(null, { suggestion: 'Suggested' }), {
        pending: { s: { projectId: 'p' } }, saved: [], patches: [],
    })
    assert.deepEqual(runAutoApply({ ephemeral: true, title: null }, null), {
        pending: { s: { projectId: 'p' } }, saved: [], patches: [],
    })
})

async function saveTitle(session, input) {
    const calls = []
    const code = dialog.slice(dialog.indexOf('async function handleSave()'), dialog.indexOf('// Expose methods'))
    const localTitle = { value: input }
    const errorMessage = { value: '' }
    await new Function('props', 'localTitle', 'isSaving', 'errorMessage', 'store', 'emit', 'close',
        'buildSessionTitlePatch', `${code}; return handleSave()`)(
        { session }, localTitle, { value: false }, errorMessage,
        {
            renameSession: async (...args) => calls.push(['rename', ...args]),
            updateSession: value => calls.push(['local', value]),
            setDraftTitle: (...args) => calls.push(['draft', ...args]),
        },
        name => calls.push([name]), () => calls.push(['close']), title.buildSessionTitlePatch,
    )
    return { calls, error: errorMessage.value }
}

test('real Save validates the unchanged automatic title through renameSession', async () => {
    assert.deepEqual(await saveTitle({ id: 's', project_id: 'p', title: 'Current', title_origin: 'auto' }, ' Current '), {
        calls: [['rename', 'p', 's', 'Current'], ['saved'], ['close']], error: '',
    })
    assert.match(dialog, /buildSessionTitlePatch\(/)
})

test('draft and ephemeral Save persist only locally', async () => {
    for (const flag of ['draft', 'ephemeral']) {
        const session = { id: 's', [flag]: true }
        assert.deepEqual(await saveTitle(session, ' Local '), {
            calls: [['local', { ...session, title: 'Local' }], ['draft', 's', 'Local'], ['saved'], ['close']], error: '',
        })
    }
})

test('invalid title inputs never save', async () => {
    assert.deepEqual(await saveTitle({ id: 's' }, ' '), { calls: [], error: 'Title cannot be empty' })
    assert.deepEqual(await saveTitle({ id: 's' }, 'x'.repeat(201)), { calls: [], error: 'Title must be 200 characters or less' })
})

test('renameSession sends a real PATCH even when the current title is unchanged', async () => {
    const start = data.indexOf('        async renameSession(')
    const code = data.slice(start, data.indexOf('        /**', start))
    const updateStart = data.indexOf('        updateSession(')
    const updateCode = data.slice(updateStart, data.indexOf('        /**', updateStart))
    const requests = []
    const actions = new Function('apiFetch', 'getSessionCutoffMs', 'markAgentIdle', 'jsonValuesEqual',
        `return { ${code} ${updateCode} }`)(async (url, options) => {
        requests.push([url, options.method, JSON.parse(options.body)])
        return { ok: true, json: async () => ({ title: 'Current', title_origin: 'user' }) }
    }, getSessionCutoffMs, () => {}, jsonValuesEqual)
    const store = { ...actions, sessions: { s: { title: 'Current', title_origin: 'auto' } },
        localState: { agentRunStates: {}, sessions: {} }, $patch(apply) { apply(this) },
        _hydrateSessionLayoutFromPersisted() {}, tryFinalizePendingBinding() {}, _tryLinkPeerDelivery() {},
    }
    await store.renameSession('p', 's', 'Current')
    assert.deepEqual(requests, [['/api/projects/p/sessions/s/', 'PATCH', { title: 'Current' }]])
    assert.equal(store.sessions.s.title_origin, 'user')
})

test('canonical real-session binding drops automatic intents without transferring them', () => {
    const start = data.indexOf('        async bindDraftSession(')
    const binding = data.slice(start, data.indexOf('        /**', start))
    assert.doesNotMatch(binding, /pendingTitleAutoApply\[sessionId\]\s*=/)
    assert.match(binding, /clearPendingTitleAutoApply\(draftId\)/)
    assert.ok(binding.indexOf('clearPendingTitleAutoApply(draftId)') < binding.indexOf('if (draftId === sessionId)'))
})

test('server title and origin updates preserve the text typed in the dialog', async () => {
    const props = reactive({ session: { id: 's', title: 'Automatic', title_origin: 'auto' } })
    const localTitle = { value: '' }
    const start = dialog.indexOf('// Sync form values when session changes')
    const code = dialog.slice(start, dialog.indexOf('// Watch for suggestion response', start))
    let stop
    new Function('props', 'localTitle', 'watch', code)(props, localTitle, (...args) => { stop = watch(...args) })
    localTitle.value = 'Typed title'
    Object.assign(props.session, { title: 'Server title', title_origin: 'user', has_pending_title: true })
    await nextTick()
    assert.equal(localTitle.value, 'Typed title')
    stop()
})

test('dialog hint and settings label use the required automatic-title copy', () => {
    assert.match(dialog, /showAutomaticTitleHint\(props\.session\)/)
    assert.match(dialog, /v-if="[^"]*utomaticTitle[^"]*" class="context-hint"/)
    assert.ok(dialog.includes('This title is automatic. Save to validate it and stop automatic updates.'))
    assert.match(read('../components/app/SettingsPopover.vue'), /:checked="titleAutoApply"[\s\S]*?>Automatic titles<\/wa-switch>/)
    const start = dialog.indexOf('function close()')
    const close = dialog.slice(start, dialog.indexOf('/**', start))
    assert.doesNotMatch(close, /store\.|localTitle\.value\s*=/)
})
