<script setup>
import { localUiMessage, resolveUiMessage } from '../../../i18n/uiMessages'
// Save the current session's layout to the named-layouts catalog: either overwrite an existing
// named layout, or create a new one (name prefilled "Default"). On save it can also set the layout
// as the default for new sessions at one or more scopes — worktree / project / global — each an
// independent, one-way checkbox (checking applies; unchecking never clears an existing default).
// The applicable scopes + their pre-check / current-default hints are resolved by the parent
// (SessionView's `layoutSaveScopes`) and passed in; here we just render + apply them.
import { ref, reactive, computed, nextTick, useId } from 'vue'
import { useLayoutsStore } from '../../../stores/layouts'
import { useSettingsStore } from '../../../stores/settings'
import { useDataStore } from '../../../stores/data'
import { apiFetch } from '../../../utils/api'

const props = defineProps({
    // The template intention to store (assignment / collapsed / resizeFractions / tabOrder) — the session's
    // current layout at save time, supplied by the parent.
    intention: { type: Object, default: () => ({}) },
    // "Set as default" scope descriptors from the parent, in display order (worktree → project →
    // global). Each: { key, label, targetProjectId (null = global), preChecked, inherited, currentName }.
    scopes: { type: Array, default: () => [] },
})

const emit = defineEmits(['saved'])

const layoutsStore = useLayoutsStore()
const settingsStore = useSettingsStore()
const dataStore = useDataStore()

const dialogRef = ref(null)
const nameInputRef = ref(null)
const saveButtonRef = ref(null)

// Sentinel for the "create a new one" choice in the target select.
const NEW = '__new__'
const target = ref(NEW)   // either NEW or an existing layout id (overwrite)
const newName = ref('')
// Per-scope checkbox state, keyed by scope.key — seeded from each scope's `preChecked` on open().
const checked = reactive({})
const errorMessage = ref('')
const isSaving = ref(false)
// The layout id once created/overwritten — remembered so an error-retry re-applies the scope
// defaults without creating a duplicate "New layout".
const savedId = ref(null)

const instanceId = useId()
const formId = `layout-save-form-${instanceId}`

const existingLayouts = computed(() => layoutsStore.getAllLayouts)
const isNew = computed(() => target.value === NEW)
// True when the session is in a worktree (the "In Worktree" checkbox is present) — drives whether the
// help text mentions worktree defaults.
const hasWorktreeScope = computed(() => props.scopes.some((s) => s.key === 'worktree'))

function syncFormState() {
    nextTick(() => saveButtonRef.value?.setAttribute('form', formId))
}

function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}

function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    if (isNew.value && nameInputRef.value) {
        const input = nameInputRef.value
        input.focus()
        const len = input.value?.length || 0
        input.setSelectionRange?.(len, len)
    }
}

function open() {
    errorMessage.value = ''
    isSaving.value = false
    savedId.value = null
    target.value = NEW
    newName.value = ''
    // Seed each scope's checkbox from its pre-check (a level is checked when it has no explicit
    // non-single-pane default of its own — see SessionView's `layoutSaveScopes`).
    for (const key of Object.keys(checked)) delete checked[key]
    for (const s of props.scopes) checked[s.key] = s.preChecked
    syncFormState()
    if (dialogRef.value) dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) dialogRef.value.open = false
}

async function handleSave() {
    if (isSaving.value) return   // guard against a concurrent re-submit while project PUTs are in flight
    errorMessage.value = ''
    // 1. Create / overwrite the layout once. Guarded by `savedId` so an error-retry (the dialog stays
    //    open when a project default PUT fails) re-applies the defaults without duplicating the layout.
    if (!savedId.value) {
        if (isNew.value) {
            const trimmed = newName.value.trim()
            if (!trimmed) {
                errorMessage.value = localUiMessage('session.layout.errors.emptyName')
                return
            }
            const dup = existingLayouts.value.some((l) => l.name.trim().toLowerCase() === trimmed.toLowerCase())
            if (dup) {
                errorMessage.value = localUiMessage('session.layout.errors.duplicateName', { name: trimmed })
                return
            }
            savedId.value = layoutsStore.upsertLayout({ name: trimmed, intention: props.intention })
        } else {
            const existing = layoutsStore.getLayoutById(target.value)
            if (!existing) {
                errorMessage.value = localUiMessage('session.layout.errors.missing')
                return
            }
            savedId.value = layoutsStore.upsertLayout({ id: existing.id, name: existing.name, intention: props.intention })
        }
    }
    const id = savedId.value
    if (!id) {
        errorMessage.value = localUiMessage('session.layout.errors.saveFailed')
        return
    }

    // 2. Apply the checked scope defaults. Global is a local, auto-synced setting; project-backed
    //    scopes (worktree / project) PUT to the project. All re-applications are idempotent (same
    //    value), so a retry after a partial failure is safe.
    isSaving.value = true
    try {
        for (const scope of props.scopes) {
            if (!checked[scope.key]) continue
            if (scope.targetProjectId == null) {
                settingsStore.setDefaultLayoutId(id)
            } else {
                const res = await apiFetch(`/api/projects/${scope.targetProjectId}/`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ default_layout_id: id }),
                })
                if (!res.ok) throw new Error('project update failed')
                dataStore.updateProject(await res.json())
            }
        }
    } catch {
        errorMessage.value = localUiMessage('session.layout.errors.defaultsFailed')
        isSaving.value = false
        return
    }

    isSaving.value = false
    emit('saved', id)
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="$t('session.layout.save.title')"
        class="layout-save-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">{{ $t('session.layout.save.target') }}</label>
                <wa-select
                    :value.prop="target"
                    @change="target = $event.target.value"
                    size="small"
                >
                    <wa-option :value="NEW">{{ $t('session.layout.save.new') }}</wa-option>
                    <wa-option v-for="l in existingLayouts" :key="l.id" :value="l.id" :label="$t('session.layout.save.overwrite', { name: l.name })">
                        {{ $t('session.layout.save.overwrite', { name: l.name }) }}
                    </wa-option>
                </wa-select>
            </div>

            <div v-if="isNew" class="form-group">
                <label class="form-label">{{ $t('common.fields.name') }}</label>
                <wa-input
                    ref="nameInputRef"
                    :value.prop="newName"
                    @input="newName = $event.target.value"
                    :placeholder="$t('session.layout.namePlaceholder')"
                ></wa-input>
            </div>

            <div v-if="props.scopes.length" class="form-group">
                <label class="form-label">{{ $t('session.layout.save.default') }}</label>
                <wa-checkbox
                    v-for="s in props.scopes"
                    :key="s.key"
                    :checked="checked[s.key]"
                    :disabled="isSaving"
                    @change="checked[s.key] = $event.target.checked"
                >{{ s.label }}
                    <span class="scope-current">{{ s.inherited ? $t('session.layout.save.currentInherited', { name: s.currentName }) : $t('session.layout.save.current', { name: s.currentName }) }}</span>
                </wa-checkbox>
                <p class="dialog-help">
                    {{ hasWorktreeScope ? $t('session.layout.save.worktreeHelp') : $t('session.layout.save.projectHelp') }}
                </p>
            </div>

            <wa-callout v-if="errorMessage" variant="danger" size="small">{{ resolveUiMessage(errorMessage) }}</wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" :disabled="isSaving" @click="close">{{ $t('common.actions.cancel') }}</wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand" :loading="isSaving">{{ $t('common.actions.save') }}</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.layout-save-dialog {
    --width: min(420px, calc(100vw - 2rem));
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
.scope-current {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
.dialog-help {
    margin: var(--wa-space-2xs) 0 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
