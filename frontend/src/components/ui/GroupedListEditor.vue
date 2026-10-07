<script setup>
import { nextTick, ref } from 'vue'
import ReorderHandle from './ReorderHandle.vue'
import { isSnippetGroup } from '../../utils/snippetGroups'
import { startGroupedReorder } from '../../utils/groupedReorder.js'

const props = defineProps({
    entries: { type: Array, required: true },
    itemName: { type: String, default: 'snippet' },
})
const emit = defineEmits(['add', 'edit', 'duplicate', 'delete', 'move', 'add-group', 'rename-group', 'delete-group'])
const expandedGroups = ref(new Set())
const editingGroupId = ref(null)

function toggleGroup(id) {
    if (expandedGroups.value.has(id)) expandedGroups.value.delete(id)
    else expandedGroups.value.add(id)
}

function expandGroup(id) {
    expandedGroups.value.add(id)
}
const groupName = ref('')
const groupNameInput = ref(null)
const addingGroup = ref(false)
const newGroupName = ref('')
const newGroupInput = ref(null)

function drag(event, handle, onDrop, onActive) {
    return startGroupedReorder(event, handle, () => props.entries, onDrop, onActive, expandGroup)
}

function reorder(from, to, groupId = null) {
    if (typeof from === 'object') {
        emit('move', from, to)
        return
    }
    emit('move', { groupId, index: from }, { groupId, index: to > from ? to + 1 : to })
}

function editGroup(group) {
    editingGroupId.value = group.id
    groupName.value = group.label
    nextTick(() => {
        groupNameInput.value?.focus()
        groupNameInput.value?.select()
    })
}

function saveGroupName(id) {
    const label = groupName.value.trim()
    if (!label) return
    emit('rename-group', id, label)
    editingGroupId.value = null
}

function openAddGroup() {
    addingGroup.value = true
    newGroupName.value = ''
    nextTick(() => newGroupInput.value?.focus())
}

function addGroup() {
    const label = newGroupName.value.trim()
    if (!label) return
    emit('add-group', label)
    addingGroup.value = false
    newGroupName.value = ''
}
</script>

