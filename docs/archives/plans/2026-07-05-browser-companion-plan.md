# Browser Companion Script — Implementation Plan (V1: presence + real history)

**Date:** 2026-07-05
**Branch:** `browser-tab` (builds on the Browser pane, see `2026-07-03-browser-pane-plan.md`)
**Status:** plan approved for writing; implementation NOT started — waiting for explicit go.

## 1. Goal

The Browser pane embeds the project's dev server in a plain cross-origin iframe. The
same-origin policy makes the embedded page a black box: the pane cannot see in-page
navigations (its address bar and Back/Forward only track URLs entered in the toolbar) and
cannot drive the page's real history.

`postMessage` is the sanctioned cross-origin channel. This plan adds an **opt-in companion
script** that the user includes in their own dev page. V1 scope, exactly two capabilities:

1. **Presence** — the companion announces itself; the pane shows a status indicator and,
   when the script is missing on a local-looking page, a hint banner with a copyable
   `<script>` snippet.
2. **Real history** — the companion reports every URL change (SPA `pushState`/`replaceState`,
   `popstate`, `hashchange`, full document loads) up to the pane; the pane's Back / Forward /
   Refresh / address-bar navigations are sent down as commands executed against the page's
   own `history` / `location`, so in-page navigation is no longer invisible or lost.

Future work (explicit non-goals here): DOM element picker for "comment on this zone",
source-location mapping (`vite-plugin-vue-inspector`), a Vite plugin auto-injecting the
snippet, console/error forwarding.

## 2. Design decisions

| Decision | Choice | Why |
|---|---|---|
| Delivery | Backend serves a built bundle at `GET /_twicc/browser-companion.js` | `/_twicc/` is the established prefix for injected scripts (broker shim, shell assets); auth-exempt by the middleware's non-API fallthrough (`auth/middleware.py` `_is_data_path`); already proxied by the Vite dev server (`'/_twicc'` entry), so the SPA origin works in dev and prod |
| Script tag type | Classic script (`defer`, **no** `type="module"`, no `crossorigin`) | Classic scripts load cross-origin without CORS; a module script would require CORS headers we don't want to add |
| Build | 4th Vite build (`vite.config.companion.js`), IIFE, source in `frontend/src/browser-companion/` | Exact replica of the broker-shim precedent; keeps the source next to the pane code and lets the companion and `BrowserPane.vue` share one protocol module |
| Protocol module | `frontend/src/browser-companion/protocol.js`, dependency-free, imported by both sides | One source of truth for message envelopes; unit-testable with `node:test` |
| Handshake | companion → `hello` (payload-free, `targetOrigin: '*'`) → host `ack` (targeted) → companion locks `hostOrigin` and only then sends `state` | At `hello` time the embedder is unknown; a hostile page embedding the user's dev server must learn nothing. The URL only flows after the ack, targeted at the acked origin. Commands are only honoured from that origin |
| Host-side validation | `event.source === iframe.contentWindow` for every message; `event.origin` must match the origin recorded at `hello` for post-hello messages | Standard postMessage hygiene; immune to other frames/windows and to multiple BrowserPane instances |
| Presence machine | `absent` / `waiting` / `present`; companion sends `bye` on `pagehide`; grace timer (3 s) armed at iframe `load` when no hello arrived | `bye` distinguishes "navigating away" from "companion never present"; the timer runs from `load` (not from `bye`) so slow dev-server compiles don't flash a false "absent" |
| iframe `src` decoupling | New `frameSrc` ref bound to the iframe; `currentUrl` becomes display/persist state only | **Critical**: binding `:src` to the live `currentUrl` would re-navigate the iframe every time the companion reports an in-page URL change |
| Navigation with companion | address bar / Home → `command {action:'navigate'}` (`location.assign`), Back/Forward → `history.back()/forward()`, Refresh → `location.reload()` | Preserves the frame's real session history; no more `:key` remount (which wipes history). All fallback paths (no companion) keep the existing V1 behavior unchanged |
| canGoBack/canGoForward | Navigation API (`window.navigation`) when available (Chromium), else `null` = unknown → buttons stay enabled | Only Chromium exposes traversal state; a no-op Back click elsewhere is harmless |
| Hint banner | Shown only when `absent` + a URL is loaded + the URL "looks local" + not dismissed + no probe banner | The snippet is only actionable on the user's own pages; browsing an external site must not nag. `looksLocalUrl()` reuses `LOCAL_HOST_RE` |
| Persistence | Companion-reported URLs feed the existing `browser_url` debounced PATCH | The restored URL after a TwiCC reload becomes the *real* last URL, in-page navigation included |
| No DB / CLI / plugin changes | — | Pure frontend + one static-file view; no migration, no drop-request, no skill |

### Known accepted limits (document, don't fight)

