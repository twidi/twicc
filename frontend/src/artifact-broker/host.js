// Artifact network-broker HOST (design §9). The trusted side of the penpal RPC
// whose other end is the injected shim (shim.js). It owns the broker decision:
// serve the artifact's own assets locally, gate cross-origin requests behind the
// per-artifact allow/deny lists (read live through getters, so a dialog change
// applies to an open preview) + an honest user prompt, and call the server
// proxy — which pins the resolved IP and blocks the cloud metadata address.
//
// Framework-agnostic on purpose: both mounts (the SPA's FilePane wrapper and the
// dedicated shell page, phase 5) call `mountBrokerHost`, passing a `showPrompt`
// that renders in their own UI.

import { WindowMessenger, connect } from 'penpal'

const DEFAULT_PROXY_URL = '/api/artifact-proxy/'

function bytesToBase64(bytes) {
    let binary = ''
    const chunk = 0x8000
    for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk))
    }
    return btoa(binary)
}

function base64ToBytes(b64) {
    const binary = atob(b64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    return bytes
}

// The canonical allowlist key — must match the backend's normalize_host_key
// (scheme://host:port, effective port explicit, lower-cased, IPv6 bracketed).
const DEFAULT_PORTS = { 'http:': '80', 'https:': '443' }

function normalizeHostKey(url) {
    const u = new URL(url)
    const scheme = u.protocol.replace(':', '').toLowerCase()
    const port = u.port || DEFAULT_PORTS[u.protocol]
    // u.host already lower-cases and brackets IPv6; strip any explicit port to
    // re-append the effective one.
    const host = u.hostname.toLowerCase()
    const bracketed = host.includes(':') ? `[${host}]` : host
    return `${scheme}://${bracketed}:${port}`
}

async function callProxy(proxyUrl, body) {
    const res = await fetch(proxyUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        credentials: 'same-origin',
    })
    if (res.status === 401) throw new Error('not authenticated')
    return await res.json()
}

// Module-level "This session" grant cache, keyed by artifact identity. It
// deliberately OUTLIVES a single host instance. The in-SPA preview re-mounts the
// host on every edit (a cache-bust query forces the iframe to reload), and the
// dedicated page re-mounts on reload — which would otherwise wipe the in-memory
// grants and make the user re-approve every already-approved host each time the
// agent iterates on the artifact. Holding the grants in MODULE scope (never on
// `window` / in DOM storage) lets them survive the reload while staying
// unreachable AND unforgeable by the same-origin artifact iframe — `sessionStorage`
// would let a malicious artifact pre-approve hosts and bypass the prompt entirely
// (design §13). Cleared only when the host page itself reloads (this module
// re-evaluates) or the tab closes. Shared by both run contexts (this file is the
// one host core).
const sessionGrants = new Map() // artifactKey -> { "scheme://host:port": { kind } }

// Tab-lifetime "this page may write its data/" grants for documents OUTSIDE an
// artifacts root (design 2026-08-05 §6). Module scope like sessionGrants: a
// preview re-mount must not re-prompt; a page reload starts fresh.
const writeGrants = new Set() // artifactKey

function artifactKeyFor(documentUrl) {
    const u = new URL(documentUrl)
    // The cache-bust token lives in the query (`?_=…`), so origin + pathname is
    // stable across reloads of the *same* artifact (and distinct per artifact).
    return u.origin + u.pathname
}

// Owner hosts, artifactKey -> the host's live `getBookmarkId` getter. Lets the
// owner UI (the share dialog) look up an open artifact's "This session" grants
// from just a bookmark id, whichever entry point asked — no per-component
// plumbing. Entries are overwritten per mount and deliberately never removed:
// a getter from a torn-down host still resolves the live bookmark, and the
// grants it points at outlive the host by design (module cache above).
const hostBookmarkGetters = new Map() // artifactKey -> () => (number|null)

/**
 * The "This session" broker grants of the artifact(s) bound to this bookmark:
 * `{ "scheme://host:port": { kind } }`. Feeds the share dialog's "allow for
 * viewers" promotion list — the share proxy only honours the PERSISTED
 * allowlist, so session-only grants are invisible to viewers until promoted.
 * Empty when the artifact hasn't run in this tab since the last page load
 * (the cache is in-memory only).
 */
