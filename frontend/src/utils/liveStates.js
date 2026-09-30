// Live states (visual refresh step 6b, docs/plans/2026-09-30-live-states-design.md §7.1).
// Pure: no Vue, no store.
import { PROCESS_STATE } from '../constants.js'

/** Whether a session's context ring pulses: it works and waits for nobody. */
export function isContextRingLive(processState, pendingRequests) {
    return processState?.state === PROCESS_STATE.ASSISTANT_TURN && !(pendingRequests?.length > 0)
}
