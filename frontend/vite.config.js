import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

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
        outDir: 'dist',
        emptyOutDir: true
    },
    server: {
        proxy: {
            '/api': 'http://localhost:3500',
            '/ws': { target: 'ws://localhost:3500', ws: true }
        }
    }
}))
