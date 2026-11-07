import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Standalone build of the dedicated artifact page's trusted SHELL (design
// §5/§9). A minimal Vue app — Vue + the shared broker composable/prompt + the
// few Web Awesome components the prompt renders — served at
// /_twicc/artifact-shell/ like the broker shim. It pulls in NONE of the main
// SPA. The `base` makes the emitted JS/CSS/font URLs resolve under that route;
// the output dir is gitignored (a build artifact, produced by `npm run build`).
export default defineConfig({
    plugins: [
        vue({
            template: {
                compilerOptions: { isCustomElement: (tag) => tag.startsWith('wa-') },
            },
        }),
    ],
    // A self-contained page — don't copy the SPA's public/ dir.
    publicDir: false,
    base: '/_twicc/artifact-shell/',
    // Lib mode doesn't substitute `process.env.NODE_ENV` the way an app build
    // does, so Vue's ESM runtime hits a `process is not defined` at load. Pin it.
    define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    build: {
        outDir: '../src/twicc/static/artifact-shell',
        emptyOutDir: true,
        cssCodeSplit: false,
        lib: {
            entry: 'src/artifact-shell/main.js',
            formats: ['es'],
            fileName: () => 'shell.js',
        },
        rolldownOptions: {
            output: {
                // Keep optional locale catalogs outside the shell entry.
                codeSplitting: true,
                // The backend asset route accepts one path segment.
                chunkFileNames: '[name]-[hash].js',
                // Stable name for the single stylesheet (referenced from the shell
                // HTML); any other asset (e.g. fonts) keeps a hashed name.
                assetFileNames: (info) => {
                    const name = info.names?.[0] || ''
                    return name.endsWith('.css') ? 'shell.css' : 'assets/[name]-[hash][extname]'
                },
            },
        },
    },
})