- In-frame navigations also push entries onto the **browser tab's** joint session history
  (the user's real Back button) — inherent iframe behavior, out of scope.
- Firefox/Safari (no Navigation API): Back/Forward buttons stay enabled even at the history
  edge; clicking is a no-op.
- If the user navigates (in-frame) to a page without the companion, the pane degrades to
  V1 behavior. The fallback toolbar stack was frozen during the companion session (its
  entries predate it — Back would jump to a long-gone page), so it is **reset to the
  current URL** as soon as the companion document goes away (`bye`), with the
  absent-transition reset kept as the lost-`bye` safety net: Back/Forward start disabled
  until the user navigates again from the toolbar. Honest degraded state over a
  misleading one.
- A `bye` can be lost (tab killed); the next `load`+grace-timer resolves the state anyway.
- If TwiCC is served over `http` and the embedded page over `https`, the page cannot load
  the snippet (mixed content, downgrade direction). Local dev is http/http — non-issue.

## 3. Protocol specification

Envelope (both directions): `{ source, v: 1, type, ...fields }`.
`source` is `'twicc-browser-companion'` (companion → host) or `'twicc-browser-host'`
(host → companion). Unknown `type`s and non-matching envelopes are silently ignored on
both sides (forward compatibility).

Companion → host:

| type | fields | when | targetOrigin |
|---|---|---|---|
| `hello` | — | script init on each document; bfcache restore (`pageshow` with `persisted`) | `'*'` (payload-free by design) |
| `state` | `url: string`, `canGoBack: bool\|null`, `canGoForward: bool\|null` | right after `ack`; on every URL/history change (microtask-coalesced) | locked host origin |
| `bye` | — | `pagehide` (navigation, reload, frame teardown) | locked host origin |

Host → companion:

| type | fields | when | targetOrigin |
|---|---|---|---|
| `ack` | — | reply to each `hello` | `event.origin` of the hello |
| `command` | `action: 'back'\|'forward'\|'reload'\|'navigate'`, `url?` (navigate only, must match `/^https?:\/\//i`) | toolbar interactions | locked companion origin |

## 4. Task 1 — Shared protocol module + `looksLocalUrl`

### 4.1 New file `frontend/src/browser-companion/protocol.js`

```js
// Message protocol between the browser-companion script (runs inside the
// embedded page, built standalone — see ../../vite.config.companion.js) and
// the BrowserPane host. Shared by both bundles; keep dependency-free.
//
// Envelope: { source, v, type, ...fields }. Unknown types are ignored by both
// sides so the protocol can grow without breaking older companions.

export const COMPANION_SOURCE = 'twicc-browser-companion'
export const HOST_SOURCE = 'twicc-browser-host'
export const PROTOCOL_VERSION = 1

export function companionMessage(type, fields = {}) {
    return { source: COMPANION_SOURCE, v: PROTOCOL_VERSION, type, ...fields }
}

export function hostMessage(type, fields = {}) {
    return { source: HOST_SOURCE, v: PROTOCOL_VERSION, type, ...fields }
}

export function isCompanionMessage(data) {
    return !!data && typeof data === 'object' && data.source === COMPANION_SOURCE && data.v === PROTOCOL_VERSION
}

export function isHostMessage(data) {
    return !!data && typeof data === 'object' && data.source === HOST_SOURCE && data.v === PROTOCOL_VERSION
}
```

### 4.2 New file `frontend/src/browser-companion/protocol.test.js`

Same `node:test` convention as `browserUrl.test.js` (run with
`node --test src/browser-companion/protocol.test.js` from `frontend/`):

```js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    COMPANION_SOURCE,
    HOST_SOURCE,
    companionMessage,
    hostMessage,
    isCompanionMessage,
    isHostMessage,
} from './protocol.js'

test('companionMessage builds a versioned envelope with extra fields', () => {
    const msg = companionMessage('state', { url: 'http://localhost:5173/x' })
    assert.deepEqual(msg, { source: COMPANION_SOURCE, v: 1, type: 'state', url: 'http://localhost:5173/x' })
})

test('hostMessage builds a versioned envelope', () => {
    assert.deepEqual(hostMessage('ack'), { source: HOST_SOURCE, v: 1, type: 'ack' })
})

test('isCompanionMessage accepts its own envelopes and rejects everything else', () => {
    assert.equal(isCompanionMessage(companionMessage('hello')), true)
    assert.equal(isCompanionMessage(hostMessage('ack')), false)
    assert.equal(isCompanionMessage(null), false)
    assert.equal(isCompanionMessage('hello'), false)
    assert.equal(isCompanionMessage({ source: COMPANION_SOURCE, v: 2, type: 'hello' }), false)
})

test('isHostMessage accepts its own envelopes and rejects everything else', () => {
    assert.equal(isHostMessage(hostMessage('command', { action: 'back' })), true)
    assert.equal(isHostMessage(companionMessage('hello')), false)
    assert.equal(isHostMessage({}), false)
})
```

### 4.3 `frontend/src/utils/browserUrl.js` — export `looksLocalUrl`

Append after `normalizeBrowserUrl` (reuses the module-level `LOCAL_HOST_RE`):

```js
// True when a normalized http(s) URL targets a local-ish host (localhost,
// bare IPs, *.local/*.test, host:port dev boxes) — used to decide whether the
// companion-script hint is actionable (it only makes sense on pages the user
// owns, not on external sites).
export function looksLocalUrl(url) {
    if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) return false
    return LOCAL_HOST_RE.test(url.replace(/^https?:\/\//i, ''))
}
```

### 4.4 `frontend/src/utils/browserUrl.test.js` — add cases

```js
test('looksLocalUrl accepts local-ish http(s) URLs', () => {
    assert.equal(looksLocalUrl('http://localhost:5173/'), true)
    assert.equal(looksLocalUrl('http://127.0.0.1:3500/app'), true)
    assert.equal(looksLocalUrl('http://myapp.test/'), true)
    assert.equal(looksLocalUrl('http://devbox:9000/'), true)
})

test('looksLocalUrl rejects public and non-http URLs', () => {
    assert.equal(looksLocalUrl('https://example.com/'), false)
    assert.equal(looksLocalUrl('https://github.com/foo'), false)
    assert.equal(looksLocalUrl('ftp://localhost/'), false)
    assert.equal(looksLocalUrl('localhost:5173'), false) // expects a full URL
    assert.equal(looksLocalUrl(null), false)
})
```

(Update the import line of the test file to also import `looksLocalUrl`.)

## 5. Task 2 — Companion script + build wiring

### 5.1 New file `frontend/src/browser-companion/companion.js`

```js
// TwiCC browser companion — included by the USER'S OWN page (typically their
// dev server) via a classic <script> tag served at /_twicc/browser-companion.js.
// When that page is embedded in TwiCC's Browser pane, the companion bridges
// the cross-origin gap over postMessage: it reports real navigation (URL
// changes, history capabilities) and executes the pane's Back / Forward /
// Reload / Navigate commands against the page's own history.
//
// Trust model: the embedder is unknown at load time, so the initial `hello`
// is payload-free and posted to '*'. The page URL only flows AFTER the host
// acks, targeted at the acked origin; commands are only honoured from it.
// Loaded outside a frame (or twice), the script does nothing.
import { companionMessage, isHostMessage } from './protocol'

if (window.parent !== window && !window.__twiccBrowserCompanion) {
    window.__twiccBrowserCompanion = true
    install()
}

function install() {
    let hostOrigin = null
    let stateScheduled = false

    function post(message, targetOrigin) {
        try {
            window.parent.postMessage(message, targetOrigin)
        } catch {
            // Host gone (frame detached mid-flight) — nothing to do.
        }
    }

    function currentState() {
        // Navigation API (Chromium): accurate traversal state. Elsewhere null
        // means "unknown" and the host keeps its buttons enabled.
        const nav = window.navigation
        return {
            url: window.location.href,
            canGoBack: nav ? nav.canGoBack === true : null,
            canGoForward: nav ? nav.canGoForward === true : null,
        }
    }

    // Coalesce bursts: a pushState patched below AND the Navigation API both
    // fire for one SPA navigation.
    function scheduleState() {
        if (!hostOrigin || stateScheduled) return
        stateScheduled = true
        queueMicrotask(() => {
            stateScheduled = false
            if (hostOrigin) post(companionMessage('state', currentState()), hostOrigin)
        })
    }

    window.addEventListener('message', (event) => {
        if (event.source !== window.parent || !isHostMessage(event.data)) return
        const message = event.data
        if (message.type === 'ack') {
            hostOrigin = event.origin
            scheduleState()
            return
        }
        if (message.type !== 'command' || event.origin !== hostOrigin) return
        if (message.action === 'back') {
            window.history.back()
        } else if (message.action === 'forward') {
            window.history.forward()
        } else if (message.action === 'reload') {
            window.location.reload()
        } else if (message.action === 'navigate' && typeof message.url === 'string' && /^https?:\/\//i.test(message.url)) {
            window.location.assign(message.url)
        }
    })

    // SPA URL changes. The History API has no event for pushState/replaceState
    // — patch them; popstate/hashchange cover traversals everywhere, and the
    // Navigation API adds accurate coverage on Chromium.
    for (const method of ['pushState', 'replaceState']) {
        const original = window.history[method]
        window.history[method] = function (...args) {
            const result = original.apply(this, args)
            scheduleState()
            return result
        }
    }
    window.addEventListener('popstate', scheduleState)
    window.addEventListener('hashchange', scheduleState)
    window.navigation?.addEventListener('currententrychange', scheduleState)

    // Distinguish "navigating away" from "companion never present": the host
    // flips to 'waiting' on bye and only declares absence after a post-load
    // grace period with no hello.
    window.addEventListener('pagehide', () => {
        if (hostOrigin) post(companionMessage('bye'), hostOrigin)
    })

    // bfcache restore: the host may have been remounted meanwhile — redo the
    // handshake from scratch.
    window.addEventListener('pageshow', (event) => {
        if (!event.persisted) return
        hostOrigin = null
        post(companionMessage('hello'), '*')
    })

    post(companionMessage('hello'), '*')
}
```

### 5.2 New file `frontend/vite.config.companion.js`

Replica of `vite.config.shim.js`:

```js
import { defineConfig } from 'vite'

// Standalone build of the browser-companion script, included by the user's
// own dev-server pages and bridging them to the Browser pane over postMessage
// (see src/browser-companion/companion.js). Must be a single self-contained
// classic IIFE — it is loaded cross-origin via a plain <script> tag, where a
// module script would require CORS. The backend serves the output at
// /_twicc/browser-companion.js (see views.browser_companion_script); the dir
// is gitignored — it is a build artifact, produced by `npm run build`.
export default defineConfig({
    // A single injected script — don't copy the SPA's public/ dir.
    publicDir: false,
    build: {
        outDir: '../src/twicc/static/browser-companion',
        emptyOutDir: true,
        lib: {
            entry: 'src/browser-companion/companion.js',
            formats: ['iife'],
            name: 'TwiccBrowserCompanion',
            fileName: () => 'companion.js',
        },
        rollupOptions: {
            output: { inlineDynamicImports: true },
        },
    },
})
```

### 5.3 `frontend/package.json` — extend the build chain

```json
"build": "vite build && vite build --config vite.config.shim.js && vite build --config vite.config.shell.js && vite build --config vite.config.companion.js",
```

### 5.4 `.gitignore` — add the artifact dir

After `src/twicc/static/artifact-shell/` (line 126):

```
src/twicc/static/browser-companion/
```

## 6. Task 3 — Backend: serve the script

### 6.1 `src/twicc/views.py` — new view

Place right after `artifact_shell_asset` (~line 3688), mirroring `artifact_broker_shim`:

```python
async def browser_companion_script(request):
    """Serve the browser-companion bundle (built by vite.config.companion.js).

    Loaded cross-origin by the user's OWN dev-server pages via a classic
    <script> tag, so it must stay reachable without TwiCC auth — like the
    broker shim, it relies on the middleware's non-API fallthrough (see
    auth/middleware.py); do not move it under /api/.
    """
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    script = settings.PACKAGE_DIR / "static" / "browser-companion" / "companion.js"
    response = await asyncio.to_thread(_raw_file_response, str(script))
    if response is None:
        raise Http404("Browser companion not built")
    return response
```

### 6.2 `src/twicc/urls.py` — register the route

After the `_twicc/artifact-shell/<str:asset>` entry (line 44):

```python
path("_twicc/browser-companion.js", views.browser_companion_script),
```

(No SPA-catch-all change needed: explicit `path()` entries match before the
`re_path` catch-all.)

### 6.3 New file `tests/test_browser_companion.py`

Style copied from `test_session_artifacts.py` (local `AsyncClient` fixture, sync
driver over async views):

```python
"""Tests for the /_twicc/browser-companion.js serving endpoint."""

import asyncio

import pytest
from django.test import AsyncClient


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


@pytest.fixture
def built_script(settings, tmp_path):
    """Point PACKAGE_DIR at a temp tree containing a built companion file."""
    script_dir = tmp_path / "static" / "browser-companion"
    script_dir.mkdir(parents=True)
    script = script_dir / "companion.js"
    script.write_text("// built companion\n")
    settings.PACKAGE_DIR = tmp_path
    return script


def test_serves_built_script(client, built_script):
    response = _run(client.get("/_twicc/browser-companion.js"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/javascript")
    assert b"built companion" in b"".join(response.streaming_content)


def test_404_when_not_built(client, settings, tmp_path):
    settings.PACKAGE_DIR = tmp_path
    response = _run(client.get("/_twicc/browser-companion.js"))
    assert response.status_code == 404


def test_post_not_allowed(client, built_script):
    response = _run(client.post("/_twicc/browser-companion.js"))
    assert response.status_code == 405


def test_open_when_password_configured(settings, built_script):
    # The user's dev page fetches the script unauthenticated: the endpoint
    # must pass the auth middleware even when a password is set.
    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$dummy"
    client = AsyncClient()
    response = _run(client.get("/_twicc/browser-companion.js"))
    assert response.status_code == 200
```

## 7. Task 4 — BrowserPane host integration

This is the core task. Full replacement of `frontend/src/components/browser/BrowserPane.vue`.
Summary of the deltas vs the current file, then the complete file:

- **`frameSrc` decoupled from `currentUrl`** (iframe binds `:src="frameSrc"`); `frameEl` ref added.
- **Companion state**: `companionStatus` (`'absent' | 'waiting' | 'present'`), module-let
  `companionOrigin`, `companionCanGoBack/Forward` refs, grace timer, window `message`
  listener (mounted/unmounted).
- **Navigation dispatch**: `navigate` / `goBack` / `goForward` / `refresh` branch on
  `companionStatus === 'present'` and send commands instead of touching the frame; fallback
  paths are byte-for-byte the V1 behavior.
- **State intake**: companion `state` updates `currentUrl` (+ debounced persist) and the
  address bar — unless the address input is focused (`addressFocused`, so typing is never
  clobbered) — plus the canGo* refs. **Inbound URLs are validated against
  `/^https?:\/\//i` before adoption** — the embedded page is untrusted and a forged
  `javascript:` URL must never reach the unsandboxed iframe `src` (TwiCC-origin XSS).
- **Presence flow**: `bye` → `waiting`; iframe `load` with no companion → arm 3 s grace
  timer → `absent` (+ advisory probe re-run, covering "server down" on companion-mode
  navigations). `hello` → `present`, `ack` reply (and clears any probe banner — a
  connected companion proves reachability). The fallback stack (frozen during the
  companion session) is reset to `[currentUrl]` in the `bye` branch, with the
  absent-transition reset kept as the lost-`bye` safety net.
- **Probe staleness guard** switches from `frameKey` to a `currentUrl` snapshot (companion
  navigations don't bump `frameKey`), and `probeResult` becomes assign-only in
  `probeCurrentUrl` (cleared at navigation points instead) so racing probes on one
  navigation can't flicker the banner.
- **UI**: plug status icon + tooltip; hint banner (absent + `looksLocalUrl` + not dismissed
  + no probe banner) with the snippet and a Copy button; updated info tooltip text.

```vue
<script setup>
// Browser pane: embeds a user-chosen URL (typically the project's dev server)
// in a plain iframe. Unlike the Artifacts preview there is NO sandbox, NO CSP
// lockdown and NO network broker — deliberate: the page runs exactly as in a
// normal browser tab (direct network, service workers, HMR websockets), it
// just cannot reach into TwiCC (cross-origin isolation applies both ways).
//
// Cross-origin iframes expose neither their current location nor their
// history. Two modes:
// - Companion mode: the embedded page includes the TwiCC companion script
//   (served at /_twicc/browser-companion.js) and reports real navigation over
//   postMessage; Back/Forward/Refresh drive the page's own history.
// - Fallback: the toolbar keeps its OWN history stack of URLs navigated via
//   the address bar / Back / Forward / Home only; links followed inside the
//   page are invisible and Refresh re-creates the iframe on the last recorded
//   URL. Deliberate, documented limits — see the info tooltip.
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { hostMessage, isCompanionMessage } from '../../browser-companion/protocol'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { apiFetch } from '../../utils/api'
import { resolveProjectBrowserUrl } from '../../utils/browserDefaults'
import { looksLocalUrl, normalizeBrowserUrl } from '../../utils/browserUrl'
import { debounce } from '../../utils/debounce'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    sessionId: { type: String, default: null },
    projectId: { type: String, default: null },
    // True while the Browser tab is the shown tab in its region — drives the
    // lazy first load (never fetch a dev server for a tab that was never opened).
    active: { type: Boolean, default: false },
    // Bumped by SessionView on explicit tab activation → focus the address bar.
    focusRequest: { type: Number, default: 0 },
})

const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const instanceId = useId()

// ── Default URL: project chain first, then the first non-archived workspace
// containing the project (worktree-aware) that carries a browserUrl.
const projectDefaultUrl = computed(() => resolveProjectBrowserUrl(props.projectId, store.projects))
const workspaceDefaultUrl = computed(() => {
    if (!props.projectId) return null
    const ws = workspacesStore.workspaces.find(
        (w) => !w.archived && w.browserUrl && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
    return ws?.browserUrl || null
})
const defaultUrl = computed(() => projectDefaultUrl.value || workspaceDefaultUrl.value)

// ── Navigation state. `urlHistory` holds address-bar-level navigations only
// (fallback mode; named to avoid shadowing window.history in this component).
// `currentUrl` is the pane's display/persist state; the iframe binds the
// SEPARATE `frameSrc` — binding :src to the live currentUrl would re-navigate
// the frame every time the companion reports an in-page URL change.
const currentUrl = ref('')       // URL currently shown ('' = blank state)
const inputUrl = ref('')         // address bar edit buffer
const frameSrc = ref('')         // what the iframe element was last pointed at
const urlHistory = ref([])
const historyIndex = ref(-1)
const frameKey = ref(0)          // bump = recreate the iframe (fallback navigate / refresh)
const loading = ref(false)
const everActivated = ref(false)
const frameEl = ref(null)
const addressFocused = ref(false)

// ── Companion channel. The embedded page may include the TwiCC companion
// script; presence is a tiny state machine driven by its hello/bye messages
// and the iframe load event:
//   absent  — determined: no companion in the current document
//   waiting — undetermined: navigation in flight / grace period running
//   present — handshake done; navigation flows both ways
const companionStatus = ref('absent')
const companionCanGoBack = ref(null)   // null = unknown (no Navigation API)
const companionCanGoForward = ref(null)
let companionOrigin = null
let helloGraceTimer = null
// True once a companion connected: the fallback stack froze during that
// session, so it must be reset when the pane later degrades to fallback mode.
let hadCompanion = false
const HELLO_GRACE_MS = 3000

function sendToCompanion(message) {
    if (!frameEl.value?.contentWindow || !companionOrigin) return
    frameEl.value.contentWindow.postMessage(message, companionOrigin)
}

function clearHelloGraceTimer() {
    if (helloGraceTimer) {
        clearTimeout(helloGraceTimer)
        helloGraceTimer = null
    }
}

function onWindowMessage(event) {
    // Only our own frame's current document, nothing else (other panes, other
    // windows, devtools). contentWindow is re-read live: a frameKey remount
    // invalidates messages from the torn-down document by identity.
    if (!frameEl.value || event.source !== frameEl.value.contentWindow) return
    const data = event.data
    if (!isCompanionMessage(data)) return
    if (data.type === 'hello') {
        companionOrigin = event.origin
        companionStatus.value = 'present'
        hadCompanion = true
        companionCanGoBack.value = null
        companionCanGoForward.value = null
        clearHelloGraceTimer()
        // A connected companion is definitive proof the page is reachable and
        // embeddable — drop any banner a slower/raced probe put up.
        probeResult.value = null
        event.source.postMessage(hostMessage('ack'), event.origin)
        return
    }
    if (event.origin !== companionOrigin) return
    if (data.type === 'state') {
        companionCanGoBack.value = typeof data.canGoBack === 'boolean' ? data.canGoBack : null
        companionCanGoForward.value = typeof data.canGoForward === 'boolean' ? data.canGoForward : null
        // The embedded page is untrusted: only adopt plain http(s) URLs. A
        // hostile page could report a javascript:/data: string that would
        // otherwise reach the UNsandboxed iframe src on a later fallback
        // navigation — i.e. script execution in TwiCC's own origin. The
        // scheme is lowercased so case-sensitive downstream checks (backend
        // validate_browser_url, mixedContentBlocked) can't be sidestepped.
        const url =
            typeof data.url === 'string' && /^https?:\/\//i.test(data.url)
                ? data.url.replace(/^https?/i, (scheme) => scheme.toLowerCase())
                : null
        if (url && url !== currentUrl.value) {
            currentUrl.value = url
            // Never clobber an in-progress address-bar edit.
            if (!addressFocused.value) inputUrl.value = url
            probeResult.value = null // a page just loaded — any diagnosis is stale
            persistUrlDebounced()
        }
    } else if (data.type === 'bye') {
        // Document going away (navigation / reload). Undetermined until the
        // next document says hello or the post-load grace period expires.
        companionStatus.value = 'waiting'
        // The frozen pre-companion stack becomes reachable the moment we
        // leave 'present' — and this waiting window is exactly when users
        // click Back ("page is taking too long"). Reset it NOW; clearing
        // hadCompanion makes the absent-transition reset a pure lost-bye
        // safety net (it must not re-wipe valid toolbar entries the user
        // accumulates during the waiting window).
        hadCompanion = false
        urlHistory.value = currentUrl.value ? [currentUrl.value] : []
        historyIndex.value = urlHistory.value.length - 1
    }
}

onMounted(() => window.addEventListener('message', onWindowMessage))
onBeforeUnmount(() => {
    window.removeEventListener('message', onWindowMessage)
    clearHelloGraceTimer()
})

const canGoBack = computed(() =>
    companionStatus.value === 'present'
        ? companionCanGoBack.value !== false // null (unknown) keeps the button usable
        : historyIndex.value > 0
)
const canGoForward = computed(() =>
    companionStatus.value === 'present'
        ? companionCanGoForward.value !== false
        : historyIndex.value < urlHistory.value.length - 1
)

// ── Per-session persistence: restore the last URL across page reloads.
// Read once at first activation (it wins over the defaults); written back
// debounced on each navigation — toolbar-initiated or companion-reported.
// Drafts have no backend row — their URL stays transient. The pane state
// lives in the refs above, never on the store's session object, so the
// session_updated echo can't clobber it.
const BROWSER_URL_PERSIST_DEBOUNCE_MS = 1000
let lastPersistedUrl = null
const persistUrlDebounced = debounce(async () => {
    const sessionRow = store.getSession(props.sessionId)
    if (!sessionRow || sessionRow.draft) return
    const url = currentUrl.value
    if (url === lastPersistedUrl) return
    try {
        const response = await apiFetch(`/api/projects/${props.projectId}/sessions/${props.sessionId}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ browser_url: url }),
        })
        if (response.ok) lastPersistedUrl = url
    } catch {
        // Transient UI state — losing a write is acceptable.
    }
}, BROWSER_URL_PERSIST_DEBOUNCE_MS)

