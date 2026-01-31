// frontend/src/main.js

// Color scheme detection (follows system preference)
// Placed before imports to apply theme class as early as possible
function applyColorScheme() {
  const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.classList.toggle('wa-dark', isDark)
  // Also set data-theme for github-markdown-css compatibility
  document.documentElement.dataset.theme = isDark ? 'dark' : 'light'
}
applyColorScheme()
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

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { router } from './router'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
