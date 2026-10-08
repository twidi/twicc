<script setup>
import { ref, reactive, computed, watch, nextTick, useId, defineAsyncComponent } from 'vue'
import { useSharesStore } from '../../stores/shares'
import { useSettingsStore } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { includeInlineArtifacts } from '../../inline-artifacts/shareOptions'
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { apiFetch } from '../../utils/api'
import { toast } from '../../composables/useToast'
import { isHtmlArtifactPath } from '../../utils/artifactBookmark'
import AppTooltip from '../ui/AppTooltip.vue'
import HelpIconButton from '../help/HelpIconButton.vue'

// Async: ArtifactBookmarkDialog.vue statically imports ShareDialog.vue, so this
// back-reference must be lazy to avoid a circular-import HMR break (project rule).
const ArtifactBookmarkDialog = defineAsyncComponent(() => import('../artifacts/ArtifactBookmarkDialog.vue'))

const props = defineProps({
    open: Boolean,
    kind: { type: String, required: true },         // 'session' | 'artifact'
    sessionId: { type: String, default: null },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },  // artifact: hosts viewers reach
    sessionGrants: { type: Object, default: () => ({}) }, // artifact: "This session" broker grants
    defaultTitle: { type: String, default: '' },     // real session title / bookmark name (placeholder)
    edit: { type: Object, default: null },          // existing serialized share when editing
})
const emit = defineEmits(['close'])
const shares = useSharesStore()
const settings = useSettingsStore()
const dataStore = useDataStore()

const bookmarkDialogRef = ref(null)
// Relies on the invariant that bookmarkId always comes from an already-loaded
// bookmark (the store lookup can only miss transiently).
function openBookmarkDialog() {
    const bm = dataStore.artifactBookmarks[props.bookmarkId]
    if (bm) bookmarkDialogRef.value?.open(bm)
}

const sharingEnabled = computed(() => !!settings.getUsableShareBaseUrl)

const form = reactive({
    label: '', display_title: '', password: '', expires_at: '', notify_on_view: false,
    // session options
    mode: 'live', max_display_mode: 'normal', include_subagents: true, include_inline_artifacts: true,
    show_timestamps: true, show_title: true,
})
const inlineDataErrors = computed(() =>
    (shares.shares[props.edit?.id] || props.edit)?.inline_artifact_data_errors || [])
const error = ref('')
const createdUrl = ref('')
// The link to copy: the freshly-created one, or the existing share's when editing.
const linkUrl = computed(() => createdUrl.value || (props.edit ? shareAbsoluteUrl(props.edit) : null))
const dialogRef = ref(null)
const submitBtnRef = ref(null)
// Unique per instance: several ShareDialogs coexist in the DOM (SessionHeader keeps
// one mounted while a session is open; ProjectView mounts the global session and
// artifact ones). A shared id made the footer submit button — associated by
// `form=<id>` — resolve via getElementById to the FIRST matching form in document
// order, so a click submitted a different (usually closed) dialog and the visible
// one looked dead. Mirrors ProjectEditDialog's per-instance formId.
const formId = `share-dialog-form-${useId()}`

// "This session" broker grants not yet on the bookmark's persisted allowlist.
// The share proxy only honours the PERSISTED list, so these hosts are invisible
// to viewers — offer to promote them at creation (checked = saved permanently
// on the artifact, the same write as the prompt's "Forever"). Create-only: the
// promotion belongs to "make this link work", not to editing link settings.
const promotableHosts = computed(() =>
    props.kind === 'artifact' && !props.edit && props.bookmarkId != null
        ? Object.entries(props.sessionGrants || {}).filter(([key]) => !(props.allowedHosts || {})[key])
        : []
)
const promote = reactive({}) // host key -> checked

// Password removal: an empty field means "unchanged" on edit, so removing an
// existing password needs an explicit intent. The clear button arms it; typing
// a replacement disarms it. (In create mode the same button just empties a
// typed value — `removePassword` never arms without an existing password.)
const removePassword = ref(false)
const clearPwId = `share-clear-pw-${useId()}`
const passwordPlaceholder = computed(() => {
    if (removePassword.value) return 'will be removed on save'
    return props.edit?.has_password ? 'unchanged — type to replace' : 'no password'
})
function onPasswordInput(e) {
    form.password = e.target.value
    if (form.password) removePassword.value = false
}
function clearOrRemovePassword() {
    if (form.password) form.password = ''
    else if (props.edit?.has_password) removePassword.value = !removePassword.value
}

