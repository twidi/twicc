/** Token-only adapter for one regular root. HTTP and WS share one revision gate. */
export function makeShareInlineAdapter({ api, tokenPath, store, canAcceptManifest = () => true, onNeedsReconcile }) {
    const base = tokenPath.replace(/\/+$/, '')
    const sourceSessionId = store.sharedSessionId
    let revision = -1, disposed = false, descriptors = new Map(), refreshRequest = null
    let manifestsPaused = false, pendingManifest = null
    const listeners = new Set(), controllers = new Set()

    function acceptManifest(wire) {
        if (disposed || !Number.isInteger(wire?.revision) || wire.revision <= revision) return null
        // Reconnect must restore source rows before replacing their retained bindings.
        // A disabled manifest still closes access immediately.
        if (wire.enabled !== false && (manifestsPaused || !canAcceptManifest(wire))) {
            const needsReconcile = !manifestsPaused
            manifestsPaused = true
            if (!pendingManifest || wire.revision > pendingManifest.revision) pendingManifest = wire
            if (needsReconcile) onNeedsReconcile?.()
            return null
        }
        revision = wire.revision
        if (pendingManifest?.revision <= revision) pendingManifest = null
        const normalized = []
        for (const item of wire.artifacts || []) {
            const occurrence = item.publication
            if (item.source_session_id !== sourceSessionId || store.getSession(sourceSessionId)?.type !== 'session'
                || !Array.isArray(occurrence) || occurrence.length !== 4 || occurrence[0] !== item.source_session_id
                || !occurrence.slice(1).every(Number.isInteger)
                || !/^[a-z][a-z0-9_-]{0,63}$/.test(item.artifact_id)
                || typeof item.entry_filename !== 'string' || /[\/\\?#\x00]/.test(item.entry_filename)
                || !/^.+\.(html|htm)$/.test(item.entry_filename)) continue
            const publication = { line_num: occurrence[1], text_block_index: occurrence[2], tag_offset: occurrence[3],
                src: `inline-artifacts/${item.artifact_id}/${item.entry_filename}` }
            normalized.push({ sourceSessionId: item.source_session_id, artifactId: item.artifact_id, publication,
                publicationKey: JSON.stringify(occurrence), status: wire.enabled ? item.status : 'not_included',
                title: item.title, height: item.height, codeRevision: wire.enabled ? item.code_revision : null })
        }
        descriptors = new Map(normalized.map(d => [JSON.stringify([d.sourceSessionId, d.artifactId]), d]))
        const manifest = { revision, descriptors: normalized }
        for (const listener of listeners) listener(manifest)
        return manifest
    }
    function validate(descriptor, runnable = true) {
        const current = descriptors.get(JSON.stringify([descriptor.sourceSessionId, descriptor.artifactId]))
        if (disposed || !current || current.publicationKey !== descriptor.publicationKey
            || current.codeRevision !== descriptor.codeRevision || current.publication.src !== descriptor.publication.src
            || current.status === 'not_included' || runnable && current.status !== 'ready') {
            throw new Error('Inline artifact unavailable')
        }
        return current
    }
    const identityPath = descriptor => `${encodeURIComponent(descriptor.sourceSessionId)}/${encodeURIComponent(descriptor.artifactId)}`
    function documentUrl(descriptor) {
        const current = validate(descriptor)
        return `${base}/inline-artifacts/${identityPath(current)}/${encodeURIComponent(current.publication.src.split('/').at(-1))}`
    }
    function brokerConfig(descriptor) {
        validate(descriptor)
        return { mode: 'share', documentUrl: documentUrl(descriptor),
            proxyUrl: `${base}/api/inline-artifacts/${identityPath(descriptor)}/proxy/`,
            inArtifactsRoot: true }
    }
    async function probe(descriptor, { signal }) {
        const response = await fetch(documentUrl(descriptor), { method: 'HEAD', credentials: 'same-origin', signal })
        if (response.ok) return { available: true }
        return { available: false, error: response.status === 401 ? 'share_password_required'
            : response.status === 403 ? 'share_forbidden' : response.status === 409 ? 'session_not_ready' : 'document_unavailable' }
    }
    function refresh({ signal } = {}) {
        if (disposed) return Promise.resolve(null)
        let request = refreshRequest
        if (!request || request.controller.signal.aborted) {
            const controller = new AbortController()
            controllers.add(controller)
            request = { controller, promise: null }
            refreshRequest = request
            request.promise = (async () => {
                try {
                    const manifest = await api.fetchInlineManifest({ signal: controller.signal })
                    return controller.signal.aborted ? null : acceptManifest(manifest)
                } finally {
                    controllers.delete(controller)
                    if (refreshRequest?.controller === controller) refreshRequest = null
                }
            })()
        }
        const abort = () => request.controller.abort()
        if (signal?.aborted) abort()
        else signal?.addEventListener('abort', abort, { once: true })
        return request.promise.finally(() => signal?.removeEventListener('abort', abort))
    }
    async function retry(descriptor) {
        validate(descriptor, false)
        const controller = new AbortController()
        controllers.add(controller)
        try {
            // Reconciliation retains a successful snapshot and retries allowed export errors only.
            const result = await api.retryInlineArtifact(descriptor.sourceSessionId, descriptor.artifactId,
                { signal: controller.signal })
            acceptManifest(result)
        } finally { controllers.delete(controller) }
    }
    return { acceptManifest, refresh, documentUrl, brokerConfig, probe, retry,
        pauseManifests() { manifestsPaused = true },
        resumeManifests(canAccept = () => true) {
            if (disposed || pendingManifest && !canAccept(pendingManifest)) return false
            manifestsPaused = false
            const pending = pendingManifest
            pendingManifest = null
            if (pending) acceptManifest(pending)
            return true
        },
        subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
        dispose() {
            disposed = true; pendingManifest = null
            controllers.forEach(controller => controller.abort()); controllers.clear(); listeners.clear()
        },
    }
}
