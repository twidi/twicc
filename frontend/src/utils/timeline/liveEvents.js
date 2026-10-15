// frontend/src/utils/timeline/liveEvents.js
//
// The window events that keep a Timeline page current (display design section 9.2). useWebSocket.js
// dispatches them; the page binds them to its feed. Pure: the event target is handed in.
//
//   twicc:timeline-changed  {latestId}  the server signal: a new event exists.
//   twicc:ws-reconnected                 the socket reopened after an outage (the existing event).
//   twicc:ws-first-open                  the socket reached OPEN for the first time (fires only then).
//
// `resync_required` is not handled here: the existing logic reloads the whole page (utils/resync.js).

export const TIMELINE_CHANGED_EVENT = 'twicc:timeline-changed'
export const WS_RECONNECTED_EVENT = 'twicc:ws-reconnected'
export const WS_FIRST_OPEN_EVENT = 'twicc:ws-first-open'

/**
 * @param {EventTarget} target - `window`.
 * @param {{notifyLatest: (id: number) => void, refresh: () => void}} feed
 * @returns {() => void} Removes the listeners.
 */
export function bindTimelineEvents(target, feed) {
    const onChanged = (event) => {
        const latestId = event.detail?.latestId
        if (Number.isInteger(latestId)) feed.notifyLatest(latestId)
    }
    const onOpen = () => feed.refresh()
    target.addEventListener(TIMELINE_CHANGED_EVENT, onChanged)
    target.addEventListener(WS_RECONNECTED_EVENT, onOpen)
    target.addEventListener(WS_FIRST_OPEN_EVENT, onOpen)
    return () => {
        target.removeEventListener(TIMELINE_CHANGED_EVENT, onChanged)
        target.removeEventListener(WS_RECONNECTED_EVENT, onOpen)
        target.removeEventListener(WS_FIRST_OPEN_EVENT, onOpen)
    }
}
