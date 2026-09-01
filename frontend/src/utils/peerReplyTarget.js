/**
 * The hard cap the backend enforces on a peer message title
 * (`peer_messages.PEER_MESSAGE_TITLE_MAX_CHARS`): an over-long title is
 * REJECTED, never truncated server-side, so the composer must not exceed it.
 */
export const PEER_MESSAGE_TITLE_MAX_CHARS = 100

/**
 * The subject proposed when replying: the parent's, prefixed once.
 *
 * Email's convention — a thread carries one "Re:" whatever its depth, so a
 * reply to a reply does not grow a prefix chain. The result is flattened to a
 * single line and truncated to the backend's cap (an ellipsis, since a
 * proposal the user can still edit beats a rejected send).
 */
export function replySubject(parentTitle, maxChars = PEER_MESSAGE_TITLE_MAX_CHARS) {
    const flat = String(parentTitle || '').replace(/\s+/g, ' ').trim()
    if (!flat) return ''
    const subject = /^re:\s/i.test(flat) ? flat : `Re: ${flat}`
    if (subject.length <= maxChars) return subject
    return `${subject.slice(0, maxChars - 1)}…`
}

/**
 * Choose whether reply-target initialization can use a normal picker candidate
 * or must ask the store's by-id loader for the session.
 */
export function chooseReplyTargetSource(sessionId, candidates) {
    const session = candidates.find(candidate => candidate.id === sessionId)
    if (session) return { kind: 'candidate', session }
    return { kind: 'load', sessionId }
}

/**
 * The delivery picker's non-pagination exclusions. Project list membership and
 * project staleness are deliberately absent: the normal picker can render a
 * worktree or stale-project row when its explicit scope produces that row.
 */
export function isReplyTargetPickerEligible(session, archivedProjectIds) {
    return !!session
        && !session.parent_session_id
        && !session.hidden
        && !session.draft
        && !session.archived
        && !archivedProjectIds.has(session.project_id)
}

/**
 * Restore an eligible hydrated target omitted only by the current page bound.
 * Existing and ineligible targets preserve the exact input array reference.
 */
export function recoverReplyTargetPagination(
    candidates,
    target,
    archivedProjectIds,
    compareSessions,
) {
    if (!isReplyTargetPickerEligible(target, archivedProjectIds)) return candidates
    if (candidates.some(candidate => candidate.id === target.id)) return candidates
    return [...candidates, target].sort(compareSessions)
}

/**
 * Let one browser paint complete before mounting work that can block the main
 * thread. The first frame paints; the second frame resumes the caller.
 */
export function waitForNextPaint(
    scheduleFrame = callback => globalThis.requestAnimationFrame(callback),
) {
    return new Promise(resolve => {
        scheduleFrame(() => scheduleFrame(resolve))
    })
}

/** Whether a pending inbound reply still waits for its local target lookup. */
export function shouldShowReplyTargetPreparation(detail, settled) {
    return !settled
        && detail?.direction === 'in'
        && detail?.status === 'pending'
        && detail?.reply_target != null
}

/** Toggle a delivery mode and identify the first allowed existing-picker activation. */
export function deliveryPickerTransition(
    currentMode,
    requestedMode,
    existingPickerMounted,
    deliveryBlocked = false,
) {
    const mode = currentMode === requestedMode ? null : requestedMode
    return {
        mode,
        prepareExisting: mode === 'existing' && !existingPickerMounted && !deliveryBlocked,
        dismissRefusalConfirmation: true,
    }
}

/** Choose which resolution actions the review dialog can expose. */
export function peerDeliveryActionVisibility(deliveryBlocked, isPending) {
    return {
        delivery: !deliveryBlocked,
        refusal: isPending,
    }
}

/** Identify the one resolution button that owns progress while an action runs. */
export function activePeerResolutionAction(busy, confirmingRefuse, mode) {
    if (!busy) return null
    if (confirmingRefuse) return 'refuse'
    if (mode === 'existing' || mode === 'new') return mode
    return null
}

/** Label the existing-session action from its selection and progress state. */
export function existingSessionActionLabel(hasSelectedSession, isPrefilling) {
    if (isPrefilling) return 'Prefilling…'
    return hasSelectedSession ? 'Prefill session composer' : 'Select a session below'
}
