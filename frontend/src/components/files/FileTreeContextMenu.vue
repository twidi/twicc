<script setup>
import { ref, computed, watch, nextTick } from 'vue'

const props = defineProps({
    visible: { type: Boolean, default: false },
    x: { type: Number, default: 0 },
    y: { type: Number, default: 0 },
    nodeName: { type: String, default: '' },
    nodeType: { type: String, default: 'file' },
    relativePath: { type: String, default: '' },
    fullPath: { type: String, default: '' },
    writable: { type: Boolean, default: false },
    writableLoading: { type: Boolean, default: false },
    // Menu of a lazy root (see FileTreePanel `lazyRoot`): only the creation actions
    createOnly: { type: Boolean, default: false },
    // Whether "Upload files…" is offered (the owning panel has an upload origin)
    canUpload: { type: Boolean, default: false },
    // 'files' = Files tab (full file ops), 'git-index' = uncommitted changes, 'git-commit' = committed
    mode: { type: String, default: 'files' },
    // Git status of the node (only relevant in git-index mode)
    stagedStatus: { type: String, default: null },
    unstagedStatus: { type: String, default: null },
    // Git status of the node (only relevant in git-commit mode)
    status: { type: String, default: null },
})

const emit = defineEmits([
    'close',
    'create-file', 'create-folder', 'upload-files', 'rename', 'move', 'delete',
    'copy-name', 'copy-relative-path', 'copy-full-path',
    'git-stage', 'git-unstage', 'git-discard',
    'download', 'download-diff',
])

const dropdownRef = ref(null)
const triggerRef = ref(null)
let openedByUs = false

const isFilesMode = computed(() => props.mode === 'files')
const isGitIndex = computed(() => props.mode === 'git-index')

const canStage = computed(() => {
    if (!isGitIndex.value) return false
    if (props.nodeType === 'directory') return true
    return !!props.unstagedStatus && props.unstagedStatus !== 'deleted'
})

const canUnstage = computed(() => {
    if (!isGitIndex.value) return false
    if (props.nodeType === 'directory') return true
    return !!props.stagedStatus
})

const canDiscard = computed(() => {
    if (!isGitIndex.value) return false
    if (props.nodeType === 'directory') return true
    return !!props.unstagedStatus && props.unstagedStatus !== 'untracked'
})

const canDelete = computed(() => {
    if (!isGitIndex.value) return false
    if (props.nodeType === 'directory') return false
    return props.unstagedStatus === 'untracked'
})

const hasGitActions = computed(() =>
    canStage.value || canUnstage.value || canDiscard.value || canDelete.value
)

// ─── Downloads ──────────────────────────────────────────────────────────────
// Files only: a directory would need an archive, which is out of scope. In git
// modes the file is always downloadable (a deleted one comes from the revision
// that still had it), but a patch only exists for a modified file — an added,
// deleted or untracked file has no meaningful diff to hand over.

const canDownload = computed(() => props.nodeType === 'file')

const canDownloadDiff = computed(() => {
    if (props.nodeType !== 'file') return false
    if (isGitIndex.value) {
        return props.stagedStatus === 'modified' || props.unstagedStatus === 'modified'
    }
    if (props.mode === 'git-commit') return props.status === 'modified'
    return false
})

function handleSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return
    emit(value)
    emit('close')
}

function handleHide() {
    if (openedByUs) {
        openedByUs = false
        emit('close')
    }
}

watch(() => props.visible, async (visible) => {
    if (visible) {
        await nextTick()
        if (triggerRef.value) {
            triggerRef.value.style.left = `${props.x}px`
            triggerRef.value.style.top = `${props.y}px`
        }
        await nextTick()
        if (dropdownRef.value) {
            openedByUs = true
            dropdownRef.value.open = true
        }
    } else {
        openedByUs = false
        if (dropdownRef.value) {
            dropdownRef.value.open = false
        }
    }
})

watch([() => props.x, () => props.y], () => {
    if (triggerRef.value && props.visible) {
        triggerRef.value.style.left = `${props.x}px`
        triggerRef.value.style.top = `${props.y}px`
    }
})
</script>