// `immediate` matters for the on-demand mounts (ProjectView's artifact/session
// dialog, ShareManagerDialog's edit dialog): they are v-if'd in at the same tick
// `open` becomes true, so the component mounts with `open` already true and a
// plain watcher never sees the false→true edge — leaving the form (title…)
// unseeded on the first open in a tab. The `if (o)` guard keeps a closed mount a
// no-op.
watch(() => props.open, (o) => { if (o) reset() }, { immediate: true })
function reset() {
    error.value = ''; createdUrl.value = ''
    removePassword.value = false
    Object.keys(promote).forEach((k) => delete promote[k])
    for (const [key] of promotableHosts.value) promote[key] = true
    const e = props.edit
    Object.assign(form, {
        // Pre-fill the title field with the real session title / bookmark name so it's
        // visible and editable; clearing it (session, title shown) falls back to the
        // live real title server-side.
        label: e?.label || '', display_title: e?.options?.display_title || props.defaultTitle || '',
        password: '', expires_at: e?.expires_at || '',
        notify_on_view: e?.notify_on_view || false,
        mode: e?.options?.mode || 'live',
        max_display_mode: e?.options?.max_display_mode || 'normal',
        include_subagents: e?.options?.include_subagents ?? true,
        include_inline_artifacts: includeInlineArtifacts(e?.options),
        show_timestamps: e?.options?.show_timestamps ?? true,
        show_title: e?.options?.show_title ?? true,
    })
}

const isSession = computed(() => props.kind === 'session')
const allowedHostList = computed(() => Object.keys(props.allowedHosts || {}))
// Network access only exists for HTML artifacts (the only ones that can fetch()
// through the broker). A `.md`/`.mmd`/image/pdf artifact has no network surface,
// so its whole allow/refuse guidance is meaningless and must not show.
const isHtmlArtifact = computed(() =>
    isHtmlArtifactPath(dataStore.artifactBookmarks[props.bookmarkId]?.relative_path))

function sessionOptions() {
    return {
        mode: form.mode, max_display_mode: form.max_display_mode,
        include_subagents: form.include_subagents,
        include_inline_artifacts: form.include_inline_artifacts,
        show_timestamps: form.show_timestamps, show_title: form.show_title,
    }
}

// Options sent for both kinds: session config (session only) + show_title (the
// master switch, both kinds) + the optional public title override. The title is
// only sent when "Show title" is on (off ⇒ the viewer sees the generic label);
// empty ⇒ omitted ⇒ viewers see the real session title / bookmark name.
function buildOptions() {
    const opts = isSession.value ? sessionOptions() : { show_title: form.show_title }
    const t = form.display_title.trim()
    if (t && form.show_title) opts.display_title = t
    return opts
}

async function handleSave() {
    error.value = ''
    if (!sharingEnabled.value) {
        error.value = 'Configure a share host in Settings → Sharing to create links.'
        return
    }
    try {
        if (props.edit) {
            const fields = { label: form.label.trim(), notify_on_view: form.notify_on_view, options: buildOptions() }
            // Present + truthy → set a new password; present + empty → clear it;
            // absent → leave unchanged.
            if (form.password) fields.password = form.password
            else if (removePassword.value) fields.password = ''
            fields.expires_at = form.expires_at || null
            await shares.patchShare(props.edit.id, fields)
            // Editing is a plain "apply my changes" action, so close on save (the URL
            // is unchanged and already known). Creating instead keeps the dialog open
            // so the freshly-minted link can be copied from the footer.
            toast.success('Share updated')
            emit('close')
        } else {
            // Promote the checked session-only grants onto the bookmark's
            // allowlist BEFORE minting the link: the share proxy reads it live,
            // so the link works from its first view. A failure aborts creation
            // (the link would ship broken).
            for (const [key, entry] of promotableHosts.value) {
                if (!promote[key]) continue
                const res = await apiFetch(`/api/artifact-bookmarks/${props.bookmarkId}/allowed-hosts/`, {
                    method: 'POST',
                    headers: { 'content-type': 'application/json' },
                    body: JSON.stringify({ url: key, kind: entry.kind }),
                })
                if (!res.ok) throw await res.json().catch(() => ({ error: `failed to allow ${key}` }))
            }
            const body = {
                kind: props.kind, label: form.label.trim(),
                password: form.password || null, expires_at: form.expires_at || null,
                notify_on_view: form.notify_on_view, options: buildOptions(),
            }
            if (isSession.value) body.session_id = props.sessionId
            else body.bookmark_id = props.bookmarkId
            const share = await shares.createShare(body)
            createdUrl.value = shareAbsoluteUrl(share)
        }
    } catch (e) {
        error.value = (e?.errors?.[0]?.message) || e?.reason || e?.error || 'Failed to save share'
    }
}

