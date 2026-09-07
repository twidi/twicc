// frontend/src/composables/useAutoApplyTitle.js
//
// Module-level reactive watcher that drives the "auto-apply suggested title"
// flow. Lives for the entire app lifetime (no setup()/component scope) so it
// survives the router.replace that bindDraftSession performs when a Codex
// draft turns into its canonical session — putting the same watcher inside
// the SessionView component dies along with the draft component, leaving the
// suggestion stranded.
//
// Inputs (all reactive sources on useDataStore):
//   - localState.pendingTitleAutoApply : map { sid: { projectId } } populated
//     by handleNeedsTitle when titleAutoApply is enabled.
//   - localState.titleSuggestions      : map populated by handleTitleSuggested
//     on the WS reply.
//   - sessions                          : becomes "real" (session.draft=false)
//     after the watcher has caught up.
//
// On match, applies the title locally (if untitled), then once the session is
// in DB calls renameSession() to persist via PATCH and drops the pending entry.

import { watchEffect } from 'vue'

import { useDataStore } from '../stores/data'

let started = false

export function startAutoApplyTitleWatcher() {
    if (started) return
    started = true

    const store = useDataStore()

    watchEffect(() => {
        const pending = store.localState.pendingTitleAutoApply
        for (const sid of Object.keys(pending)) {
            const meta = pending[sid]
            const suggestion = store.getTitleSuggestion(sid)
            const suggestionEntry = store.getTitleSuggestionEntry(sid)
            const session = store.getSession(sid)

            // Session not yet in the store (draft still hydrating, or
            // canonical hasn't landed via session_updated): wait.
            if (!session) continue

            // Generation definitively failed (entry exists but suggestion is
            // null after every backend retry) — drop the pending entry so we
            // stop iterating over it on every reactive flush.
            if (suggestionEntry && !suggestion) {
                store.clearPendingTitleAutoApply(sid)
                continue
            }

            // No suggestion yet — keep watching.
            if (!suggestion) continue

            // Apply locally if the session has no title yet. Respect a manual
            // rename or any title the watcher already absorbed from JSONL.
            if (!session.title) {
                session.title = suggestion
            }

            // Persist via API once the session is a real DB row, then drop
            // the pending entry. ``renameSession`` does its own optimistic
            // update + PATCH and propagates the protect-title machinery on
            // the backend.
            if (session.ephemeral) {
                store.setDraftTitle(sid, session.title || suggestion)
                store.clearPendingTitleAutoApply(sid)
            } else if (!session.draft) {
                store.clearPendingTitleAutoApply(sid)
                store.renameSession(meta.projectId, sid, suggestion)
            }
        }
    })
}
