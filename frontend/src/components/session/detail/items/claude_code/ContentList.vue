<script setup>
import { computed } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import { useCodeCommentsStore } from '../../../../../stores/codeComments'
import { DISPLAY_MODE } from '../../../../../constants'
import { sdkBlockToMediaItem } from '../../../../../utils/fileUtils'
import { getInternalCollapsibleGroups, getPrefixSuffixBoundaries, isVisibleItem } from '../../../../../utils/contentVisibility'
import GroupToggle from '../../GroupToggle.vue'
import TextContent from '../TextContent.vue'
import MediaThumbnailGroup from '../../../../media/MediaThumbnailGroup.vue'
import DocumentContent from './DocumentContent.vue'
import ToolUseContent from '../ToolUseContent.vue'
import ThinkingContent from './ThinkingContent.vue'
import UnknownEntry from '../UnknownEntry.vue'

const store = useDataStore()
const codeCommentsStore = useCodeCommentsStore()

// Check if we're in simplified mode (only mode with collapsible groups).
// Uses the session's effective mode so a per-session debug override wins.
const isSimplifiedMode = computed(() => store.getEffectiveDisplayMode(props.sessionId) === DISPLAY_MODE.SIMPLIFIED)

const props = defineProps({
    items: {
        type: Array,
        required: true
    },
    role: {
        type: String,
        required: true,
        validator: (value) => ['user', 'assistant', 'items'].includes(value)
    },
    // Context for store lookups
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    lineNum: {
        type: Number,
        required: true
    },
    // The whole SessionItem is already behind a session-level GroupToggle.
    // Once that group is expanded, render its content without another nested
    // toggle. Internal groups remain available for ALWAYS messages.
    externallyGrouped: {
        type: Boolean,
        default: false
    },
    timestamp: {
        type: String,
        default: null
    },
    // Props for external groups (connected prefix/suffix)
    groupHead: {
        type: Number,
        default: null
    },
    groupTail: {
        type: Number,
        default: null
    },
    prefixExpanded: {
        type: Boolean,
        default: false
    },
    suffixExpanded: {
        type: Boolean,
        default: false
    },
    // Indices of entries rendered elsewhere: the native media blocks an
    // attachment strip already shows (no separate image group, no document
    // placeholder), and blank text of an attachments-only message.
    hiddenIndices: {
        type: Array,
        default: () => []
    }
})

const emit = defineEmits(['toggle-suffix'])

/**
 * Build the ``extra`` prop forwarded to ``ToolUseContent`` for tool_use
 * blocks. Surfaces the backend-spliced task payload so the Claude Code
 * tool helpers can render the Task* summary / detail without an extra
 * round-trip:
 *   - ``twiccTaskData`` / ``twiccTasksTotal`` — single task lookup for
 *     ``TaskCreate`` / ``TaskUpdate`` / ``TaskGet``;
 *   - ``twiccTasksData`` — full task list snapshot, attached to
 *     ``TaskList`` as well as to the by-id tools (frozen at the moment
 *     of the call so historical statuses are preserved across recompute).
 * Returns ``null`` when nothing is attached, keeping the prop shape clean
 * for tools that don't need it.
 */
function getToolExtra(item) {
    const taskData = item?.twiccTaskData
    const tasksTotal = item?.twiccTasksTotal
    const tasksData = item?.twiccTasksData
    if (taskData == null && tasksTotal == null && tasksData == null) return null
    return {
        twiccTaskData: taskData ?? null,
        twiccTasksTotal: tasksTotal ?? null,
        twiccTasksData: tasksData ?? null,
    }
}

// Get expanded internal groups from store
const expandedInternalGroups = computed(() => {
    const groups = store.getInternalExpandedGroups(props.sessionId, props.lineNum)
    return new Set(groups)
})

// Compute connected prefix/suffix boundaries
const prefixSuffixBoundaries = computed(() => {
    return getPrefixSuffixBoundaries(props.items, props.groupHead, props.groupTail)
})

