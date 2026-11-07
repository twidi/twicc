// frontend/src/composables/useTimelineFeed.test.js
//
// Run with:  node --test src/composables/useTimelineFeed.test.js   (from the frontend dir)
//
// The composable imports `utils/api` (which pulls the auth store and the router) without extensions, so node
// cannot import it. Like statisticsWiring.test.js, the test evaluates its real source with the network call
// injected, and the real `buildTimelineUrl`.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { reactive } from 'vue'
import { i18n, loadLocaleMessages } from '../i18n/index.js'
import { localUiMessage } from '../i18n/uiMessages.js'
import { remoteUiError } from '../i18n/remoteMessages.js'

import { buildTimelineUrl, createFeedState, createTimelineFeed } from '../utils/timeline/feed.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

function loadComposable(apiFetch) {
    const source = readFileSync(new URL('./useTimelineFeed.js', import.meta.url), 'utf8')
    const body = source.replace(/^import .*$/gm, '').replace(/^export /gm, '')
    return new Function(
        'apiFetch', 'buildTimelineUrl', 'createFeedState', 'createTimelineFeed', 'reactive', 'localUiMessage', 'remoteUiError',
        `${body}\nreturn { fetchTimelinePage, useTimelineFeed }`,
    )(apiFetch, buildTimelineUrl, createFeedState, createTimelineFeed, reactive, localUiMessage, remoteUiError)
}

const okBody = { events: [], has_more: false, truncated: false, latest_id: null, sessions: {} }

test('fetchTimelinePage puts the filter query into the URL', async () => {
    const urls = []
    const { fetchTimelinePage } = loadComposable(async (url) => {
        urls.push(url)
        return { ok: true, json: async () => okBody }
    })
    const body = await fetchTimelinePage({
        projectIds: ['p1'], query: { view: 'waits_problems', families: 'sessions,turns' }, before: null, afterId: 4,
    })
    assert.deepEqual(body, okBody)
    assert.deepEqual(urls, ['/api/timeline/?project_ids=p1&view=waits_problems&families=sessions%2Cturns&after_id=4'])
})

test('the feed of the composable sends the query of setScope on every request', async () => {
    const urls = []
    const { useTimelineFeed } = loadComposable(async (url) => {
        urls.push(url)
        return { ok: true, json: async () => ({ ...okBody, latest_id: 3 }) }
    })
    const feed = useTimelineFeed()
    feed.setScope([], { session: 's1' })
    await feed.idle()
    feed.refresh()
    await feed.idle()
    assert.deepEqual(urls, ['/api/timeline/?session=s1', '/api/timeline/?session=s1&after_id=3'])
})

test('fetchTimelinePage rejects on a failed response', async () => {
    const { fetchTimelinePage } = loadComposable(async () => ({ ok: false, status: 500 }))
    await assert.rejects(fetchTimelinePage({ projectIds: [], query: {}, before: null, afterId: null }), /500/)
})


test('retained failed page message follows locale changes with its status intact', async context => {
    context.after(() => { i18n.global.locale.value = 'en' })
    const { useTimelineFeed } = loadComposable(async () => ({ ok: false, status: 503 }))
    const feed = useTimelineFeed()
    feed.setScope([], {})
    await feed.idle()
    const error = feed.state.loadError.error
    assert.ok(error instanceof Error)
    assert.equal(error.message, i18n.global.t('session.timeline.requestFailed', { status: 503 }))
    i18n.global.locale.value = 'pt-BR'
    assert.equal(error.message, i18n.global.t('session.timeline.requestFailed', { status: 503 }))
})

test('a retained backend validation error follows locale changes without another request', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    i18n.global.locale.value = 'en'
    let requests = 0
    const params = { name: 'limit', value: 'invalid' }
    const { useTimelineFeed } = loadComposable(async () => {
        requests++
        return { ok: false, status: 400, json: async () => ({
            error: 'limit must be an integer, got invalid.', code: 'invalid_limit',
            ui_message: { key: 'backend.timeline.integer', params },
        }) }
    })
    const feed = useTimelineFeed()
    feed.setScope([], {})
    await feed.idle()
    const error = feed.state.loadError.error
    assert.equal(error.code, 'invalid_limit')
    assert.equal(error.message, i18n.global.t('backend.timeline.integer', params))
    i18n.global.locale.value = 'pt-BR'
    assert.equal(error.message, i18n.global.t('backend.timeline.integer', params))
    assert.equal(feed.state.loadError.error, error)
    assert.equal(requests, 1)
})

test('non-JSON and empty failures retain the localized HTTP fallback', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    for (const json of [
        async () => { throw new SyntaxError('HTML response') },
        async () => ({}),
        async () => ({ ui_message: { key: 'unknown.key', params: {} } }),
        async () => ({ ui_message: { key: 'backend.timeline.integer', params: [] } }),
        async () => null,
    ]) {
        i18n.global.locale.value = 'en'
        const { fetchTimelinePage } = loadComposable(async () => ({ ok: false, status: 502, json }))
        let failure
        await assert.rejects(fetchTimelinePage({ projectIds: [], query: {} }), error => {
            failure = error
            return error.message === i18n.global.t('session.timeline.requestFailed', { status: 502 })
        })
        i18n.global.locale.value = 'pt-BR'
        assert.equal(failure.message, i18n.global.t('session.timeline.requestFailed', { status: 502 }))
    }
})

test('unknown and malformed metadata preserve backend diagnostics across locale changes', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    for (const payload of [
        { error: 'Unrecognized backend prose', ui_message: { key: 'unknown.key', params: {} } },
        { error: 'Malformed descriptor', ui_message: { key: 'backend.timeline.integer', params: [] } },
        { message: 'Backend message detail', ui_message: { key: 'unknown.key', params: {} } },
    ]) {
        i18n.global.locale.value = 'en'
        const { fetchTimelinePage } = loadComposable(async () => ({ ok: false, status: 502, json: async () => payload }))
        const diagnostic = payload.error ?? payload.message
        let failure
        await assert.rejects(fetchTimelinePage({ projectIds: [], query: {} }), error => {
            failure = error
            return error.message === diagnostic
        })
        assert.equal(failure.uiMessage, undefined)
        i18n.global.locale.value = 'pt-BR'
        assert.equal(failure.message, diagnostic)
    }
})
