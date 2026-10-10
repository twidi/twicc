import { defineConfig } from 'vite'

// Standalone build of the browser-companion script, included by the user's
// own dev-server pages and bridging them to the Browser pane over postMessage
// (see src/browser-companion/companion.js). Must be a single self-contained
// classic IIFE — it is loaded cross-origin via a plain <script> tag, where a
// module script would require CORS. The backend serves the output at
// /_twicc/browser-companion.js (see views.browser_companion_script); the dir
// is gitignored — it is a build artifact, produced by `npm run build`.
export default defineConfig({
    // A single injected script — don't copy the SPA's public/ dir.
    publicDir: false,
    build: {
        outDir: '../src/twicc/static/browser-companion',
        emptyOutDir: true,
        lib: {
            entry: 'src/browser-companion/companion.js',
            formats: ['iife'],
            name: 'TwiccBrowserCompanion',
            fileName: () => 'companion.js',
        },
        rolldownOptions: {
            output: { codeSplitting: false },
        },
    },
})
