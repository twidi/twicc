// The upload lifecycle controller, driven with fake dependencies: a fake
// clock and timers, a fake `apiFetch` (real `Response` objects), a fake tus
// `Upload` factory, a fake toast, a fake event source and wake lock.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createUploadsController, PROBE_AFTER_MS, TOAST_DURATION_MS } from './controller.js'
import { fingerprintFile } from './ids.js'
import { RETRY_DELAYS } from './transport.js'

const TAB = 'tab-1'
const ORIGIN = { panel: 'files', key: 'session:s1' }
const START = 1_800_000_000_000

// ── Fakes ────────────────────────────────────────────────────────────────────

async function flush() {
    for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve))
}

function createClock() {
    let t = START
    let seq = 0
    const timers = new Map()
    return {
        now: () => t,
        setTimeout: (fn, ms) => {
            const id = ++seq
            timers.set(id, { fn, at: t + (ms || 0) })
            return id
        },
        clearTimeout: id => { timers.delete(id) },
        async advance(ms) {
            const end = t + ms
            for (;;) {
                await flush()
                let next = null
                for (const [id, timer] of timers) {
                    if (timer.at <= end && (!next || timer.at < next[1].at)) next = [id, timer]
                }
                if (!next) break
                timers.delete(next[0])
                t = next[1].at
                next[1].fn()
            }
            t = end
            await flush()
        },
    }
}

function respond(status, body = null, { upload = true } = {}) {
    const headers = upload ? { 'X-Twicc-Upload': '1' } : {}
    if (body === null || status === 204) return new Response(null, { status, headers })
    return new Response(JSON.stringify(body), { status, headers: { ...headers, 'Content-Type': 'application/json' } })
}

function tusError(status, { upload = false, body = null } = {}) {
    const err = new Error('tus error')
    err.originalRequest = {}
    if (status !== null) {
        err.originalResponse = {
            getStatus: () => status,
            getHeader: name => (upload && name === 'X-Twicc-Upload' ? '1' : null),
            getBody: () => body,
        }
    }
    return err
}

let idSeq = 0
function newId() {
    idSeq += 1
    return idSeq.toString(16).padStart(32, '0')
}

function makeRecord(fields = {}) {
    return {
        id: newId(),
        client_id: 'other-tab:0000000000000000',
        state: 'active',
        version: 1,
        filename: 'f.txt',
        target_dir: '/p',
        size: 5,
        offset: 0,
        origin: { ...ORIGIN },
        fingerprint: 'fp',
        final_path: null,
        error: null,
        created_at: new Date(START).toISOString(),
        updated_at: new Date(START).toISOString(),
        ...fields,
    }
}

/**
 * A fake backend: records every request, answers with the handler of its
 * method (`POST`, `GET`, `DELETE`, `HEAD`). A handler may return a Response,
 * throw (network error), or return a promise that never settles.
 */
function createHarness(options = {}) {
    const clock = createClock()
    const requests = []
    const server = {
        records: new Map(), // client_id → record created by POST
        POST: req => {
            const existing = server.records.get(req.body.client_id)
            if (existing) return respond(200, existing)
            const record = makeRecord({
                client_id: req.body.client_id,
                filename: req.body.filename,
                size: req.body.size,
                target_dir: req.body.target_dir,
                origin: req.body.origin,
                fingerprint: req.body.fingerprint,
            })
            server.records.set(req.body.client_id, record)
            return respond(201, record)
        },
        GET: () => respond(200, { uploads: [], now: new Date(clock.now()).toISOString() }),
        DELETE: () => respond(204),
        HEAD: () => respond(200),
        ignoreAbort: false, // true: an answer already on its way is not stopped by an abort
    }
    const apiFetch = async (url, init = {}) => {
        const req = {
            url,
            method: init.method || 'GET',
            headers: init.headers || {},
            body: init.body ? JSON.parse(init.body) : null,
            signal: init.signal,
        }
        requests.push(req)
        const answer = server[req.method](req)
        if (req.signal && !server.ignoreAbort && answer && typeof answer.then === 'function') {
            return new Promise((resolve, reject) => {
                req.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
                answer.then(resolve, reject)
            })
        }
        return answer
    }
    const uploads = []
    const createTusUpload = (file, opts) => {
        const upload = {
            file,
            options: opts,
            started: 0,
            aborted: 0,
            start() { this.started += 1 },
            abort() { this.aborted += 1 },
        }
        uploads.push(upload)
        return upload
    }
    const toasts = []
    const toast = {
        success: (message, opts = {}) => toasts.push({ type: 'success', message, ...opts }),
        error: (message, opts = {}) => toasts.push({ type: 'error', message, ...opts }),
    }
    const listeners = new Map()
    const events = {
        on(type, handler) {
            if (!listeners.has(type)) listeners.set(type, new Set())
            listeners.get(type).add(handler)
            return () => listeners.get(type).delete(handler)
        },
        emit(type, event = {}) {
            for (const handler of listeners.get(type) || []) handler(event)
        },
    }
    const env = { authenticated: true, appNavigation: false, visible: true, unauthorized: 0 }
    const wakeLock = { requests: 0, releases: 0, request() { this.requests += 1 }, release() { this.releases += 1 } }
    let hex = 0
    const controller = createUploadsController({
        apiFetch,
        createTusUpload,
        toast,
        now: clock.now,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        tabId: TAB,
        randomHex: n => (hex++).toString(16).padStart(n, '0'),
        isAuthenticated: () => env.authenticated,
        onUnauthorized: () => { env.unauthorized += 1 },
        isAppNavigation: () => env.appNavigation,
        wakeLock,
        isVisible: () => env.visible,
        events,
        ...options,
    })
    const helpers = {
        clock, requests, server, uploads, toasts, events, env, wakeLock, controller,
        list: () => [...controller.entries.values()],
        byState: state => [...controller.entries.values()].filter(e => e.localState === state),
        posts: () => requests.filter(r => r.method === 'POST'),
        deletes: () => requests.filter(r => r.method === 'DELETE'),
        heads: () => requests.filter(r => r.method === 'HEAD'),
        gets: () => requests.filter(r => r.method === 'GET'),
        errorToasts: () => toasts.filter(t => t.type === 'error'),
        lastUpload: () => uploads[uploads.length - 1],
        async pick(names = ['a.txt'], content = 'hello') {
            const files = names.map(name => new File([content], name))
            await controller.startUploads({ files, targetDir: '/p', apiPrefix: '/api/projects/p1', origin: ORIGIN })
            await flush()
            return files
        },
    }
    return helpers
}

/** Serve `upload_state` records for an entry, bumping the version. */
function next(record, fields = {}) {
    return { ...record, version: record.version + 1, ...fields }
}

// ── Creation and transfer ────────────────────────────────────────────────────

