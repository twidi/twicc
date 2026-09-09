import { defineStore } from 'pinia'
import { ref } from 'vue'
import { isTipAvailable } from './tipsConstraints'

const LS_ENABLED_KEY = 'twicc.tips.enabled'

export const useTipsStore = defineStore('tips', () => {
    // Read-only manifest pushed by the backend at boot / WS connect.
    // Shape : { <key>: { title, platform, os, providers_any, providers_all } }
    const manifest = ref({})

    // Synced seen state : { <key>: ISO timestamp }
    const seenTips = ref({})

    // Currently displayed toast tip key (in-memory, per-tab).
    // Watched by TipToast.vue to swap content in-place via the "Next tip" button.
    const currentToastTipKey = ref(null)

    // Epoch ms. The scheduler refuses to show a tip while Date.now() < nextEligibleTime.
    // Set at scheduler init to (now + FIRST_TIP_DELAY_MS), then on every voluntary
    // dismiss to (now + TIP_COOLDOWN_MS). In-memory, per-tab.
    const nextEligibleTime = ref(0)

    // Per-device on/off, persisted in localStorage. Default ON.
    const lsEnabled = localStorage.getItem(LS_ENABLED_KEY)
    const enabled = ref(lsEnabled === null ? true : lsEnabled === 'true')

    function applyManifest(remote) {
        manifest.value = remote || {}
    }

    function applySeenTips(remote) {
        seenTips.value = remote || {}
    }

    function setEnabled(value) {
        enabled.value = !!value
        localStorage.setItem(LS_ENABLED_KEY, String(enabled.value))
    }

    function _sendSeenTips() {
        // Lazy import to avoid circular dependency (store → composable → store).
        import('../composables/useWebSocket').then(({ sendUpdateSeenTips }) => {
            sendUpdateSeenTips(seenTips.value)
        })
    }

    function markSeen(key) {
        if (!manifest.value[key]) return
        // Refresh timestamp on every call : a user re-opening an already-seen tip
        // from the settings list legitimately updates the "Seen X ago" ordering.
        seenTips.value = { ...seenTips.value, [key]: new Date().toISOString() }
        _sendSeenTips()
    }

    function resetAllSeen() {
        if (Object.keys(seenTips.value).length === 0) return
        seenTips.value = {}
        _sendSeenTips()
    }

    function getAvailableTips(env) {
        return Object.entries(manifest.value)
            .filter(([_, tip]) => isTipAvailable(tip, env))
            .map(([key, tip]) => ({ key, ...tip }))
    }

    function getCandidates(env) {
        return getAvailableTips(env).filter((t) => !(t.key in seenTips.value))
    }

    function pickRandom(candidates, exclude = []) {
        const pool = candidates.filter((t) => !exclude.includes(t.key))
        if (pool.length === 0) return null
        return pool[Math.floor(Math.random() * pool.length)]
    }

    return {
        manifest, seenTips, currentToastTipKey, nextEligibleTime, enabled,
        applyManifest, applySeenTips, setEnabled,
        markSeen, resetAllSeen,
        getAvailableTips, getCandidates, pickRandom,
    }
})
