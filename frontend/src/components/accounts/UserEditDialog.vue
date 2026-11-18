<script setup>
// UserEditDialog.vue — create or edit one account (admin panel).
// Form pattern: components/project/ProjectEditDialog.vue (form id + submit button
// outside the form, focus on wa-after-show, show/after-show guarded against
// events bubbling from the nested wa-select).
import { computed, nextTick, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { createUser, deleteUser, revokeUserSessions, updateUser } from '../../utils/accountsApi'

const props = defineProps({
    // null = create mode
    user: { type: Object, default: null },
    // The signed-in account id: it cannot demote, disable or delete itself.
    selfId: { type: Number, default: null },
})

const emit = defineEmits(['saved', 'deleted'])

const { t } = useI18n()

const MIN_PASSWORD_LENGTH = 8

const dialogRef = ref(null)
const emailInputRef = ref(null)
const saveButtonRef = ref(null)

const instanceId = useId()
const formId = `user-edit-form-${instanceId}`

const localEmail = ref('')
const localName = ref('')
const localRole = ref('member')
const localPassword = ref('')
const localActive = ref(true)
const isSaving = ref(false)
const errorMessage = ref('')
const notice = ref('')
const confirmDelete = ref(false)

const isCreateMode = computed(() => !props.user)
const isSelf = computed(() => !!props.user && props.user.id === props.selfId)

const roleOptions = computed(() => [
    { value: 'member', label: t('accounts.roles.member') },
    { value: 'admin', label: t('accounts.roles.admin') },
])

function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) saveButtonRef.value.setAttribute('form', formId)
    })
}

function focusFirstInput() {
    const input = emailInputRef.value
    if (!input) return
    input.focus()
    const len = input.value?.length || 0
    input.setSelectionRange?.(len, len)
}

function handleDialogShow(e) {
    if (e.target !== dialogRef.value) return
    syncFormState()
}

function handleDialogAfterShow(e) {
    if (e.target !== dialogRef.value) return
    focusFirstInput()
}

function open() {
    errorMessage.value = ''
    notice.value = ''
    confirmDelete.value = false
    localEmail.value = props.user?.email || ''
    localName.value = props.user?.display_name || ''
    localRole.value = props.user?.role || 'member'
    localActive.value = props.user ? props.user.is_active : true
    localPassword.value = ''
    syncFormState()
    if (dialogRef.value) dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) dialogRef.value.open = false
}

function validate() {
    const email = localEmail.value.trim()
    if (!email || !email.includes('@')) return localUiMessage('accounts.errors.invalidEmail')
    if (localPassword.value && localPassword.value.length < MIN_PASSWORD_LENGTH) {
        return localUiMessage('accounts.errors.passwordTooShort', { min: MIN_PASSWORD_LENGTH })
    }
    return ''
}

async function handleSave() {
    errorMessage.value = validate()
    if (errorMessage.value) return
    isSaving.value = true
    const payload = {
        email: localEmail.value.trim(),
        display_name: localName.value.trim(),
        role: localRole.value,
    }
    if (localPassword.value) payload.password = localPassword.value
    let result
    if (isCreateMode.value) {
        result = await createUser(payload)
    } else {
        if (!isSelf.value) payload.is_active = localActive.value
        result = await updateUser(props.user.id, payload)
    }
    isSaving.value = false
    if (!result.ok) {
        errorMessage.value = result.error
        return
    }
    emit('saved', result.data.user)
    close()
}

async function runAction(action, successKey) {
    errorMessage.value = ''
    notice.value = ''
    isSaving.value = true
    const result = await action()
    isSaving.value = false
    if (!result.ok) {
        errorMessage.value = result.error
        return false
    }
    notice.value = localUiMessage(successKey)
    if (result.data?.user) emit('saved', result.data.user)
    return true
}

function unlinkGoogle() {
    return runAction(() => updateUser(props.user.id, { unlink_google: true }), 'accounts.edit.googleUnlinked')
}

function revokeSessions() {
    return runAction(() => revokeUserSessions(props.user.id), 'accounts.edit.sessionsRevoked')
}

async function removePassword() {
    await runAction(() => updateUser(props.user.id, { clear_password: true }), 'accounts.edit.passwordRemoved')
}

