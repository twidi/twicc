<script setup>
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages'
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
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
    if (removePassword.value) return t('sharing.edit.passwordRemovedPlaceholder')
    return props.edit?.has_password ? t('sharing.edit.passwordUnchangedPlaceholder') : t('sharing.edit.noPasswordPlaceholder')
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
        error.value = localUiMessage('sharing.edit.hostRequired')
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
            toast.success(t('sharing.edit.updated'))
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
                if (!res.ok) throw await res.json().catch(() => ({ error: localUiMessage('sharing.edit.allowHostFailed', { host: key }) }))
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
        error.value = (e?.errors?.[0]?.message) || e?.reason || e?.error || localUiMessage('sharing.edit.saveFailed')
    }
}

function copyUrl() { navigator.clipboard.writeText(linkUrl.value); toast.success(t('sharing.edit.urlCopied')) }

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
    <wa-dialog ref="dialogRef" :open="open" :label="edit ? $t('sharing.edit.editTitle') : $t('sharing.edit.createTitle')"
               style="--width: min(560px, calc(100vw - 2rem))"
               @wa-after-show="onAfterShow" @wa-hide="onHide">
        <form :id="formId" @submit.prevent="handleSave">
            <wa-callout v-if="error" variant="danger">{{ resolveUiMessage(error) }}</wa-callout>
            <wa-callout v-for="failure in inlineDataErrors" :key="failure.artifact_id" variant="warning">
                {{ $t('sharing.edit.dataExportFailed', { artifactId: failure.artifact_id }) }}
            </wa-callout>

            <wa-callout v-if="!sharingEnabled" variant="warning">
                {{ $t('sharing.edit.noHost') }}
            </wa-callout>

            <wa-callout variant="warning">
                {{ isSession ? $t('sharing.edit.transcriptWarning') : $t('sharing.edit.artifactWarning') }}
                <HelpIconButton help-key="sharing" :label="$t('sharing.edit.learn')" />
            </wa-callout>

            <label>{{ $t('sharing.edit.privateLabel') }}
                <wa-input id="share-label-input" :value="form.label"
                          @input="form.label = $event.target.value" :placeholder="$t('sharing.edit.labelPlaceholder')"></wa-input>
            </label>

            <label>{{ $t('sharing.edit.viewerTitle') }}
                <wa-input :value="form.display_title" @input="form.display_title = $event.target.value"
                          :placeholder="defaultTitle || $t('sharing.edit.defaultTitle')"
                          :disabled="!form.show_title"></wa-input>
            </label>
            <wa-switch :checked="form.show_title"
                       @change.stop="form.show_title = $event.target.checked">
                {{ $t('sharing.edit.showTitle') }} <span class="switch-hint">{{ $t('sharing.edit.showTitleHint') }}</span>
            </wa-switch>

            <template v-if="isSession">
                <label>{{ $t('sharing.edit.mode') }}
                    <wa-select :value="form.mode" @change.stop="form.mode = $event.target.value">
                        <wa-option value="live">{{ $t('sharing.edit.live') }}</wa-option>
                        <wa-option value="snapshot">{{ $t('sharing.edit.snapshot') }}</wa-option>
                    </wa-select>
                </label>
                <label>{{ $t('sharing.edit.maxDetail') }}
                    <wa-select :value="form.max_display_mode" @change.stop="form.max_display_mode = $event.target.value">
                        <wa-option value="conversation">{{ $t('sharing.edit.conversation') }}</wa-option>
                        <wa-option value="simplified">{{ $t('sharing.edit.simplified') }}</wa-option>
                        <wa-option value="normal">{{ $t('sharing.edit.normal') }}</wa-option>
                        <wa-option value="debug">{{ $t('sharing.edit.debug') }}</wa-option>
                    </wa-select>
                </label>
                <wa-switch :checked="form.include_subagents" @change.stop="form.include_subagents = $event.target.checked">{{ $t('sharing.edit.subagents') }}</wa-switch>
                <wa-switch :checked="form.include_inline_artifacts" @change.stop="form.include_inline_artifacts = $event.target.checked">{{ $t('sharing.edit.inlineArtifacts') }}</wa-switch>
                <p v-if="form.include_inline_artifacts" class="net-hint">{{ $t('sharing.edit.inlineArtifactsHint') }}</p>
                <wa-switch :checked="form.show_timestamps" @change.stop="form.show_timestamps = $event.target.checked">{{ $t('sharing.edit.timestamps') }}</wa-switch>
            </template>

            <template v-else-if="isHtmlArtifact">
                <p class="net-hint">
                    {{ $t('sharing.edit.snapshotData') }}
                </p>
                <wa-callout v-if="allowedHostList.length" variant="neutral">
                    {{ $t('sharing.edit.allowedHosts') }}
                    <ul><li v-for="h in allowedHostList" :key="h"><code>{{ h }}</code></li></ul>
                </wa-callout>
                <wa-callout v-if="promotableHosts.length" variant="warning">
                    {{ $t('sharing.edit.promoteHosts') }}
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
                    {{ $t('sharing.edit.noNetwork') }}
                </wa-callout>
                <p v-else-if="!edit" class="net-hint">
                    {{ $t('sharing.edit.unlistedHosts') }}
                </p>
                <p class="net-hint">
                    {{ $t('sharing.edit.networkHint') }}
                    <wa-button size="small" appearance="outlined" @click="openBookmarkDialog">{{ $t('sharing.edit.manageNetwork') }}</wa-button>
                </p>
            </template>

            <label>{{ $t('sharing.edit.password') }}
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
                        {{ form.password ? $t('sharing.edit.clear') : (removePassword ? $t('sharing.edit.keepPassword') : $t('sharing.edit.removePassword')) }}
                    </AppTooltip>
                </div>
            </label>
            <label>{{ $t('sharing.edit.expires') }}
                <wa-input type="datetime-local" :value="form.expires_at"
                          @input="form.expires_at = $event.target.value"></wa-input>
            </label>
            <wa-switch :checked="form.notify_on_view" @change.stop="form.notify_on_view = $event.target.checked">{{ $t('sharing.edit.notify') }}</wa-switch>

        </form>
        <div slot="footer" class="dialog-footer">
            <wa-button v-if="linkUrl" class="copy-link" @click="copyUrl"><wa-icon slot="start" name="copy"></wa-icon>{{ $t('sharing.edit.copyLink') }}</wa-button>
            <wa-button @click="emit('close')">{{ $t('common.actions.close') }}</wa-button>
            <wa-button ref="submitBtnRef" type="submit" variant="brand" :form="formId" :disabled="!sharingEnabled">{{ edit ? $t('common.actions.save') : $t('sharing.edit.createLink') }}</wa-button>
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