<template>
    <Teleport to="body">
        <wa-dropdown
            ref="dropdownRef"
            placement="bottom-start"
            :distance="0"
            class="context-menu-dropdown"
            @wa-select="handleSelect"
            @wa-after-hide="handleHide"
        >
            <span
                ref="triggerRef"
                slot="trigger"
                class="context-menu-trigger"
            ></span>

            <wa-dropdown-item disabled class="context-menu-header">
                {{ nodeName }}
            </wa-dropdown-item>
            <wa-divider></wa-divider>

            <!-- ═══ Files mode: file operations ═══ -->
            <template v-if="isFilesMode">
                <wa-dropdown-item
                    v-if="nodeType === 'directory'"
                    value="create-file"
                    :disabled="writableLoading || !writable"
                >
                    <wa-icon slot="icon" name="file-circle-plus"></wa-icon>
                    New file
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="nodeType === 'directory'"
                    value="create-folder"
                    :disabled="writableLoading || !writable"
                >
                    <wa-icon slot="icon" name="folder-plus"></wa-icon>
                    New folder
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="nodeType === 'directory' && canUpload"
                    value="upload-files"
                    :disabled="writableLoading || !writable"
                >
                    <wa-icon slot="icon" name="upload"></wa-icon>
                    Upload files…
                </wa-dropdown-item>
                <wa-divider v-if="nodeType === 'directory' && !createOnly"></wa-divider>

                <wa-dropdown-item
                    v-if="!createOnly"
                    value="rename"
                    :disabled="writableLoading || !writable"
                >
                    <wa-icon slot="icon" name="pencil"></wa-icon>
                    Rename
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="!createOnly"
                    value="move"
                    :disabled="writableLoading || !writable"
                >
                    <wa-icon slot="icon" name="arrow-right-arrow-left"></wa-icon>
                    Move
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="!createOnly"
                    value="delete"
                    class="danger-item"
                    :disabled="writableLoading || !writable"
                >
                    <wa-icon slot="icon" name="trash"></wa-icon>
                    Delete
                </wa-dropdown-item>
                <wa-divider v-if="!createOnly"></wa-divider>
            </template>

            <!-- ═══ Git index mode: git operations ═══ -->
            <template v-if="isGitIndex && hasGitActions">
                <wa-dropdown-item v-if="canStage" value="git-stage">
                    <wa-icon slot="icon" name="circle-plus"></wa-icon>
                    {{ nodeType === 'directory' ? 'Stage all' : 'Stage' }}
                </wa-dropdown-item>
                <wa-dropdown-item v-if="canUnstage" value="git-unstage">
                    <wa-icon slot="icon" name="circle-minus"></wa-icon>
                    {{ nodeType === 'directory' ? 'Unstage all' : 'Unstage' }}
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="canDiscard"
                    value="git-discard"
                    class="danger-item"
                >
                    <wa-icon slot="icon" name="arrow-rotate-left"></wa-icon>
                    {{ nodeType === 'directory' ? 'Discard all changes' : 'Discard changes' }}
                </wa-dropdown-item>
                <wa-dropdown-item
                    v-if="canDelete"
                    value="delete"
                    class="danger-item"
                >
                    <wa-icon slot="icon" name="trash"></wa-icon>
                    Delete
                </wa-dropdown-item>
                <wa-divider></wa-divider>
            </template>

            <!-- ═══ Downloads (files only — directories would need an archive) ═══ -->
            <template v-if="canDownload && !createOnly">
                <wa-dropdown-item value="download">
                    <wa-icon slot="icon" name="download"></wa-icon>
                    {{ isFilesMode ? 'Download' : 'Download file' }}
                </wa-dropdown-item>
                <wa-dropdown-item v-if="canDownloadDiff" value="download-diff">
                    <wa-icon slot="icon" name="code-compare"></wa-icon>
                    Download diff
                </wa-dropdown-item>
                <wa-divider></wa-divider>
            </template>

            <!-- ═══ Copy actions (always available, except on a create-only menu) ═══ -->
            <template v-if="!createOnly">
                <wa-dropdown-item value="copy-name">
                    <wa-icon slot="icon" name="copy"></wa-icon>
                    <div>Copy name</div>
                    <div class="copy-preview">{{ nodeName }}</div>
                </wa-dropdown-item>
                <wa-dropdown-item value="copy-relative-path">
                    <wa-icon slot="icon" name="copy"></wa-icon>
                    <div>Copy relative path</div>
                    <div class="copy-preview">{{ relativePath }}</div>
                </wa-dropdown-item>
                <wa-dropdown-item value="copy-full-path">
                    <wa-icon slot="icon" name="copy"></wa-icon>
                    <div>Copy full path</div>
                    <div class="copy-preview">{{ fullPath }}</div>
                </wa-dropdown-item>
            </template>
        </wa-dropdown>
    </Teleport>
</template>

<style scoped>
.context-menu-trigger {
    position: fixed;
    width: 0;
    height: 0;
    overflow: hidden;
    pointer-events: none;
}

.danger-item::part(base) {
    color: var(--wa-color-danger-fill-loud);
}

.context-menu-header {
    font-size: var(--wa-font-size-s);
}

.copy-preview {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}
</style>