// An https TwiCC page cannot embed an http iframe (the browser blocks it
// silently as mixed content) — explain instead of showing a dead frame.
const mixedContentBlocked = computed(
    () => window.location.protocol === 'https:' && currentUrl.value.startsWith('http://')
)

// Fallback-mode hard navigation: point the (re-created) iframe at a URL.
function showFrame(url) {
    currentUrl.value = url
    inputUrl.value = url
    frameSrc.value = url
    frameKey.value++
    loading.value = true
    companionStatus.value = 'waiting'
    companionCanGoBack.value = null
    companionCanGoForward.value = null
    clearHelloGraceTimer()
    probeResult.value = null // clear a stale diagnosis right away
    probeCurrentUrl()
    persistUrlDebounced()
}

function navigate(rawInput) {
    const url = normalizeBrowserUrl(rawInput)
    if (!url) return
    if (companionStatus.value === 'present') {
        // Navigate in place — preserves the frame's real session history.
        sendToCompanion(hostMessage('command', { action: 'navigate', url }))
        currentUrl.value = url
        inputUrl.value = url
        loading.value = true
        probeResult.value = null
        persistUrlDebounced()
        return
    }
    // Fallback: truncate forward entries, then push (skip contiguous repeats).
    const stack = urlHistory.value.slice(0, historyIndex.value + 1)
    if (stack[stack.length - 1] !== url) stack.push(url)
    urlHistory.value = stack
    historyIndex.value = stack.length - 1
    showFrame(url)
}

