// Entry for the dedicated artifact page's trusted shell (design §5/§9). It
// mounts a deliberately MINIMAL Vue app — only Vue + the shared broker
// composable/prompt + the few Web Awesome components the prompt renders — so the
// bundle stays small and pulls in none of the main SPA. The backend serves this
// at /_twicc/artifact-shell/shell.js and injects the per-bookmark data the app
// reads below.

import { createApp } from 'vue'

// Web Awesome: design tokens + ONLY the components the consent prompt uses.
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
// Depth, glass and motion tokens: the consent prompt dialog looks the same here as in the SPA preview.
import '../styles/depth.css'
import '../styles/glass.css'
import '../styles/motion.css'
import '@awesome.me/webawesome/dist/components/dialog/dialog.js'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'

import ArtifactShellApp from './ArtifactShellApp.vue'
import { recordShareView } from '../share-recent/recordView'
import { initArtifactShellColorScheme } from '../share-session/theme'
import { installWaMotionStyles } from '../utils/waMotionStyles'

// The consent prompt dialog opens and closes like the SPA's (see utils/waMotionStyles.js).
installWaMotionStyles()

// The shell's chrome (footer + banners) follows the browser's color scheme.
// No switcher here: the only chrome is the footer strip, and the iframed
// artifact keeps its own internal theme the shell can't read (sandboxed), so a
// toggle recoloring just the footer would mislead. Follow the OS, nothing else.
initArtifactShellColorScheme()

const dataEl = document.getElementById('twicc-shell-data')
const shellData = dataEl ? JSON.parse(dataEl.textContent) : {}

if (shellData.mode === 'share') {
    // Record this artifact share in the browser's recent list (share host homepage).
    recordShareView({ tokenPath: shellData.tokenPath, kind: 'artifact', title: shellData.title || '' })
}
createApp(ArtifactShellApp, {
    innerDocUrl: shellData.innerDocUrl,
    bookmarkId: shellData.bookmarkId ?? null,
    allowedHosts: shellData.allowedHosts ?? {},
    deniedHosts: shellData.deniedHosts ?? {},
    // Share mode: no bookmark id, no persistence, proxy through the share token.
    mode: shellData.mode === 'share' ? 'share' : 'owner',
    proxyUrl: shellData.mode === 'share' ? `${shellData.tokenPath}/api/proxy/` : undefined,
    snapshotAt: shellData.snapshotAt ?? null,
    tokenPath: shellData.tokenPath ?? null,
}).mount('#app')
