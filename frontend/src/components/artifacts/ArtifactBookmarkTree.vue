<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { matchQuery } from '../../utils/textFilter'
import { ARTIFACT_ICON, artifactTypeIcon } from '../../utils/artifactBookmark'
import ProjectBadge from '../project/ProjectBadge.vue'

const props = defineProps({
    bookmarks: { type: Array, default: () => [] },
    selectedBookmarkId: { type: [String, Number], default: null },
    searchQuery: { type: String, default: '' },
    currentProjectId: { type: String, default: null },
    mainProjectId: { type: String, default: null },
})

const emit = defineEmits(['select', 'focus'])
const dataStore = useDataStore()
const rootOpen = ref(true)
const groupOpen = reactive({})

function projectLabel(bookmark) {
    const project = dataStore.getProject(bookmark.project_id)
    const ownName = dataStore.getProjectDisplayName(bookmark.project_id) || ''
    if (!project?.worktree_of) return ownName
    const parentName = dataStore.getProjectDisplayName(project.worktree_of) || ''
    return `${parentName} ${ownName}`.trim()
}

const groups = computed(() => {
    const query = props.searchQuery.trim()
    const matchesBookmark = bookmark => (
        !query ||
        matchQuery(query, bookmark.name) ||
        matchQuery(query, bookmark.relative_path) ||
        matchQuery(query, projectLabel(bookmark))
    )
    const matching = props.bookmarks.filter(matchesBookmark)
    const byProject = new Map()
    for (const bookmark of matching) {
        if (!byProject.has(bookmark.project_id)) byProject.set(bookmark.project_id, [])
        byProject.get(bookmark.project_id).push(bookmark)
    }

    return [...byProject.entries()]
        .map(([projectId, projectBookmarks]) => {
            let order = 3
            if (projectId === props.currentProjectId) {
                order = 0
            } else if (projectId === props.mainProjectId && props.currentProjectId !== props.mainProjectId) {
                order = 1
            } else if (projectBookmarks.some(bookmark => bookmark._visibilitySource === 'workspace')) {
                order = 2
            }
            return {
                id: `project:${projectId}`,
                projectId,
                order,
                bookmarks: projectBookmarks,
            }
        })
        .sort((a, b) => {
            if (a.order !== b.order) return a.order - b.order
            return projectLabel(a.bookmarks[0]).localeCompare(projectLabel(b.bookmarks[0]))
        })
})

const searching = computed(() => !!props.searchQuery.trim())

function toggleRoot() {
    rootOpen.value = !rootOpen.value
}

function toggleGroup(groupId) {
    groupOpen[groupId] = !(groupOpen[groupId] !== false)
}

function isGroupOpen(groupId) {
    return searching.value || groupOpen[groupId] !== false
}

function activateNode(event, path, action) {
    event.currentTarget.focus({ preventScroll: true })
    emit('focus', path)
    action()
}

function selectBookmark(event, bookmark, groupId) {
    const path = `bookmark:${groupId}:${bookmark.id}`
    event.currentTarget.focus({ preventScroll: true })
    emit('focus', path)
    emit('select', bookmark)
}

function isSelected(bookmark) {
    return props.selectedBookmarkId != null && String(bookmark.id) === String(props.selectedBookmarkId)
}

watch(() => props.selectedBookmarkId, (id) => {
    if (id == null) return
    const bookmark = props.bookmarks.find(row => String(row.id) === String(id))
    if (!bookmark) return
    rootOpen.value = true
    groupOpen[`project:${bookmark.project_id}`] = true
})
</script>