// Compute internal collapsible groups (reuses boundaries from above)
const internalGroups = computed(() => {
    return getInternalCollapsibleGroups(props.items, prefixSuffixBoundaries.value)
})

// Compute visibility for each item
const visibleItems = computed(() => {
    const result = []

    // In non-simplified modes, show everything without toggles
    if (!isSimplifiedMode.value || props.role === 'items' || props.externallyGrouped) {
        for (let itemIndex = 0; itemIndex < props.items.length; itemIndex++) {
            result.push({ index: itemIndex, item: props.items[itemIndex], show: true })
        }
        return result
    }

    // Simplified mode: apply group visibility logic
    const { prefixEndIndex, suffixStartIndex } = prefixSuffixBoundaries.value
    const groups = internalGroups.value

    // Track current internal group as we iterate
    let groupIndex = 0
    let currentGroup = groups[groupIndex] || null

    for (let itemIndex = 0; itemIndex < props.items.length; itemIndex++) {
        const item = props.items[itemIndex]

        // Visible content types are always shown, no toggle logic needed
        if (isVisibleItem(item)) {
            result.push({ index: itemIndex, item, show: true })
            continue
        }

        // Non-visible item: determine visibility and toggle
        let show = false
        let showToggleBefore = false
        let toggleType = null
        let groupStartIndex = null
        let groupSize = 0

        // Check if part of connected prefix (external group ending here)
        if (prefixEndIndex != null && itemIndex <= prefixEndIndex) {
            show = props.prefixExpanded
            // No toggle for prefix - it's managed at session level by the group_head item
        }
        // Check if part of connected suffix (external group starting here)
        else if (suffixStartIndex != null && itemIndex >= suffixStartIndex) {
            show = props.suffixExpanded
            // Show toggle BEFORE first suffix element (collapsed or expanded)
            if (itemIndex === suffixStartIndex) {
                showToggleBefore = true
                toggleType = 'suffix'
                groupSize = props.items.length - suffixStartIndex
            }
        }
        // Otherwise check internal groups
        else if (currentGroup) {
            // Advance to next group if we've passed the current one
            while (currentGroup && itemIndex > currentGroup.endIndex) {
                groupIndex++
                currentGroup = groups[groupIndex] || null
            }

            // Check if we're in the current group
            if (currentGroup && itemIndex >= currentGroup.startIndex && itemIndex <= currentGroup.endIndex) {
                const isExpanded = expandedInternalGroups.value.has(currentGroup.startIndex)
                show = isExpanded
                groupStartIndex = currentGroup.startIndex
                // Show toggle BEFORE the first element of the group (collapsed or expanded)
                if (itemIndex === currentGroup.startIndex) {
                    showToggleBefore = true
                    toggleType = 'internal'
                    groupSize = currentGroup.endIndex - currentGroup.startIndex + 1
                }
            }
        }

        // Determine if toggle should show expanded state
        let toggleExpanded = false
        if (toggleType === 'suffix') {
            toggleExpanded = props.suffixExpanded
        } else if (toggleType === 'internal' && groupStartIndex != null) {
            toggleExpanded = expandedInternalGroups.value.has(groupStartIndex)
        }

        result.push({
            index: itemIndex,
            item,
            show,
            showToggleBefore,
            groupStartIndex,
            groupSize,
            toggleType,
            toggleExpanded
        })
    }

    return result
})

// =============================================================================
// Media grouping: collect all image items into a single thumbnail group
// =============================================================================

// Collect all image items from the content array (for grouping into thumbnails)
const hiddenIndexSet = computed(() => new Set(props.hiddenIndices))

const allImageItems = computed(() => {
    return props.items
        .map((item, index) => ({ item, index }))
        .filter(({ item, index }) => item.type === 'image' && !hiddenIndexSet.value.has(index))
})

