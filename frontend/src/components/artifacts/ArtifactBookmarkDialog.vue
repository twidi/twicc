<script setup>
// ArtifactBookmarkDialog.vue — create/edit (and remove) an artifact bookmark.
// Mirrors ProjectEditDialog.vue's form/submit/focus/guard patterns.
import { ref, computed, reactive, nextTick, useId, onMounted, onBeforeUnmount } from 'vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getSessionGrantsForBookmark } from '../../artifact-broker/host'
import { apiFetch } from '../../utils/api'
import { isHtmlArtifactPath, suggestArtifactBookmarkName } from '../../utils/artifactBookmark'
import ShareDialog from '../share/ShareDialog.vue'
import AccessLogList from '../share/AccessLogList.vue'

const props = defineProps({
    sessionId: { type: String, default: null },
    relativePath: { type: String, default: null },
    // Absolute path of the artifact file, and its HTML content when the host
    // already has it in memory (desktop preview). Both feed the create-mode name
    // suggestion: content is parsed for a <title>/<h1>; when absent for an HTML
    // artifact (e.g. mobile), the dialog fetches it via the abs path.
    fileAbsPath: { type: String, default: null },
    htmlContent: { type: String, default: null },
    // When mounted as a network-access helper from a ShareDialog, the share entry
    // point is suppressed to avoid share→bookmark→share recursion (the user is
    // already in a share dialog).
    hideShareEntry: { type: Boolean, default: false },
})
const emit = defineEmits(['saved', 'removed'])
const store = useDataStore()
const settingsStore = useSettingsStore()

// Share entry point (edit mode only — an existing bookmark id is required).
const showShareDialog = ref(false)
const existingAllowedHosts = ref({})
const shareSessionGrants = ref({})
const sharingEnabled = computed(() => !!settingsStore.getUsableShareBaseUrl)
function openShareDialog() {
    // Snapshot the artifact's session-only broker grants at open, like
    // ProjectView's global handler — the create dialog offers to promote them.
    shareSessionGrants.value = getSessionGrantsForBookmark(existingId.value)
    showShareDialog.value = true
}

// Network access (edit mode): denial rows + the bookmark's live dicts. The
// store copy refreshes via the artifact_bookmark_updated WS broadcast; the
// snapshot ref is only a fallback until it lands.
const denials = ref(null) // null = loading; [] = none
const liveBookmark = computed(() => store.artifactBookmarks[existingId.value] || null)
const allowedHosts = computed(() => liveBookmark.value?.allowed_hosts || existingAllowedHosts.value || {})
const deniedHosts = computed(() => liveBookmark.value?.denied_hosts || {})
// Network access only exists for HTML artifacts (the only ones that can fetch()
// through the broker); hide the whole section for `.md`/`.mmd`/image/pdf bookmarks.
const isHtmlArtifact = computed(() =>
    isHtmlArtifactPath(liveBookmark.value?.relative_path || props.relativePath))

async function fetchDenials() {
    if (!isEditMode.value) return
    const id = existingId.value
    let next = []
    try {
        const res = await apiFetch(`/api/artifact-bookmarks/${id}/network-denials/`)
        if (res.ok) next = (await res.json()).denials
    } catch {
        /* network failure — show "no activity" rather than loading forever */
    }
    if (existingId.value !== id) return // reopened on another bookmark meanwhile
    denials.value = next
}
function onDenialsPing(e) {
    if (e.detail?.bookmarkId === existingId.value && dialogRef.value?.open) fetchDenials()
}
onMounted(() => window.addEventListener('twicc:network-denials-updated', onDenialsPing))
onBeforeUnmount(() => window.removeEventListener('twicc:network-denials-updated', onDenialsPing))