async function handleDelete() {
    if (!confirmDelete.value) {
        confirmDelete.value = true
        return
    }
    isSaving.value = true
    const result = await deleteUser(props.user.id)
    isSaving.value = false
    if (!result.ok) {
        errorMessage.value = result.error
        confirmDelete.value = false
        return
    }
    emit('deleted', props.user.id)
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="isCreateMode ? $t('accounts.edit.createTitle') : $t('accounts.edit.editTitle')"
        class="user-edit-dialog"
        @wa-show="handleDialogShow"
        @wa-after-show="handleDialogAfterShow"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">{{ $t('accounts.fields.email') }}</label>
                <wa-input
                    ref="emailInputRef"
                    type="email"
                    size="small"
                    autocomplete="off"
                    :value="localEmail"
                    @input="localEmail = $event.target.value"
                    :disabled="isSaving"
                ></wa-input>
                <p class="form-help-text">{{ $t('accounts.edit.emailHint') }}</p>
            </div>

            <div class="form-group">
                <label class="form-label">{{ $t('accounts.fields.displayName') }}</label>
                <wa-input
                    size="small"
                    maxlength="100"
                    :value="localName"
                    @input="localName = $event.target.value"
                    :disabled="isSaving"
                ></wa-input>
            </div>

            <div class="form-group">
                <label class="form-label">{{ $t('accounts.fields.role') }}</label>
                <wa-select
                    size="small"
                    :value.prop="localRole"
                    :disabled="isSaving || isSelf"
                    @change="localRole = $event.target.value"
                >
                    <wa-option v-for="option in roleOptions" :key="option.value" :value="option.value">{{ option.label }}</wa-option>
                </wa-select>
                <p class="form-help-text">{{ localRole === 'admin' ? $t('accounts.roles.adminHint') : $t('accounts.roles.memberHint') }}</p>
            </div>

            <div class="form-group">
                <label class="form-label">{{ isCreateMode ? $t('accounts.fields.password') : $t('accounts.fields.newPassword') }}</label>
                <wa-input
                    type="password"
                    size="small"
                    autocomplete="new-password"
                    password-toggle
                    :value="localPassword"
                    @input="localPassword = $event.target.value"
                    :disabled="isSaving"
                ></wa-input>
                <p class="form-help-text">
                    {{ isCreateMode ? $t('accounts.edit.passwordHintCreate', { min: MIN_PASSWORD_LENGTH }) : $t('accounts.edit.passwordHintEdit', { min: MIN_PASSWORD_LENGTH }) }}
                </p>
            </div>

            <template v-if="!isCreateMode">
                <wa-switch
                    size="small"
                    :checked="localActive"
                    :disabled="isSaving || isSelf"
                    @change="localActive = $event.target.checked"
                >
                    {{ $t('accounts.fields.active') }}
                </wa-switch>

                <wa-divider></wa-divider>

                <div class="account-actions">
                    <wa-button v-if="user.google_linked" size="small" appearance="outlined" :disabled="isSaving" @click="unlinkGoogle">
                        <wa-icon slot="start" family="brands" name="google"></wa-icon>
                        {{ $t('accounts.edit.unlinkGoogle') }}
                    </wa-button>
                    <wa-button v-if="user.has_password" size="small" appearance="outlined" :disabled="isSaving" @click="removePassword">
                        <wa-icon slot="start" name="key"></wa-icon>
                        {{ $t('accounts.edit.removePassword') }}
                    </wa-button>
                    <wa-button size="small" appearance="outlined" :disabled="isSaving" @click="revokeSessions">
                        <wa-icon slot="start" name="right-from-bracket"></wa-icon>
                        {{ $t('accounts.edit.revokeSessions') }}
                    </wa-button>
                    <wa-button
                        v-if="!isSelf"
                        size="small"
                        variant="danger"
                        :appearance="confirmDelete ? 'accent' : 'outlined'"
                        :disabled="isSaving"
                        @click="handleDelete"
                    >
                        <wa-icon slot="start" name="trash"></wa-icon>
                        {{ confirmDelete ? $t('accounts.edit.confirmDelete') : $t('common.actions.delete') }}
                    </wa-button>
                </div>
            </template>

            <wa-callout v-if="notice" variant="success" size="small">
                <wa-icon slot="icon" name="circle-check"></wa-icon>
                {{ resolveUiMessage(notice) }}
            </wa-callout>
            <wa-callout v-if="errorMessage" variant="danger" size="small">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                {{ resolveUiMessage(errorMessage) }}
            </wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" :disabled="isSaving" @click="close">
                {{ $t('common.actions.cancel') }}
            </wa-button>
            <wa-button ref="saveButtonRef" type="submit" variant="brand" :disabled="isSaving">
                {{ isCreateMode ? $t('common.actions.create') : $t('common.actions.save') }}
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.user-edit-dialog {
    --width: min(520px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.form-help-text {
    margin: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.account-actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}

.dialog-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}
</style>
