import { defineConfig } from 'vite'
import { flattenTheme } from './src/artifact-theme/flattenTheme.js'

// Standalone CSS build for HTML artifacts: theme.css (TwiCC design tokens, injected into
// every artifact document next to the broker shim) and kit.css (opt-in base styles). The
// backend serves the output at /_twicc/artifact-theme/<file> (see views.artifact_theme_asset);
// the dir is gitignored — it is a build artifact, produced by `npm run build`.
export default defineConfig({
    publicDir: false,
    css: {
        postcss: {
            plugins: [flattenTheme({ match: file => file.endsWith('/artifact-theme/theme.css') })],
        },
    },
    build: {
        outDir: '../src/twicc/static/artifact-theme',
        emptyOutDir: true,
        cssCodeSplit: true,
        rollupOptions: {
            input: {
                theme: 'src/artifact-theme/theme.css',
                kit: 'src/artifact-theme/kit.css',
            },
            output: { assetFileNames: '[name][extname]' },
        },
    },
})