test('a pick creates, then sends with the tus options of §6.5', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [post] = h.posts()
    assert.equal(post.url, '/api/projects/p1/uploads/')
    assert.equal(post.body.filename, 'a.txt')
    assert.equal(post.body.size, 5)
    assert.equal(post.body.target_dir, '/p')
    assert.deepEqual(post.body.origin, ORIGIN)
    assert.equal(post.body.fingerprint, await fingerprintFile(new File(['hello'], 'a.txt')))
    assert.ok(post.body.client_id.startsWith(`${TAB}:`))
    assert.equal('root' in post.body, false)
    const [entry] = h.list()
    assert.equal(entry.localState, 'sending')
    assert.equal(entry.local, true)
    const upload = h.lastUpload()
    assert.equal(upload.started, 1)
    assert.equal(upload.options.uploadUrl, `/api/uploads/${entry.server.id}/`)
    assert.equal(upload.options.endpoint, undefined)
    assert.equal(upload.options.chunkSize, 8 * 1024 * 1024)
    assert.deepEqual(upload.options.retryDelays, [0, 1000, 3000, 5000, 10000, 20000, 30000, 60000])
    assert.equal(upload.options.storeFingerprintForResuming, false)
})

test('root is sent only for the /api prefix with a root restriction', async () => {
    const h = createHarness()
    await h.controller.startUploads({
        files: [new File(['x'], 'r.txt')], targetDir: '/art/s', apiPrefix: '/api', root: '/art', origin: ORIGIN,
    })
    await h.controller.startUploads({
        files: [new File(['x'], 'n.txt')], targetDir: '/art/s', apiPrefix: '/api', root: null, origin: ORIGIN,
    })
    await flush()
    const [withRoot, withoutRoot] = h.posts()
    assert.equal(withRoot.url, '/api/uploads/')
    assert.equal(withRoot.body.root, '/art')
    assert.equal('root' in withoutRoot.body, false)
})

test('a file that cannot be read: error toast, no entry', async () => {
    const h = createHarness()
    const broken = { name: 'bad.bin', size: 3, slice: () => ({ arrayBuffer: async () => { throw new Error('x') } }) }
    await h.controller.startUploads({ files: [broken], targetDir: '/p', apiPrefix: '/api', origin: ORIGIN })
    assert.equal(h.list().length, 0)
    assert.equal(h.errorToasts().length, 1)
    assert.match(h.errorToasts()[0].title, /bad\.bin/)
})

test('pump: at most 3 entries hold a slot; the slot count is derived', async () => {
    const h = createHarness()
    let release
    const gate = new Promise(resolve => { release = resolve })
    const post = h.server.POST
    h.server.POST = req => gate.then(() => post(req))
    await h.pick(['1', '2', '3', '4', '5'])
    assert.equal(h.byState('creating').length, 3)
    assert.equal(h.byState('queued').length, 2)
    release()
    await flush()
    // Every creation answered: 5 sending would exceed 3 → 3 sending, 2 queued… until one ends.
    assert.equal(h.byState('sending').length, 3)
    assert.equal(h.byState('queued').length, 2)
    h.uploads[0].options.onSuccess()
    await flush()
    assert.equal(h.byState('sending').length, 3)
    assert.equal(h.byState('queued').length, 1)
})

test('creation 4xx with X-Twicc-Upload removes the entry with a toast; without it, it is retried', async () => {
    const h = createHarness()
    h.server.POST = () => respond(403, { error: 'Path out of scope' })
    await h.pick(['a.txt'])
    assert.equal(h.list().length, 0)
    assert.equal(h.errorToasts().length, 1)
    assert.equal(h.errorToasts()[0].message, 'Path out of scope')
    assert.equal(h.errorToasts()[0].duration, TOAST_DURATION_MS)

    const h2 = createHarness()
    let calls = 0
    const post = h2.server.POST
    h2.server.POST = req => (++calls === 1 ? respond(429, null, { upload: false }) : post(req))
    await h2.pick(['a.txt'])
    await h2.clock.advance(0)
    assert.equal(h2.posts().length, 2)
    assert.equal(h2.posts()[0].body.client_id, h2.posts()[1].body.client_id)
    assert.equal(h2.list()[0].localState, 'sending')
    assert.equal(h2.errorToasts().length, 0)
})

test('creation 507 from the upload code: entry removed, toast', async () => {
    const h = createHarness()
    h.server.POST = () => respond(507, { error: 'Not enough disk space' })
    await h.pick(['a.txt'])
    assert.equal(h.list().length, 0)
    assert.equal(h.errorToasts().length, 1)
})

test('creation: every 5xx except 507 is retried in place with the retryDelays, then pauses', async () => {
    const h = createHarness()
    h.server.POST = () => respond(502, null, { upload: false })
    await h.pick(['a.txt'])
    const [entry] = h.list()
    assert.equal(entry.localState, 'creating')
    assert.equal(entry.creationUnanswered, true)
    for (const delay of RETRY_DELAYS) await h.clock.advance(delay)
    assert.equal(h.posts().length, RETRY_DELAYS.length + 1)
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'network')
    assert.equal(h.errorToasts().length, 1)
})

test('creation: a transient failure never pauses the queue', async () => {
    const h = createHarness()
    let calls = 0
    const post = h.server.POST
    h.server.POST = req => (++calls <= 2 ? respond(503, null, { upload: false }) : post(req))
    await h.pick(['a.txt', 'b.txt'])
    await h.clock.advance(2000)
    assert.equal(h.byState('sending').length, 2)
    assert.equal(h.byState('paused').length, 0)
    assert.equal(h.errorToasts().length, 0)
})

test('creation: a network error or a 30 s timeout is retried like no answer', async () => {
    const h = createHarness()
    let calls = 0
    const post = h.server.POST
    h.server.POST = req => {
        calls += 1
        if (calls === 1) throw new TypeError('network')
        if (calls === 2) return new Promise(() => {}) // never answers
        return post(req)
    }
    await h.pick(['a.txt'])
    await h.clock.advance(0) // first retry delay
    assert.equal(calls, 2)
    await h.clock.advance(30_000) // the timeout aborts the POST
    await h.clock.advance(1000) // second retry delay
    assert.equal(calls, 3)
    assert.equal(h.list()[0].localState, 'sending')
})

test('a 500 with X-Twicc-Upload after the retries → error pause; without it → network pause', async () => {
    const h = createHarness()
    h.server.POST = () => respond(500, { error: 'boom' })
    await h.pick(['a.txt'])
    for (const delay of RETRY_DELAYS) await h.clock.advance(delay)
    const [entry] = h.list()
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'error')
    assert.equal(h.errorToasts().length, 1)
    assert.match(h.errorToasts()[0].message, /retry it from the Files tab/)

    const h2 = createHarness()
    h2.server.POST = () => respond(500, null, { upload: false })
    await h2.pick(['a.txt'])
    for (const delay of RETRY_DELAYS) await h2.clock.advance(delay)
    assert.equal(h2.list()[0].pauseReason, 'network')
})

