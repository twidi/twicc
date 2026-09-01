import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Backend port for proxy (passed by devctl.py via environment)
const backendPort = process.env.BACKEND_PORT || '3500'

// Optional: allow a custom host for dev tunnels (e.g. ngrok, Cloudflare Tunnel)
const devAllowedHost = process.env.DEV_HOSTNAME

// Dev-only: embed the browser-companion script in TwiCC's own pages, so a dev
// instance loaded inside another instance's Browser tab (TwiCC-in-TwiCC)
// reports real in-page navigation. Relative src: it resolves against whatever
// origin THIS instance is reached at (localhost or a dev-tunnel hostname) and
// rides the /_twicc proxy below — no mixed-content, no per-env URL. The script
// is inert outside an iframe, so it costs nothing in normal top-level use.
// Production pages are untouched: users opt in on their own apps instead.
const injectBrowserCompanion = {
    name: 'twicc-inject-browser-companion',
    apply: 'serve',
    transformIndexHtml() {
        return [{
            tag: 'script',
            attrs: { src: '/_twicc/browser-companion.js', defer: true },
            injectTo: 'head',
        }]
    },
}

export default defineConfig(({ command }) => ({
    plugins: [
        vue({
            template: {
                compilerOptions: {
                    isCustomElement: (tag) => tag.startsWith('wa-')
                }
            }
        }),
        injectBrowserCompanion,
    ],
    // Use /static/ base only for production build (Django serves static files)
    // In dev mode, use root path
    base: command === 'build' ? '/static/' : '/',
    build: {
        outDir: '../src/twicc/static/frontend',
        emptyOutDir: true
    },
    // Ensure all CodeMirror packages and their shared dependency (style-mod)
    // are pre-bundled together. Without this, Vite's dep optimizer may fail
    // to resolve style-mod, preventing CM6 from injecting its CSS.
    optimizeDeps: {
        include: [
            'codemirror',
            '@codemirror/state',
            '@codemirror/view',
            '@codemirror/language',
            '@codemirror/merge',
            '@codemirror/autocomplete',
            'style-mod',
        ],
    },
    server: {
        allowedHosts: devAllowedHost ? [devAllowedHost] : [],
        proxy: {
            '/api': `http://localhost:${backendPort}`,
            '/rpc': `http://localhost:${backendPort}`,
            '/artifacts': `http://localhost:${backendPort}`,
            '/project-icons': `http://localhost:${backendPort}`,
            // Peer-instance endpoints (Bearer-auth API): a dev hostname can
            // then serve as `peerBaseUrl`, so two dev instances can pair. The
            // origin gate routes on the raw Host header, so it must NOT be
            // rewritten to the proxy target (the string shorthand implies
            // `changeOrigin: true`).
            '/peer': { target: `http://localhost:${backendPort}`, changeOrigin: false },
            // The broker shim, injected into backend-served artifact iframes.
            '/_twicc': `http://localhost:${backendPort}`,
            '/ws': { target: `ws://localhost:${backendPort}`, ws: true }
        }
    }
}))
