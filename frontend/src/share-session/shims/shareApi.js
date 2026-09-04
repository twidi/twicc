// Fetch layer for the share bundle — all requests stay under the share token path.
export function isSessionNotReadyError(error) {
    return error?.status === 409 && error?.code === 'session_not_ready'
}

export function makeShareApi(tokenPath) {
    const base = tokenPath.replace(/\/+$/, '')
    async function jget(url) {
        const res = await fetch(url, { credentials: 'same-origin' })
        if (!res.ok) {
            let code = null
            try { code = (await res.json())?.error || null } catch { /* non-JSON error */ }
            const error = new Error(`share fetch ${res.status}`)
            error.status = res.status
            error.code = code
            throw error
        }
        return res.json()
    }
    return {
        base,
        fetchMeta: () => jget(`${base}/api/meta/`),
        fetchItemsMetadata: (subagentId = null) =>
            jget(subagentId ? `${base}/api/subagent/${subagentId}/items/metadata/` : `${base}/api/items/metadata/`),
        fetchItems: (rangesQS, subagentId = null) =>
            jget(subagentId ? `${base}/api/subagent/${subagentId}/items/?${rangesQS}` : `${base}/api/items/?${rangesQS}`),
        fetchToolResults: (lineNum, toolId, subagentId = null) =>
            jget(subagentId
                ? `${base}/api/subagent/${subagentId}/items/${lineNum}/tool-results/${toolId}/`
                : `${base}/api/items/${lineNum}/tool-results/${toolId}/`),
        fetchToolStates: (subagentId = null) =>
            jget(subagentId ? `${base}/api/subagent/${subagentId}/tool-states/` : `${base}/api/tool-states/`),
        // Raw tool_result item(s) for a tool call — the ceiling-exempt source of the
        // Edit/apply_patch full-file diff (structuredPatch/originalFile live there).
        fetchBackendPatchItems: (toolId, subagentId = null) =>
            jget(subagentId ? `${base}/api/subagent/${subagentId}/backend-patch/${toolId}/` : `${base}/api/backend-patch/${toolId}/`),
        fetchSubagents: () => jget(`${base}/api/subagents/`),
        mediaUrl: (filename) => `${base}/media/${filename}`,
    }
}

// Module-scoped singleton so the shim stores (which can't take constructor args)
// reach the same API instance the app configured at boot.
let _api = null
export function setShareApi(api) { _api = api }
export function shareApi() {
    if (!_api) throw new Error('share API not configured')
    return _api
}
