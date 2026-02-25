import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Backend port for proxy (passed by devctl.py via environment)
const backendPort = process.env.VITE_BACKEND_PORT || '3500'

export default defineConfig(({ command }) => ({
    plugins: [
        vue({
            template: {
                compilerOptions: {
                    isCustomElement: (tag) => tag.startsWith('wa-')
                }
            }
        })
    ],
    // Use /static/ base only for production build (Django serves static files)
    // In dev mode, use root path
    base: command === 'build' ? '/static/' : '/',
    build: {
        outDir: '../src/twicc/static/frontend',
        emptyOutDir: true
    },
    server: {
        proxy: {
            '/api': `http://localhost:${backendPort}`,
            '/ws': { target: `ws://localhost:${backendPort}`, ws: true }
        }
    }
}))
