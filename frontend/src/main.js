// frontend/src/main.js

// Theme management
// Placed before imports to apply theme class as early as possible
// This prevents a flash of wrong theme on page load
let currentThemeMode = localStorage.getItem('twicc-settings')
    ? JSON.parse(localStorage.getItem('twicc-settings')).themeMode || 'system'
    : 'system'

function applyColorScheme() {
    let isDark
    if (currentThemeMode === 'system') {
        isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    } else {
        isDark = currentThemeMode === 'dark'
    }
    document.documentElement.classList.toggle('wa-dark', isDark)
    // Also set data-theme for github-markdown-css compatibility
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light'
}

// Export function for settings store to call when theme mode changes
export function setThemeMode(mode) {
    currentThemeMode = mode
    applyColorScheme()
}

// Apply initial theme immediately (before CSS imports)
applyColorScheme()
// Listen for system preference changes (only matters when mode is 'system')
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyColorScheme)

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
import '@awesome.me/webawesome/dist/components/input/input.js'
import '@awesome.me/webawesome/dist/components/color-picker/color-picker.js'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { router } from './router'
import App from './App.vue'
import { initSettings } from './stores/settings'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// Initialize settings (localStorage persistence, theme, font size, display mode watchers)
initSettings()

app.mount('#app')
