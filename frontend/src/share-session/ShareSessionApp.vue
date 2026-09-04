<script setup>
import { ref, reactive, provide, onMounted, onUnmounted, computed } from 'vue'
import ShareItemsList from './ShareItemsList.vue'
import SharedSubagentView from './SharedSubagentView.vue'
import GlobalMediaPreview from '../components/media/GlobalMediaPreview.vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { getProviderIcon } from '../providers'
import ProviderIcon from '../components/ui/ProviderIcon.vue'
import { makeShareApi, setShareApi } from './shims/shareApi'
import { connectShareLive } from './shims/shareLive'
import { loadViewerPrefs } from './viewerPrefs'

const props = defineProps({ tokenPath: String, meta: Object })

const api = makeShareApi(props.tokenPath); setShareApi(api)
provide('shareApi', api)

const store = useDataStore()
const settings = useSettingsStore()
const meta = reactive({ ...props.meta })
const revoked = ref(false)
const ready = computed(() => meta.ready !== false)

// Seed a session-ish object the reused components read via getSession.
store.setSession({
    id: meta.session_id, provider: meta.provider, project_id: 'share',
    title: meta.title || 'Shared session',
    last_line: meta.last_line, git_directory: null, cwd: null, artifacts_dir: null,
    created_at: meta.created_at, last_updated_at: meta.last_updated_at,
    compacted: meta.compacted === true,
})
// Show-timestamps: the viewer's persisted choice wins (carries across shares);
// otherwise fall back to this share's own default. Detail level is NOT persisted —
// it's re-derived from the share's max on every load (see defaultDisplayMode).
settings.areMessageTimestampsShown = loadViewerPrefs().showTimestamps ?? (meta.show_timestamps !== false)
settings.setDisplayMode(defaultDisplayMode())

const displayModes = computed(() => boundedModes(meta.max_display_mode || 'normal'))
function boundedModes(max) {
    const order = ['conversation', 'simplified', 'normal', 'debug']
    return order.slice(0, order.indexOf(max) + 1)
}
function clampMode(m) { return boundedModes(meta.max_display_mode || 'normal').includes(m) ? m : 'normal' }
// Default detail level = the share's max, but never open a viewer straight into
// raw-JSON debug: cap the default at normal (debug stays selectable in the menu).
function defaultDisplayMode() {
    const max = meta.max_display_mode || 'normal'
    return max === 'debug' ? 'normal' : max
}
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1)

const providerIcon = computed(() => getProviderIcon(meta.provider))

// Subagent overlay stack (design §8.6). Opening one is reflected in the URL hash
// (#agent=<id>[,<nested>…]) through the History API — the share bundle has no
// router — so browser Back closes the drawer instead of leaving the share page.
const subagentStack = ref([])

function seedAgentSession(id, slug = null) {
    // Seed the subagent as a store session so the reused SessionItem dispatch can
    // resolve its provider (subagents inherit the root's) AND its display label.
    // Without it getSession returns null → provider undefined → every item falls to
    // UnknownEntry ("Unhandled event").
    store.setSession({
        id, provider: meta.provider, slug: slug || store.getSession(id)?.slug || null,
        project_id: 'share', title: null, last_line: 0,
        git_directory: null, cwd: null, artifacts_dir: null,
    })
}

function agentUrl(stack) {
    const base = location.pathname + location.search
    return stack.length ? `${base}#agent=${stack.join(',')}` : base
}
function openSubagent(agentId) {
    if (!store.getSession(agentId)) seedAgentSession(agentId)
    subagentStack.value.push(agentId)
    history.pushState({ shareAgentStack: [...subagentStack.value] }, '', agentUrl(subagentStack.value))
}
function closeSubagent() { history.back() }                                  // pop one → popstate syncs
function clearSubagents() { if (subagentStack.value.length) history.go(-subagentStack.value.length) }
function onPopState(e) {
    subagentStack.value = Array.isArray(e.state?.shareAgentStack) ? e.state.shareAgentStack : []
}

