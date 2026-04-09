// frontend/src/main.js

// Theme management - must be initialized before CSS imports to prevent flash
import { initTheme } from './utils/theme'
initTheme()

// Web Awesome theme and components
import '@awesome.me/webawesome/dist/styles/webawesome.css';
import '@awesome.me/webawesome/dist/styles/themes/awesome.css'
import '@awesome.me/webawesome/dist/components/badge/badge.js'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/card/card.js'
import '@awesome.me/webawesome/dist/components/divider/divider.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/progress-bar/progress-bar.js'
import '@awesome.me/webawesome/dist/components/progress-ring/progress-ring.js'
import '@awesome.me/webawesome/dist/components/option/option.js'
import '@awesome.me/webawesome/dist/components/select/select.js'
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
import { useDataStore } from './stores/data'
import { useCodeCommentsStore } from './stores/codeComments'

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

// Fetch synced settings defaults from backend before initializing the store.
// This ensures SETTINGS_SCHEMA has backend-provided defaults before loadSettings()
// runs, making the backend the single source of truth for synced setting defaults.
{
    let settingsFailed = false
    try {
        const resp = await fetch('/api/settings/')
        if (resp.ok) {
            const { settings, default_settings } = await resp.json()
            applyDefaultSettings(default_settings, settings)
        } else {
            settingsFailed = true
        }
    } catch {
        settingsFailed = true
    }
    if (settingsFailed) {
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
        throw new Error('Backend unreachable — cannot fetch settings')
    }
}

// Initialize settings (localStorage persistence, theme, font size, display mode watchers)
initSettings()

// Hydrate drafts from IndexedDB (async, non-blocking)
// Order matters: sessions first so draft messages have their session available
const dataStore = useDataStore()
dataStore.hydrateDraftSessions().then(() => {
    dataStore.hydrateDraftMessages()
    dataStore.hydrateAttachments()
})

// Periodically clean up orphan draft sessions (every 2 hours).
// A draft becomes orphan when its session was created on the backend but the
// IndexedDB entry was never removed (e.g. tab closed mid-send, crash).
const DRAFT_CLEANUP_INTERVAL_MS = 2 * 60 * 60 * 1000
setInterval(() => dataStore.cleanupOrphanDraftSessions(), DRAFT_CLEANUP_INTERVAL_MS)

// Hydrate code comments from IndexedDB (async, non-blocking)
const codeCommentsStore = useCodeCommentsStore()
codeCommentsStore.hydrateComments()

app.mount('#app')
