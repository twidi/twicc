/** Private adapter. Every operation is confined to one frozen regular session. */
export function makeOwnerInlineAdapter({ sessionId, store, api }) {
    let revision = 0
    let disposed = false
    const session = () => store.getSession(sessionId)
    const key = publication => JSON.stringify([sessionId, publication.line_num,
        publication.text_block_index, publication.tag_offset])

    function validate(descriptor) {
        if (disposed || descriptor.sourceSessionId !== sessionId || session()?.type !== 'session') {
            throw new Error('invalid inline artifact source')
        }
        const publication = session().inline_artifacts?.[descriptor.artifactId]
        if (!publication || key(publication) !== descriptor.publicationKey
            || publication.src !== descriptor.publication.src) throw new Error('stale inline artifact publication')
        const parts = publication.src.split('/')
        if (parts.length !== 3 || parts[0] !== 'inline-artifacts' || parts[1] !== descriptor.artifactId
            || !/^[a-z][a-z0-9_-]{0,63}$/.test(descriptor.artifactId)
            || !/^.+\.(html|htm)$/.test(parts[2]) || /[\\?#\x00]/.test(parts[2])) {
            throw new Error('invalid inline artifact publication')
        }
        return publication
    }

    function documentUrl(descriptor) {
        const publication = validate(descriptor)
        return `/api/sessions/${encodeURIComponent(sessionId)}/inline-artifacts/${encodeURIComponent(descriptor.artifactId)}/${encodeURIComponent(publication.src.split('/')[2])}`
    }

    function manifest() {
        const descriptors = []
        if (!disposed && session()?.type === 'session') {
            for (const [artifactId, publication] of Object.entries(session().inline_artifacts || {})) {
                descriptors.push({ sourceSessionId: sessionId, artifactId, publication,
                    publicationKey: key(publication), status: 'ready', title: publication.title,
                    height: publication.height, codeRevision: null })
            }
        }
        return { revision: ++revision, descriptors }
    }

    function brokerConfig(descriptor) {
        const publication = validate(descriptor)
        const path = publication.src
        const bookmark = () => store.artifactBookmarkFor(sessionId, path)
        const post = (suffix, url, kind) => {
            const id = bookmark()?.id
            if (id == null) return Promise.resolve()
            return api(`/api/artifact-bookmarks/${id}/${suffix}/`, { method: 'POST',
                headers: { 'content-type': 'application/json' }, body: JSON.stringify({ url, kind }) })
        }
        return {
            documentUrl: documentUrl(descriptor),
            getBookmarkId: () => bookmark()?.id ?? null,
            getAllowedHosts: () => bookmark()?.allowed_hosts ?? {},
            getDeniedHosts: () => bookmark()?.denied_hosts ?? {},
            persistAllow: (url, kind) => post('allowed-hosts', url, kind),
            onDenied: (url, kind) => { post('network-denials', url, kind).catch(() => {}) },
            inArtifactsRoot: true,
            getDataDirLabel: () => `${path.slice(0, path.lastIndexOf('/') + 1)}data/`,
        }
    }

    async function probe(descriptor, { signal }) {
        const response = await api(documentUrl(descriptor), { method: 'HEAD', signal })
        return response.ok ? { available: true } : { available: false, error: 'document_unavailable' }
    }

    return { manifest, documentUrl, brokerConfig, probe,
        retry: descriptor => { validate(descriptor) }, dispose: () => { disposed = true } }
}