test('creation 401: stays creating (the login redirect follows)', async () => {
    const h = createHarness()
    h.server.POST = () => respond(401, null, { upload: false })
    await h.pick(['a.txt'])
    await h.clock.advance(60_000)
    assert.equal(h.posts().length, 1)
    assert.equal(h.list()[0].localState, 'creating')
})

test('creation 201 of a finalizing record → null; active with error → error pause', async () => {
    const h = createHarness()
    h.server.POST = req => respond(201, makeRecord({ client_id: req.body.client_id, state: 'finalizing', size: 0 }))
    await h.controller.startUploads({ files: [new File([], 'z')], targetDir: '/p', apiPrefix: '/api', origin: ORIGIN })
    await flush()
    assert.equal(h.list()[0].localState, null)

    const h2 = createHarness()
    h2.server.POST = req => respond(201, makeRecord({ client_id: req.body.client_id, size: 0, error: 'disk full' }))
    await h2.controller.startUploads({ files: [new File([], 'z')], targetDir: '/p', apiPrefix: '/api', origin: ORIGIN })
    await flush()
    assert.equal(h2.list()[0].localState, 'paused')
    assert.equal(h2.list()[0].pauseReason, 'error')
    assert.equal(h2.errorToasts().length, 1)
})

test('zero-byte upload completed in the 201: success toast, entry removed, completion event', async () => {
    const h = createHarness()
    const completed = []
    h.controller.onCompleted(record => completed.push(record))
    h.server.POST = req => respond(201, makeRecord({
        client_id: req.body.client_id, state: 'completed', size: 0, final_path: '/p/z (1)',
    }))
    await h.controller.startUploads({ files: [new File([], 'z')], targetDir: '/p', apiPrefix: '/api', origin: ORIGIN })
    await flush()
    assert.equal(h.list().length, 0)
    assert.equal(completed.length, 1)
    assert.deepEqual(h.toasts.map(t => [t.type, t.title, t.message]), [['success', 'Uploaded z (1)', '/p']])
})

// ── networkToastShown and the outage ────────────────────────────────────────

test('outage with a queue: one toast, queued entries stay queued, restart posts again', async () => {
    const h = createHarness()
    const up = h.server.POST
    await h.pick(['x.txt'])
    const [x] = h.list()
    // The backend goes down.
    h.server.POST = () => { throw new TypeError('network') }
    h.uploads[0].options.onError(tusError(null))
    await flush()
    assert.equal(x.pauseReason, 'network')
    assert.equal(h.errorToasts().length, 1)
    // A pick restarts x and creates d and e; f and g wait for a slot.
    await h.pick(['d.txt', 'e.txt', 'f.txt', 'g.txt'])
    const byName = name => h.list().find(e => e.filename === name)
    assert.equal(x.localState, 'sending')
    assert.equal(byName('d.txt').localState, 'creating')
    assert.equal(byName('f.txt').localState, 'queued')
    // x fails again: the queue is gated, f and g stay queued.
    h.uploads[1].options.onError(tusError(502))
    await flush()
    for (const delay of RETRY_DELAYS) await h.clock.advance(delay)
    for (const name of ['d.txt', 'e.txt']) {
        assert.equal(byName(name).localState, 'paused')
        assert.equal(byName(name).pauseReason, 'network')
        assert.equal(byName(name).creationUnanswered, true)
    }
    assert.equal(byName('f.txt').localState, 'queued')
    assert.equal(byName('g.txt').localState, 'queued')
    assert.equal(h.posts().filter(p => p.body.filename === 'f.txt').length, 0)
    assert.equal(h.errorToasts().length, 1) // one toast for the whole outage
    // The server comes back: the restart posts again with the same client_id.
    const dClientId = byName('d.txt').clientId
    h.server.POST = up
    h.events.emit('online')
    await flush()
    assert.ok(h.posts().filter(p => p.body.filename === 'd.txt').every(p => p.body.client_id === dClientId))
    assert.equal(byName('d.txt').localState, 'sending')
    assert.equal(x.localState, 'sending')
    assert.equal(byName('f.txt').localState, 'queued')
    assert.equal(h.controller.internals.networkToastShown, false) // reset by the 2xx POST
    // A later outage toasts again.
    h.lastUpload().options.onError(tusError(null))
    await flush()
    assert.equal(h.errorToasts().length, 2)
})

test('networkToastShown: onProgress does not reset it; onChunkComplete, onSuccess, 2xx POST and GET do', async () => {
    const h = createHarness()
    await h.pick(['a.txt', 'b.txt'])
    const [ua, ub] = h.uploads
    ua.options.onError(tusError(null))
    assert.equal(h.controller.internals.networkToastShown, true)
    ub.options.onProgress(3, 5)
    assert.equal(h.controller.internals.networkToastShown, true)
    ub.options.onChunkComplete(5, 5, 5)
    assert.equal(h.controller.internals.networkToastShown, false)

    const h2 = createHarness()
    await h2.pick(['a.txt', 'b.txt'])
    h2.uploads[0].options.onError(tusError(null))
    h2.uploads[1].options.onSuccess()
    assert.equal(h2.controller.internals.networkToastShown, false)

    const h3 = createHarness()
    await h3.pick(['a.txt'])
    h3.uploads[0].options.onError(tusError(null))
    await h3.controller.reconcile()
    assert.equal(h3.controller.internals.networkToastShown, false)
    await h3.pick(['b.txt'])
    assert.equal(h3.controller.internals.networkToastShown, false)
})

test('a later outage toasts again; the automatic restart toasts once', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    h.uploads[0].options.onError(tusError(null))
    await flush()
    h.events.emit('online')
    await flush()
    h.uploads[1].options.onError(tusError(null))
    await flush()
    assert.equal(h.errorToasts().length, 1) // same outage
    h.events.emit('online')
    await flush()
    h.uploads[2].options.onChunkComplete(5, 5, 5) // the server answered
    h.uploads[2].options.onError(tusError(null))
    await flush()
    assert.equal(h.errorToasts().length, 2)
})

test('onProgress updates sentBytes at most 4 times per second', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    const upload = h.lastUpload()
    upload.options.onProgress(1, 5)
    upload.options.onProgress(2, 5)
    assert.equal(entry.sentBytes, 1)
    await h.clock.advance(250)
    upload.options.onProgress(3, 5)
    assert.equal(entry.sentBytes, 3)
})

// ── tus onError ──────────────────────────────────────────────────────────────

