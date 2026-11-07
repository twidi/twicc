<script setup>
import { ref, useId, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { apiFetch } from '../../utils/api'
import FileTreePanel from './FileTreePanel.vue'

const { t } = useI18n()

const props = defineProps({
    apiPrefix: { type: String, required: true },
    projectId: { type: String, default: null },
    sessionId: { type: String, default: null },
    isDraft: { type: Boolean, default: false },
})

const emit = defineEmits(['moved'])

const dialogRef = ref(null)
const saveButtonRef = ref(null)
const fileTreePanelRef = ref(null)
const formId = `move-form-${useId()}`

const sourcePath = ref('')
const sourceName = ref('')
const nodeType = ref('file')
const rootPath = ref(null)
const tree = ref(null)
const treeLoading = ref(false)
const treeError = ref(null)
const selectedDir = ref(null)
const isMoving = ref(false)
const errorMessage = ref('')

async function open({ path, name, type, treeRootPath }) {
    sourcePath.value = path
    sourceName.value = name
    nodeType.value = type
    rootPath.value = treeRootPath
    selectedDir.value = null
    errorMessage.value = ''
    isMoving.value = false

    syncFormState()
    if (dialogRef.value) {
        dialogRef.value.open = true
    }

    await loadTree(treeRootPath)

    // Pre-select the parent directory of the source
    const parentDir = path.substring(0, path.lastIndexOf('/'))
    if (parentDir && fileTreePanelRef.value) {
        await nextTick()
        fileTreePanelRef.value.scrollToPath(parentDir)
        onDirSelect(parentDir)
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

function syncFormState() {
    nextTick(() => {
        if (saveButtonRef.value) {
            saveButtonRef.value.setAttribute('form', formId)
        }
    })
}

async function loadTree(dirPath) {
    treeLoading.value = true
    treeError.value = null
    tree.value = null
    try {
        const res = await apiFetch(
            `${props.apiPrefix}/directory-tree/?path=${encodeURIComponent(dirPath)}&directories_only=1`
        )
        if (res.ok) {
            tree.value = await res.json()
        } else {
            const data = await res.json().catch(() => ({}))
            treeError.value = data.error || localUiMessage('files.errors.loadTree')
        }
    } catch {
        treeError.value = localUiMessage('files.errors.network')
    } finally {
        treeLoading.value = false
    }
}

async function lazyLoadDir(path) {
    const res = await apiFetch(
        `${props.apiPrefix}/directory-tree/?path=${encodeURIComponent(path)}&directories_only=1`
    )
    if (res.ok) {
        return await res.json()
    }
    return null
}

function onDirSelect(pathFromTree) {
    if (!rootPath.value) return
    if (pathFromTree.startsWith('/')) {
        selectedDir.value = pathFromTree
    } else {
        selectedDir.value = `${rootPath.value}/${pathFromTree}`
    }
}

function displaySelectedDir() {
    if (!selectedDir.value || !rootPath.value) return ''
    if (selectedDir.value === rootPath.value) return rootPath.value
    const prefix = rootPath.value + '/'
    return selectedDir.value.startsWith(prefix)
        ? selectedDir.value.slice(prefix.length)
        : selectedDir.value
}

async function handleMove() {
    if (isMoving.value || !selectedDir.value) return

    isMoving.value = true
    errorMessage.value = ''

    let response
    try {
        response = await apiFetch(`${props.apiPrefix}/file-move/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: sourcePath.value,
                destination_dir: selectedDir.value,
            }),
        })
    } catch {
        errorMessage.value = localUiMessage('files.errors.networkRetry')
        isMoving.value = false
        return
    }

    const data = await response.json().catch(() => ({}))
    if (!response.ok) {
        errorMessage.value = data.error || localUiMessage('files.errors.moveHttp', { status: response.status })
        isMoving.value = false
        return
    }

    emit('moved', { oldPath: sourcePath.value, newPath: data.new_path })
    isMoving.value = false
    close()
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="$t(nodeType === 'directory' ? 'files.move.title.directory' : 'files.move.title.file')"
        class="move-dialog"
        @wa-show="syncFormState"
    >
        <form :id="formId" class="dialog-content" @submit.prevent="handleMove">
            <div class="move-source">
                <i18n-t keypath="files.move.source" scope="global">
                    <template #name><strong>{{ sourceName }}</strong></template>
                </i18n-t>
            </div>

            <div class="tree-wrapper">
                <FileTreePanel
                    ref="fileTreePanelRef"
                    :tree="tree"
                    :loading="treeLoading"
                    :error="resolveUiMessage(treeError, { t })"
                    :root-path="rootPath"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :is-draft="isDraft"
                    :lazy-load-fn="lazyLoadDir"
                    :show-refresh="false"
                    :show-shared-options="false"
                    directories-only
                    :compact-folders="false"
                    @file-select="onDirSelect"
                />
            </div>

            <div v-if="selectedDir" class="move-destination">
                <i18n-t keypath="files.move.destination" scope="global">
                    <template #path><span class="destination-path">{{ displaySelectedDir() }}</span></template>
                </i18n-t>
            </div>

            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ resolveUiMessage(errorMessage, { t }) }}
            </wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close" :disabled="isMoving">
                {{ $t('common.actions.cancel') }}
            </wa-button>
            <wa-button
                ref="saveButtonRef"
                type="submit"
                variant="brand"
                :disabled="isMoving || !selectedDir"
            >
                <wa-spinner v-if="isMoving" slot="start"></wa-spinner>
                {{ $t('files.actions.move') }}
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.move-dialog {
    --width: min(550px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.move-source {
    font-size: var(--wa-font-size-m);
}

.move-source strong {
    word-break: break-all;
}

.tree-wrapper {
    height: 300px;
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    overflow: hidden;
}

.move-destination {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.destination-path {
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-normal);
    word-break: break-all;
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    width: 100%;
}
</style>