// One entry per host, ordered pending → denied → allowed, then by host.
// Allowed/denied are seeded FIRST: a denial row for such a host only adds
// provenance detail, it never flips the stored state or kind.
const hostRows = computed(() => {
    const rows = new Map()
    const ensure = (host, kind, state) => {
        let row = rows.get(host)
        if (!row) {
            row = { host, kind, state, total: 0, provenances: new Map() }
            rows.set(host, row)
        }
        return row
    }
    for (const [host, entry] of Object.entries(allowedHosts.value)) ensure(host, entry.kind, 'allowed')
    for (const [host, entry] of Object.entries(deniedHosts.value)) ensure(host, entry.kind, 'denied')
    for (const d of denials.value || []) {
        const row = ensure(d.host_key, d.kind, 'pending')
        row.total += d.count
        const pkey = d.share ? d.share.id : '__owner__'
        let prov = row.provenances.get(pkey)
        if (!prov) {
            prov = { label: d.share ? d.share.label || d.share.id : 'You, in preview', count: 0, entries: [] }
            row.provenances.set(pkey, prov)
        }
        prov.count += d.count
        // GET returns rows newest-first, so entries stay sorted by `at` desc.
        prov.entries.push({ at: d.last_at, ip: d.ip, user_agent: d.user_agent, count: d.count })
    }
    const order = { pending: 0, denied: 1, allowed: 2 }
    return [...rows.values()].sort((a, b) => order[a.state] - order[b.state] || a.host.localeCompare(b.host))
})

const checked = reactive({}) // pending host -> bool
const selectedHosts = computed(() => Object.keys(checked).filter((h) => checked[h]))
const expandedHosts = reactive({}) // host -> bool (level 2: provenances)
const expandedProvs = reactive({}) // `${host}|${pkey}` -> bool (level 3: log detail)
function toggleHost(host) {
    expandedHosts[host] = !expandedHosts[host]
}
function toggleProv(host, pkey) {
    const k = `${host}|${pkey}`
    expandedProvs[k] = !expandedProvs[k]
}
const confirmAllow = reactive({ armed: false, hosts: [] })
const sectionError = ref('')

function resetNetworkSection() {
    denials.value = null
    sectionError.value = ''
    confirmAllow.armed = false
    confirmAllow.hosts = []
    for (const k of Object.keys(checked)) delete checked[k]
    for (const k of Object.keys(expandedHosts)) delete expandedHosts[k]
    for (const k of Object.keys(expandedProvs)) delete expandedProvs[k]
}

function kindOf(host) {
    return hostRows.value.find((r) => r.host === host)?.kind
}

// Adaptive non-public confirm copy — mirrors ArtifactBrokerPrompt's sentences.
const NON_PUBLIC_TARGET = {
    loopback: 'the machine TwiCC runs on (localhost)',
    lan: 'an address on the local network of the machine TwiCC runs on',
}
const confirmNonPublic = computed(() =>
    confirmAllow.hosts.map((h) => ({ host: h, kind: kindOf(h) })).filter((e) => e.kind && e.kind !== 'public'),
)

async function postHost(pathSuffix, method, body) {
    sectionError.value = ''
    const res = await apiFetch(`/api/artifact-bookmarks/${existingId.value}/${pathSuffix}/`, {
        method,
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
    })
    if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        sectionError.value = err.error || `Request failed (${res.status})`
        throw new Error(sectionError.value)
    }
}
async function performAllow(hosts) {
    try {
        for (const h of hosts) await postHost('allowed-hosts', 'POST', { url: h, kind: kindOf(h) })
    } catch {
        return
    }
    hosts.forEach((h) => delete checked[h])
    fetchDenials()
}
function allowHosts(hosts) {
    // Non-public gate (design N7): allowing from this list bypasses the consent
    // prompt, so loopback/lan hosts ALWAYS go through the confirm callout —
    // re-arming replaces any previous pending confirm, so the callout always
    // describes the latest request. Only "Allow anyway" (confirmAllowNow)
    // performs a non-public allow.
    const nonPublic = hosts.filter((h) => kindOf(h) !== 'public')
    if (nonPublic.length) {
        confirmAllow.hosts = hosts
        confirmAllow.armed = true
        return
    }
    performAllow(hosts)
}
function confirmAllowNow() {
    // Snapshot before disarming; drop hosts whose row vanished meanwhile
    // (e.g. a cross-tab change) — their kind is unknown now.
    const hosts = confirmAllow.hosts.filter((h) => kindOf(h) !== undefined)
    confirmAllow.armed = false
    confirmAllow.hosts = []
    if (hosts.length) performAllow(hosts)
}
async function denyHost(h) {
    try {
        await postHost('denied-hosts', 'POST', { url: h, kind: kindOf(h) })
    } catch {
        return
    }
    delete checked[h]
}
async function undenyHost(h) {
    try {
        await postHost('denied-hosts', 'DELETE', { url: h })
    } catch {
        /* error shown via sectionError */
    }
}
async function removeAllowed(h) {
    try {
        await postHost('allowed-hosts', 'DELETE', { url: h })
    } catch {
        /* error shown via sectionError */
    }
}

