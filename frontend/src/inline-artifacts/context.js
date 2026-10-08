/** Provided by the owning regular session view; native subagent text never uses it. */
export const INLINE_ARTIFACT_CONTEXT = Symbol('inlineArtifactContext')

// The bound shim reports this document's captured generation with ready/height
// messages. contentWindow alone cannot distinguish successive navigations.
export const INLINE_ARTIFACT_RELOAD_QUERY = '_twicc_reload'

/** Identity includes the source session, independently from the owning view/share. */
export function artifactKey(sourceSessionId, artifactId) {
    return JSON.stringify([sourceSessionId, artifactId])
}

/**
 * Adapter contract (no private stores are imported by the shared runtime):
 * documentUrl(descriptor): exact document route, without a reload query.
 * brokerConfig(descriptor): configuration captured for a navigation generation.
 * probe(descriptor, {signal}): Promise<{available, error}> from an exact-route HEAD.
 * retry(descriptor): refresh/retry the current descriptor on explicit Reload.
 * dispose(): settle adapter-owned work when the owning view is destroyed.
 *
 * Attachment contract: placeholderEl, clipEl, isSuppressed(), focusConversation().
 * Optional isVisible() distinguishes duplicate presentations; attachment order
 * selects the first visible, unsuppressed placement. setVisible() supplies a
 * visibility fallback only for attachments without a getter. A false hint
 * never hides another attachment whose getter is true. setActive() gates
 * the entire owning view.
 */