function copyUrl() { navigator.clipboard.writeText(linkUrl.value); toast.success('Share URL copied') }

function onAfterShow(e) {
    // Guard bubbling wa-after-show from a nested wa-select/wa-switch panel: re-running
    // the focus logic on the dialog would steal focus from an opening dropdown and
    // snap it shut (the classic nested-WA-event trap).
    if (e.target !== dialogRef.value) return
    nextTick(() => {
        // Pin the submit button's form association (wa-button reflects `:form`, but
        // set it explicitly too — same belt-and-suspenders as ProjectEditDialog).
        submitBtnRef.value?.setAttribute('form', formId)
        dialogRef.value?.querySelector('#share-label-input')?.focus()
    })
}
// Guard bubbling wa-hide from nested wa-select/wa-switch (only the dialog's own closes).
function onHide(e) { if (e.target === dialogRef.value) emit('close') }
</script>

<template>
    <wa-dialog ref="dialogRef" :open="open" :label="edit ? 'Edit share' : 'Create share'"
               style="--width: min(560px, calc(100vw - 2rem))"
               @wa-after-show="onAfterShow" @wa-hide="onHide">
        <form :id="formId" @submit.prevent="handleSave">
            <wa-callout v-if="error" variant="danger">{{ error }}</wa-callout>
            <wa-callout v-for="failure in inlineDataErrors" :key="failure.artifact_id" variant="warning">
                Saved data export failed for {{ failure.artifact_id }}. The share keeps its last complete saved data.
            </wa-callout>

            <wa-callout v-if="!sharingEnabled" variant="warning">
                No share host configured. Set one in Settings → Sharing before creating links.
            </wa-callout>

            <wa-callout variant="warning">
                Anyone with the link can read this {{ isSession ? 'transcript' : 'artifact' }} as-is,
                including file paths, commands and output. There is no redaction.
                <HelpIconButton help-key="sharing" label="Learn about sharing" />
            </wa-callout>

            <label>Label (private)
                <wa-input id="share-label-input" :value="form.label"
                          @input="form.label = $event.target.value" placeholder="e.g. for Alice"></wa-input>
            </label>

            <label>Title (shown to viewers)
                <wa-input :value="form.display_title" @input="form.display_title = $event.target.value"
                          :placeholder="defaultTitle || 'Default title'"
                          :disabled="!form.show_title"></wa-input>
            </label>
            <wa-switch :checked="form.show_title"
                       @change.stop="form.show_title = $event.target.checked">
                Show title <span class="switch-hint">— off shows viewers a generic label</span>
            </wa-switch>

            <template v-if="isSession">
                <label>Snapshot / live
                    <wa-select :value="form.mode" @change.stop="form.mode = $event.target.value">
                        <wa-option value="live">Live (follows the session)</wa-option>
                        <wa-option value="snapshot">Snapshot (frozen now)</wa-option>
                    </wa-select>
                </label>
                <label>Max detail
                    <wa-select :value="form.max_display_mode" @change.stop="form.max_display_mode = $event.target.value">
                        <wa-option value="conversation">Conversation</wa-option>
                        <wa-option value="simplified">Simplified</wa-option>
                        <wa-option value="normal">Normal</wa-option>
                        <wa-option value="debug">Debug (raw JSON)</wa-option>
                    </wa-select>
                </label>
                <wa-switch :checked="form.include_subagents" @change.stop="form.include_subagents = $event.target.checked">Include subagents</wa-switch>
                <wa-switch :checked="form.include_inline_artifacts" @change.stop="form.include_inline_artifacts = $event.target.checked">Include inline artifacts</wa-switch>
                <p v-if="form.include_inline_artifacts" class="net-hint">Includes inline artifact pages, their assets, and their saved data/ files. Visitors cannot modify the saved data.</p>
                <wa-switch :checked="form.show_timestamps" @change.stop="form.show_timestamps = $event.target.checked">Show timestamps</wa-switch>
            </template>

            <template v-else-if="isHtmlArtifact">
                <p class="net-hint">
                    The snapshot includes any files the artifact saved under its data/ folder.
                </p>
                <wa-callout v-if="allowedHostList.length" variant="neutral">
                    Viewers will be able to reach these hosts (already allowed on this artifact):
                    <ul><li v-for="h in allowedHostList" :key="h"><code>{{ h }}</code></li></ul>
                </wa-callout>
                <wa-callout v-if="promotableHosts.length" variant="warning">
                    These hosts are allowed only for your current session — viewers won't reach
                    them unless you allow them on this artifact. Checked hosts are saved
                    permanently when the link is created:
                    <ul class="promote-list">
                        <li v-for="[h] in promotableHosts" :key="h">
                            <wa-checkbox :checked="promote[h]"
                                         @change.stop="promote[h] = $event.target.checked">
                                <code>{{ h }}</code>
                            </wa-checkbox>
                        </li>
                    </ul>
                </wa-callout>
                <!-- Network guidance (create only): viewers only reach the PERSISTED
                     allowlist, so tell the owner what to do when it's (or may be)
                     incomplete — full callout when nothing is allowed anywhere, a
                     quiet one-liner when some hosts already are. -->
                <wa-callout v-if="!edit && !allowedHostList.length && !promotableHosts.length"
                            variant="neutral">
                    No network access is allowed on this artifact — any request it makes
                    will be refused for viewers. If it needs the network, manage its
                    allowed hosts from the bookmark settings.
                </wa-callout>
                <p v-else-if="!edit" class="net-hint">
                    Requests to hosts not listed above will be refused for viewers.
                </p>
                <p class="net-hint">
                    Network access (allowed and refused hosts) is managed in the bookmark settings.
                    <wa-button size="small" appearance="outlined" @click="openBookmarkDialog">Manage network access…</wa-button>
                </p>
            </template>

            <label>Password (optional)
                <div class="password-row">
                    <wa-input type="password" :value="form.password"
                              @input="onPasswordInput"
                              :placeholder="passwordPlaceholder"></wa-input>
                    <wa-button v-if="form.password || edit?.has_password" :id="clearPwId"
                               appearance="plain" :variant="removePassword ? 'danger' : 'neutral'"
                               class="pw-clear" @click.stop="clearOrRemovePassword">
                        <wa-icon :name="removePassword ? 'arrow-rotate-left' : 'xmark'"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="form.password || edit?.has_password" :for="clearPwId">
                        {{ form.password ? 'Clear' : (removePassword ? 'Keep the password' : 'Remove the password') }}
                    </AppTooltip>
                </div>
            </label>
            <label>Expires (optional)
                <wa-input type="datetime-local" :value="form.expires_at"
                          @input="form.expires_at = $event.target.value"></wa-input>
            </label>
            <wa-switch :checked="form.notify_on_view" @change.stop="form.notify_on_view = $event.target.checked">Notify me when viewed</wa-switch>

        </form>
        <div slot="footer" class="dialog-footer">
            <wa-button v-if="linkUrl" class="copy-link" @click="copyUrl"><wa-icon slot="start" name="copy"></wa-icon>Copy link</wa-button>
            <wa-button @click="emit('close')">Close</wa-button>
            <wa-button ref="submitBtnRef" type="submit" variant="brand" :form="formId" :disabled="!sharingEnabled">{{ edit ? 'Save' : 'Create link' }}</wa-button>
        </div>
        <ArtifactBookmarkDialog v-if="kind === 'artifact'" ref="bookmarkDialogRef" hide-share-entry />
    </wa-dialog>
</template>

<style scoped>
form {
    display: flex;
    flex-direction: column;
    row-gap: 1em;
}
label { display: block; font-size: var(--wa-font-size-s); font-weight: 600; }
label wa-input, label wa-select { margin-top: 0.3rem; font-weight: 400; }
wa-switch { display: block; }
.switch-hint { font-weight: 400; color: var(--wa-color-text-quiet); }
.password-row { display: flex; gap: 0.3rem; align-items: center; margin-top: 0.3rem; }
.password-row wa-input { flex: 1; margin-top: 0; }
.promote-list {
    list-style: none;
    padding-left: 0;
    margin: 0.5rem 0 0;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}
.net-hint {
    margin: 0;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
.copy-link { margin-right: auto; }
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
    align-items: center;
    flex-wrap: wrap;
}
</style>
