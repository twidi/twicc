// frontend/src/stores/sessionTreeExpansion.js
// Pinia wrapper around the pure expansion rules (utils/sessionTreeExpansion.js).
// Only `explicit` is persisted, in localStorage; the rest is session-only.

import { ref, watch } from 'vue'
import { defineStore, acceptHMRUpdate } from 'pinia'
import {
    createExpansionState,
    isExpanded as isExpandedRule,
    reconcileExpansion,
    setExpanded,
    revealPath,
} from '../utils/sessionTreeExpansion'

const STORAGE_KEY = 'twicc.sessionTree.explicit'

function loadExplicit() {
    try {
        const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY))
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
    } catch {
        return {}
    }
}

export const useSessionTreeExpansionStore = defineStore('sessionTreeExpansion', () => {
    const state = ref(createExpansionState(loadExplicit()))

    watch(() => state.value.explicit, (explicit) => {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(explicit))
        } catch {
            // Storage unavailable or full: the state stays session-only.
        }
    }, { deep: true })

    const isExpanded = (id) => isExpandedRule(state.value, id)
    const reconcile = (tree) => reconcileExpansion(state.value, tree)
    const toggle = (id) => setExpanded(state.value, id, !isExpandedRule(state.value, id))
    const reveal = (tree, id, previousPath = []) => revealPath(state.value, tree, id, previousPath)

    return { state, isExpanded, reconcile, toggle, reveal }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useSessionTreeExpansionStore, import.meta.hot))
}
