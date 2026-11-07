import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { localizedContentManifest } from './contentMetadata.js'
import { isTipAvailable } from './tipsConstraints'

// Sibling of the tips store (stores/tips.js). Help pages are surfaced as a
// modal dialog driven by explicit code events / buttons — there is no
// random scheduler, so no cooldown / "next" plumbing here. The constraint
// evaluation (platform / os / providers) is shared with tips via
// isTipAvailable; help meta carries the same fields.
export const useHelpStore = defineStore('help', () => {
    // Read-only manifest pushed by the backend at boot / WS connect.
    // Shape: { <key>: { title, platform, os, providers_any, providers_all } }
    const rawManifest = ref({})
    const manifest = computed(() => localizedContentManifest('help', rawManifest.value))

    // Synced seen state: { <key>: ISO timestamp }
    const seenHelp = ref({})

    // The help page currently shown in the global HelpDialog (or null).
    const currentHelpKey = ref(null)

    // Whether the current open shows the "Don't show this again" switch.
    // Decided per call site (open()'s showSwitch arg) — not in front-matter.
    // When shown, closing commits the seen-state; when hidden, it is left
    // untouched.
    const currentHelpShowSwitch = ref(true)

    function applyManifest(remote) {
        rawManifest.value = remote || {}
    }

    function applySeenHelp(remote) {
        seenHelp.value = remote || {}
    }

    function _sendSeenHelp() {
        // Lazy import to avoid circular dependency (store -> composable -> store).
        import('../composables/useWebSocket').then(({ sendUpdateSeenHelp }) => {
            sendUpdateSeenHelp(seenHelp.value)
        })
    }

    function markSeen(key) {
        if (!manifest.value[key]) return
        seenHelp.value = { ...seenHelp.value, [key]: new Date().toISOString() }
        _sendSeenHelp()
    }

    function unmarkSeen(key) {
        if (!(key in seenHelp.value)) return
        const next = { ...seenHelp.value }
        delete next[key]
        seenHelp.value = next
        _sendSeenHelp()
    }

    function getAvailableHelp(env) {
        return Object.entries(manifest.value)
            .filter(([_, meta]) => isTipAvailable(meta, env))
            .map(([key, meta]) => ({ key, ...meta }))
    }

    // Open a help page in the global dialog. `showSwitch` controls whether
    // the "Don't show this again" switch appears — and therefore whether
    // closing commits the seen-state. Defaults to true.
    function open(key, { showSwitch = true } = {}) {
        if (!manifest.value[key]) return
        currentHelpShowSwitch.value = showSwitch
        currentHelpKey.value = key
    }

    function close() {
        currentHelpKey.value = null
        currentHelpShowSwitch.value = true
    }

    // Fire a help page automatically from a code event. No-ops unless the
    // page exists, the env matches its constraints, nothing is already open,
    // and the page is not yet seen. Automatic opens always show the switch
    // so the user can dismiss it.
    function maybeAutoShow(key, env) {
        const meta = manifest.value[key]
        if (!meta) return
        if (currentHelpKey.value !== null) return
        if (env && !isTipAvailable(meta, env)) return
        if (key in seenHelp.value) return
        open(key, { showSwitch: true })
    }

    return {
        manifest, seenHelp, currentHelpKey, currentHelpShowSwitch,
        applyManifest, applySeenHelp,
        markSeen, unmarkSeen,
        getAvailableHelp, open, close, maybeAutoShow,
    }
})