function goBack() {
    if (companionStatus.value === 'present') {
        sendToCompanion(hostMessage('command', { action: 'back' }))
        return
    }
    if (!canGoBack.value) return
    historyIndex.value--
    showFrame(urlHistory.value[historyIndex.value])
}

function goForward() {
    if (companionStatus.value === 'present') {
        sendToCompanion(hostMessage('command', { action: 'forward' }))
        return
    }
    if (!canGoForward.value) return
    historyIndex.value++
    showFrame(urlHistory.value[historyIndex.value])
}

function refresh() {
    if (!currentUrl.value) return
    if (companionStatus.value === 'present') {
        // In-place reload — keeps in-page navigation and session history.
        sendToCompanion(hostMessage('command', { action: 'reload' }))
        loading.value = true
        return
    }
    showFrame(currentUrl.value)
}

function goHome() {
    if (defaultUrl.value) navigate(defaultUrl.value)
}

function openExternal() {
    if (currentUrl.value) window.open(currentUrl.value, '_blank', 'noopener')
}

function onAddressSubmit() {
    navigate(inputUrl.value)
}

function onFrameLoad() {
    // Fires even for framing-refused pages (the error document loads) — it
    // only means "network settled", not "content visible".
    loading.value = false
    // Presence: a companion's hello normally arrives BEFORE load (deferred
    // scripts run first). If it hasn't by now, give slow injectors a short
    // grace, then declare absence. The probe re-runs on absence so a dead
    // server is diagnosed even on companion-mode navigations (which skip
    // showFrame and its probe call).
    if (companionStatus.value === 'present') return
    clearHelloGraceTimer()
    helloGraceTimer = setTimeout(() => {
        helloGraceTimer = null
        companionStatus.value = 'absent'
        companionCanGoBack.value = null
        companionCanGoForward.value = null
        if (hadCompanion) {
            // The fallback stack froze during the companion session — its
            // entries predate it, and Back would jump to a long-gone page.
            // Restart from the current URL: an honest degraded state.
            hadCompanion = false
            urlHistory.value = currentUrl.value ? [currentUrl.value] : []
            historyIndex.value = urlHistory.value.length - 1
        }
        probeCurrentUrl()
    }, HELLO_GRACE_MS)
}

