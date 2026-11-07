import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { parse as parseTemplate } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { i18n, loadLocaleMessages } from '../../i18n/index.js'
import { localUiMessage, localUiError, resolveUiMessage } from '../../i18n/uiMessages.js'
import { remoteUiMessage } from '../../i18n/remoteMessages.js'
import { normalizeBrowserUrl } from '../../utils/browserUrl.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

const files = new Map()
function component(file) {
    if (!files.has(file)) {
        const source = readFileSync(new URL(file, import.meta.url), 'utf8')
        const { descriptor } = parse(source, { filename: file })
        const ast = compileScript(descriptor, { id: file }).scriptSetupAst
        files.set(file, { descriptor, ast })
    }
    return files.get(file)
}
function handler(file, name, bindings) {
    const { descriptor, ast } = component(file)
    const node = ast.find(node => node.type === 'FunctionDeclaration' && node.id.name === name)
    assert.ok(node, `${file}: ${name}`)
    return runInNewContext(`(${descriptor.scriptSetup.content.slice(node.start, node.end)})`, {
        localUiMessage, localUiError, resolveUiMessage, remoteUiMessage, URLSearchParams, ...bindings,
    })
}
function renderFragment(file, predicate) {
    const nodes = []
    function visit(node) {
        if (predicate(node)) nodes.push(node)
        for (const child of node.children ?? []) visit(child)
    }
    visit(parseTemplate(component(file).descriptor.template.content))
    assert.ok(nodes.length, `find fragment in ${file}`)
    return nodes.map(node => {
        const compiled = compileTemplate({
            source: node.loc.source.replace(/v-else-if=/g, 'v-if='), filename: file, id: 'cached-message',
            compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') },
        })
        assert.deepEqual(compiled.errors, [])
        return runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
    })
}
async function renderCallout(file, bindings) {
    const field = 'errorMessage' in bindings ? 'errorMessage' : 'error'
    const [render] = renderFragment(file, node => node.tag === 'wa-callout' && node.loc.source.includes(`resolveUiMessage(${field})`))
    const app = Vue.createSSRApp({ render, setup: () => ({ resolveUiMessage, ...bindings }) })
    app.use(i18n)
    return renderToString(app)
}
async function assertLocaleSwitch(file, bindings, message) {
    const original = i18n.global.locale.value
    try {
        const outputs = []
        for (const locale of ['en', 'pt-BR', 'en']) {
            i18n.global.locale.value = locale
            const html = await renderCallout(file, bindings)
            const text = resolveUiMessage(message)
            assert.ok(html.includes(text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')), html)
            outputs.push(text)
        }
        assert.notEqual(outputs[0], outputs[1], 'cached local message changes without repeating the action')
        assert.equal(outputs[0], outputs[2])
    } finally { i18n.global.locale.value = original }
}

test('preset required, reserved and duplicate errors follow locale while the form remains invalid', async () => {
    const errorMessage = Vue.ref('')
    const formData = Vue.ref({ name: '' })
    const save = handler('./AgentSettingsPresetsDialog.vue', 'handleSave', {
        errorMessage, formData, RESERVED_PRESET_NAMES: new Set(['Default']),
        presetsStore: { findIndexByName: () => 1 }, props: { provider: 'codex' }, editIndex: Vue.ref(null),
    })
    for (const name of ['', 'Default', 'Existing']) {
        formData.value.name = name
        save()
        assert.equal(errorMessage.value.type, 'local-ui-message')
        await assertLocaleSwitch('./AgentSettingsPresetsDialog.vue', { errorMessage }, errorMessage.value)
    }
})

test('Changelog retains a local HTTP detail descriptor inside its translated wrapper', async () => {
    const error = Vue.ref(null)
    const open = handler('./ChangelogDialog.vue', 'open', {
        loading: Vue.ref(false), error, versions: Vue.ref([]), selectedVersion: Vue.ref(''),
        currentEntryIdx: Vue.ref(0), renderedHtml: Vue.ref(''), dialogRef: Vue.ref({}), settingsStore: {},
        fetchChangelog: async () => { throw localUiError('help.changelog.errors.fetch', { status: 503 }) },
    })
    await open()
    assert.equal(error.value.params.status, 503)
    await assertLocaleSwitch('./ChangelogDialog.vue', { error }, error.value)
    error.value = 'External host failed'
    for (const locale of ['en', 'pt-BR']) {
        i18n.global.locale.value = locale
        const html = await renderCallout('./ChangelogDialog.vue', { error })
        assert.ok(html.includes('External host failed'))
        assert.ok(html.includes(i18n.global.t('changelog.loadError', { error: error.value })))
    }
    i18n.global.locale.value = 'en'
})

test('notification target HTTP fallback remains local metadata and external diagnostics remain text', async () => {
    const targetTestErrors = Vue.ref({})
    const bindings = {
        targetTestErrors, effectiveTargetUrl: () => 'https://notify.test', testingTargets: Vue.ref({}),
        externalRows: Vue.ref([{ url: 'https://notify.test' }]), liveUrls: Vue.ref({}), persistExternalRows() {},
        fetch: async () => ({ ok: false, status: 503, json: async () => ({}) }),
    }
    await handler('./NotificationSettings.vue', 'testExternalTarget', bindings)(0)
    const message = targetTestErrors.value[0]
    assert.equal(message.params.status, 503)
    const [render] = renderFragment('./NotificationSettings.vue', node => node.tag === 'div' && node.loc.source.startsWith('<div v-if="targetTestErrors[index]"'))
    for (const locale of ['en', 'pt-BR']) {
        i18n.global.locale.value = locale
        const app = Vue.createSSRApp({ render, setup: () => ({ targetTestErrors, index: 0, resolveUiMessage }) })
        app.use(i18n)
        assert.ok((await renderToString(app)).includes(resolveUiMessage(message)))
    }
    bindings.fetch = async () => ({ ok: false, json: async () => ({ error: 'External destination error' }) })
    await handler('./NotificationSettings.vue', 'testExternalTarget', bindings)(0)
    assert.equal(targetTestErrors.value[0], 'External destination error')
    i18n.global.locale.value = 'en'
})

test('search caches local failures as descriptors and preserves server query prose', async () => {
    const error = Vue.ref(null)
    const bindings = {
        query: Vue.ref('word'), results: Vue.ref([]), totalSessions: Vue.ref(0), matchMode: Vue.ref('all'),
        error, offset: Vue.ref(0), isLoading: Vue.ref(false), LIMIT: 20,
        isWorkspaceFilter: Vue.ref(false), filters: {}, dateRange: Vue.ref({}),
        apiFetch: async () => ({ ok: false, status: 503 }),
    }
    await handler('./SearchOverlay.vue', 'performSearch', bindings)()
    await assertLocaleSwitch('./SearchOverlay.vue', { error }, error.value)
    bindings.apiFetch = async () => ({ ok: false, status: 400, json: async () => ({ error: 'Invalid external expression' }) })
    await handler('./SearchOverlay.vue', 'performSearch', bindings)()
    assert.equal(error.value, 'Invalid external expression')
})

test('usage-history HTTP failures survive catch without losing their message descriptor', async () => {
    const errorMessage = Vue.ref('')
    const fetchData = handler('./UsageGraphDialog.vue', 'fetchData', {
        _fetchGeneration: 0, snapshotsRaw: Vue.ref([]), errorMessage,
        isRefreshing: Vue.ref(false), isLoading: Vue.ref(false),
        currentRange: Vue.ref({ days: 7, bucket: 30 }), selectedProvider: Vue.ref('codex'),
        fetch: async () => ({ ok: false }),
    })
    await fetchData()
    assert.equal(errorMessage.value.type, 'local-ui-message')
    await assertLocaleSwitch('./UsageGraphDialog.vue', { errorMessage }, errorMessage.value)
})

test('Android malformed state retains a local descriptor through the refresh catch', async () => {
    const error = Vue.ref('')
    const readState = handler('../sidebar/AndroidAppMenu.vue', 'readState', { client: { request: async () => ({}) } })
    const refresh = handler('../sidebar/AndroidAppMenu.vue', 'refreshState', { error, readState, disposed: false })
    await refresh()
    await assertLocaleSwitch('../sidebar/AndroidAppMenu.vue', { error }, error.value)
})

test('bulk archive preset and failed-request fallbacks update without rerunning the request', async () => {
    const error = Vue.ref(null)
    const base = { error, currentPreset: Vue.ref('invalid'), presetToDate: () => null }
    await handler('../sidebar/BulkArchiveConfirmDialog.vue', 'refreshCount', base)()
    await assertLocaleSwitch('../sidebar/BulkArchiveConfirmDialog.vue', { error }, error.value)
    base.currentPreset.value = '7d'
    const bindings = { ...base, presetToDate: () => '2026-11-01', count: Vue.ref(null),
        hasArchivedInScope: Vue.ref(false), abortController: null, AbortController,
        currentTitleQuery: Vue.ref(''), currentIncludeArchivedProjects: Vue.ref(false), props: { scope: {} },
        dataStore: { bulkArchiveSessions: async () => { throw {} } },
    }
    await handler('../sidebar/BulkArchiveConfirmDialog.vue', 'refreshCount', bindings)()
    assert.equal(error.value.key, 'session.bulkArchive.errors.count')
    await assertLocaleSwitch('../sidebar/BulkArchiveConfirmDialog.vue', { error }, error.value)
})

test('workspace scan plural metadata and browser URL errors reach the parent unchanged', async () => {
    const scanFeedback = Vue.ref('')
    const formData = Vue.ref({ name: 'Workspace', projectIds: [], autoProjectPatterns: ['*'] })
    const scan = handler('../workspace/WorkspaceManageDialog.vue', 'scanNow', {
        scanFeedback, formData, dataStore: { getListableProjects: [{ id: 'a', directory: '/a' }, { id: 'b', directory: '/b' }] },
        matchPattern: () => true, clearScanFeedbackLater() {},
    })
    scan()
    assert.equal(scanFeedback.value.count, 2)
    const feedback = Vue.computed(() => resolveUiMessage(scanFeedback.value))
    i18n.global.locale.value = 'en'
    assert.equal(feedback.value, '2 projects added')
    i18n.global.locale.value = 'pt-BR'
    assert.equal(feedback.value, '2 projetos adicionados')
    const errorMessage = Vue.ref('')
    const rows = Vue.ref([{ url: '://invalid', label: '' }])
    const getEntries = handler('../browser/BrowserUrlListEditor.vue', 'getEntries', { rows, normalizeBrowserUrl })
    const save = handler('../workspace/WorkspaceManageDialog.vue', 'handleSave', {
        errorMessage, formData, workspacesStore: { getAllWorkspaces: [] }, browserUrlsEditorRef: Vue.ref({ getEntries }),
    })
    for (const urls of [['://invalid'], ['https://example.test', 'https://example.test/']]) {
        rows.value = urls.map(url => ({ url, label: '' }))
        save()
        assert.equal(errorMessage.value.type, 'local-ui-message')
        await assertLocaleSwitch('../workspace/WorkspaceManageDialog.vue', { errorMessage }, errorMessage.value)
        await assertLocaleSwitch('../project/ProjectEditDialog.vue', { errorMessage }, errorMessage.value)
    }
    i18n.global.locale.value = 'en'
})

test('login keeps auth error metadata instead of caching its current translation', async () => {
    const error = Vue.ref('')
    const message = localUiMessage('auth.login.errors.invalidPassword')
    const submit = handler('../../views/LoginView.vue', 'handleSubmit', {
        error, password: Vue.ref('bad'), loading: Vue.ref(false),
        authStore: { login: async () => ({ success: false, error: 'Invalid password', errorMessage: message }) },
        focusPasswordInput: async () => {},
    })
    await submit()
    assert.equal(Vue.toRaw(error.value), message)
    await assertLocaleSwitch('../../views/LoginView.vue', { error }, message)
})

test('command target messages can move their complete rich project slot in both palette modes', async () => {
    const renders = renderFragment('./CommandPalette.vue', node => node.tag === 'i18n-t' && node.loc.source.includes('command-target-line'))
    assert.equal(renders.length, 2)
    const instance = i18n
    instance.global.mergeLocaleMessage('en', { 'navigation.commands.newSessionInTarget': 'New Session in {target}' })
    instance.global.mergeLocaleMessage('pt-BR', { 'navigation.commands.newSessionInTarget': '{target} — destino' })
    for (const render of renders) {
        for (const locale of ['en', 'pt-BR']) {
            instance.global.locale.value = locale
            const app = Vue.createSSRApp({ render, setup: () => ({ t: { messageKey: 'navigation.commands.newSessionInTarget', label: '<project>&', project: {} } }) })
            app.use(instance)
            app.component('ProjectMark', { render: () => Vue.h('b', { class: 'project-mark' }, 'mark') })
            const html = (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
            assert.ok(html.includes('&lt;project&gt;&amp;'))
            assert.match(html, locale === 'en' ? />New Session in <span/ : /<\/span> — destino<\/span>$/)
            assert.ok(html.includes('<b class="project-mark">mark</b>'))
        }
    }
    instance.global.locale.value = 'en'
})

test('credit count parameters use selected locale while cardinal plural counts stay numeric', () => {
    const locale = Vue.ref('en')
    const format = handler('../../views/ProjectView.vue', 'formatCreditCount', { locale })
    assert.equal(format(1234567), '1,234,567')
    locale.value = 'pt-BR'
    assert.equal(format(1234567), '1.234.567')
    assert.equal(format('?'), '?')
    const template = component('../../views/ProjectView.vue').descriptor.template.content
    const creditCalls = [...template.matchAll(/\$t\('usage.extra.credits', \{ count: ([^]*?) \}, ([^]*?)\) \}\}/g)]
    assert.equal(creditCalls.length, 4)
    for (const [, display, plural] of creditCalls) {
        assert.ok(display.startsWith('formatCreditCount('))
        assert.ok(!plural.includes('formatCreditCount'))
    }
})


test('previous dashboard counts use the active locale for both trend directions', async () => {
    const renders = renderFragment('../activity/ActivityDashboard.vue', node =>
        node.tag === 'span' && node.loc.source.includes('period.prevMainValue'))
    assert.equal(renders.length, 2)
    for (const locale of ['en', 'pt-BR']) {
        for (const direction of ['up', 'down']) {
            const outputs = []
            for (const render of renders) {
                const app = Vue.createSSRApp({ render, setup: () => ({ locale, period: {
                    prevMainValue: 1234567, mainTrend: { direction },
                } }) })
                outputs.push(await renderToString(app))
            }
            const displayed = outputs.filter(html => html.includes('<span'))
            assert.equal(displayed.length, 1)
            assert.ok(displayed[0].includes(new Intl.NumberFormat(locale).format(1234567)))
        }
    }
})

test('changelog preview keeps external alt text and a local fallback key', () => {
    let opened
    const unnamed = { src: 'https://example.test/a.png', getAttribute: () => null }
    const named = { src: 'https://example.test/b.png', getAttribute: () => 'External release image' }
    unnamed.parentElement = { querySelectorAll: () => [unnamed, named] }
    const click = handler('./ChangelogDialog.vue', 'onImageClick', {
        openMediaPreview: items => { opened = items },
    })
    click({ currentTarget: unnamed })
    assert.equal(opened[0].name, null)
    assert.equal(opened[0].nameKey, 'changelog.image')
    assert.equal(opened[1].name, 'External release image')
})

test('all extra-credit readouts render grouped locale numbers and keep the plural branch', async () => {
    const renders = renderFragment('../../views/ProjectView.vue', node =>
        node.tag === 'span' && node.loc.source.includes("$t('usage.extra.credits'") &&
        !node.children?.some(child => child.tag === 'span' && child.loc.source.includes("$t('usage.extra.credits'")))
    assert.equal(renders.length, 4)
    const locale = Vue.ref('en')
    const formatCreditCount = handler('../../views/ProjectView.vue', 'formatCreditCount', { locale })
    for (const language of ['en', 'pt-BR']) {
        locale.value = language
        i18n.global.locale.value = language
        for (const render of renders) {
            const app = Vue.createSSRApp({ render, setup: () => ({ formatCreditCount,
                quotaExtraUsage: { usedCredits: 1234567, monthlyLimit: 1234567, remainingCredits: 1234567 },
                quotaExtraUsageRingColor: 'green',
            }) })
            app.use(i18n)
            const html = await renderToString(app)
            assert.ok(html.includes(new Intl.NumberFormat(language).format(1234567)), html)
            assert.ok(html.includes(i18n.global.t('usage.extra.credits', { count: formatCreditCount(1234567) }, 1234567)), html)
        }
    }
    i18n.global.locale.value = 'en'
})
