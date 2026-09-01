// frontend/src/main.js

// Theme management - must be initialized before CSS imports to prevent flash
import { initTheme } from './utils/theme'
initTheme()

// Web Awesome base styles and themes (all free themes loaded for runtime switching)
import '@awesome.me/webawesome/dist/styles/webawesome.css';
import '@awesome.me/webawesome/dist/styles/themes/awesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
import '@awesome.me/webawesome/dist/styles/themes/shoelace.css'
// Shared transcript CSS tokens (also imported by the share bundle — design §8.8).
import './styles/transcript-tokens.css'
import '@awesome.me/webawesome/dist/components/badge/badge.js'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/button-group/button-group.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/card/card.js'
import '@awesome.me/webawesome/dist/components/comparison/comparison.js'
import '@awesome.me/webawesome/dist/components/divider/divider.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/progress-bar/progress-bar.js'
import '@awesome.me/webawesome/dist/components/progress-ring/progress-ring.js'
import '@awesome.me/webawesome/dist/components/option/option.js'
import '@awesome.me/webawesome/dist/components/select/select.js'
import '@awesome.me/webawesome/dist/components/radio-group/radio-group.js'
import '@awesome.me/webawesome/dist/components/radio/radio.js'
import '@awesome.me/webawesome/dist/components/skeleton/skeleton.js'
import '@awesome.me/webawesome/dist/components/spinner/spinner.js'
import '@awesome.me/webawesome/dist/components/split-panel/split-panel.js'
import '@awesome.me/webawesome/dist/components/switch/switch.js'
import '@awesome.me/webawesome/dist/components/tag/tag.js'
import '@awesome.me/webawesome/dist/components/details/details.js'
import '@awesome.me/webawesome/dist/components/tab/tab.js'
import '@awesome.me/webawesome/dist/components/tab-group/tab-group.js'
import '@awesome.me/webawesome/dist/components/tab-panel/tab-panel.js'
import '@awesome.me/webawesome/dist/components/popover/popover.js'
import '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'
import '@awesome.me/webawesome/dist/components/slider/slider.js'
import '@awesome.me/webawesome/dist/components/dialog/dialog.js'
import '@awesome.me/webawesome/dist/components/dropdown/dropdown.js'
import '@awesome.me/webawesome/dist/components/dropdown-item/dropdown-item.js'
import '@awesome.me/webawesome/dist/components/input/input.js'
import '@awesome.me/webawesome/dist/components/color-picker/color-picker.js'
import '@awesome.me/webawesome/dist/components/textarea/textarea.js'
import '@awesome.me/webawesome/dist/components/checkbox/checkbox.js'
import '@awesome.me/webawesome/dist/components/relative-time/relative-time.js'
import '@awesome.me/webawesome/dist/components/popup/popup.js'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createNotivue } from 'notivue'
import { router } from './router'
import App from './App.vue'
import { applyDefaultSettings, initSettings } from './stores/settings'
import { setTwiccLaunchPrefix } from './utils/twiccLaunch'
import { useAuthStore } from './stores/auth'
import { useDataStore } from './stores/data'
import { useCodeCommentsStore } from './stores/codeComments'
import { useWorkspacesStore } from './stores/workspaces'
import { useTerminalConfigStore } from './stores/terminalConfig'
import { useMessageSnippetsStore } from './stores/messageSnippets'
import { useTipsStore } from './stores/tips'
import { useHelpStore } from './stores/help'
import { useBenchmarksStore } from './stores/benchmarks'
import { useAgentSettingsPresetsStore } from './stores/agentSettingsPresets'
import { getProviderStore } from './providers'
import { computeUsageData } from './utils/usage'

// Notivue CSS
import 'notivue/notification.css'
import 'notivue/animations.css'

// CodeMirror search panel overrides (Web Awesome themed)
import './styles/codemirror-search.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// Configure Notivue toast system
const notivue = createNotivue({
    position: 'top-center',
    limit: 3,
    enqueue: true,
    pauseOnHover: true,
    pauseOnTabChange: false,
    // NOTE: Do NOT set duration in 'global' — Notivue merges configs as
    // { ...typeConfig, ...globalConfig, ...pushOptions }, so a global duration
    // would override all type-specific durations.
    notifications: {
        success: {
            duration: 5000
        },
        info: {
            duration: 5000
        },
        warning: {
            duration: 15000
        },
        error: {
            duration: 20000
        },
        promise: {
            duration: Infinity
        }
    }
})
app.use(notivue)