const dialogRef = ref(null)
const nameInputRef = ref(null)
const saveButtonRef = ref(null)

const existingId = ref(null)
const localName = ref('')
// True once the user typed in the name field — freezes the auto-filled
// suggestion so an async title extraction never overwrites their edit.
const nameUserEdited = ref(false)
const localScope = ref('project')
const isSaving = ref(false)
const errorMessage = ref('')

const isEditMode = computed(() => existingId.value !== null)
const instanceId = useId()
const formId = `artifact-bookmark-form-${instanceId}`

// wa-button doesn't expose `form` as a property — set it via setAttribute.
function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) saveButtonRef.value.setAttribute('form', formId)
    })
}

function focusName() {
    const input = nameInputRef.value
    if (!input) return
    input.focus()
    const len = input.value?.length || 0
    input.setSelectionRange?.(len, len)
}

// Guard against wa-show / wa-after-show bubbling up from the nested wa-select.
function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}
function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    focusName()
}

// Fetch the artifact's raw content (create mode, HTML without in-memory content)
// via the same session-scoped endpoint FilePane uses. Returns null on any miss.
async function fetchArtifactHtml() {
    const abs = props.fileAbsPath
    const projectId = store.sessions[props.sessionId]?.project_id
    if (!abs || !projectId || !props.sessionId) return null
    try {
        const res = await apiFetch(
            `/api/projects/${projectId}/sessions/${props.sessionId}/file-content/?path=${encodeURIComponent(abs)}`,
        )
        if (!res.ok) return null
        const data = await res.json()
        if (data.binary || data.error) return null
        return data.content || null
    } catch {
        return null
    }
}

// Try to upgrade the path-derived suggestion to the HTML <title>/<h1>. Applied
// only if still in create mode on the same artifact and the user hasn't typed.
async function maybeUpgradeNameFromHtml() {
    if (!isHtmlArtifactPath(props.relativePath)) return
    const rp = props.relativePath
    const html = props.htmlContent || (await fetchArtifactHtml())
    if (!html) return
    const better = suggestArtifactBookmarkName({ relativePath: rp, htmlContent: html })
    if (better && !isEditMode.value && !nameUserEdited.value && props.relativePath === rp) {
        localName.value = better
    }
}

/** Open in create mode (existing = null) or edit mode (an existing bookmark). */
function open(existing = null) {
    errorMessage.value = ''
    isSaving.value = false
    nameUserEdited.value = false
    resetNetworkSection()
    if (existing) {
        existingId.value = existing.id
        localName.value = existing.name || ''
        localScope.value = existing.scope || 'project'
        existingAllowedHosts.value = existing.allowed_hosts || {}
        // Only HTML artifacts have a network surface; skip the denials fetch otherwise
        // (the section is hidden anyway).
        if (isHtmlArtifactPath(existing.relative_path)) fetchDenials()
    } else {
        existingId.value = null
        // Auto-fill a suggested name: the path-derived fallback shows instantly,
        // then an HTML <title>/<h1> replaces it if found and untouched.
        localName.value = suggestArtifactBookmarkName({ relativePath: props.relativePath })
        localScope.value = 'project'
        maybeUpgradeNameFromHtml()
    }
    syncFormState()
    if (dialogRef.value) dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) dialogRef.value.open = false
}

async function handleSave() {
    if (isSaving.value) return
    const name = localName.value.trim()
    if (!name) {
        errorMessage.value = 'Name is required'
        return
    }
    isSaving.value = true
    errorMessage.value = ''
    try {
        if (isEditMode.value) {
            await store.updateArtifactBookmark(existingId.value, { name, scope: localScope.value })
        } else {
            await store.createArtifactBookmark({
                sessionId: props.sessionId,
                relativePath: props.relativePath,
                name,
                scope: localScope.value,
            })
        }
        emit('saved')
        close()
    } catch (err) {
        errorMessage.value = err?.message || 'Failed to save bookmark'
    } finally {
        isSaving.value = false
    }
}

