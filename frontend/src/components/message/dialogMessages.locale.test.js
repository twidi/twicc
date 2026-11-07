import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'
import { createAppI18n, loadLocaleMessages } from '../../i18n/index.js'
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { remoteUiMessage } from '../../i18n/remoteMessages.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

// Exercise the actual validation handlers without mounting the browser-only
// custom elements. Their injected state is the same ref shape as Vue setup.
function handler(path, name, state) {
    const source = readFileSync(new URL(path, import.meta.url), 'utf8')
    const fn = source.match(new RegExp(`(?:async )?function ${name}\\([^]*?\\n}`))?.[0]
    assert.ok(fn, `${name} exists`)
    return runInNewContext(`(${fn})`, { ...state, localUiMessage, remoteUiMessage })
}

function visibleNotice(path, name, state, composer) {
    const source = readFileSync(new URL(path, import.meta.url), 'utf8')
    // Use the component's display expression so the test also catches a
    // regression that renders the descriptor object directly.
    const expression = source.match(new RegExp(`{{\\s*(resolveUiMessage\\(${name}\\))\\s*}}`))?.[1]
    assert.ok(expression, `${name} resolves at display time`)
    return computed(() => runInNewContext(expression, {
        [name]: state.value,
        resolveUiMessage: value => resolveUiMessage(value, composer),
    }))
}

function assertLocaleChange(path, name, state, composer, key, params = {}, count) {
    const visible = visibleNotice(path, name, state, composer)
    const stored = state.value
    assert.equal(stored.key, key)
    assert.equal(visible.value, composer.t(key, params, count ?? {}))
    composer.locale.value = 'pt-BR'
    assert.equal(visible.value, composer.t(key, params, count ?? {}))
    assert.equal(state.value, stored, 'locale changes do not rerun validation or replace its state')
}

test('snippet validation and duplicate warnings follow locale while the form stays open', () => {
    const path = './MessageSnippetsDialog.vue'
    const composer = createAppI18n().global
    const state = {
        errorMessage: ref(''), warningMessage: ref(''), groupMode: ref(true),
        formData: ref({ label: '', text: '', scope: 'global' }), editIndex: ref(null),
        editScope: ref('global'), editSourceGroupId: ref(null),
        messageSnippetsStore: { snippets: { global: [{ label: 'Same' }] } },
        extractPlaceholders: () => [], flattenGroupedEntries: entries => entries,
        getGroupItems: entries => entries,
    }
    const save = handler(path, 'handleSave', state)
    save()
    assertLocaleChange(path, 'errorMessage', state.errorMessage, composer, 'composer.snippets.errors.groupNameRequired')
    composer.locale.value = 'en'
    state.groupMode.value = false
    state.formData.value = { label: 'Same', text: 'User message', scope: 'global' }
    save()
    assertLocaleChange(path, 'warningMessage', state.warningMessage, composer, 'composer.snippets.warnings.duplicateLabel')
    assert.equal(state.formData.value.text, 'User message')
})

test('project validation and plural workspace feedback retain live locale parameters', async () => {
    const path = '../project/ProjectEditDialog.vue'
    const composer = createAppI18n().global
    const state = {
        errorMessage: ref(''), isSaving: ref(false), localName: ref('x'.repeat(26)),
        workspaceScanFeedback: ref(''), localDirectory: ref('/project'),
        allWorkspaces: ref([{ id: 'a', autoProjectPatterns: ['*'] }, { id: 'b', autoProjectPatterns: ['*'] }]),
        selectedWorkspaceIds: ref([]), matchPattern: () => true, clearWorkspaceScanFeedbackLater: () => {},
    }
    await handler(path, 'handleSave', state)()
    assertLocaleChange(path, 'errorMessage', state.errorMessage, composer, 'project.edit.errors.nameTooLong')
    composer.locale.value = 'en'
    handler(path, 'scanMatchingWorkspaces', state)()
    assertLocaleChange(path, 'workspaceScanFeedback', state.workspaceScanFeedback, composer, 'project.edit.workspaces.addedCount', { count: 2 }, 2)
    assert.deepEqual(state.selectedWorkspaceIds.value, ['a', 'b'])
})

test('worktree validation and backend failures preserve their different display contracts', async () => {
    const path = '../project/WorktreeDialog.vue'
    const composer = createAppI18n().global
    const state = {
        errorMessage: ref(''), isCreating: ref(false), trimmedBranch: ref(''), localPath: ref('/worktree'),
        branchIsNew: ref(false), parentProject: ref({ id: 'project' }),
        apiFetch: async () => ({ ok: false, json: async () => ({ error: 'Provider error: create_goal' }) }),
    }
    const create = handler(path, 'handleCreate', state)
    await create()
    assertLocaleChange(path, 'errorMessage', state.errorMessage, composer, 'project.worktrees.branchRequired')
    state.trimmedBranch.value = 'feature/user-branch'
    await create()
    const visible = visibleNotice(path, 'errorMessage', state.errorMessage, composer)
    assert.equal(visible.value, 'Provider error: create_goal')
    composer.locale.value = 'en'
    assert.equal(visible.value, 'Provider error: create_goal')
    assert.equal(state.trimmedBranch.value, 'feature/user-branch')
})