// ── Advisory probe: the two silent-blank-frame cases (server down, framing
// refused) are invisible client-side; ask the backend. Failures are ignored —
// the endpoint is advisory.
// probeResult is CLEARED at navigation points (showFrame, companion navigate,
// companion-reported page load) and only ASSIGNED here — never reset at probe
// start. Two probes can race on one navigation (showFrame's immediate one and
// the post-load absence one); both converge on the same value for the same
// URL, so a late response can't flicker an already-displayed banner away.
const probeResult = ref(null)

async function probeCurrentUrl() {
    const url = currentUrl.value
    if (!url) return
    try {
        const response = await apiFetch(`/api/browser-frame-check/?url=${encodeURIComponent(url)}`)
        if (!response.ok) return
        const data = await response.json()
        if (currentUrl.value !== url) return // user navigated meanwhile
        probeResult.value = data.reachable === false || data.embeddable === false ? data : null
    } catch {
        // advisory only
    }
}

// ── Lazy init: on first activation, auto-load the session's persisted URL
// (survives page reloads) or, failing that, the resolved default.
watch(
    () => props.active,
    (active) => {
        if (!active || everActivated.value) return
        everActivated.value = true
        const saved = store.getSession(props.sessionId)?.browser_url || null
        lastPersistedUrl = saved
        const initial = saved || defaultUrl.value
        if (initial) navigate(initial)
    },
    { immediate: true }
)

