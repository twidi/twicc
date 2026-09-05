import { defineStore } from 'pinia'

const pendingRefresh = new WeakMap()

export const useMcpStore = defineStore('mcp', {
    state: () => ({
        connections: [], requests: [], config: {}, error: '', loadError: '', loading: false,
        // Reference instant for the TTL filter below, advanced by McpManager.
        clock: Date.now(),
    }),
    getters: {
        /**
         * Pending requests that have not run out their TTL.
         *
         * A request expires on its own and nothing announces it: no mutation
         * happens, so no WebSocket broadcast fires, and the owner snapshot
         * simply stops listing it. `expires_at` travels in the payload, so
         * drop the request locally the second it lapses — McpManager arms one
         * timer on the nearest deadline — instead of polling the endpoint.
         *
         * An unreadable `expires_at` keeps the request: never hide a real one.
         */
        pendingRequests(state) {
            return state.requests.filter(row => !(new Date(row.expires_at).getTime() <= state.clock))
        },
    },
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
