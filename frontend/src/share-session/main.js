// Entry for the read-only shared-session viewer (design §8). Mounts a minimal
// Pinia app over the reused transcript tree; the store imports are aliased to
// shims (vite.config.share.js), so no SPA/auth/router/WS code is pulled in.
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { initShareTheme } from './theme'

initShareTheme()

// Web Awesome: tokens + the elements the transcript tree actually renders.
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/button-group/button-group.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/tag/tag.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/divider/divider.js'
import '@awesome.me/webawesome/dist/components/spinner/spinner.js'
import '@awesome.me/webawesome/dist/components/switch/switch.js'
import '@awesome.me/webawesome/dist/components/slider/slider.js'
import '@awesome.me/webawesome/dist/components/select/select.js'
import '@awesome.me/webawesome/dist/components/option/option.js'
import '@awesome.me/webawesome/dist/components/details/details.js'
import '@awesome.me/webawesome/dist/components/dialog/dialog.js'
import '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'
import '@awesome.me/webawesome/dist/components/popover/popover.js'

import '../styles/transcript-tokens.css'
import '../styles/depth.css'
import '../styles/glass.css'
import '../styles/motion.css'
import ShareSessionApp from './ShareSessionApp.vue'
import ShareDocApp from './ShareDocApp.vue'
import ShareRecentApp from './ShareRecentApp.vue'   // 3.20
// Glass popovers: cut the body border under the arrow (see glass.css).
import { installGlassArrowGap } from '../utils/glassArrowGap'
installGlassArrowGap()
import { recordShareView } from '../share-recent/recordView'  // 3.19

const el = document.getElementById('twicc-share-data')
const data = el ? JSON.parse(el.textContent) : {}

let app
if (data.mode === 'recent') {
    // Share host homepage (/share/, no token): the localStorage-backed recent list.
    app = createApp(ShareRecentApp)
} else {
    // A real share page was opened → record it in this browser's recent list.
    recordShareView({
        tokenPath: data.tokenPath,
        kind: data.mode === 'session' ? 'session' : 'artifact',
        title: (data.meta && data.meta.title) || '',
    })
    app = createApp(data.mode === 'doc' ? ShareDocApp : ShareSessionApp, {
        tokenPath: data.tokenPath,
        meta: data.meta || {},
    })
}
app.use(createPinia())
app.mount('#app')
document.body.classList.remove('loading')
