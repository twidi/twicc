<script setup>
// Root of the dedicated artifact page's trusted shell (design §5/§9). It iframes
// the artifact's inner document and mounts the *same* broker host + consent
// prompt the in-SPA preview uses, through the shared `useArtifactBroker`
// composable — so an artifact behaves identically in both contexts.
import { ref, onMounted } from 'vue'
import { useArtifactBroker } from '../composables/useArtifactBroker'
import ArtifactBrokerPrompt from '../components/artifacts/ArtifactBrokerPrompt.vue'
import BrandLogo from '../components/ui/BrandLogo.vue'

const props = defineProps({
    // Backend-served inner-doc URL (/artifacts/<id>/__twicc_doc__).
    innerDocUrl: { type: String, required: true },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },
    deniedHosts: { type: Object, default: () => ({}) },
    mode: { type: String, default: 'owner' },
    proxyUrl: { type: String, default: undefined },
    snapshotAt: { type: [String, null], default: null },
    tokenPath: { type: [String, null], default: null },
})

const iframeRef = ref(null)

// Persist "Forever" onto the bookmark's allowlist. The page holds the TwiCC
// session cookie, so a same-origin POST is authenticated — no auth-store/router
// dependency, which keeps this bundle free of the main SPA.
async function persistAllow(url, kind) {
    if (props.bookmarkId == null) return
    await fetch(`/api/artifact-bookmarks/${props.bookmarkId}/allowed-hosts/`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ url, kind }),
    })
}

// Record a prompt denial server-side (fire-and-forget), same POST pattern.
function onDenied(url, kind) {
    if (props.bookmarkId == null) return
    fetch(`/api/artifact-bookmarks/${props.bookmarkId}/network-denials/`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ url, kind }),
    }).catch(() => {})
}

// documentUrl is the inner doc; its own relative assets resolve to
// /artifacts/<id>/<asset>, so the artifact's directory is the inner doc's parent.
// Share mode: hosts the artifact tried to reach that the owner never allowed
// (the proxy answered `not_allowed`). A viewer can't grant anything, so show an
// honest, dismissible notice instead of letting the page fail mysteriously.
const blockedHosts = ref([])
function noteBlockedHost(hostKey) {
    if (!blockedHosts.value.includes(hostKey)) blockedHosts.value.push(hostKey)
}

const { brokerPrompt, onBrokerDecision } = useArtifactBroker(
    iframeRef,
    () => ({
        documentUrl: new URL(props.innerDocUrl, location.href).href,
        getBookmarkId: () => props.bookmarkId,
        // The island is static, so these getters are reload-only (accepted by
        // design — unlike the SPA preview there is no live store behind them).
        getAllowedHosts: () => props.allowedHosts,
        getDeniedHosts: () => props.deniedHosts,
        persistAllow: props.mode === 'share' ? undefined : persistAllow,
        onDenied: props.mode === 'share' ? undefined : onDenied,
        mode: props.mode,
        proxyUrl: props.proxyUrl,
        onBlocked: props.mode === 'share' ? noteBlockedHost : undefined,
        // The dedicated page serves bookmarks, which live under a session's
        // artifacts root by construction → data/ writes are silent (design
        // 2026-08-05 §6). Share mode never reaches the write path: the host
        // rejects it before the gate.
        inArtifactsRoot: true,
    }),
    [iframeRef],
)

// Share mode: poll the snapshot freshness (D7) for the "updated — reload" banner.
const updated = ref(false)
// `window` isn't in scope inside a Vue template (name lookup targets the
// component instance), so reload through a real method rather than an inline
// `location.reload()` — which resolved to `undefined.reload()` and threw.
function reloadPage() { window.location.reload() }
onMounted(() => {
    if (props.mode !== 'share' || !props.tokenPath) return
    setInterval(async () => {
        if (document.hidden) return
        try {
            const m = await (await fetch(`${props.tokenPath}/api/artifact-meta/`, { credentials: 'same-origin' })).json()
            if (m.snapshot_at && m.snapshot_at !== props.snapshotAt) updated.value = true
        } catch { /* ignore */ }
    }, 30000)
})
</script>

<template>
    <!-- Same sandbox as the in-SPA preview: scripts run, but top-level
         navigation, popups and modals do not. Same-origin kept (localStorage
         works; design §13). -->
    <div v-if="updated" class="share-update-banner">
        This artifact was updated — <a href="#" @click.prevent="reloadPage">Reload</a>
    </div>
    <iframe
        ref="iframeRef"
        :src="innerDocUrl"
        class="artifact-frame"
        sandbox="allow-scripts allow-same-origin allow-forms"
        title="Artifact"
    ></iframe>
    <!-- Share mode gets the same "Shared with TwiCC" footer as the session/doc
         share viewers; the in-app owner page (a plain tool view) does not. -->
    <footer v-if="mode === 'share'" class="share-footer"><BrandLogo :size="16" /> Shared with
        <a href="https://github.com/twidi/twicc" target="_blank" rel="noopener noreferrer">TwiCC</a></footer>
    <!-- Share mode never prompts (server enforces the owner allowlist, D6). -->
    <ArtifactBrokerPrompt v-if="mode !== 'share'" :prompt="brokerPrompt" @decision="onBrokerDecision" />
    <div v-if="blockedHosts.length" class="share-blocked-banner">
        This artifact tried to reach <code>{{ blockedHosts.join(', ') }}</code> —
        {{ blockedHosts.length > 1 ? 'these hosts are' : 'this host is' }} not authorized
        by the share owner, so parts of the page may not work.
        <a href="#" @click.prevent="blockedHosts = []">Dismiss</a>
    </div>
</template>

<style>
/* The shell is the whole tab; the artifact iframe fills the viewport. */
html,
body {
    margin: 0;
    height: 100%;
    background: var(--wa-color-surface-default);
}
#app {
    height: 100vh; /* fallback for browsers without dvh */
    height: 100dvh; /* dynamic viewport: iframe + footer stay within the visible
                       area even as mobile browser chrome shows/hides */
    display: flex;
    flex-direction: column;
}
.artifact-frame {
    display: block;
    width: 100%;
    flex: 1 1 auto;
    min-height: 0;
    border: 0;
    /* WA's base stylesheet gives block flow elements a bottom margin; here it
       would leave a blank strip between the iframe and the footer. */
    margin: 0;
}
/* Same look as the session/doc share footers (share-session bundle); restated
   here because that bundle's stylesheet isn't loaded in the artifact shell. */
.share-footer {
    flex: 0 0 auto;
    text-align: center;
    color: var(--wa-color-text-quiet);
    background: var(--wa-color-surface-default);
    font-size: var(--wa-font-size-s);
    line-height: 1.2;
    padding: 0.5rem 0;
}
.share-footer a {
    color: inherit;
    text-decoration: underline;
}
.share-update-banner {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 10;
    padding: 0.5rem 1rem;
    text-align: center;
    background: #0891b2;
    color: #fff;
    font: 500 0.9rem system-ui, -apple-system, sans-serif;
}
.share-update-banner a {
    color: #fff;
    text-decoration: underline;
}
/* Bottom-anchored so it never fights the top "updated — reload" banner. */
.share-blocked-banner {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 10;
    padding: 0.5rem 1rem;
    text-align: center;
    background: #b45309;
    color: #fff;
    font: 500 0.9rem system-ui, -apple-system, sans-serif;
}
.share-blocked-banner code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.share-blocked-banner a {
    color: #fff;
    text-decoration: underline;
    margin-left: 0.5rem;
}
</style>