test('onShouldRetry is the §6.5 classification: proxy 4xx retried, 401 and 404 not', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const { onShouldRetry } = h.lastUpload().options
    assert.equal(onShouldRetry(tusError(429)), true)
    assert.equal(onShouldRetry(tusError(429, { upload: true })), false)
    assert.equal(onShouldRetry(tusError(401)), false)
    assert.equal(onShouldRetry(tusError(404)), false)
    assert.equal(onShouldRetry(tusError(507, { upload: true })), false)
})

test('PATCH: a proxy 4xx after the retries → network pause; 401 → onUnauthorized at once', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    h.lastUpload().options.onError(tusError(413))
    await flush()
    assert.equal(h.list()[0].pauseReason, 'network')

    const h2 = createHarness()
    await h2.pick(['a.txt'])
    h2.lastUpload().options.onError(tusError(401))
    await flush()
    assert.equal(h2.env.unauthorized, 1)
    assert.equal(h2.list()[0].localState, 'sending')
    assert.equal(h2.errorToasts().length, 0)
})

test('onError 507 → error pause with the disk-space reason; the File stays', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onError(tusError(507, { upload: true }))
    await flush()
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'error')
    assert.equal(entry.local, true)
    assert.match(h.errorToasts()[0].message, /disk space/)
})

test('onError with start() failing (no originalRequest) → error pause', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const err = new Error('no uploadUrl')
    h.lastUpload().options.onError(err)
    await flush()
    assert.equal(h.list()[0].pauseReason, 'error')
})

test('onError 404 + failed reconcile() → network pause', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    h.server.GET = () => { throw new TypeError('down') }
    h.lastUpload().options.onError(tusError(404))
    await flush()
    const [entry] = h.list()
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'network')
    assert.equal(h.errorToasts().length, 1)
})

test('onError proxy 404 + successful reconcile() returning the same active record → network pause', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    const record = entry.server
    h.server.GET = () => respond(200, { uploads: [record], now: new Date(h.clock.now()).toISOString() })
    h.lastUpload().options.onError(tusError(404))
    await flush()
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'network')
})

test('onError 410 + reconcile() bringing the failed record: one failure toast, entry removed', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    const failed = next(entry.server, { state: 'failed', error: 'expired' })
    h.server.GET = () => respond(200, { uploads: [failed], now: new Date(h.clock.now()).toISOString() })
    h.lastUpload().options.onError(tusError(410, { upload: true }))
    await flush()
    assert.equal(h.list().length, 0)
    assert.deepEqual(h.errorToasts().map(t => t.message), ['expired'])
})

test('a retryable status while the record is active with error → error pause with that reason', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    // The server record says the finalization failed (the entry is still sending here).
    h.controller.applyServerRecord(next(entry.server, { offset: 5, error: 'copy failed' }))
    h.lastUpload().options.onError(tusError(500, { upload: true }))
    await flush()
    assert.equal(entry.pauseReason, 'error')
    assert.match(h.errorToasts()[0].message, /copy failed/)
})

test('a callback of a replaced tus.Upload does nothing', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const old = h.lastUpload()
    old.options.onError(tusError(null))
    await flush()
    h.events.emit('online')
    await flush()
    assert.equal(h.uploads.length, 2)
    old.options.onSuccess()
    old.options.onError(tusError(507, { upload: true }))
    assert.equal(h.list()[0].localState, 'sending')
})

// ── applyServerRecord ────────────────────────────────────────────────────────

test('rule 1: a GET item after a terminal broadcast never brings the upload back', async () => {
    const h = createHarness()
    const record = makeRecord()
    h.controller.applyServerRecord(record, { fromWs: true })
    h.controller.applyServerRecord(next(record, { state: 'cancelled' }), { fromWs: true })
    assert.equal(h.list().length, 0)
    h.controller.applyServerRecord(record)
    assert.equal(h.list().length, 0)
})

test('rule 3: a first-seen completed tombstone emits the completion event, no entry', () => {
    const h = createHarness()
    const events = []
    h.controller.onCompleted(r => events.push(r.id))
    const record = makeRecord({ state: 'completed', final_path: '/p/f.txt' })
    h.controller.applyServerRecord(record)
    assert.deepEqual(events, [record.id])
    assert.equal(h.list().length, 0)
    assert.ok(h.controller.internals.handledTerminal.has(record.id))
    h.controller.applyServerRecord(next(record))
    assert.deepEqual(events, [record.id])
})

test('rule 3: attach by clientId; never into an entry holding another upload (duplicate tab)', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    // Same client_id, another server upload (a duplicated tab reusing ids).
    const other = makeRecord({ client_id: entry.clientId })
    h.controller.applyServerRecord(other, { fromWs: true })
    assert.equal(h.list().length, 2)
    assert.equal(entry.server.id !== other.id, true)
    assert.equal(h.controller.entries.get(other.id).local, false)
})

test('rule 3: an entry created here receives the WebSocket record before the POST answer', async () => {
    const h = createHarness()
    let release
    const post = h.server.POST
    h.server.POST = req => new Promise(resolve => { release = () => resolve(post(req)) })
    await h.pick(['a.txt'])
    const [entry] = h.list()
    const record = makeRecord({ client_id: entry.clientId })
    h.server.records.set(entry.clientId, record)
    h.controller.applyServerRecord(record, { fromWs: true })
    assert.equal(entry.server.id, record.id)
    assert.equal(entry.localState, 'creating')
    release()
    await flush()
    assert.equal(h.list().length, 1)
    assert.equal(entry.localState, 'sending')
})

test('rule 3: reload then a new upload with the same client_id: the two entries stay separate', async () => {
    const h = createHarness()
    // An upload of this tab before a reload: now non-local. Its client_id is
    // the one the next pick generates (the fake randomHex starts at 0).
    const clientId = `${TAB}:0000000000000000`
    const old = makeRecord({ client_id: clientId })
    h.controller.applyServerRecord(old, { fromWs: true })
    await h.pick(['new.txt'])
    assert.equal(h.posts()[0].body.client_id, clientId)
    assert.equal(h.list().length, 2)
    const fresh = h.controller.entries.get(clientId)
    assert.notEqual(fresh.server.id, old.id)
    // A later record of the old upload goes to its own entry.
    h.controller.applyServerRecord(next(old, { offset: 3 }), { fromWs: true })
    assert.equal(h.controller.entries.get(old.id).server.offset, 3)
    assert.equal(fresh.server.offset, 0)
    assert.equal(fresh.localState, 'sending')
    assert.equal(h.controller.entries.get(old.id).local, false)
})

test('finalization failed → Retry → the HEAD loses the network → network pause, then the restart', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onSuccess()
    h.controller.applyServerRecord(next(entry.server, { offset: 5, error: 'copy failed' }), { fromWs: true })
    assert.equal(entry.pauseReason, 'error')
    assert.equal(h.errorToasts().length, 1)
    h.controller.retry(entry.key)
    assert.equal(entry.localState, 'sending')
    h.lastUpload().options.onError(tusError(null))
    await flush()
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'network')
    assert.equal(h.errorToasts().length, 2)
    assert.equal(h.errorToasts()[1].title, 'Upload paused')
    h.events.emit('online')
    assert.equal(entry.localState, 'sending')
    assert.equal(h.uploads.length, 3)
})