// Convert image items to normalized MediaItem format for MediaThumbnailGroup
const mediaGroupData = computed(() => {
    return allImageItems.value
        .map(({ item }) => sdkBlockToMediaItem(item))
        .filter(mediaItem => mediaItem !== null)
})

// Index of the first image in the items array (render thumbnail group at this position)
const firstImageIndex = computed(() => {
    return allImageItems.value.length > 0 ? allImageItems.value[0].index : -1
})

function toggleInternalGroup(startIndex) {
    store.toggleInternalExpandedGroup(props.sessionId, props.lineNum, startIndex)
}

/** Whether the parent message's line number range contains any tool comments. */
const parentRangeCommentsCount = computed(() => {
    const rootSessionId = props.parentSessionId || props.sessionId
    if (codeCommentsStore.countBySession(props.projectId, rootSessionId) === 0) return 0
    if (codeCommentsStore.countBySource(props.projectId, rootSessionId, 'tool') === 0) return 0
    const subagentId = props.parentSessionId ? props.sessionId : ''
    const comments = codeCommentsStore.getCommentsBySession(props.projectId, rootSessionId)
        .filter(c => c.source === 'tool' && c.toolLineNum != null && c.subagentSessionId === subagentId)
    const head = props.groupHead ?? props.lineNum
    const tail = props.groupTail ?? props.lineNum
    return comments.filter(c => c.toolLineNum >= head && c.toolLineNum <= tail).length
})
</script>

<template>
    <template v-for="entry in visibleItems" :key="entry.index">
        <!-- Toggle BEFORE the first element of a group -->
        <GroupToggle
            v-if="entry.showToggleBefore && entry.toggleType === 'internal'"
            :expanded="entry.toggleExpanded"
            :item-count="entry.groupSize"
            :comments-count="parentRangeCommentsCount"
            @toggle="toggleInternalGroup(entry.groupStartIndex)"
        />
        <!-- Toggle for suffix (emits to parent for session-level handling) -->
        <GroupToggle
            v-else-if="entry.showToggleBefore && entry.toggleType === 'suffix'"
            :expanded="entry.toggleExpanded"
            :item-count="entry.groupSize"
            :comments-count="parentRangeCommentsCount"
            @toggle="emit('toggle-suffix')"
        />

        <!-- Content element -->
        <template v-if="entry.show && !hiddenIndexSet.has(entry.index)">
            <TextContent
                v-if="entry.item.type === 'text'"
                :text="entry.item.text"
                :role="role"
            />
            <!-- First image: render all images as a grouped thumbnail strip -->
            <MediaThumbnailGroup
                v-else-if="entry.item.type === 'image' && entry.index === firstImageIndex"
                :items="mediaGroupData"
            />
            <!-- Subsequent images: already rendered in the group above, skip -->
            <template v-else-if="entry.item.type === 'image'">
            </template>
            <DocumentContent
                v-else-if="entry.item.type === 'document'"
                :source="entry.item.source"
                :title="entry.item.title"
                :role="role"
            />
            <ToolUseContent
                v-else-if="entry.item.type === 'tool_use'"
                :name="entry.item.name"
                :input="entry.item.input"
                :tool-id="entry.item.id"
                :project-id="projectId"
                :session-id="sessionId"
                :parent-session-id="parentSessionId"
                :line-num="lineNum"
                :timestamp="timestamp"
                :extra="getToolExtra(entry.item)"
            />
            <ThinkingContent
                v-else-if="entry.item.type === 'thinking'"
                :thinking="entry.item.thinking"
                :session-id="sessionId"
                :detail-key="`line:${lineNum}:${entry.index}`"
                :streaming="entry.item.streaming || false"
            />
            <UnknownEntry
                v-else
                :type="entry.item.type"
                :data="entry.item"
                :session-id="sessionId"
                :detail-key="`line:${lineNum}:${entry.index}`"
            />
        </template>
    </template>
</template>
