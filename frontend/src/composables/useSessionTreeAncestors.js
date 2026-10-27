// Fetches the ancestors that sidebar tree mode needs but the store lacks.
//
// Rules (spec §11):
// - a 404 (hidden or sub-agent parent) is final: the id goes to `notFound`;
// - a thrown error only clears the in-flight mark. The fetch is retried when
//   the missing-id list or the candidate count changes (no timer, no loop);
// - an id already in flight is not requested twice.

import { computed, watch } from 'vue'
import { useDataStore } from '../stores/data'
import { findMissingAncestorIds } from '../utils/sessionTree.js'

/**
 * @param {import('vue').Ref<Object[]>} candidates - tree candidate sessions
 * @param {import('vue').Ref<boolean>} enabled - tree mode on/off
 */
export function useSessionTreeAncestors(candidates, enabled) {
    const store = useDataStore()
    const inFlight = new Set()
    const notFound = new Set()

    const missingIds = computed(() => {
        if (!enabled.value) return []
        return findMissingAncestorIds(
            candidates.value,
            (id) => store.sessions[id] ?? null,
            notFound,
        )
    })
    // Primitive sources: unrelated store updates do not re-trigger the watcher.
    const missingKey = computed(() => missingIds.value.join(','))
    const candidateCount = computed(() => candidates.value.length)

    const run = () => {
        for (const id of missingIds.value) {
            if (inFlight.has(id) || notFound.has(id)) continue
            inFlight.add(id)
            store.loadSessionById(id)
                .then((session) => {
                    if (session === null) notFound.add(id)
                })
                .catch(() => {
                    // Already logged by the store. Retried on the next change.
                })
                .finally(() => {
                    inFlight.delete(id)
                })
        }
    }

    watch([missingKey, candidateCount], run, { immediate: true })
}
