import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { computed, effectScope, reactive } from 'vue'
import { i18n } from '../i18n/index.js'
import { localUiError, localUiMessage, resolveUiMessage } from '../i18n/uiMessages.js'
import { useMcpStore } from './mcp.js'
import { useSummariesStore } from './summaries.js'
import { summaryGenerationAvailability } from '../utils/ttsConfig.js'
import { useTemporarySummaryGeneration } from '../composables/useTemporarySummaryGeneration.js'

function setup(context) {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
    context.after(() => { i18n.global.locale.value = 'en' })
}
const tick = () => new Promise(resolve => setImmediate(resolve))

test('MCP retained errors and writable public strings follow locale without altering server errors', async context => {
    setup(context)
    const store = useMcpStore()
    store.request = async () => new Response('{}', { status: 503 })
    await store.refresh()
    await store.act('test')
    const load = computed(() => store.loadError), action = computed(() => store.error)
    assert.equal(load.value, i18n.global.t('settings.mcp.loadFailed'))
    assert.equal(action.value, i18n.global.t('settings.mcp.actionFailed'))
    i18n.global.locale.value = 'pt-BR'
    assert.equal(load.value, i18n.global.t('settings.mcp.loadFailed'))
    assert.equal(action.value, i18n.global.t('settings.mcp.actionFailed'))
    store.error = localUiMessage('settings.mcp.actionFailed')
    assert.equal(action.value, i18n.global.t('settings.mcp.actionFailed'))
    store.request = async () => new Response('{"error":"Native MCP detail"}', { status: 400 })
    await store.act('test')
    i18n.global.locale.value = 'en'
    assert.equal(action.value, 'Native MCP detail')
})

test('summary lookup and acquisition retain local descriptors and public string errors', async context => {
    setup(context)
    const store = useSummariesStore(), source = { session_id: 's', line_num: 1 }
    store._fetch = async () => new Response('{}', { status: 503 })
    const [result] = await store.resolve([source])
    const lookup = computed(() => store.lookupErrors['message:s:1'])
    const subscription = store.request({ text: 'Captured user text', purpose: 'selection' })
    await assert.rejects(subscription.ready)
    const error = computed(() => subscription.entry.error)
    assert.equal(typeof result.error, 'string')
    assert.equal(typeof error.value, 'string')
    i18n.global.locale.value = 'pt-BR'
    const expected = i18n.global.t('session.summary.httpFailed', { status: 503 })
    assert.equal(lookup.value, expected)
    assert.equal(result.error, expected)
    assert.equal(error.value, expected)
    assert.equal(subscription.entry.source.text, 'Captured user text')
    subscription.release()
    store._fetch = async () => new Response('{"error":"Provider detail"}', { status: 400 })
    const external = store.request({ text: 'Another source', purpose: 'selection' })
    await assert.rejects(external.ready, /Provider detail/)
    i18n.global.locale.value = 'en'
    assert.equal(external.entry.error, 'Provider detail')
    external.release()
})

test('temporary engine evidence retains local lookup descriptors', async context => {
    setup(context)
    const scope = effectScope()
    context.after(() => scope.stop())
    const settings = reactive({ summaryModel: 'openrouter', enabledProviders: [] })
    const message = localUiMessage('session.summary.checkEngineFailed')
    const result = { errorMessage: message, get error() { return resolveUiMessage(this.errorMessage) } }
    const availability = scope.run(() => useTemporarySummaryGeneration({ text: 'Source', purpose: 'selection' },
        settings, reactive({ keyPresent: true }), { resolve: async () => [result] }))
    await tick()
    assert.equal(availability.value.lookupFailed, true)
    i18n.global.locale.value = 'pt-BR'
    assert.equal(availability.value.unavailableReason, i18n.global.t('session.summary.checkEngineFailed'))
})

test('local Error instances retain locale identity when cached', context => {
    setup(context)
    const error = localUiError('session.summary.httpFailed', { status: 504 })
    const message = computed(() => error.message)
    assert.ok(error instanceof Error)
    assert.equal(typeof message.value, 'string')
    i18n.global.locale.value = 'pt-BR'
    assert.equal(message.value, i18n.global.t('session.summary.httpFailed', { status: 504 }))
    assert.equal(error.uiMessage.key, 'session.summary.httpFailed')
})


test('summary routing keeps public reason strings and additive locale descriptors', context => {
    setup(context)
    const limited = summaryGenerationAvailability('openrouter', [], true, false)
    const unavailable = summaryGenerationAvailability('auto', [], false)
    const active = summaryGenerationAvailability('openrouter', [], true, true)
    assert.equal(limited.unavailableReason, i18n.global.t('settings.speech.sourceLimit'))
    assert.equal(unavailable.unavailableReason, i18n.global.t('settings.speech.noSummaryEngine'))
    assert.equal(active.unavailableReason, '')
    assert.equal(active.unavailableReasonMessage, null)
    i18n.global.locale.value = 'pt-BR'
    assert.equal(resolveUiMessage(limited.unavailableReasonMessage), i18n.global.t('settings.speech.sourceLimit'))
    assert.equal(resolveUiMessage(unavailable.unavailableReasonMessage), i18n.global.t('settings.speech.noSummaryEngine'))
})