if (ready.value && meta.include_subagents) {
    provide('openSubagent', openSubagent)
    api.fetchSubagents().then((links) => {
        store.setAgentLinks(meta.session_id, links)
        // Seed each subagent's slug so the drawer labels them like the owner UI
        // (Agent <slug>, else Agent <shortId>) via getAgentDisplayLabel.
        for (const l of links) if (l.agent_id) seedAgentSession(l.agent_id, l.agent_slug)
    }).catch(() => {})
}
provide('sessionActive', ref(true))
// A snapshot (or a share closed under the viewer) is a frozen transcript: no tool
// can be running, so the reused tree drops its running spinners / result polling.
// Live shares keep the real state (tool-states fetch + WS share_tool_state).
provide('transcriptFrozen', computed(() => meta.mode !== 'live' || revoked.value))

onMounted(() => {
    window.addEventListener('popstate', onPopState)
    // Anchor a base history entry (stack empty), then re-open any agents encoded in
    // the URL on load/reload — so Back from a deep-linked agent returns to the session.
    const hash = /#agent=([^&]*)/.exec(location.hash)
    history.replaceState({ shareAgentStack: [] }, '', location.pathname + location.search)
    if (meta.include_subagents && hash && hash[1]) {
        for (const id of hash[1].split(',').filter(Boolean)) openSubagent(id)
    }
    if (ready.value && meta.mode === 'live') {
        connectShareLive({
            tokenPath: props.tokenPath, sessionId: meta.session_id,
            // The consumer forwards subagent traffic too — route by the message's
            // own session_id, never assume the root.
            onItems: (items, sid) => store.addSessionItems(sid || meta.session_id, items),
            // Fresh meta can carry a TIGHTENED max_display_mode: re-clamp the
            // viewer's current mode so the select never sits on a now-invalid value.
            onMeta: (m) => { Object.assign(meta, m); settings.setDisplayMode(clampMode(settings.displayMode)) },
            onToolState: (m) => store.setToolState(
                m.session_id, m.tool_use_id, m.result_count, m.completed_at,
                m.error ?? null, m.extra ?? null, m.tool_result_line_nums || [],
            ),
            // Assistant-turn indicator: inject/drop the reused "<Provider> is
            // thinking" synthetic message (root session only).
            onProcessState: (m) => store.setLiveAssistantTurn(meta.session_id, m.state === 'assistant_turn'),
            // A subagent spawned live becomes openable (seed its link + session so
            // the tool card's "View Agent" resolves it).
            onAgentLink: (link) => {
                if (!link?.agent_id) return
                store.addAgentLink(meta.session_id, link)
                seedAgentSession(link.agent_id, link.agent_slug)
            },
            onClosed: () => { revoked.value = true; store.setLiveAssistantTurn(meta.session_id, false) },
        })
    }
})
onUnmounted(() => window.removeEventListener('popstate', onPopState))
</script>

<template>
    <div class="share-shell">
        <header class="share-header">
            <div class="share-title">
                <ProviderIcon v-if="providerIcon" :provider="meta.provider" />
                <strong>{{ meta.title || 'Shared session' }}</strong>
            </div>
            <wa-button id="share-menu-trigger" size="small" appearance="plain" class="share-menu-button">
                <wa-icon name="bars" label="View options"></wa-icon>
            </wa-button>
            <wa-popover for="share-menu-trigger" placement="bottom-end" class="share-menu">
                <div class="share-menu-content">
                    <label class="share-menu-field">Detail level
                        <wa-select size="small" :value="settings.displayMode"
                                   @change.stop="settings.setDisplayMode($event.target.value)">
                            <wa-option v-for="m in displayModes" :key="m" :value="m">{{ capitalize(m) }}</wa-option>
                        </wa-select>
                    </label>
                    <wa-switch size="small" :checked="settings.areMessageTimestampsShown"
                               @change.stop="settings.setMessageTimestampsShown($event.target.checked)">Show timestamps</wa-switch>
                    <!-- Above: per-share view options. Below: the viewer's own -->
                    <!-- persisted preferences (carry across every share). -->
                    <wa-divider></wa-divider>
                    <label class="share-menu-field">Color scheme
                        <wa-select size="small" :value="settings._colorScheme"
                                   @change.stop="settings.setColorScheme($event.target.value)">
                            <wa-option value="system">System</wa-option>
                            <wa-option value="light">Light</wa-option>
                            <wa-option value="dark">Dark</wa-option>
                        </wa-select>
                    </label>
                    <label class="share-menu-field">Font size ({{ settings.getFontSize }}px)
                        <wa-slider size="small" :min.prop="12" :max.prop="32" :step.prop="1"
                                   :value.prop="settings.getFontSize"
                                   @input.stop="settings.setFontSize($event.target.value)"></wa-slider>
                    </label>
                </div>
            </wa-popover>
        </header>

        <wa-callout v-if="revoked" variant="warning" class="share-banner">
            This share is no longer available.
        </wa-callout>

        <wa-callout v-else-if="!ready" variant="neutral" class="share-banner">
            This shared session is being prepared. Refresh this page later.
        </wa-callout>

        <ShareItemsList
            v-else
            :session-id="meta.session_id"
            :last-line="meta.last_line"
        />

        <SharedSubagentView v-if="ready && subagentStack.length"
            :stack="subagentStack" @close="closeSubagent" @clear="clearSubagents" />

        <GlobalMediaPreview />
        <footer class="share-footer">Shared with
            <a href="https://github.com/twidi/twicc" target="_blank" rel="noopener noreferrer">TwiCC</a></footer>
    </div>
