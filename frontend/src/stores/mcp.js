import { defineStore } from 'pinia'

const pendingRefresh = new WeakMap()

export const useMcpStore = defineStore('mcp', {
    state: () => ({ connections: [], requests: [], config: {}, error: '', loadError: '', loading: false }),
    actions: {
        async request(options) {
            const { apiFetch } = await import('../utils/api')
            return apiFetch('/api/mcp/', options)
        },
        async refresh() {
            if (pendingRefresh.has(this)) return pendingRefresh.get(this)
            this.loading = true
            const task = (async () => {
            try {
                const response = await this.request()
                if (!response.ok) throw new Error('Cannot load MCP connections.')
                const data = await response.json()
                this.connections = data.connections
                this.requests = data.requests
                this.config = data.config
                this.loadError = ''
            } catch (error) { this.loadError = error.message }
            finally { this.loading = false; pendingRefresh.delete(this) }
            })()
            pendingRefresh.set(this, task)
            return task
        },
        async act(action, fields = {}) {
            this.error = ''
            try {
                const response = await this.request({
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-TwiCC-MCP-Owner': '1' },
                    body: JSON.stringify({ action, ...fields }),
                })
                const data = await response.json()
                if (!response.ok) throw new Error(data.error || 'MCP action failed.')
                await pendingRefresh.get(this)
                await this.refresh()
                return true
            } catch (error) { this.error = error.message; return false }
        },
    },
})