<template>
    <div class="bookmark-tree-root file-tree-node">
        <div
            class="node-label is-directory is-toggle"
            data-path="virtual:bookmarks"
            data-type="directory"
            :data-open="rootOpen ? 'true' : 'false'"
            role="treeitem"
            tabindex="-1"
            @click="activateNode($event, 'virtual:bookmarks', toggleRoot)"
        >
            <wa-icon :name="ARTIFACT_ICON" class="node-icon"></wa-icon>
            <span class="node-name">Bookmarked artifacts</span>
        </div>

        <div v-if="rootOpen" class="node-children" role="group">
            <template v-if="groups.length">
                <div v-for="group in groups" :key="group.id" class="file-tree-node">
                    <div
                        class="node-label is-directory is-toggle"
                        :data-path="`virtual:bookmarks:${group.id}`"
                        data-type="directory"
                        :data-open="isGroupOpen(group.id) ? 'true' : 'false'"
                        role="treeitem"
                        tabindex="-1"
                        @click="activateNode($event, `virtual:bookmarks:${group.id}`, () => toggleGroup(group.id))"
                    >
                        <ProjectBadge
                            :project-id="group.projectId"
                            gap="var(--wa-space-3xs)"
                            class="group-badge"
                        />
                    </div>

                    <div v-if="isGroupOpen(group.id)" class="node-children" role="group">
                        <div v-for="bookmark in group.bookmarks" :key="bookmark.id" class="file-tree-node">
                            <div
                                class="node-label bookmark-node is-file is-clickable"
                                :class="{ 'is-selected': isSelected(bookmark) }"
                                :data-path="`bookmark:${group.id}:${bookmark.id}`"
                                data-type="bookmark"
                                role="treeitem"
                                tabindex="-1"
                                :title="`${bookmark.name} — ${bookmark.relative_path}`"
                                @click="selectBookmark($event, bookmark, group.id)"
                            >
                                <wa-icon :name="artifactTypeIcon(bookmark.file_ext)" class="node-icon bookmark-icon"></wa-icon>
                                <span class="node-name bookmark-name">{{ bookmark.name }}</span>
                            </div>
                        </div>
                    </div>
                </div>
            </template>
            <div v-else-if="searching" class="bookmark-empty">No bookmarked artifacts match.</div>
        </div>
    </div>
</template>

<style scoped>
.file-tree-node {
    user-select: none;
    font-size: var(--wa-font-size-m);
    display: flex;
    flex-direction: column;
    align-items: stretch;
    width: 100%;
}

.node-children {
    display: flex;
    flex-direction: column;
    align-items: stretch;
}

.node-label {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-3xs) var(--wa-space-xs);
    line-height: 1.6;
    white-space: nowrap;
    width: 100%;
    min-width: max-content;
    box-sizing: border-box;
    outline: none;
    cursor: pointer;
    color: var(--wa-color-text-normal);
    background: var(--node-bg-color, var(--wa-color-surface-default));
}

.bookmark-tree-root > .node-label {
    padding-left: var(--wa-space-2xs);
}

.bookmark-tree-root > .node-children > .file-tree-node > .node-label {
    padding-left: calc(var(--wa-space-m) + var(--wa-space-2xs));
}

.bookmark-tree-root > .node-children > .file-tree-node > .node-children .node-label {
    padding-left: calc(2 * var(--wa-space-m) + var(--wa-space-2xs));
}

.node-label:hover,
.node-label:focus {
    --node-bg-color: var(--wa-color-surface-raised);
}

.node-label.is-selected {
    --node-bg-color: var(--wa-color-surface-lowered);
}

.node-label.is-selected .bookmark-name {
    text-decoration: underline;
}

.node-icon {
    flex: 0 0 auto;
    width: var(--wa-space-m);
    height: var(--wa-space-m);
}

.bookmark-icon {
    color: var(--wa-color-text-quiet);
}

.node-name {
    overflow: hidden;
    text-overflow: ellipsis;
}

.group-badge {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    overflow: hidden;
}

.group-badge {
    font-weight: 400;
}

.bookmark-name {
    color: var(--wa-color-text-quiet);
}

.bookmark-empty {
    padding: var(--wa-space-xs) var(--wa-space-m);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
