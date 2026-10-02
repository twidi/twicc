const MERMAID_FENCE_RE = /(?:^|\n)[ \t]*(?:`{3,}|~{3,})[ \t]*mermaid\b/i

function deterministic(value) {
    if (Array.isArray(value)) return value.map(deterministic)
    if (value && typeof value === 'object') {
        return Object.fromEntries(Object.keys(value).sort().map(key => [key, deterministic(value[key])]))
    }
    return value
}

export function markdownReferenceContextKey(references) {
    return JSON.stringify(deterministic(references ?? {}))
}

export function markdownBlockCacheKey({ source, referenceContext, theme, slashTag }) {
    return JSON.stringify([source, referenceContext, MERMAID_FENCE_RE.test(source) ? theme : null, Boolean(slashTag)])
}