test('rule 4: a record not newer than the stored one is ignored', () => {
    const h = createHarness()
    const record = makeRecord({ version: 5, offset: 3 })
    h.controller.applyServerRecord(record)
    h.controller.applyServerRecord({ ...record, version: 4, offset: 1 })
    h.controller.applyServerRecord({ ...record, version: 5, offset: 2 })
    assert.equal(h.controller.entries.get(record.id).server.offset, 3)
    h.controller.applyServerRecord({ ...record, version: 6, offset: 4 })
    assert.equal(h.controller.entries.get(record.id).server.offset, 4)
})

test('rule 2: an orphan clientId is deleted when its upload shows up; it leaves the set on success', async () => {
    const h = createHarness()
    h.server.POST = () => { throw new TypeError('network') }
    await h.pick(['a.txt'])
    const [entry] = h.list()
    await h.controller.cancel(entry.key) // creating: abort → orphan
    await flush()
    assert.ok(h.controller.internals.orphanClientIds.has(entry.clientId))
    let deleteStatus = 500
    h.server.DELETE = () => respond(deleteStatus)
    const record = makeRecord({ client_id: entry.clientId })
    h.controller.applyServerRecord(record, { fromWs: true })
    await flush()
    assert.equal(h.deletes().length, 1)
    assert.ok(h.controller.internals.orphanClientIds.has(entry.clientId))
    assert.equal(h.list().length, 0)
    deleteStatus = 204
    h.controller.applyServerRecord(next(record), { fromWs: true })
    await flush()
    assert.equal(h.deletes().length, 2)
    assert.equal(h.controller.internals.orphanClientIds.has(entry.clientId), false)
})

test('rule 5: seenAt sets lastSeenAt; receivedAt is now', () => {
    const h = createHarness()
    const record = makeRecord()
    h.controller.applyServerRecord(record, { seenAt: START - 1000 })
    const entry = h.controller.entries.get(record.id)
    assert.equal(entry.lastSeenAt, START - 1000)
    assert.equal(entry.receivedAt, START)
})

test('rule 7: active with error → error pause; short offset after finalizing → queued; late record → nothing', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onSuccess()
    assert.equal(entry.localState, null)
    // A late active record of an earlier chunk: nothing.
    h.controller.applyServerRecord(next(entry.server, { offset: 3 }), { fromWs: true })
    assert.equal(entry.localState, null)
    // finalizing, then active with a short offset (recovery) → queued, then sending.
    h.controller.applyServerRecord(next(entry.server, { state: 'finalizing', offset: 5 }), { fromWs: true })
    h.controller.applyServerRecord(next(entry.server, { state: 'active', offset: 2 }), { fromWs: true })
    assert.equal(entry.localState, 'sending')
    assert.equal(entry.sentBytes, 2)
    // A failed finalization that kept the bytes.
    h.lastUpload().options.onSuccess()
    h.controller.applyServerRecord(next(entry.server, { state: 'active', offset: 5, error: 'disk full' }), { fromWs: true })
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'error')
    assert.match(h.errorToasts()[0].message, /disk full/)
})

test('rule 0: a WebSocket record ignored by rules 1–4 still restarts; a GET item does not', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onError(tusError(null))
    await flush()
    assert.equal(entry.pauseReason, 'network')
    // An old (ignored) record from a GET: no restart.
    h.controller.applyServerRecord(entry.server)
    assert.equal(entry.localState, 'paused')
    // The same ignored record by WebSocket: restart.
    h.controller.applyServerRecord(entry.server, { fromWs: true })
    assert.equal(entry.localState, 'sending')
    assert.equal(h.uploads.length, 2)
})

// ── Terminal handling and toasts ─────────────────────────────────────────────

test('completed: success toast with the final name, completion event, entry removed', async () => {
    const h = createHarness()
    const events = []
    h.controller.onCompleted(r => events.push(r))
    await h.pick(['a.txt'])
    const [entry] = h.list()
    const upload = h.lastUpload()
    h.controller.applyServerRecord(next(entry.server, { state: 'completed', offset: 5, final_path: '/p/a (1).txt' }), { fromWs: true })
    assert.equal(upload.aborted, 1)
    assert.equal(h.list().length, 0)
    assert.equal(events.length, 1)
    assert.deepEqual(h.toasts.map(t => [t.type, t.title, t.message, t.duration]),
        [['success', 'Uploaded a (1).txt', '/p', TOAST_DURATION_MS]])
})

test('a non-local entry does not toast on its end', () => {
    const h = createHarness()
    const record = makeRecord()
    h.controller.applyServerRecord(record)
    h.controller.applyServerRecord(next(record, { state: 'failed', error: 'expired' }))
    assert.equal(h.toasts.length, 0)
    assert.equal(h.list().length, 0)
})

test('cancelled elsewhere → one toast; a cancel of this tab whose DELETE answer was lost → none', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.controller.applyServerRecord(next(entry.server, { state: 'cancelled' }), { fromWs: true })
    assert.equal(h.errorToasts().length, 1)
    assert.match(h.errorToasts()[0].message, /another tab or on another device/)

    const h2 = createHarness()
    await h2.pick(['a.txt'])
    const [e2] = h2.list()
    h2.server.DELETE = () => { throw new TypeError('lost') }
    await h2.controller.cancel(e2.key)
    // Back to queued, which the pump sends again.
    assert.equal(e2.localState, 'sending')
    await flush()
    h2.controller.applyServerRecord(next(e2.server, { state: 'cancelled' }), { fromWs: true })
    assert.equal(h2.toasts.length, 0)
    assert.equal(h2.list().length, 0)
})

// ── reconcile() ──────────────────────────────────────────────────────────────

test('reconcile(): stalled at once from the server now; own tab id without File stalled at once', async () => {
    const h = createHarness()
    const old = makeRecord({ updated_at: new Date(START - 20 * 60_000).toISOString() })
    const fresh = makeRecord({ updated_at: new Date(START - 1000).toISOString() })
    const mine = makeRecord({ client_id: `${TAB}:abcdef0123456789`, updated_at: new Date(START).toISOString() })
    // The server clock is 1 h ahead of the client clock.
    const serverNow = new Date(START + 3_600_000)
    h.server.GET = () => respond(200, {
        uploads: [
            { ...old, updated_at: new Date(Date.parse(old.updated_at) + 3_600_000).toISOString() },
            { ...fresh, updated_at: new Date(Date.parse(fresh.updated_at) + 3_600_000).toISOString() },
            mine,
        ],
        now: serverNow.toISOString(),
    })
    // No entry and no timer yet: only reconcile() itself can refresh `now`.
    await h.clock.advance(3_600_000)
    await h.controller.reconcile()
    const get = id => h.controller.entries.get(id)
    assert.equal(h.controller.isStalled(get(old.id)), true)
    assert.equal(h.controller.isStalled(get(fresh.id)), false)
    assert.equal(h.controller.isStalled(get(mine.id)), true)
    assert.deepEqual(h.controller.entryActions(get(mine.id)).buttons, ['resume', 'cancel'])
    assert.equal(h.controller.statusByOrigin.value['files|session:s1'].allStalled, false)
})