</template>

<style>
/* App-shell layout: the viewer fills the viewport so the transcript list scrolls
   INTERNALLY. The VirtualScroller needs a bounded-height parent (it uses
   height:100% + overscroll-behavior:contain); without one it grows to full
   content height and swallows the wheel over the content, leaving only the page
   margins scrollable. This mirrors the SPA's SessionItemsList flex chain. */
html, body { height: 100%; margin: 0; }
/* Dynamic viewport so the pinned footer stays within the visible area even as
   mobile browser chrome shows/hides (matches the HTML-artifact share shell). */
#app { height: 100vh; height: 100dvh; }
.share-shell { max-width: 60rem; margin: 0 auto; padding: 0 1rem; height: 100%;
    display: flex; flex-direction: column; }
.share-header { flex: 0 0 auto; display: flex; justify-content: space-between;
    align-items: center; gap: 1rem; padding: .5rem 0;
    position: relative; z-index: 1; }
/* Full-bleed surface + shadow: the header content stays within the centered
   .share-shell (max-width 60rem), but its background and drop shadow extend to
   the viewport edges through a pseudo-element — margin-inline: calc(50% - 50vw)
   pushes it out to the full width. The page never scrolls vertically (the
   transcript scrolls internally), so 50vw has no scrollbar gap. */
.share-header::before {
    content: '';
    position: absolute;
    inset: 0;
    margin-inline: calc(50% - 50vw);
    background: var(--wa-color-surface-default);
    box-shadow: var(--wa-shadow-m);
    z-index: -1;
    pointer-events: none;
}
/* Indented to roughly line up with the transcript cards. Kept on the title, not
   .share-header, so the header box stays centred and the full-bleed ::before
   surface/shadow still reaches both viewport edges. */
.share-title { display: flex; align-items: center; gap: .5rem; min-width: 0; padding-left: 1.5rem; }
.share-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.share-menu-button { flex: 0 0 auto; }
.share-menu-content { display: flex; flex-direction: column; gap: .75rem; min-width: 13rem; }
/* Neutralise the divider's own block margin so only the flex gap spaces it. */
.share-menu-content wa-divider { margin-block: 0; }
.share-menu-field { display: flex; flex-direction: column; gap: .3rem;
    font-size: var(--wa-font-size-s); font-weight: 600; }
.share-menu-field wa-select { font-weight: 400; }
.share-menu-field wa-slider { margin-top: .5rem; }
.share-banner { flex: 0 0 auto; }
.share-items-list { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
    overflow: hidden; position: relative; }
.share-items-list .session-items { flex: 1; min-height: 0; }
.share-footer { flex: 0 0 auto; text-align: center; color: var(--wa-color-text-quiet);
    background: var(--wa-color-surface-default); font-size: var(--wa-font-size-s);
    line-height: 1.2; padding: .5rem 0; }
.share-footer a { color: inherit; text-decoration: underline; }
/* Print: let everything flow (the scroller still only renders its virtualized
   window, but at least it isn't clipped to one viewport). */
@media print {
    html, body, #app, .share-shell { height: auto; }
    .share-items-list, .share-items-list .session-items { overflow: visible; min-height: 0; }
    .share-header, .share-footer { display: none; }
}
</style>