<template>
    <div class="grouped-editor" data-group-editor>
        <div class="entry-list" data-group-list data-group-id="" data-reorder-list>
            <div
                v-for="(entry, index) in entries"
                :key="isSnippetGroup(entry) ? entry.id : `item-${index}`"
                :class="isSnippetGroup(entry) ? 'entry-group' : 'entry-row'"
                data-group-row
                :data-index="index"
                data-reorder-row
            >
                <template v-if="isSnippetGroup(entry)">
                    <div class="group-header" :data-group-header="entry.id" @click="toggleGroup(entry.id)">
                        <ReorderHandle
                            :index="index"
                            :count="entries.length"
                            :disabled="false"
                            :drag="drag"
                            @reorder="reorder"
                            @click.stop
                        />
                        <form
                            v-if="editingGroupId === entry.id"
                            class="group-name-form"
                            @click.stop
                            @submit.prevent="saveGroupName(entry.id)"
                            @keydown.esc.stop.prevent="editingGroupId = null"
                        >
                            <input
                                :ref="element => { groupNameInput = element }"
                                v-model="groupName"
                                class="group-name-input"
                                aria-label="Group name"
                                required
                            />
                            <button type="submit" class="action-btn" title="Save group name" aria-label="Save group name" :disabled="!groupName.trim()">
                                <wa-icon name="check" />
                            </button>
                            <button type="button" class="action-btn" title="Cancel" aria-label="Cancel group rename" @click="editingGroupId = null">
                                <wa-icon name="xmark" />
                            </button>
                        </form>
                        <template v-else>
                            <button
                                type="button"
                                class="group-toggle"
                                :aria-expanded="expandedGroups.has(entry.id)"
                                :aria-label="`${expandedGroups.has(entry.id) ? 'Collapse' : 'Expand'} ${entry.label} (${entry.items.length})`"
                                @click.stop="toggleGroup(entry.id)"
                            >
                                <wa-icon :name="expandedGroups.has(entry.id) ? 'chevron-down' : 'chevron-right'" class="group-chevron" />
                                <wa-icon name="folder" class="group-icon" />
                                <span class="group-label">{{ entry.label }}</span>
                                <span class="group-count">({{ entry.items.length }})</span>
                            </button>
                            <div class="item-actions">
                                <button
                                    type="button"
                                    class="action-btn"
                                    :title="`Add ${itemName}`"
                                    :aria-label="`Add ${itemName} to ${entry.label}`"
                                    @click.stop="emit('add', entry.id)"
                                ><wa-icon name="plus" /></button>
                                <button type="button" class="action-btn" title="Rename group" aria-label="Rename group" @click.stop="editGroup(entry)">
                                    <wa-icon name="pen-to-square" />
                                </button>
                                <button
                                    type="button"
                                    class="action-btn action-btn-danger"
                                    title="Delete group and keep its items outside groups"
                                    aria-label="Delete group and keep its items outside groups"
                                    @click.stop="emit('delete-group', entry.id)"
                                ><wa-icon name="trash-can" /></button>
                            </div>
                        </template>
                    </div>
                    <div v-show="expandedGroups.has(entry.id)" class="entry-list child-list" data-group-list :data-group-id="entry.id" data-reorder-list>
                        <div
                            v-for="(item, itemIndex) in entry.items"
                            :key="itemIndex"
                            class="entry-row"
                            data-group-row
                            :data-index="itemIndex"
                            data-reorder-row
                        >
                            <ReorderHandle
                                :index="itemIndex"
                                :count="entry.items.length"
                                :disabled="false"
                                :drag="drag"
                                @reorder="(from, to) => reorder(from, to, entry.id)"
                            />
                            <div class="item-content"><slot name="item" :entry="item" :index="itemIndex" :groupId="entry.id" /></div>
                            <div class="item-actions">
                                <button type="button" class="action-btn" :title="`Edit ${itemName}`" :aria-label="`Edit ${itemName}`" @click="emit('edit', itemIndex, entry.id)"><wa-icon name="pen-to-square" /></button>
                                <button type="button" class="action-btn" :title="`Duplicate ${itemName}`" :aria-label="`Duplicate ${itemName}`" @click="emit('duplicate', itemIndex, entry.id)"><wa-icon name="copy" /></button>
                                <button type="button" class="action-btn action-btn-danger" :title="`Delete ${itemName}`" :aria-label="`Delete ${itemName}`" @click="emit('delete', itemIndex, entry.id)"><wa-icon name="trash-can" /></button>
                            </div>
                        </div>
                        <span v-if="!entry.items.length" class="empty-group">Drop {{ itemName }} here</span>
                    </div>
                </template>
                <template v-else>
                    <ReorderHandle
                        :index="index"
                        :count="entries.length"
                        :disabled="false"
                        :drag="drag"
                        @reorder="reorder"
                    />
                    <div class="item-content"><slot name="item" :entry="entry" :index="index" :groupId="null" /></div>
                    <div class="item-actions">
                        <button type="button" class="action-btn" :title="`Edit ${itemName}`" :aria-label="`Edit ${itemName}`" @click="emit('edit', index, null)"><wa-icon name="pen-to-square" /></button>
                        <button type="button" class="action-btn" :title="`Duplicate ${itemName}`" :aria-label="`Duplicate ${itemName}`" @click="emit('duplicate', index, null)"><wa-icon name="copy" /></button>
                        <button type="button" class="action-btn action-btn-danger" :title="`Delete ${itemName}`" :aria-label="`Delete ${itemName}`" @click="emit('delete', index, null)"><wa-icon name="trash-can" /></button>
                    </div>
                </template>
            </div>
        </div>
        <div class="editor-actions" data-group-outside>
            <button type="button" class="add-item-btn" :aria-expanded="addingGroup" @click="openAddGroup">
                <wa-icon name="folder-plus" /> Add group
            </button>
            <button type="button" class="add-item-btn" @click="emit('add', null)">
                <wa-icon name="plus" /> Add {{ itemName }}
            </button>
        </div>
        <form v-if="addingGroup" class="group-name-form new-group-form" @submit.prevent="addGroup" @keydown.esc.stop.prevent="addingGroup = false">
            <input ref="newGroupInput" v-model="newGroupName" class="group-name-input" placeholder="Group name" aria-label="New group name" required />
            <button type="submit" class="action-btn" title="Add group" aria-label="Add group" :disabled="!newGroupName.trim()"><wa-icon name="check" /></button>
            <button type="button" class="action-btn" title="Cancel" aria-label="Cancel new group" @click="addingGroup = false"><wa-icon name="xmark" /></button>
        </form>
    </div>
