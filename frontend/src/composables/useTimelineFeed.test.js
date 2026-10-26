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

import { buildTimelineUrl, createFeedState, createTimelineFeed } from '../utils/timeline/feed.js'

function loadComposable(apiFetch) {
    const source = readFileSync(new URL('./useTimelineFeed.js', import.meta.url), 'utf8')
    const body = source.replace(/^import .*$/gm, '').replace(/^export /gm, '')
    return new Function(
        'apiFetch', 'buildTimelineUrl', 'createFeedState', 'createTimelineFeed', 'reactive',
        `${body}\nreturn { fetchTimelinePage, useTimelineFeed }`,
    )(apiFetch, buildTimelineUrl, createFeedState, createTimelineFeed, reactive)
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
