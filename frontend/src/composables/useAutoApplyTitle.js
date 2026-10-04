// frontend/src/composables/useAutoApplyTitle.js
//
// Module-level watcher for local ephemeral title suggestions. It survives
// session navigation and ephemeral canonical-id binding.
//
// Inputs (all reactive sources on useDataStore):
//   - localState.pendingTitleAutoApply : map { sid: { projectId } } populated
//     by handleNeedsTitle for ephemeral sessions when automation is enabled.
//   - localState.titleSuggestions      : map populated by handleTitleSuggested
//     on the WS reply.
//   - sessions                          : local ephemeral session objects.
//
// Apply locally if untitled, save through setDraftTitle, and clear the intent.
// Real-session title automation belongs to the backend.

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
            const suggestion = store.getTitleSuggestion(sid)
            const suggestionEntry = store.getTitleSuggestionEntry(sid)
            const session = store.getSession(sid)

            // Wait while the local session hydrates.
            if (!session) continue

            // Discard stale real-session intents before changing any title.
            if (!session.ephemeral) {
                store.clearPendingTitleAutoApply(sid)
                continue
            }

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

            store.setDraftTitle(sid, session.title || suggestion)
            store.clearPendingTitleAutoApply(sid)
        }
    })
}