export function getSessionGrantsForBookmark(bookmarkId) {
    if (bookmarkId == null) return {}
    const out = {}
    for (const [key, getBookmarkId] of hostBookmarkGetters) {
        let id = null
        try { id = getBookmarkId() } catch { continue } // stale getter
        if (id !== bookmarkId) continue
        Object.assign(out, sessionGrants.get(key))
    }
    return out
}

/**
 * Build the broker host core.
 *
 * @param {object} opts
 * @param {string} opts.documentUrl  The artifact document's URL (to recognize its own same-origin assets).
 * @param {() => (number|null)} opts.getBookmarkId  Returns the *current* bookmark id (or null). Evaluated per prompt / per call — a bookmark can be created or removed while the artifact stays open (the host is not re-created on a bookmark change), and "Forever" must reflect the live state.
 * @param {() => object} opts.getAllowedHosts  Returns the *current* persisted allowlist `{ "scheme://host:port": { kind } }`. A getter, not a snapshot: an allowlist edit in the bookmark dialog must apply to an open preview without a re-mount.
 * @param {() => object} opts.getDeniedHosts  Returns the *current* persisted denylist, same shape. Read live per request for the same reason.
 * @param {(target: {host: string, ip: string, kind: string, canRemember: boolean}) => Promise<'session'|'forever'|'deny'>} opts.showPrompt
 * @param {(url: string, kind: string) => Promise<void>} [opts.persistAllow]  Persist "allow forever" (bookmarked only).
 * @param {(url: string, kind: string) => void} [opts.onDenied]  Owner mode: called (fire-and-forget) when the user denies a prompt, so the caller can record the denial server-side.
 * @param {(hostKey: string) => void} [opts.onBlocked]  Share mode only: called with the normalized host key when the server proxy refuses a host the owner never allowed (`not_allowed`).
 * @param {boolean} [opts.inArtifactsRoot]  True when the document lives under a session's artifacts root: its `data/` writes are silent (that folder is the agent's own workspace). False (the default) prompts once per tab (design 2026-08-05 §6).
 * @param {() => string} [opts.getDataDirLabel]  Human-readable location of the store, shown in the data-write prompt (the real directory on disk, not the serving URL). Falls back to the document URL's own `data/`.
 */
