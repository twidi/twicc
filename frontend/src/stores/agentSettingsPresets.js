// Cross-provider agent settings presets store.
//
// Shape: { [provider]: { presets: [...] } }. The on-disk format is
// identical for every provider — this store just keys the in-memory
// state by provider so the AgentSettingsPresetsDialog and the picker can
// render whichever set the calling context cares about.
//
// Wire: seeded from /api/bootstrap/ (providers.<key>.agent_settings_presets)
// and from the WS message ``agent_settings_presets_updated`` (which carries
// ``provider`` + ``config`` in its payload — same convention as
// ``usage_updated``). Mutations go out as ``update_agent_settings_presets``.

import { defineStore, acceptHMRUpdate } from 'pinia'
import { ref } from 'vue'

const PRESET_FIELDS = [
    'model',
    'context_max',
    'effort',
    'thinking',
    'permission_mode',
    'permission_mode_if_untrusted',
    'claude_in_chrome',
    'fast_mode',
]

// Names owned by ``twicc info presets`` (e.g. the synthetic "effective
// defaults" entry). Users cannot pick them for their own presets — the
// dialog rejects matches in handleSave(). Kept in sync with
// ``twicc.agent_settings_presets.RESERVED_PRESET_NAMES``.
export const RESERVED_PRESET_NAMES = new Set(['__defaults__'])

function normalizePreset(raw) {
    const preset = { name: typeof raw?.name === 'string' ? raw.name : '' }
    for (const field of PRESET_FIELDS) {
        preset[field] = raw && field in raw ? raw[field] : null
    }
    return preset
}

export const useAgentSettingsPresetsStore = defineStore('agentSettingsPresets', () => {
    // Keyed by provider wire key. Each entry is the array of normalized presets.
    const byProvider = ref({})
    // Keyed by provider wire key. ``true`` once the first config (bootstrap or
    // WS) has been applied — lets components distinguish "empty list" from
    // "not loaded yet".
    const initializedByProvider = ref({})

    function getPresets(provider) {
        return byProvider.value[provider] ?? []
    }

    function isInitialized(provider) {
        return !!initializedByProvider.value[provider]
    }

    function applyConfig(provider, config) {
        if (!provider) return
        const list = Array.isArray(config?.presets) ? config.presets : []
        byProvider.value[provider] = list.map(normalizePreset)
        initializedByProvider.value[provider] = true
    }

    async function _send(provider) {
        // Lazy import: useWebSocket.js may be loaded after this store; the
        // dynamic import keeps the dep one-way at module-eval time.
        const { sendUpdateAgentSettingsPresets } = await import('../composables/useWebSocket')
        sendUpdateAgentSettingsPresets(provider, { presets: getPresets(provider) })
    }

    function findIndexByName(provider, name, excludeIndex = null) {
        const target = name.trim().toLowerCase()
        return getPresets(provider).findIndex(
            (p, i) => i !== excludeIndex && p.name.trim().toLowerCase() === target
        )
    }

    function findByName(provider, name, excludeIndex = null) {
        const idx = findIndexByName(provider, name, excludeIndex)
        return idx === -1 ? null : getPresets(provider)[idx]
    }

    function add(provider, preset) {
        const list = byProvider.value[provider] ?? []
        list.push(normalizePreset(preset))
        byProvider.value[provider] = list
        _send(provider)
    }

    function update(provider, index, preset) {
        const list = getPresets(provider)
        if (index < 0 || index >= list.length) return
        list.splice(index, 1, normalizePreset(preset))
        _send(provider)
    }

    function remove(provider, index) {
        const list = getPresets(provider)
        if (index < 0 || index >= list.length) return
        list.splice(index, 1)
        _send(provider)
    }

    function duplicate(provider, index) {
        const list = getPresets(provider)
        if (index < 0 || index >= list.length) return
        const source = list[index]
        const baseName = `${source.name} (copy)`
        let candidate = baseName
        let n = 2
        while (findIndexByName(provider, candidate) !== -1) {
            candidate = `${baseName} ${n}`
            n += 1
        }
        const copy = normalizePreset({ ...source, name: candidate })
        list.splice(index + 1, 0, copy)
        _send(provider)
    }

    function reorder(provider, index, target) {
        const list = getPresets(provider)
        if (index === target || index < 0 || index >= list.length || target < 0 || target >= list.length) return
        const [moved] = list.splice(index, 1)
        list.splice(target, 0, moved)
        _send(provider)
    }

    return {
        byProvider,
        initializedByProvider,
        getPresets,
        isInitialized,
        applyConfig,
        findIndexByName,
        findByName,
        add,
        update,
        remove,
        duplicate,
        reorder,
    }
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useAgentSettingsPresetsStore, import.meta.hot))
}