// ── Focus the address bar on explicit tab activation (keyboard / tab click),
// mirroring the other ACTIVATION_FOCUS_TABS panels.
const addressInputRef = ref(null)
watch(
    () => props.focusRequest,
    () => {
        requestAnimationFrame(() => addressInputRef.value?.focus())
    }
)

// ── Companion hint: snippet banner + status icon --------------------------
const snippetDismissed = ref(false)
const snippetCopied = ref(false)
const companionSnippet = computed(
    () => `<script src="${window.location.origin}/_twicc/browser-companion.js" defer><\/script>`
)
// Only nag where the snippet is actionable: a loaded local-ish page, no
// higher-priority diagnostic (probe banner) on screen.
const showCompanionHint = computed(
    () =>
        companionStatus.value === 'absent' &&
        !!currentUrl.value &&
        looksLocalUrl(currentUrl.value) &&
        !snippetDismissed.value &&
        !probeResult.value &&
        !mixedContentBlocked.value
)

async function copySnippet() {
    try {
        await navigator.clipboard.writeText(companionSnippet.value)
        snippetCopied.value = true
        setTimeout(() => {
            snippetCopied.value = false
        }, 1500)
    } catch {
        // Clipboard unavailable (permissions) — the snippet is selectable text.
    }
}

const companionTooltip = computed(() => {
    if (companionStatus.value === 'present') {
        return 'Companion connected — the toolbar follows the page’s real navigation and history.'
    }
    if (companionStatus.value === 'waiting') return 'Checking for the TwiCC companion script…'
    return 'No companion script on this page — history is toolbar-only. Click to see how to add it.'
})