async function handleRemove() {
    if (isSaving.value || !isEditMode.value) return
    isSaving.value = true
    errorMessage.value = ''
    try {
        await store.deleteArtifactBookmark(existingId.value)
        emit('removed')
        close()
    } catch (err) {
        errorMessage.value = err?.message || 'Failed to remove bookmark'
    } finally {
        isSaving.value = false
    }
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="isEditMode ? 'Edit artifact bookmark' : 'Bookmark artifact'"
        class="artifact-bookmark-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">{{ $t('common.fields.name') }}</label>
                <wa-input
                    ref="nameInputRef"
                    :value.prop="localName"
                    @input="localName = $event.target.value; nameUserEdited = true"
                    :placeholder="$t('artifacts.bookmarks.namePlaceholder')"
                    maxlength="255"
                ></wa-input>
            </div>
            <div class="form-group">
                <label class="form-label">{{ $t('artifacts.bookmarks.scope') }}</label>
                <wa-select
                    :value.prop="localScope"
                    @change="localScope = $event.target.value"
                    size="small"
                >
                    <wa-option value="project">{{ $t('artifacts.bookmarks.scopeProject') }}</wa-option>
                    <wa-option value="workspace">{{ $t('artifacts.bookmarks.scopeWorkspace') }}</wa-option>
                    <wa-option value="all">{{ $t('artifacts.bookmarks.scopeAll') }}</wa-option>
                </wa-select>
                <div class="form-hint">{{ $t('artifacts.bookmarks.scopeHint') }}</div>
            </div>
            <div v-if="isEditMode && isHtmlArtifact" class="form-group network-section">
                <label class="form-label">{{ $t('artifacts.network.title') }}</label>
                <p v-if="denials === null" class="muted">{{ $t('artifacts.network.loading') }}</p>
                <p v-else-if="!hostRows.length" class="muted">{{ $t('artifacts.network.empty') }}</p>
                <template v-else>
                    <div v-for="row in hostRows" :key="row.host" class="host-row">
                        <div class="host-row-main">
                            <wa-checkbox
                                v-if="row.state === 'pending'"
                                :checked="!!checked[row.host]"
                                @change.stop="checked[row.host] = $event.target.checked"
                            ></wa-checkbox>
                            <wa-tag
                                size="small"
                                :variant="row.state === 'allowed' ? 'success' : row.state === 'denied' ? 'danger' : 'warning'"
                            >{{ row.state }}</wa-tag>
                            <code class="host-key">{{ row.host }}</code>
                            <wa-tag v-if="row.kind !== 'public'" size="small" variant="warning">{{ row.kind }}</wa-tag>
                            <button v-if="row.total" class="denial-count" type="button" @click="toggleHost(row.host)">
                                {{ $t('artifacts.network.refusedCount', { count: row.total }, row.total) }}
                            </button>
                            <span class="host-actions">
                                <wa-button
                                    v-if="row.state !== 'allowed'"
                                    size="small"
                                    appearance="outlined"
                                    @click="allowHosts([row.host])"
                                >{{ $t('artifacts.network.allow') }}</wa-button>
                                <wa-button
                                    v-if="row.state === 'pending'"
                                    size="small"
                                    appearance="outlined"
                                    @click="denyHost(row.host)"
                                >{{ $t('artifacts.network.deny') }}</wa-button>
                                <wa-button
                                    v-if="row.state === 'denied'"
                                    size="small"
                                    appearance="plain"
                                    @click="undenyHost(row.host)"
                                >{{ $t('artifacts.network.undoDeny') }}</wa-button>
                                <wa-button
                                    v-if="row.state === 'allowed'"
                                    size="small"
                                    appearance="plain"
                                    variant="danger"
                                    @click="removeAllowed(row.host)"
                                >{{ $t('artifacts.bookmarks.removeAction') }}</wa-button>
                            </span>
                        </div>
                        <!-- Level 2: provenances (share links / owner preview). -->
                        <ul v-if="expandedHosts[row.host]" class="prov-list">
                            <li v-for="[pkey, p] in row.provenances" :key="pkey">
                                <div class="prov-row">
                                    <span class="prov-label">{{ p.label }}</span>
                                    <button class="denial-count" type="button" @click="toggleProv(row.host, pkey)">
                                        {{ p.count }}×
                                    </button>
                                </div>
                                <!-- Level 3: date/ip/ua detail. -->
                                <AccessLogList v-if="expandedProvs[`${row.host}|${pkey}`]" :entries="p.entries" />
                            </li>
                        </ul>
                    </div>
                    <wa-button
                        v-if="selectedHosts.length"
                        size="small"
                        variant="brand"
                        @click="allowHosts(selectedHosts)"
                    >
                        {{ $t('artifacts.network.allowSelected', { count: selectedHosts.length }) }}
                    </wa-button>
                    <wa-callout v-if="confirmAllow.armed" variant="warning">
                        <ul class="confirm-hosts">
                            <li v-for="e in confirmNonPublic" :key="e.host">
                                <i18n-t keypath="artifacts.network.resolvesTo" scope="global">
                                    <template #host><code>{{ e.host }}</code></template>
                                    <template #kind>{{ e.kind }}</template>
                                    <template #target>{{ NON_PUBLIC_TARGET[e.kind] }}</template>
                                </i18n-t>
                            </li>
                        </ul>
                        <p class="confirm-text">
                            {{ $t('artifacts.network.sharedHostWarning') }}
                        </p>
                        <div class="confirm-actions">
                            <wa-button size="small" variant="warning" @click="confirmAllowNow">
                                {{ $t('artifacts.network.allowAnyway') }}
                            </wa-button>
                            <wa-button size="small" appearance="plain" @click="confirmAllow.armed = false">
                                {{ $t('common.actions.cancel') }}
                            </wa-button>
                        </div>
                    </wa-callout>
                    <wa-callout v-if="sectionError" variant="danger" size="small">{{ sectionError }}</wa-callout>
                </template>
                <div class="form-hint">
                    {{ $t('artifacts.network.hostsHint') }}
                </div>
            </div>
            <wa-callout v-if="errorMessage" variant="danger" size="small">{{ errorMessage }}</wa-callout>
        </form>
        <div slot="footer" class="dialog-footer">
            <wa-button
                v-if="isEditMode"
                variant="danger"
                appearance="outlined"
                class="footer-remove"
                :disabled="isSaving"
                @click="handleRemove"
            >
                {{ $t('artifacts.bookmarks.removeAction') }}
            </wa-button>
            <wa-button
                v-if="isEditMode && !hideShareEntry"
                variant="neutral"
                appearance="outlined"
                :disabled="!sharingEnabled"
                :title="sharingEnabled ? 'Share this artifact' : 'Configure a share host in Settings → Sharing'"
                @click="openShareDialog"
            >
                <wa-icon name="share-nodes" slot="start"></wa-icon>
                {{ $t('artifacts.sharing.shareMenu') }}
            </wa-button>
            <wa-button variant="neutral" appearance="outlined" :disabled="isSaving" @click="close">{{ $t('common.actions.cancel') }}</wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand" :disabled="isSaving">
                {{ isEditMode ? $t('common.actions.save') : $t('artifacts.bookmarks.bookmark') }}
            </wa-button>
        </div>
        <ShareDialog v-if="isEditMode && !hideShareEntry" :open="showShareDialog" kind="artifact"
                     :bookmark-id="existingId" :allowed-hosts="allowedHosts"
                     :session-grants="shareSessionGrants"
                     :default-title="localName" @close="showShareDialog = false" />
    </wa-dialog>
</template>

<style scoped>
.artifact-bookmark-dialog {
    --width: min(560px, calc(100vw - 2rem));
}
.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}
.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}
.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}
.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
    flex-wrap: wrap;
}
.footer-remove {
    margin-right: auto;
}
/* Network access section — visually quiet, mirrors ShareListPanel's rows. */
.network-section {
    font-size: 0.85rem;
}
.muted {
    margin: 0;
    color: var(--wa-color-text-quiet);
}
.host-row {
    padding: 0.4rem 0;
    border-bottom: 1px solid var(--wa-color-surface-border);
}
.host-row-main {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}
.host-key {
    flex: 1;
    min-width: 3rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
/* WA's native.css sizes bare <button>s like form controls — reset to a quiet
   inline button, same idiom as ShareListPanel's .share-views. */
.denial-count {
    display: inline;
    height: auto;
    padding: 0;
    background: none;
    border: none;
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    font-size: 0.85rem;
    text-decoration: underline dotted;
    white-space: nowrap;
}
.host-actions {
    margin-left: auto;
    display: flex;
    gap: 0.25rem;
}
.prov-list {
    list-style: none;
    margin: 0.35rem 0 0;
    padding: 0 0 0 1.5rem;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.8rem;
}
.prov-row {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
}
.prov-label {
    color: var(--wa-color-text-quiet);
}
.confirm-hosts {
    margin: 0 0 0.35rem;
    padding-left: 1.1rem;
}
.confirm-text {
    margin: 0;
}
.confirm-actions {
    display: flex;
    gap: 0.5rem;
    margin-top: 0.5rem;
}
</style>