test('reconcile(): the now refresh at its end makes an old entry stalled before the timer ticks', async () => {
    const h = createHarness()
    // A non-local entry: the 15 s timer runs and now = START.
    h.controller.applyServerRecord(makeRecord(), { fromWs: true })
    assert.equal(h.controller.now.value, START)
    await h.clock.advance(14_000) // the timer has not ticked
    const current = h.clock.now()
    const old = makeRecord({ updated_at: new Date(current - 185_000).toISOString() })
    h.server.GET = () => respond(200, { uploads: [old], now: new Date(current).toISOString() })
    await h.controller.reconcile()
    assert.equal(h.controller.isStalled(h.controller.entries.get(old.id)), true)
})

test('reconcile(): no drop of queued / creating entries, nor of an entry received after requestedAt', async () => {
    const h = createHarness()
    let releasePost
    const post = h.server.POST
    h.server.POST = req => new Promise(resolve => { releasePost = () => resolve(post(req)) })
    await h.pick(['a.txt'])
    const creating = h.list()[0]
    let releaseGet
    h.server.GET = () => new Promise(resolve => {
        releaseGet = () => resolve(respond(200, { uploads: [], now: new Date(h.clock.now()).toISOString() }))
    })
    const reconciling = h.controller.reconcile()
    await flush()
    // A record received after the GET started is kept.
    await h.clock.advance(10)
    const late = makeRecord()
    h.controller.applyServerRecord(late, { fromWs: true })
    releaseGet()
    await reconciling
    assert.ok(h.controller.entries.get(late.id))
    assert.equal(creating.localState, 'creating')
    assert.ok(h.controller.entries.get(creating.key))
    releasePost()
    await flush()
})

test('reconcile(): a creating entry with its record and a queued entry with a record are not dropped', async () => {
    // A creating entry that already received its WebSocket record.
    const h = createHarness()
    let releasePost
    const post = h.server.POST
    h.server.POST = req => new Promise(resolve => { releasePost = () => resolve(post(req)) })
    await h.pick(['c.txt'])
    const [creating] = h.list()
    h.controller.applyServerRecord(makeRecord({ client_id: creating.clientId }), { fromWs: true })
    assert.ok(creating.server)
    assert.equal(creating.localState, 'creating')
    await h.clock.advance(10)
    await h.controller.reconcile()
    assert.equal(h.controller.entries.get(creating.key), creating)
    releasePost()
    await flush()

    // A queued entry with a record (a failed cancel while a network pause gates the pump).
    const h2 = createHarness()
    await h2.pick(['a.txt', 'b.txt'])
    const [a, b] = h2.list()
    h2.uploads[0].options.onError(tusError(null))
    await flush()
    h2.server.DELETE = () => respond(409)
    await h2.controller.cancel(b.key)
    assert.equal(b.localState, 'queued')
    assert.ok(b.server)
    await h2.clock.advance(10)
    await h2.controller.reconcile()
    assert.equal(h2.controller.entries.get(b.key), b)
    assert.equal(h2.controller.entries.has(a.key), false) // the paused one is dropped
})

test('reconcile(): a local entry absent from the answer is dropped with one "lost" toast', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const upload = h.lastUpload()
    await h.clock.advance(10)
    await h.controller.reconcile()
    assert.equal(h.list().length, 0)
    assert.equal(upload.aborted, 1)
    assert.deepEqual(h.errorToasts().map(t => t.message), ['The upload is no longer on the server.'])
})

test('reconcile(): a dropped cancelling entry shows no toast; a failed GET changes nothing', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    await h.controller.cancel(h.list()[0].key) // 204: stays cancelling
    assert.equal(h.list()[0].localState, 'cancelling')
    h.server.GET = () => respond(503, null, { upload: false })
    await h.clock.advance(10)
    await h.controller.reconcile()
    assert.equal(h.list().length, 1)
    h.server.GET = () => { throw new TypeError('down') }
    await h.controller.reconcile()
    assert.equal(h.list().length, 1)
    h.server.GET = createHarness().server.GET
    await h.controller.reconcile()
    assert.equal(h.list().length, 0)
    assert.equal(h.toasts.length, 0)
})

// ── Cancel ───────────────────────────────────────────────────────────────────

test('cancel during creating: the POST is aborted and unanswered → orphan set, no transfer, no toast', async () => {
    const h = createHarness()
    let release
    const post = h.server.POST
    h.server.POST = req => new Promise(resolve => { release = () => resolve(post(req)) })
    await h.pick(['a.txt'])
    const [entry] = h.list()
    await h.controller.cancel(entry.key)
    assert.equal(entry.cancelRequested, true)
    assert.equal(h.posts()[0].signal.aborted, true)
    await flush()
    assert.equal(h.list().length, 0)
    assert.ok(h.controller.internals.orphanClientIds.has(entry.clientId))
    assert.equal(h.posts().length, 1)
    assert.equal(h.uploads.length, 0)
    assert.equal(h.toasts.length, 0)
    assert.equal(h.gets().length, 1) // reconcile()
    release()
})

test('cancel during creating with a 201 already in: DELETE, no transfer, no toast', async () => {
    const h = createHarness()
    h.server.ignoreAbort = true
    let release
    const post = h.server.POST
    h.server.POST = req => new Promise(resolve => { release = () => resolve(post(req)) })
    await h.pick(['a.txt'])
    const [entry] = h.list()
    await h.controller.cancel(entry.key)
    assert.equal(entry.cancelRequested, true)
    release()
    await flush()
    assert.equal(h.deletes().length, 1)
    assert.equal(h.uploads.length, 0)
    assert.equal(entry.localState, 'cancelling')
    h.controller.applyServerRecord(next(entry.server, { state: 'cancelled' }), { fromWs: true })
    assert.equal(h.list().length, 0)
    assert.equal(h.toasts.length, 0)
})

