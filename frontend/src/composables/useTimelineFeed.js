// frontend/src/composables/useTimelineFeed.js
//
// The Timeline page's feed: the pure controller of utils/timeline/feed.js with a reactive state and the
// real network call. One instance per page (the page is kept alive per scope, so the list survives a
// scope switch).

import { reactive } from 'vue'
import { apiFetch } from '../utils/api'
import { buildTimelineUrl, createFeedState, createTimelineFeed } from '../utils/timeline/feed'

/**
 * @param {{projectIds: string[], query: Object<string, string>, before: (string|null), afterId: (number|null)}} params
 *   `query`: the filter parameters (`utils/timeline/filters.js` `toQuery`).
 * @returns {Promise<Object>} The response body of `GET /api/timeline/`.
 */
export async function fetchTimelinePage({ projectIds, query, before, afterId }) {
    const response = await apiFetch(buildTimelineUrl({ projectIds, query, before, afterId }))
    if (!response.ok) throw new Error(`Timeline request failed (${response.status})`)
    return response.json()
}

export function useTimelineFeed() {
    return createTimelineFeed({
        fetchPage: fetchTimelinePage,
        state: reactive(createFeedState()),
        onLiveError: (error) => console.warn('Timeline live fetch failed', error),
    })
}