function onCompanionIconClick() {
    if (companionStatus.value === 'absent') snippetDismissed.value = false
}

// ── Save-URL menu -------------------------------------------------------------
const project = computed(() => store.getProject(props.projectId))
const mainRepoProject = computed(() =>
    project.value?.worktree_of ? store.getProject(project.value.worktree_of) : null
)
const memberWorkspaces = computed(() =>
    workspacesStore.workspaces.filter(
        (w) => !w.archived && workspacesStore.workspaceContainsProject(w.id, props.projectId)
    )
)
const canSave = computed(() => !!currentUrl.value && !!project.value)
const saveError = ref('')

async function saveToProject(projectId) {
    try {
        const response = await apiFetch(`/api/projects/${projectId}/`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ default_browser_url: currentUrl.value }),
        })
        if (!response.ok) {
            const data = await response.json().catch(() => ({}))
            throw new Error(data.error || `Failed to save (${response.status})`)
        }
        store.updateProject(await response.json())
    } catch (e) {
        saveError.value = e.message || 'Failed to save URL'
    }
}

function onSaveSelect(event) {
    const value = event.detail?.item?.value
    if (!value || !currentUrl.value) return
    saveError.value = ''
    if (value === 'project') {
        saveToProject(props.projectId)
    } else if (value === 'main-repo') {
        saveToProject(project.value.worktree_of)
    } else if (value.startsWith('ws:')) {
        workspacesStore.updateWorkspace(value.slice(3), { browserUrl: currentUrl.value })
    }
}
</script>
```

Template deltas (rest unchanged):

```html
            <wa-input
                ref="addressInputRef"
                class="browser-address"
                size="small"
                autocomplete="off"
                placeholder="Enter a URL — e.g. localhost:5173"
                :value="inputUrl"
                @input="inputUrl = $event.target.value"
                @focus="addressFocused = true"
                @blur="addressFocused = false"
                @keydown.enter.prevent="onAddressSubmit"
            >
                <wa-spinner v-if="loading" slot="start"></wa-spinner>
                <wa-icon v-else slot="start" name="globe"></wa-icon>
            </wa-input>
```

Refresh button title becomes mode-agnostic:

```html
            <wa-button appearance="plain" size="small" class="browser-btn" :disabled="!currentUrl" title="Refresh" @click="refresh">
```

Companion status icon, inserted between the open-external button and the info icon:

```html
            <wa-icon
                :id="`browser-companion-${instanceId}`"
                name="plug"
                class="companion-status"
                :class="companionStatus"
                @click="onCompanionIconClick"
            ></wa-icon>
            <AppTooltip :for="`browser-companion-${instanceId}`">{{ companionTooltip }}</AppTooltip>
```

Info tooltip text (replaces the current one):

```html
            <AppTooltip :for="`browser-info-${instanceId}`">
                Pages that include the TwiCC companion script report their real
                navigation here — Back/Forward/Refresh drive the page's own
                history. Without it, the toolbar only tracks URLs entered here
                and links followed inside the page are invisible to it. Some
                sites refuse to be embedded (X-Frame-Options) and stay blank;
                logins may not persist inside a frame. Keyboard shortcuts pause
                while the page has focus — click TwiCC's chrome to get them back.
            </AppTooltip>
```

Hint banner, after the mixed-content callout:

```html
        <wa-callout v-if="showCompanionHint" variant="neutral" size="small" class="browser-banner">
            <wa-icon slot="icon" name="plug"></wa-icon>
            <div class="companion-hint">
                <span>
                    This page doesn't include the TwiCC companion script, so
                    Back/Forward only track URLs entered in the toolbar. Add it
                    to your dev page for real history tracking:
                </span>
                <code class="companion-snippet">{{ companionSnippet }}</code>
                <div class="companion-hint-actions">
                    <wa-button size="small" appearance="outlined" @click="copySnippet">
                        {{ snippetCopied ? 'Copied!' : 'Copy snippet' }}
                    </wa-button>
                    <wa-button size="small" appearance="plain" @click="snippetDismissed = true">Dismiss</wa-button>
                </div>
            </div>
        </wa-callout>
```

The iframe gains the element ref and binds `frameSrc`:

```html
            <iframe
                v-if="everActivated && currentUrl && !mixedContentBlocked"
                ref="frameEl"
                :key="frameKey"
                :src="frameSrc"
                class="browser-frame"
                allow="clipboard-read; clipboard-write; fullscreen"
                title="Browser"
                @load="onFrameLoad"
            ></iframe>
```

Style additions:

```css
.companion-status {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    opacity: 0.55;
    cursor: pointer;
}

.companion-status.present {
    color: var(--wa-color-success-fill-loud);
    opacity: 1;
    cursor: default;
}

.companion-hint {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.companion-snippet {
    font-size: var(--wa-font-size-xs);
    background: var(--wa-color-surface-lowered);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-s);
    user-select: all;
    overflow-wrap: anywhere;
}

