import { localUiMessage } from '../i18n/uiMessages.js'
import { remoteUiError } from '../i18n/remoteMessages.js'
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
    if (!response.ok) {
        let data
        try {
            data = await response.json()
        } catch {
            // A proxy or server can return a non-JSON error page.
        }
        throw remoteUiError(data, localUiMessage('session.timeline.requestFailed', { status: response.status }))
    }
    return response.json()
}

export function useTimelineFeed() {
    return createTimelineFeed({
        fetchPage: fetchTimelinePage,
        state: reactive(createFeedState()),
        onLiveError: (error) => console.warn('Timeline live fetch failed', error),
    })
}