// Resolve authentication before fetching any protected data. /api/bootstrap/
// is behind the password-auth middleware, so on a locked instance we must
// send the user to /login first (the router guard handles that). Once the
// user logs in, LoginView triggers a full page reload so this whole init
// cycle re-runs with an authenticated session.
const authStore = useAuthStore()
await authStore.checkAuth()

// An unprotected instance (no password) refuses non-local access: there's
// nothing to authenticate against, so it must not be reachable over the
// network. The backend tells us via /api/auth/check/. Render a terminal screen
// here — before mounting Vue or fetching any protected data — explaining how to
// enable access by setting a password.
if (authStore.accessDenied) {
    const cmd = authStore.setPasswordCommand || 'twicc password set'
    const esc = (s) => String(s).replace(/[&<>"']/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ))
    document.getElementById('app').innerHTML = `
        <div style="display:flex;align-items:center;justify-content:center;min-height:100vh;padding:2rem;font-family:system-ui,sans-serif">
            <div style="max-width:520px;padding:2rem;border-radius:12px;background:#422006;border:1px solid #854d0e;color:#fcd34d">
                <h2 style="margin:0 0 .75rem;font-size:1.25rem;color:#fde68a">Access blocked</h2>
                <p style="margin:0 0 1rem;line-height:1.5">
                    For your security, TwiCC refuses remote access while it isn't
                    protected by a password. Set one on the machine running TwiCC,
                    restart it, then reload this page:
                </p>
                <pre id="twicc-blocked-cmd" title="Click to copy" style="margin:0;padding:.75rem 1rem;border-radius:8px;background:#1c1207;color:#fde68a;overflow:auto;text-wrap:wrap;font-size:.9rem;cursor:pointer"><code>${esc(cmd)}</code></pre>
                <p id="twicc-blocked-hint" style="margin:.5rem 0 0;font-size:.8rem;opacity:.7">Click the command to copy</p>
            </div>
        </div>`
    const copyCmd = async () => {
        let ok = false
        try {
            // navigator.clipboard only exists in secure contexts; remote access
            // is often plain HTTP, so fall back to execCommand on a temp textarea.
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(cmd)
                ok = true
            } else {
                const ta = document.createElement('textarea')
                ta.value = cmd
                ta.style.position = 'fixed'
                ta.style.opacity = '0'
                document.body.appendChild(ta)
                ta.select()
                ok = document.execCommand('copy')
                document.body.removeChild(ta)
            }
        } catch {
            ok = false
        }
        const hint = document.getElementById('twicc-blocked-hint')
        if (hint) {
            hint.textContent = ok ? 'Copied!' : 'Press Ctrl/Cmd+C to copy'
            if (ok) setTimeout(() => { hint.textContent = 'Click the command to copy' }, 1500)
        }
    }
    document.getElementById('twicc-blocked-cmd')?.addEventListener('click', copyCmd)
    throw new Error('Access blocked — TwiCC is not password protected on a non-local URL')
}

if (!authStore.needsLogin) {
    // Fetch bootstrap data from backend before initializing stores.
    // This single call returns settings, workspaces, terminal config, and message snippets
    // so the UI has everything it needs before mount (without waiting for the WebSocket).
    let bootstrapData
    let bootstrapFailed = false
    try {
        const resp = await fetch('/api/bootstrap/')
        if (resp.ok) {
            bootstrapData = await resp.json()
            const { settings, settings_version, default_settings, dev_mode, uvx_mode, twicc_launch_prefix, providers, disabledProvidersPresent, disabledProviders, providerStates, claudeHybridEnabled } = bootstrapData
            // Seed the launch prefix into its neutral module *before* any
            // store / provider helper is instantiated — providers read it
            // synchronously via ``getTwiccLaunchPrefix()`` (see
            // ``frontend/src/utils/twiccLaunch.js``).
            setTwiccLaunchPrefix(twicc_launch_prefix)
            applyDefaultSettings(default_settings, settings, dev_mode, uvx_mode, settings_version, disabledProvidersPresent, disabledProviders, claudeHybridEnabled)
            // Seed the data store's provider lifecycle map.
            useDataStore().applyProviderStates(providerStates ?? {})
            // Seed each provider's bootstrap-driven state (agent-setting categories
            // for ``classifyAgentSettingsChanges``, model registry for capability
            // and retired-model lookups, agent-setting presets). Optional chaining:
            // providers without one of those concerns simply won't expose the
            // corresponding setter.
            const presetsStore = useAgentSettingsPresetsStore()
            for (const [provider, providerData] of Object.entries(providers ?? {})) {
                // Cross-provider: presets land in the shared keyed store, not on
                // each provider's own store (the on-disk format is identical).
                if (providerData?.agent_settings_presets !== undefined) {
                    presetsStore.applyConfig(provider, providerData.agent_settings_presets)
                }
                const providerStore = getProviderStore(provider)
                if (!providerStore) continue
                if (providerData?.agent_settings_categories) {
                    providerStore.setAgentSettingsCategories?.(providerData.agent_settings_categories)
                }
                if (providerData?.model_registry) {
                    providerStore.setModelRegistry?.(providerData.model_registry)
                }
            }
        } else {
            bootstrapFailed = true
        }
    } catch {
        bootstrapFailed = true
    }
    if (bootstrapFailed) {
        document.getElementById('app').innerHTML = `
            <div style="display:flex;align-items:center;justify-content:center;min-height:100vh;padding:2rem;font-family:system-ui,sans-serif">
                <div style="max-width:480px;padding:2rem;border-radius:12px;background:#451a1a;border:1px solid #7f1d1d;color:#fca5a5">
                    <h2 style="margin:0 0 .75rem;font-size:1.25rem;color:#fecaca">Backend unreachable</h2>
                    <p style="margin:0;line-height:1.5">
                        TwiCC could not connect to the backend server.
                        Try restarting the backend and refreshing this page.
                    </p>
                </div>
            </div>`
        throw new Error('Backend unreachable — cannot fetch bootstrap data')
    }

    // Initialize settings (localStorage persistence, theme, font size, display mode watchers)
    initSettings()

    // Apply bootstrap data to stores so the UI has workspaces, snippets, and terminal config
    // immediately available. The WebSocket will re-push these on (re)connect for live updates.
    useWorkspacesStore().applyWorkspaces(bootstrapData.workspaces)
    useTerminalConfigStore().applyConfig(bootstrapData.terminal_config)
    useMessageSnippetsStore().applyConfig(bootstrapData.message_snippets)
    useTipsStore().applyManifest(bootstrapData.tips_manifest)
    useTipsStore().applySeenTips(bootstrapData.seen_tips)
    useHelpStore().applyManifest(bootstrapData.help_manifest)
    useHelpStore().applySeenHelp(bootstrapData.seen_help)
    useBenchmarksStore().applyBenchmarks(bootstrapData.benchmarks)

    // Hydrate drafts from IndexedDB (async, non-blocking)
    // Order matters: sessions first so draft messages have their session available
    const dataStore = useDataStore()

    // Seed per-provider usage from the bootstrap so the UI can render immediately
    // without waiting for the WS connect-time ``usage_updated`` push. The WS will
    // still send fresh data on (re)connect and at every periodic sync — those
    // updates flow through the same setUsage path.
    {
        for (const [provider, providerData] of Object.entries(bootstrapData.providers ?? {})) {
            if (providerData?.tracks_usage && providerData.usage) {
                const computed = computeUsageData(providerData.usage)
                getProviderStore(provider)?.setUsage(true, 'bootstrap', providerData.usage, computed)
            }
        }
    }

    dataStore.hydrateDraftSessions().then(() => {
        dataStore.hydrateDraftMessages()
        dataStore.hydrateAttachments()
        dataStore.hydrateInflightSends()
    })

    // Wire the global auto-apply title watcher. Module-level watchEffect that
    // survives router.replace (which would otherwise tear down a watcher held
    // inside SessionView when a draft binds to its canonical id).
    const { startAutoApplyTitleWatcher } = await import('./composables/useAutoApplyTitle')
    startAutoApplyTitleWatcher()

    // Periodically clean up orphan draft sessions (every 2 hours).
    // A draft becomes orphan when its session was created on the backend but the
    // IndexedDB entry was never removed (e.g. tab closed mid-send, crash).
    const DRAFT_CLEANUP_INTERVAL_MS = 2 * 60 * 60 * 1000
    setInterval(() => dataStore.cleanupOrphanDraftSessions(), DRAFT_CLEANUP_INTERVAL_MS)

    // Hydrate code comments from IndexedDB (async, non-blocking)
    const codeCommentsStore = useCodeCommentsStore()
    codeCommentsStore.hydrateComments()
}

app.mount('#app')