export function createBrokerHost({ documentUrl, getBookmarkId, getAllowedHosts, getDeniedHosts, showPrompt, persistAllow, onDenied, mode = 'owner', proxyUrl = DEFAULT_PROXY_URL, onBlocked, inArtifactsRoot = false, getDataDirLabel }) {
    // Per-artifact "This session" grants persist across host re-mounts via the
    // module cache. They pair with the LIVE persisted lists below (never merged
    // into a snapshot): the DB-backed grants are re-read through the getters on
    // every check, so a dialog-side allow/deny applies to an open preview.
    const artifactKey = artifactKeyFor(documentUrl)
    let sessionGranted = sessionGrants.get(artifactKey)
    if (!sessionGranted) {
        sessionGranted = {}
        sessionGrants.set(artifactKey, sessionGranted)
    }
    const allowedNow = () => (typeof getAllowedHosts === 'function' ? getAllowedHosts() : {}) || {}
    const deniedNow = () => (typeof getDeniedHosts === 'function' ? getDeniedHosts() : {}) || {}
    const canPersist = typeof persistAllow === 'function'
    const currentBookmarkId = () => (typeof getBookmarkId === 'function' ? getBookmarkId() : null)
    // Owner hosts register for the bookmark-id grant lookup above (a share page
    // has no bookmark and nothing to promote).
    if (mode !== 'share') hostBookmarkGetters.set(artifactKey, currentBookmarkId)
    // The directory the artifact lives under; its own assets resolve below it.
    const ownDir = new URL('.', documentUrl).href

    // A host is covered iff a grant still resolves to the kind it was approved
    // for (a rebind → re-prompt). Grants come from two places checked in order:
    // this artifact's `sessionGranted` cache (the "This session" grants, which
    // survive a host re-mount — same shape, just not written to the DB) and the
    // live persisted "Forever" allowlist read through `allowedNow()`.
    function isAllowed(key, kind) {
        const entry = sessionGranted[key] || allowedNow()[key]
        return !!(entry && entry.kind === kind)
    }

    // Consent gate. Serializes prompts (one dialog at a time, across hosts) AND
    // coalesces a burst to the *same* host into a single decision: while a host's
    // prompt is pending (or in-flight requests are awaiting it), every request to
    // that host shares the one outcome — allow or deny — instead of each raising
    // its own prompt. Cleared once settled, so a later request re-evaluates fresh
    // (an approved host passes `isAllowed`; a denied one re-asks).
    let gateChain = Promise.resolve()
    const pendingGate = {}
    function gate(key, target, url) {
        if (pendingGate[key]) return pendingGate[key]
        const run = gateChain.then(async () => {
            if (isAllowed(key, target.kind)) return // approved while we waited
            // Evaluated per prompt: "Forever" is offered only if there is a
            // bookmark to persist onto *right now* — un-bookmarking while the
            // artifact stays open must drop the option (and bookmarking must add
            // it) without a reload.
            const canRemember = canPersist && currentBookmarkId() != null
            const decision = await showPrompt({
                type: 'network', host: key, ip: target.ip, kind: target.kind, canRemember,
            })
            if (decision === 'deny') {
                // Fire-and-forget: the caller records the denial server-side.
                onDenied?.(url, target.kind)
                throw new Error('denied by user')
            }
            // "Forever" additionally persists onto the bookmark (survives a tab
            // close); every grant is also recorded in the module cache so it
            // survives the artifact reloading (but not a page reload / tab close).
            if (decision === 'forever' && canRemember) await persistAllow(url, target.kind)
            sessionGranted[key] = { kind: target.kind }
        })
        gateChain = run.then(() => {}, () => {}) // keep the chain alive on either outcome
        const settled = run.finally(() => {
            if (pendingGate[key] === settled) delete pendingGate[key]
        })
        pendingGate[key] = settled
        return settled
    }

    // Consent for data/ writes outside an artifacts root (design 2026-08-05 §6).
    // Serialized on the SAME gateChain as network prompts (one dialog at a time)
    // and coalesced per artifact: concurrent writes share one prompt/decision.
    // Tab-lifetime only — no "Forever" (nothing to persist onto; design §6).
    let pendingWriteGate = null
    function writeGate() {
        if (writeGrants.has(artifactKey)) return Promise.resolve()
        if (pendingWriteGate) return pendingWriteGate
        const run = gateChain.then(async () => {
            if (writeGrants.has(artifactKey)) return // granted while we waited
            const label = (typeof getDataDirLabel === 'function' && getDataDirLabel())
                || new URL(ownDir).pathname + 'data/'
            const decision = await showPrompt({ type: 'data-write', path: label })
            if (decision === 'deny') throw new Error('denied by user')
            writeGrants.add(artifactKey)
        })
        gateChain = run.then(() => {}, () => {}) // keep the chain alive on either outcome
        const settled = run.finally(() => {
            if (pendingWriteGate === settled) pendingWriteGate = null
        })
        pendingWriteGate = settled
        return settled
    }

    async function hostDirectFetch(req) {
        // Run the request in the host's own (SPA) browser context, not through
        // the server proxy. For same-origin targets this is what lets the
        // browser attach your TwiCC session cookie (authenticated) — exactly as
        // for the artifact's own assets. Headers are forwarded unchanged. §6.6.
        const init = { method: req.method, headers: req.headers }
        if (req.body_base64) init.body = base64ToBytes(req.body_base64)
        const resp = await fetch(req.url, init)
        const buf = new Uint8Array(await resp.arrayBuffer())
        return {
            status: resp.status,
            reason: resp.statusText,
            headers: Object.fromEntries(resp.headers.entries()),
            body_base64: buf.length ? bytesToBase64(buf) : undefined,
        }
    }

    async function proxyFetch(req) {
        const url = new URL(req.url)
        const sameOrigin = url.origin === location.origin

        // The artifact's own files → served directly, no prompt (§6.6). Writes
        // and data/ requests additionally carry the data-store protocol
        // (design 2026-08-05): confined to <ownDir>data/, doc header set by US
        // (never trusted from the artifact), consent outside an artifacts root.
        // Behaviour change, deliberate: an own-dir write outside data/ (or on a
        // share) now REJECTS the artifact's fetch instead of surfacing the
        // server's 405 response.
        if (sameOrigin && url.href.startsWith(ownDir)) {
            const isWrite = req.method !== 'GET' && req.method !== 'HEAD'
            const dataDir = ownDir + 'data/'
            const inData = url.href === dataDir || url.href.startsWith(dataDir)
            if (isWrite && mode === 'share') throw new Error('broker: shared artifact is read-only')
            if (isWrite && !inData) throw new Error('broker: writes allowed only under data/')
            if (isWrite || inData) {
                if (isWrite && !inArtifactsRoot) await writeGate() // throws on deny
                // Overwrite any artifact-supplied homonym — this header is the
                // server's proof the write comes from a served document.
                req.headers = {
                    ...req.headers,
                    'x-twicc-artifact-doc': new URL(documentUrl).pathname,
                }
            }
            return await hostDirectFetch(req)
        }

        // Share mode (design §9.3/D6): no preflight, no prompt. The server proxy
        // enforces the owner's allowlist; a non-listed host comes back as an error
        // which surfaces to the artifact as a failed fetch. Same-origin non-asset
        // targets are still brokered (never host-direct) — a viewer holds no cookie.
        if (mode === 'share') {
            const res = await callProxy(proxyUrl, { mode: 'fetch', request: req })
            if (res.error) {
                // "The owner never allowed this host" is worth surfacing to the
                // shell: the viewer can't grant anything and the artifact may
                // swallow the rejection. Other errors stay plain fetch failures.
                if (res.reason === 'not_allowed') onBlocked?.(normalizeHostKey(req.url))
                throw new Error(`broker: ${res.reason || res.error}`)
            }
            return res
        }

        // Persisted deny (design 2026-07-10 §5): checked first among egress
        // paths and read live, so a Deny in the bookmark dialog applies to an
        // open preview immediately and overrides an earlier session grant.
        const key = normalizeHostKey(req.url)
        if (deniedNow()[key]) {
            delete sessionGranted[key]
            throw new Error('denied by owner')
        }

        // Everything else is brokered the same way — cross-origin AND any other
        // same-origin target (e.g. TwiCC's own API). No target is special-cased:
        // only the cloud metadata address is ever blocked; everything else is
        // reachable with the user's per-host consent. Resolve the true target
        // first (honest prompt + pin for the cross-origin proxy).
        const pre = await callProxy(proxyUrl, { bookmark_id: currentBookmarkId(), mode: 'preflight', request: req })
        if (pre.error) throw new Error(`blocked: ${pre.reason || pre.error}`)
        const target = pre.target // { ip, kind }

        // Already allowed (a persisted "Forever" or a prior "This session" grant)
        // and still the same kind → no prompt, no queue. Otherwise gate it through
        // serialized, per-host-coalesced consent (which may prompt, and throws
        // "denied by user" on refusal). A kind change (rebind) is not "allowed" →
        // it re-prompts.
        if (!isAllowed(key, target.kind)) {
            await gate(key, target, req.url)
        }

        // Same-origin runs host-direct (browser attaches your TwiCC session →
        // authenticated); cross-origin goes through the pinning server proxy.
        if (sameOrigin) return await hostDirectFetch(req)

        const res = await callProxy(proxyUrl, {
            bookmark_id: currentBookmarkId(),
            mode: 'fetch',
            grant: 'once',
            pinned_ip: target.ip,
            request: req,
        })
        if (res.error) throw new Error(`broker: ${res.reason || res.error}`)
        return res
    }

    return { proxyFetch }
}