test('cancel during creating, then 201, then DELETE 409 → queued, then sending; cancelRequested cleared', async () => {
    const h = createHarness()
    h.server.ignoreAbort = true
    h.server.DELETE = () => respond(409)
    let release
    const post = h.server.POST
    h.server.POST = req => new Promise(resolve => { release = () => resolve(post(req)) })
    await h.pick(['a.txt'])
    const [entry] = h.list()
    await h.controller.cancel(entry.key)
    release()
    await flush()
    assert.equal(h.deletes().length, 1)
    assert.equal(entry.cancelRequested, false)
    // Back to queued (never creating again: no second POST), which the pump sends.
    assert.equal(h.posts().length, 1)
    assert.equal(entry.localState, 'sending')
    assert.equal(h.uploads.length, 1)
    assert.equal(h.toasts.length, 0)
})

test('cancel during a creation retry delay ends it at once, no further POST', async () => {
    const h = createHarness()
    h.server.POST = () => respond(502, null, { upload: false })
    await h.pick(['a.txt'])
    await h.clock.advance(0) // second POST, then waits 1000 ms
    const postsBefore = h.posts().length
    const [entry] = h.list()
    await h.controller.cancel(entry.key)
    await flush()
    assert.equal(h.list().length, 0)
    await h.clock.advance(120_000)
    assert.equal(h.posts().length, postsBefore)
    assert.ok(h.controller.internals.orphanClientIds.has(entry.clientId))
    assert.equal(h.toasts.length, 0)
})

test('cancel of a queued entry without server record: removed, no request', async () => {
    const h = createHarness()
    let release
    const post = h.server.POST
    h.server.POST = req => new Promise(resolve => { release = () => resolve(post(req)) })
    await h.pick(['1', '2', '3', '4'])
    const queued = h.byState('queued')[0]
    await h.controller.cancel(queued.key)
    assert.equal(h.controller.entries.has(queued.key), false)
    assert.equal(h.deletes().length, 0)
    assert.equal(h.gets().length, 0)
    release()
})

test('cancel of a paused entry without server record and creationUnanswered: orphan + reconcile', async () => {
    const h = createHarness()
    h.server.POST = () => { throw new TypeError('down') }
    await h.pick(['a.txt'])
    for (const delay of RETRY_DELAYS) await h.clock.advance(delay)
    const [entry] = h.list()
    assert.equal(entry.localState, 'paused')
    await h.controller.cancel(entry.key)
    assert.equal(h.list().length, 0)
    assert.ok(h.controller.internals.orphanClientIds.has(entry.clientId))
    assert.equal(h.gets().length, 1)
})

test('cancel during sending: DELETE 409 → queued, no toast, the slot is freed', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    const upload = h.lastUpload()
    h.server.DELETE = () => respond(409)
    await h.controller.cancel(entry.key)
    await flush()
    assert.equal(upload.aborted, 1)
    assert.ok(h.controller.internals.cancelledHere.has(entry.server.id))
    // Back to queued, which the pump sends again (it has a server record).
    assert.equal(entry.localState, 'sending')
    assert.equal(h.uploads.length, 2)
    assert.equal(entry.cancelRequested, false)
    assert.equal(h.toasts.length, 0)
    assert.deepEqual(h.controller.entryActions(entry).buttons, ['cancel'])
})

test('cancel from paused with a failed DELETE → paused again', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onError(tusError(507, { upload: true }))
    await flush()
    h.server.DELETE = () => respond(500)
    await h.controller.cancel(entry.key)
    assert.equal(entry.localState, 'paused')
    assert.equal(entry.pauseReason, 'error')
})

test('cancel: DELETE 404 → entry removed, then reconcile()', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    h.server.DELETE = () => respond(404)
    await h.controller.cancel(h.list()[0].key)
    assert.equal(h.list().length, 0)
    assert.equal(h.gets().length, 1)
})

test('cancel: an entry removed by a terminal record meanwhile is not restored', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    let answer
    h.server.DELETE = () => new Promise(resolve => { answer = resolve })
    const cancelling = h.controller.cancel(entry.key)
    await flush()
    h.controller.applyServerRecord(next(entry.server, { state: 'completed', offset: 5, final_path: '/p/a.txt' }), { fromWs: true })
    answer(respond(409))
    await cancelling
    assert.equal(h.list().length, 0)
})

test('cancel is not possible while finalizing', async () => {
    const h = createHarness()
    const record = makeRecord({ state: 'finalizing' })
    h.controller.applyServerRecord(record)
    await h.controller.cancel(record.id)
    assert.equal(h.deletes().length, 0)
})

// ── Restarts, Retry, Resume ──────────────────────────────────────────────────

test('online with only queued entries runs the pump', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onSuccess()
    // A queued entry that no pump promoted yet (no entry is paused).
    entry.localState = 'queued'
    h.events.emit('online')
    assert.equal(entry.localState, 'sending')
    assert.equal(h.uploads.length, 2)
})

test('a WebSocket reconnection restarts network-paused entries (reconcile first)', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onError(tusError(null))
    await flush()
    h.server.GET = () => respond(200, { uploads: [entry.server], now: new Date(h.clock.now()).toISOString() })
    await h.controller.reconnected()
    assert.equal(h.gets().length, 1)
    assert.equal(entry.localState, 'sending')
})

test('Retry on one network-paused entry restarts all of them; on an error pause too', async () => {
    const h = createHarness()
    await h.pick(['a.txt', 'b.txt', 'c.txt'])
    const [a, b, c] = h.list()
    h.uploads[0].options.onError(tusError(null))
    h.uploads[1].options.onError(tusError(null))
    h.uploads[2].options.onError(tusError(507, { upload: true }))
    await flush()
    h.controller.retry(a.key)
    assert.deepEqual([a, b, c].map(e => e.localState), ['sending', 'sending', 'paused'])

    const h2 = createHarness()
    await h2.pick(['a.txt', 'b.txt'])
    const [x, y] = h2.list()
    h2.uploads[0].options.onError(tusError(null))
    h2.uploads[1].options.onError(tusError(507, { upload: true }))
    await flush()
    h2.controller.retry(y.key)
    assert.deepEqual([x, y].map(e => e.localState), ['sending', 'sending'])
})

test('Retry of an entry with a server record goes to sending, never to creating', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onError(tusError(507, { upload: true }))
    await flush()
    h.controller.retry(entry.key)
    assert.equal(entry.localState, 'sending')
    assert.equal(h.posts().length, 1)
})

test('a pick while another entry is network-paused → both start', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [a] = h.list()
    h.lastUpload().options.onError(tusError(null))
    await flush()
    await h.pick(['b.txt'])
    assert.deepEqual(h.list().map(e => e.localState), ['sending', 'sending'])
    assert.equal(a.localState, 'sending')
})

