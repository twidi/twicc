// frontend/src/main.js

// Theme management - must be initialized before CSS imports to prevent flash
import { initTheme } from './utils/theme'
initTheme()

// Web Awesome theme and components
import '@awesome.me/webawesome/dist/styles/webawesome.css';
import '@awesome.me/webawesome/dist/styles/themes/awesome.css'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/card/card.js'
import '@awesome.me/webawesome/dist/components/divider/divider.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/progress-ring/progress-ring.js'
import '@awesome.me/webawesome/dist/components/option/option.js'
import '@awesome.me/webawesome/dist/components/select/select.js'
import '@awesome.me/webawesome/dist/components/skeleton/skeleton.js'
import '@awesome.me/webawesome/dist/components/spinner/spinner.js'
import '@awesome.me/webawesome/dist/components/split-panel/split-panel.js'
import '@awesome.me/webawesome/dist/components/switch/switch.js'
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
import '@awesome.me/webawesome/dist/components/relative-time/relative-time.js'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createNotivue } from 'notivue'
import { router } from './router'
import App from './App.vue'
import { initSettings } from './stores/settings'
import { useDataStore } from './stores/data'

// Notivue CSS
import 'notivue/notification.css'
import 'notivue/animations.css'

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
    notifications: {
        global: {
            duration: 3000
        },
        success: {
            duration: 3000
        },
        error: {
            duration: 8000
        },
        promise: {
            duration: Infinity
        }
    }
})
app.use(notivue)

// Initialize settings (localStorage persistence, theme, font size, display mode watchers)
initSettings()

// Hydrate drafts from IndexedDB (async, non-blocking)
// Order matters: sessions first so draft messages have their session available
const dataStore = useDataStore()
dataStore.hydrateDraftSessions().then(() => {
    dataStore.hydrateDraftMessages()
    dataStore.hydrateAttachments()
})

app.mount('#app')
