import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

// Standalone read-only transcript viewer bundle (design §8). Reuses the SPA's
// transcript component tree verbatim; the store imports are aliased to shims so
// none of the main SPA (auth store, router, WebSocket) is pulled in.
const r = (p) => fileURLToPath(new URL(p, import.meta.url))

export default defineConfig({
    plugins: [
        vue({ template: { compilerOptions: { isCustomElement: (tag) => tag.startsWith('wa-') } } }),
    ],
    publicDir: false,
    base: '/_twicc/share/',
    define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    resolve: {
        alias: [
            { find: /^\.\/useWebSocket(\.js)?$/, replacement: r('src/share-session/shims/noWebSocket.js') },
            { find: /.*\/utils\/api(\.js)?$/, replacement: r('src/share-session/shims/noPrivateApi.js') },
            { find: /.*\/stores\/data(\.js)?$/, replacement: r('src/share-session/shims/dataStoreShim.js') },
            { find: /.*\/stores\/settings(\.js)?$/, replacement: r('src/share-session/shims/settingsStoreShim.js') },
            { find: /.*\/stores\/codeComments(\.js)?$/, replacement: r('src/share-session/shims/codeCommentsShim.js') },
            { find: /.*\/composables\/useWebSocket(\.js)?$/, replacement: r('src/share-session/shims/noWebSocket.js') },
            // Cut the app router and its SPA dependencies from reused components.
            { find: /.*\/router(\.js)?$/, replacement: r('src/share-session/shims/noRouter.js') },
        ],
    },
    build: {
        outDir: '../src/twicc/static/share-session',
        emptyOutDir: true,
        cssCodeSplit: false,
        lib: { entry: 'src/share-session/main.js', formats: ['es'], fileName: () => 'share-session.js' },
        rolldownOptions: {
            output: {
                // Public viewers never request optional locale chunks.
                codeSplitting: true,
                // The backend asset route accepts one path segment.
                chunkFileNames: '[name]-[hash].js',
                assetFileNames: (info) => {
                    const name = info.names?.[0] || ''
                    return name.endsWith('.css') ? 'share-session.css' : 'assets/[name]-[hash][extname]'
                },
            },
        },
    },
})
