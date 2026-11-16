// frontend/src/providers/gemini/store.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { ref } from 'vue'
import { EFFORT, PERMISSION_MODE, UNTRUSTED_PERMISSION_MODES } from './constants'

export const useGeminiStore = defineStore('gemini', () => {
    // ─── Per-session agent settings defaults ─────────────────────────────
    //
    // Default values applied to new Gemini sessions and used as the fallback
    // for the dropdowns in the message input. Persisted to localStorage and
    // synchronized to the backend via the synced settings orchestrator.
    //
    // Gemini has no thinking / Chrome / fast mode / context choice: the
    // matching refs stay null so the generic readers (project agent
    // defaults, draft resolution) treat them as unset, like the other
    // providers' unsupported fields.

    const defaultModel = ref(null)
    const defaultEffort = ref(null)
    const defaultPermissionMode = ref(null)
    // Default permission mode seeded when a session is created in an
    // UNTRUSTED (or unknown-trust) project. Restricted to the
    // untrusted-allowed set (trust design §13.1).
    const defaultUntrustedPermissionMode = ref(null)
    const defaultContextMax = ref(null)
    const defaultThinking = ref(null)
    const defaultClaudeInChrome = ref(null)
    const defaultFastMode = ref(null)

    function setDefaultModel(value) {
        if (typeof value === 'string' && value.length > 0) defaultModel.value = value
    }
    function setDefaultEffort(value) {
        if (Object.values(EFFORT).includes(value)) defaultEffort.value = value
    }
    function setDefaultPermissionMode(value) {
        if (Object.values(PERMISSION_MODE).includes(value)) defaultPermissionMode.value = value
    }
    function setDefaultUntrustedPermissionMode(value) {
        if (UNTRUSTED_PERMISSION_MODES.includes(value)) defaultUntrustedPermissionMode.value = value
    }

    // ─── Agent settings categories (live/idle/startup) ───────────────────
    //
    // Seeded from the bootstrap payload ``providers.gemini.agent_settings_categories``.

    const agentSettingsCategories = ref({ live: [], idle: [], startup: [] })

    function setAgentSettingsCategories(value) {
        if (!value || typeof value !== 'object') return
        agentSettingsCategories.value = {
            live: Array.isArray(value.live) ? value.live : [],
            idle: Array.isArray(value.idle) ? value.idle : [],
            startup: Array.isArray(value.startup) ? value.startup : [],
        }
    }

    // ─── Model registry ──────────────────────────────────────────────────
    //
    // Seeded from the bootstrap payload ``providers.gemini.model_registry``.

    const modelRegistry = ref([])

    function setModelRegistry(registry) {
        modelRegistry.value = Array.isArray(registry) ? registry : []
    }

    return {
        defaultModel,
        defaultEffort,
        defaultPermissionMode,
        defaultUntrustedPermissionMode,
        defaultContextMax,
        defaultThinking,
        defaultClaudeInChrome,
        defaultFastMode,
        setDefaultModel,
        setDefaultEffort,
        setDefaultPermissionMode,
        setDefaultUntrustedPermissionMode,
        agentSettingsCategories,
        setAgentSettingsCategories,
        modelRegistry,
        setModelRegistry,
    }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useGeminiStore, import.meta.hot))
}
