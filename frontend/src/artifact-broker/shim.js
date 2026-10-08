// Artifact network-broker shim (design §8). Runs first, inside an untrusted
// artifact iframe (injected as the first child of <head> by the backend). It
// transparently routes the artifact's fetch()/XHR through the trusted host
// (the iframe's parent) over a penpal RPC, so widget code and third-party libs
// use plain fetch() and never see CORS. This is DX, not the security boundary:
// the iframe's CSP `connect-src 'none'` is what actually blocks egress — the
// shim only needs to cover the happy path (fetch + XHR).

import { BatchInterceptor } from '@mswjs/interceptors'
import browserInterceptors from '@mswjs/interceptors/presets/browser'
import { WindowMessenger, connect } from 'penpal'
import { createInlineHeightObserver } from '../inline-artifacts/heightObserver.js'

// Arbitrary bytes <-> base64 (btoa/atob only handle binary strings). Bodies
// cross postMessage as base64 because Request/Response aren't structured-clone.
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

async function serializeRequest(request) {
    const clone = request.clone()
    let bodyBase64
    if (request.method !== 'GET' && request.method !== 'HEAD') {
        const buf = new Uint8Array(await clone.arrayBuffer())
        if (buf.length) bodyBase64 = bytesToBase64(buf)
    }
    return {
        url: request.url,
        method: request.method,
        headers: Object.fromEntries(request.headers.entries()),
        body_base64: bodyBase64,
    }
}

function reconstructResponse(serialized) {
    const body = serialized.body_base64 ? base64ToBytes(serialized.body_base64) : null
    const headers = { ...(serialized.headers || {}) }
    // The browser recomputes content-length for the body we hand it; a stale
    // value from upstream would mismatch.
    delete headers['content-length']
    return new Response(body, {
        status: serialized.status,
        statusText: serialized.reason || '',
        headers,
    })
}

// Penpal's connect() sends one SYN and only re-sends when it receives the other
// side's SYN — there is NO periodic retry. So a single missed handshake (the host
// not yet mounted/listening when the shim comes up — e.g. the host re-binds on the
// iframe's `load`, which fires only after the shim) would hang until timeout and
// then fail every request permanently, forcing a manual refresh. We instead retry
// the WHOLE handshake on a short cadence until a host answers, within a generous
// budget: the broker becomes usable as soon as the host is up, and a transient
// miss self-heals on its own.
const HANDSHAKE_ATTEMPT_TIMEOUT_MS = 2000 // per connect() attempt
const HANDSHAKE_RETRY_DELAY_MS = 200 // gap between attempts
const HANDSHAKE_MAX_WAIT_MS = 30000 // overall budget before a request gives up

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

// Connect to the host, retrying the handshake until it succeeds or the budget runs
// out. Each attempt needs a FRESH messenger — penpal forbids reusing one, and a
// timed-out attempt tears its own window listener down — so we build a new
// WindowMessenger per try. Returns the host proxy, or throws once the budget is
// exhausted.
async function connectToHost() {
    const deadline = Date.now() + HANDSHAKE_MAX_WAIT_MS
    let lastError
    do {
        const messenger = new WindowMessenger({
            remoteWindow: window.parent,
            // The host validates the artifact; same-origin in practice.
            allowedOrigins: ['*'],
        })
        try {
            return await connect({ messenger, timeout: HANDSHAKE_ATTEMPT_TIMEOUT_MS }).promise
        } catch (err) {
            lastError = err
            await delay(HANDSHAKE_RETRY_DELAY_MS)
        }
    } while (Date.now() < deadline)
    throw lastError ?? new Error('host handshake timed out')
}

// Persistence sugar over the data/ store (design 2026-08-05 §5). Pure wrapper
// over the same fetch() the interceptor already routes through the host — no
// extra penpal method. Its real point is detectability: an artifact can test
// `window.twicc?.data`, while nothing advertises that a PUT would be accepted.
function installDataApi() {
    const data = {
        async get(name) {
            const res = await fetch('data/' + name)
            if (res.status === 404) return null
            if (!res.ok) throw new Error(`twicc.data.get: ${res.status}`)
            return await res.json()
        },
        async set(name, value) {
            const res = await fetch('data/' + name, {
                method: 'PUT',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify(value),
            })
            if (!res.ok) {
                let detail = ''
                try { detail = (await res.json()).error || '' } catch { /* not JSON */ }
                throw new Error(`twicc.data.set: ${res.status}${detail ? ` (${detail})` : ''}`)
            }
        },
        async list() {
            const res = await fetch('data/')
            if (res.status === 404) return []
            if (!res.ok) throw new Error(`twicc.data.list: ${res.status}`)
            return (await res.json()).files
        },
        async remove(name) {
            const res = await fetch('data/' + name, { method: 'DELETE' })
            if (res.status === 404) return false
            if (!res.ok) throw new Error(`twicc.data.remove: ${res.status}`)
            return true
        },
    }
    window.twicc = Object.assign(window.twicc || {}, { data })
}

function main() {
    // A top-level document (opened directly, without a broker wrapper) has no host
    // and never will — fail fast instead of retrying against ourselves.
    const isTopLevel = window.parent === window

    // One shared, lazily-started handshake. Requests intercepted before it settles
    // await this same promise. On a whole-cycle give-up we clear it so a LATER
    // request kicks off a fresh retry cycle, instead of staying wedged on one
    // rejected promise forever (the old behaviour that forced a manual refresh).
    let hostPromise = null
    function getHost() {
        if (isTopLevel) return Promise.reject(new Error('no host'))
        if (!hostPromise) {
            hostPromise = connectToHost()
            hostPromise.catch(() => {
                hostPromise = null
            })
        }
        return hostPromise
    }

    // Start connecting immediately so the host binds as early as possible; nothing
    // awaits the result until a request needs it.
    if (!isTopLevel) {
        const observer = createInlineHeightObserver({ document, window, getHost })
        window.addEventListener('pagehide', () => observer.dispose(), { once: true })
    }

    // Armed SYNCHRONOUSLY, before any artifact script runs (the shim is injected
    // as a blocking <script> first in <head>). A fetch/XHR fired during the
    // handshake window is captured and held here, never leaked to the native
    // fetch the CSP blocks.
    const interceptor = new BatchInterceptor({
        name: 'twicc-artifact-broker',
        interceptors: browserInterceptors,
    })
    interceptor.apply()

    // Exposed only once the interceptor is armed: every call it makes is a
    // fetch() that MUST be routed through the host (the CSP blocks the native
    // one), so advertising the API earlier could hand out a broken store.
    installDataApi()

    interceptor.on('request', async ({ request, controller }) => {
        let host
        try {
            host = await getHost()
        } catch {
            // No host within the retry budget. Fail with a clear broker error —
            // never fall through to the native fetch the CSP would block. A later
            // request starts a fresh retry cycle (getHost cleared the promise).
            controller.errorWith(new TypeError('broker: host unavailable'))
            return
        }
        let serialized
        try {
            serialized = await serializeRequest(request)
        } catch (err) {
            controller.errorWith(new TypeError(`broker: bad request (${err?.message || err})`))
            return
        }
        try {
            const response = await host.proxyFetch(serialized)
            controller.respondWith(reconstructResponse(response))
        } catch (err) {
            // Denied by the user, blocked by the guard, or upstream failure.
            controller.errorWith(new TypeError(`broker: ${err?.message || 'request denied'}`))
        }
    })
}

main()