</template>

<style scoped>
.grouped-editor { container-type: inline-size; }

.grouped-editor, .entry-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.entry-list {
    position: relative;
}

.entry-row, .group-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
}

.entry-row { border: 1px solid transparent; }

.entry-group {
    min-width: 0;
    border: 1px solid var(--wa-color-border-base, transparent);
    border-radius: var(--wa-border-radius-m);
    overflow: hidden;
}

.group-header {
    min-height: 36px;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.group-toggle {
    display: flex;
    align-items: center;
    flex: 1;
    gap: var(--wa-space-xs);
    min-width: 0;
    align-self: stretch;
    height: auto;
    padding: var(--wa-space-2xs) 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    text-align: left;
    justify-content: flex-start;
    cursor: pointer;
}

.group-chevron, .group-icon, .group-count { flex: none; }
.grouped-editor wa-icon { margin: 0; }
.group-count { color: var(--wa-color-text-quiet); font-weight: normal; }

.group-label, .item-content {
    flex: 1;
    min-width: 0;
    overflow: hidden;
}

.group-label {
    flex: 0 1 auto;
    white-space: nowrap;
    text-overflow: ellipsis;
}

.group-icon {
    color: var(--wa-color-text-quiet);
}

.child-list {
    min-height: 44px;
    margin: var(--wa-space-2xs) 0 var(--wa-space-xs) var(--wa-space-m);
}

.child-list > .entry-row { border-right: 0; }

.item-actions {
    display: flex;
    flex: none;
    gap: var(--wa-space-3xs);
}

.action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    border-radius: var(--wa-border-radius-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
    padding: var(--wa-space-xs);
    line-height: 1;
    cursor: pointer;
}

.action-btn:hover:not(:disabled) {
    background: var(--wa-color-surface-raised);
    color: var(--wa-color-text-base);
}

.action-btn-danger:hover {
    color: var(--wa-color-danger-60);
}

.action-btn:disabled {
    opacity: 0.4;
    cursor: default;
}

.add-item-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-xs);
    min-height: 32px;
    background: none;
    border: 1px dashed var(--wa-color-border-base);
    border-radius: var(--wa-border-radius-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    cursor: pointer;
}

.add-item-btn:hover {
    border-color: var(--wa-color-brand-border-normal);
    color: var(--wa-color-brand-text);
}

.editor-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
    min-height: 44px;
}

.empty-group {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.empty-group {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    padding: var(--wa-space-xs);
}

.group-name-form {
    display: flex;
    align-items: center;
    flex: 1;
    gap: var(--wa-space-3xs);
    min-width: 0;
}

.new-group-form {
    padding: var(--wa-space-2xs) 0;
}

.group-name-input {
    flex: 1;
    min-width: 0;
    border: 1px solid var(--wa-color-border-base);
    border-radius: var(--wa-border-radius-s);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    background: var(--wa-form-control-background-color);
    color: var(--wa-color-text-normal);
    font: inherit;
}

.group-toggle:focus-visible, .action-btn:focus-visible, .add-item-btn:focus-visible, .group-name-input:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

@container (width < 24rem) {
    .group-header { gap: var(--wa-space-3xs); }
    .group-toggle { gap: var(--wa-space-2xs); }
    .group-toggle .group-icon { display: none; }
}

@media (pointer: coarse) {
    .action-btn, .add-item-btn {
        min-height: 44px;
        min-width: 44px;
    }
}
</style>