test('Resume: size and fingerprint must match; the entry becomes local and sends', async () => {
    const h = createHarness()
    const file = new File(['hello'], 'renamed-by-picker.txt')
    const record = makeRecord({ client_id: `${TAB}:1`, offset: 2, fingerprint: await fingerprintFile(file) })
    h.controller.applyServerRecord(record)
    const entry = h.controller.entries.get(record.id)
    assert.equal(h.controller.isStalled(entry), true)
    assert.equal(h.controller.statusByOrigin.value['files|session:s1'].allStalled, true)
    assert.equal(await h.controller.resume(record.id, new File(['hellO'], 'x')), false)
    assert.equal(await h.controller.resume(record.id, new File(['hell'], 'x')), false)
    assert.equal(h.errorToasts().length, 2)
    assert.equal(await h.controller.resume(record.id, file), true)
    assert.equal(entry.local, true)
    assert.equal(h.controller.internals.hasLocalFile(record.id), true)
    assert.equal(h.controller.isStalled(entry), false)
    assert.equal(h.controller.statusByOrigin.value['files|session:s1'].allStalled, false)
    assert.equal(entry.localState, 'sending')
    assert.equal(entry.sentBytes, 2)
    assert.equal(h.posts().length, 0)
})

test('Resume while another entry is network-paused → both restart', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [a] = h.list()
    h.lastUpload().options.onError(tusError(null))
    await flush()
    const file = new File(['hello'], 'b')
    const record = makeRecord({ client_id: `${TAB}:1`, fingerprint: await fingerprintFile(file) })
    h.controller.applyServerRecord(record)
    await h.controller.resume(record.id, file)
    assert.equal(a.localState, 'sending')
    assert.equal(h.controller.entries.get(record.id).localState, 'sending')
})

test('Resume: the entry changed during the fingerprint read → nothing stored', async () => {
    const h = createHarness()
    const file = new File(['hello'], 'b')
    const record = makeRecord({ client_id: `${TAB}:1`, fingerprint: await fingerprintFile(file) })
    h.controller.applyServerRecord(record)
    const resuming = h.controller.resume(record.id, file)
    h.controller.applyServerRecord(next(record, { state: 'finalizing', offset: 5 }))
    assert.equal(await resuming, false)
    const entry = h.controller.entries.get(record.id)
    assert.equal(entry.local, false)
    assert.equal(entry.localState, null)
    assert.equal(h.uploads.length, 0)
})

test('retryFinalization: one HEAD with Tus-Resumable; one error toast on a failed answer', async () => {
    const h = createHarness()
    const record = makeRecord({ offset: 5, size: 5, error: 'disk full' })
    h.controller.applyServerRecord(record)
    await h.controller.retryFinalization(record.id)
    assert.equal(h.heads().length, 1)
    assert.equal(h.heads()[0].url, `/api/uploads/${record.id}/`)
    assert.equal(h.heads()[0].headers['Tus-Resumable'], '1.0.0')
    assert.equal(h.toasts.length, 0)
    h.server.HEAD = () => respond(507)
    await h.controller.retryFinalization(record.id)
    assert.equal(h.errorToasts().length, 1)
    h.server.HEAD = () => { throw new TypeError('down') }
    await h.controller.retryFinalization(record.id)
    assert.equal(h.errorToasts().length, 2)
})

// ── Timers, wake lock, beforeunload ──────────────────────────────────────────

test('stalled rule: a non-local entry becomes stalled after 180 s through the 15 s timer', async () => {
    const h = createHarness()
    const record = makeRecord()
    h.controller.applyServerRecord(record, { fromWs: true })
    const entry = h.controller.entries.get(record.id)
    assert.equal(h.controller.isStalled(entry), false)
    await h.clock.advance(180_000)
    assert.equal(h.controller.isStalled(entry), false)
    await h.clock.advance(15_000)
    assert.equal(h.controller.isStalled(entry), true)
    assert.equal(h.controller.statusByOrigin.value['files|session:s1'].allStalled, true)
})

test('finalization probe: one HEAD per 60 s for a local entry stuck in finalizing', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const [entry] = h.list()
    h.lastUpload().options.onSuccess()
    h.controller.applyServerRecord(next(entry.server, { state: 'finalizing', offset: 5 }), { fromWs: true })
    await h.clock.advance(PROBE_AFTER_MS)
    assert.equal(h.heads().length, 0)
    await h.clock.advance(15_000)
    assert.equal(h.heads().length, 1)
    assert.equal(h.heads()[0].headers['Tus-Resumable'], '1.0.0')
    await h.clock.advance(45_000)
    assert.equal(h.heads().length, 1)
    await h.clock.advance(30_000)
    assert.equal(h.heads().length, 2)
    h.controller.applyServerRecord(next(entry.server, { state: 'completed', final_path: '/p/a.txt' }), { fromWs: true })
    await h.clock.advance(300_000)
    assert.equal(h.heads().length, 2)
})

test('wake lock: held while an entry is sending, requested again when visible', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    assert.equal(h.wakeLock.requests, 1)
    h.env.visible = false
    h.events.emit('visibilitychange')
    h.env.visible = true
    h.events.emit('visibilitychange')
    assert.equal(h.wakeLock.requests, 2)
    h.lastUpload().options.onSuccess()
    assert.equal(h.wakeLock.releases, 1)
})

test('wake lock absent: nothing happens', async () => {
    const h = createHarness({ wakeLock: null })
    await h.pick(['a.txt'])
    h.lastUpload().options.onSuccess()
    assert.equal(h.list()[0].localState, null)
})

test('beforeunload asks while a local entry works; silent after markAppNavigation and when not authenticated', async () => {
    const h = createHarness()
    const unload = () => {
        const event = { prevented: false, returnValue: undefined, preventDefault() { this.prevented = true } }
        h.events.emit('beforeunload', event)
        return event.prevented
    }
    assert.equal(unload(), false)
    await h.pick(['a.txt'])
    assert.equal(unload(), true)
    h.env.authenticated = false
    assert.equal(unload(), false)
    h.env.authenticated = true
    h.env.appNavigation = true
    assert.equal(unload(), false)
    h.env.appNavigation = false
    h.lastUpload().options.onSuccess() // waiting for the server: nothing to lose here
    assert.equal(unload(), false)
})

test('statusByOrigin follows the entries (sentBytes while sending)', async () => {
    const h = createHarness()
    await h.pick(['a.txt', 'b.txt'])
    h.uploads[0].options.onProgress(5, 5)
    assert.deepEqual(h.controller.statusByOrigin.value['files|session:s1'], { count: 2, percent: 50, allStalled: false })
    assert.equal(h.controller.entriesForOrigin(ORIGIN).length, 2)
    assert.equal(h.controller.entriesForOrigin({ panel: 'artifacts', key: 'session:s1' }).length, 0)
})

test('dispose stops the listeners and transfers', async () => {
    const h = createHarness()
    await h.pick(['a.txt'])
    const upload = h.lastUpload()
    h.controller.dispose()
    assert.equal(upload.aborted, 1)
    h.events.emit('online')
    const event = { preventDefault() { throw new Error('should not be called') } }
    h.events.emit('beforeunload', event)
})