.companion-hint-actions {
    display: flex;
    gap: var(--wa-space-xs);
}
```

### Implementation notes for Task 4

- `<\/script>` inside `companionSnippet` is escaped on purpose — a literal `</script>`
  inside a `<script setup>` string would terminate the SFC block.
- `wa-icon` name `plug` is Font Awesome Free — but per the FA-availability gotcha, verify
  with a 200 check on ka-f.fontawesome.com (read the response with Read, not Bash) before
  relying on it; fall back to `circle-nodes`/`link` if 403.
- `@focus`/`@blur` on `wa-input` are native (unprefixed) events and compose fine.
- No SessionView / router / store changes — the pane's public props are untouched.

## 8. Task 5 — Docs sync (CLAUDE.md + AGENTS.md)

1. **CLAUDE.md**, Artifact Network Broker section, the "not HMR'd" sentence — extend:

   > The shim + shell bundles are **not HMR'd** — `cd frontend && npm run build` after
   > editing `artifact-broker/*` or `artifact-shell/*`.

   becomes (same sentence, one more path; the companion is not broker-related, but this is
   the one place that documents non-HMR'd standalone bundles):

   > The shim + shell + browser-companion bundles are **not HMR'd** — `cd frontend &&
   > npm run build` after editing `artifact-broker/*`, `artifact-shell/*` or
   > `browser-companion/*`.

2. **CLAUDE.md**, `Session` model bullet, `browser_url` clause — append one clause after
   "mutable + synced like `layout`":

   > ; the pane upgrades from toolbar-tracked to real in-page history when the embedded
   > page includes the opt-in companion script (`/_twicc/browser-companion.js`, source
   > `frontend/src/browser-companion/`, postMessage bridge)

3. **AGENTS.md** — mirror both edits byte-for-byte in the corresponding sentences (per the
   AGENTS.md-follows-CLAUDE.md rule).

## 9. Task 6 — Verification

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/browser-tab
# Frontend unit tests
cd frontend && node --test src/utils/browserUrl.test.js src/browser-companion/protocol.test.js
# Builds (SPA + shim + shell + companion; verify static/browser-companion/companion.js exists)
npm run build && test -f ../src/twicc/static/browser-companion/companion.js
# Backend
cd .. && TWICC_DATA_DIR=$PWD uv run pytest tests/ -x -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

Manual E2E script (for the user, after implementation + `devctl start`):

1. Add the snippet to a local dev page (e.g. TwiCC's own `frontend/index.html` in another
   worktree, or any Vite app): `<script src="http://localhost:<port>/_twicc/browser-companion.js" defer></script>`.
2. Open the Browser tab on that URL → plug icon turns green, no hint banner.
3. Click links / trigger SPA navigations inside the page → address bar follows; reload
   TwiCC → the tab restores the *real* last URL.
4. Back/Forward → the page traverses its own history (SPA states included); Refresh keeps
   the current in-page location.
5. Load a local page *without* the snippet → gray plug + hint banner with Copy; Dismiss
   hides it; clicking the plug icon brings it back. Browse `https://example.com` → no
   banner (non-local URL).
6. Stop the dev server, click Refresh → probe banner ("server did not respond") appears
   after the grace period.

## 10. Gotchas encountered at design time (do not re-litigate)

- **`:src` must not bind the live `currentUrl`** — assigning the attribute re-navigates
  the frame; companion-reported URL updates would trigger reload loops. Hence `frameSrc`.
- **Hello is payload-free and posted to `'*'`** — deliberate: no URL leak to unknown
  embedders. The ack→lock choreography is the security boundary; keep it.
- **Grace timer runs from the iframe `load` event, not from `bye`** — a cold Vite compile
  can hold a navigation for >10 s; timing from `bye` would flash a false "absent".
- **`load` fires for error documents too** — that's why absence triggers the advisory
  probe: it distinguishes "no companion" from "server down".
- **Classic script, not a module** — `type="module"` would subject the cross-origin fetch
  to CORS. The IIFE build keeps the tag classic.
- **The companion must bail when `window.parent === window`** — the script is harmless if
  a page includes it while opened directly in a normal tab.
- **Dock moves reload iframes** (Teleport DOM move) — the companion re-runs its handshake
  on the reload; the presence machine self-heals. No special handling.
- **Address bar focus guard** — companion `state` updates skip `inputUrl` while the input
  is focused, so a user's half-typed URL is never clobbered.
- **Inbound `state.url` is untrusted input** (review finding, BLOCKER): a hostile page
  browsed inside the pane can speak the protocol and report `javascript:`/`data:` URLs;
  unvalidated, they would reach the unsandboxed iframe `src` on a later fallback
  navigation — script execution in TwiCC's own origin. Intake therefore only adopts
  URLs matching `/^https?:\/\//i`. Toolbar input was already safe (`normalizeBrowserUrl`
  rejects non-http(s) schemes), as is the persisted-URL restore path (it re-enters via
  `navigate()`); keep all three guards.
- **`probeResult` is assign-only in `probeCurrentUrl`** (review finding): one navigation
  can legitimately trigger two probes (immediate + post-load absence); resetting to null
  at probe start let a late first response wipe a banner the second had just displayed.
- **Fallback stack reset on companion loss** (review finding): the stack freezes while a
  companion drives navigation, so after degradation Back would jump to a pre-companion
  page the user never was on recently. Reset to `[currentUrl]` — Back starts disabled,
  which is honest, instead of wrong. The reset fires **in the `bye` branch**, not only
  at the absent transition: the `bye → load → grace` window (10 s+ on a cold Vite
  compile) is exactly when users click Back, and the stale stack must already be gone.
  The `bye` branch also clears `hadCompanion`, making the absent-transition reset a
  pure lost-`bye` safety net — it must not re-wipe valid toolbar entries the user
  accumulated during the waiting window.
