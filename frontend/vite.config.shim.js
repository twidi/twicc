import { defineConfig } from 'vite'

// Standalone build of the artifact network-broker shim (design §8). It is
// injected into untrusted artifact iframes, so it must be a single, fully
// self-contained IIFE (penpal + @mswjs/interceptors bundled inline, no module
// graph, no imports). The backend serves the output at
// /_twicc/artifact-broker-shim.js (see views.artifact_broker_shim); the dir is
// gitignored — it is a build artifact, produced by `npm run build`.
export default defineConfig({
    // The shim is a single injected script — don't copy the SPA's public/ dir.
    publicDir: false,
    build: {
        outDir: '../src/twicc/static/artifact-broker',
        emptyOutDir: true,
        // Inline everything; no code-splitting for an injected script.
        cssCodeSplit: false,
        lib: {
            entry: 'src/artifact-broker/shim.js',
            formats: ['iife'],
            name: 'TwiccArtifactBroker',
            fileName: () => 'shim.js',
        },
        rolldownOptions: {
            output: { codeSplitting: false },
        },
    },
})