/**
 * Wire the broker host onto an artifact iframe over penpal. Returns the penpal
 * connection (call `.destroy()` to tear down). `opts` are forwarded to
 * `createBrokerHost`.
 */
export function mountBrokerHost(iframe, opts) {
    const host = createBrokerHost(opts)
    const messenger = new WindowMessenger({
        remoteWindow: iframe.contentWindow,
        allowedOrigins: ['*'], // same-origin in practice; the window target is the real bound
    })
    return connect({ messenger, methods: { proxyFetch: (req) => host.proxyFetch(req), ...createInlineBrokerMethods(opts) } })
}

/** Optional RPCs share the bound iframe connection, with document generation checks. */
export function createInlineBrokerMethods(opts) {
    const current = report => Number.isInteger(opts.inlineGeneration)
        && report?.inlineGeneration === opts.inlineGeneration
    const mode = () => opts.getInlineMode?.() ?? 'inline'
    return {
        reportInlineReady(report) { if (current(report)) opts.onInlineReady?.() },
        getInlineState(report) {
            if (!current(report)) return null
            return { inlineGeneration: opts.inlineGeneration, mode: mode(), requestedHeight: opts.inlineRequestedHeight ?? 360 }
        },
        reportInlineHeight(report) {
            if (!current(report) || !Number.isFinite(report.height) || report.mode !== 'inline' || mode() !== 'inline') return
            opts.onInlineHeight?.(report.height)
        },
        requestInlineEscape(report) { if (current(report)) opts.onInlineEscape?.() },
    }
}
