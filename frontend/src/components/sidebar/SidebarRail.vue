<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useRailActiveSessions } from '../../composables/useRailActiveSessions'
import { useSessionTreeAncestors } from '../../composables/useSessionTreeAncestors'
import { useSessionTreeExpansionStore } from '../../stores/sessionTreeExpansion'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useRailRecentProjects } from '../../composables/useRailRecentProjects.js'
import { createRailScopeNavigation } from '../../utils/railScopeNavigation.js'
import ProjectBadge from '../project/ProjectBadge.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'
import ProjectMark from '../project/ProjectMark.vue'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import ProcessActivityIndicator from '../ui/ProcessActivityIndicator.vue'
import SessionListItem from '../session/list/SessionListItem.vue'
import { useSettingsStore } from '../../stores/settings'
import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { useTimelineSpeechStore } from '../../stores/timelineSpeech'
import { ALL_PROJECTS_ID, MOBILE_BREAKPOINT } from '../../constants.js'
import { extractWorkspaceId, isWorkspaceProjectId } from '../../utils/workspaceIds.js'
import { resolveRailItems } from '../../utils/sidebarRail.js'
import AppTooltip, { onTooltipDismissal } from '../ui/AppTooltip.vue'
import { createRailSessionLongPress } from '../../utils/railSessionLongPress.js'
import SettingsPopover from '../app/SettingsPopover.vue'
import PeerInboxBadge from '../peer/PeerInboxBadge.vue'
import NewSessionProjectPicker from '../project/NewSessionProjectPicker.vue'
import AndroidAppMenuAnchor from './AndroidAppMenuAnchor.vue'

const props = defineProps({
    mode: { type: String, required: true, validator: value => ['home', 'sessions', 'artifacts'].includes(value) },
    sidebarOpen: { type: Boolean, default: false },
    peerConfigured: { type: Boolean, required: true },
    inboxCount: { type: Number, required: true },
    settingsAnchor: { type: Object, default: null },
})
const emit = defineEmits(['new-session', 'home', 'select-mode', 'search', 'palette', 'inbox', 'toggle-sidebar'])
const settingsStore = useSettingsStore()
const tts = useTextToSpeechStore()
const speech = useTimelineSpeechStore()
const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const { recentProjects, recentWorkspaces } = useRailRecentProjects(store, workspacesStore, settingsStore)
const route = useRoute()
const router = useRouter()
const currentSessionId = computed(() => props.mode === 'sessions' ? route.params.sessionId || null : null)
const treeExpansion = useSessionTreeExpansionStore()
const RAIL_PREVIEW_MAX_DESCENDANTS = 5
const isTreeMode = computed(() => settingsStore.isShowSessionsAsTree)
// Tree mode (spec §14): the user's explicit collapse is the only collapsed state; the rail never writes it.
const { rows, treeCandidates } = useRailActiveSessions(store, () => currentSessionId.value, {
    treeEnabled: () => isTreeMode.value,
    isCollapsed: id => treeExpansion.state.explicit[id] === false,
})
useSessionTreeAncestors(treeCandidates, isTreeMode)

// Tree mode: consecutive rows of the same tree (groupId) get a linking line. Singles get none.
const railRows = computed(() => rows.value.map((row, index, all) => {
    const group = row.groupId
    const linkedBefore = !!group && all[index - 1]?.groupId === group
    const linkedAfter = !!group && all[index + 1]?.groupId === group
    return {
        ...row,
        groupClass: linkedBefore || linkedAfter ? [
            'rail-session--grouped',
            { 'rail-session--dot': row.showMarkDot, 'rail-session--next-dot': linkedAfter && all[index + 1].showMarkDot },
            linkedBefore ? (linkedAfter ? 'rail-session--group-middle' : 'rail-session--group-last') : 'rail-session--group-first',
        ] : null,
    }
}))

const hasCentralContent = computed(() => rows.value.length || recentProjects.value.length || recentWorkspaces.value.length)
const navigateScope = createRailScopeNavigation(router)
// The Timeline button must not cover the page it opens with the mobile drawer.
const navigateTimelineScope = createRailScopeNavigation(router, () => {
    if (!window.matchMedia(`(width < ${MOBILE_BREAKPOINT}px)`).matches) window.dispatchEvent(new Event('twicc:open-sidebar'))
})
function scopeSelected(kind, id) {
    // The bare route is the Timeline; Stats is the same scope page on another tab, so the scope stays selected.
    return kind === 'project'
        ? (route.name === 'project' || route.name === 'project-stats') && route.params.projectId === id && !route.query.workspace
        : (route.name === 'projects-all' || route.name === 'projects-stats') && route.query.workspace === id
}
function scopeClick(kind, id, event) {
    if (longPress.consumeClick(`${kind}:${id}`, event)) return
    longPress.cancel()
    navigateScope(kind, id)
}
const sessionTooltips = new Map()
const touchInput = ref(false)
const sessionTooltipTrigger = computed(() => settingsStore.isTouchDevice || touchInput.value ? 'manual' : 'hover focus')
const longPress = createRailSessionLongPress({
    document,
    show: id => sessionTooltips.get(id)?.show(),
    isOpen: id => sessionTooltips.get(id)?.isOpen() ?? false,
    hide: id => {
        if (id !== undefined) sessionTooltips.get(id)?.hide()
        else for (const tooltip of sessionTooltips.values()) tooltip.hide()
    },
})
const stopDismissalWatch = onTooltipDismissal(longPress.cancel)

function setSessionTooltip(id, tooltip) {
    setEntryTooltip(`session:${id}`, tooltip)
}

function setEntryTooltip(id, tooltip) {
    if (tooltip) sessionTooltips.set(id, tooltip)
    else {
        sessionTooltips.delete(id)
        longPress.cancel()
    }
}

function handleEntryTooltipHide(id, event) {
    if (event.target === event.currentTarget) longPress.clearAutoHide(id)
}

function sessionPointerDown(session, event) {
    entryPointerDown(`session:${session.id}`, event)
}

function entryPointerDown(id, event) {
    touchInput.value = event.pointerType === 'touch'
    // Vue renders after pointerdown. Set the native trigger before default focus.
    for (const tooltip of sessionTooltips.values()) tooltip.setTrigger(sessionTooltipTrigger.value)
    longPress.start(id, event)
}

function sessionPointerEnter(event) {
    if (event.pointerType !== 'mouse' || settingsStore.isTouchDevice) return
    longPress.clearAutoHide()
    touchInput.value = false
    for (const tooltip of sessionTooltips.values()) tooltip.setTrigger('hover focus')
}

function sessionKeyboardInput() {
    if (settingsStore.isTouchDevice || !touchInput.value) return
    longPress.cancel()
    touchInput.value = false
    for (const tooltip of sessionTooltips.values()) tooltip.setTrigger('hover focus')
}

document.addEventListener('keydown', sessionKeyboardInput, { capture: true })

function sessionClick(session, event) {
    if (!longPress.consumeClick(`session:${session.id}`, event)) openSession(session)
}

watch([() => route.fullPath, () => props.mode, () => props.sidebarOpen], () => longPress.cancel(), { flush: 'sync' })
onBeforeUnmount(() => {
    document.removeEventListener('keydown', sessionKeyboardInput, { capture: true })
    stopDismissalWatch()
    longPress.dispose()
})

function openSession(session) {
    longPress.cancel()
    router.push(sessionRouteLocation(session, route))
}

function projectColor(projectId) {
    const project = store.getProject(projectId)
    return project?.color || (project?.worktree_of ? store.getProject(project.worktree_of)?.color : null) || null
}
const items = computed(() => resolveRailItems({
    mode: props.mode,
    sidebarOpen: props.sidebarOpen,
    peerConfigured: props.peerConfigured,
    inboxCount: props.inboxCount,
    isMac: settingsStore.isMac,
    speechStatus: tts.status,
    listeningTimelineLabel: speech.activePageId ? speech.activeLabel : null,
}))
const top = computed(() => items.value.filter(item => item.group === 'top'))
const bottom = computed(() => items.value.filter(item => item.group === 'bottom'))

function activate(item) {
    if (item.id === 'sessions' || item.id === 'artifacts') emit('select-mode', item.id)
    else if (item.id === 'timeline') {
        const pageId = speech.activePageId
        if (!pageId || pageId === ALL_PROJECTS_ID) return navigateTimelineScope('all-projects')
        if (isWorkspaceProjectId(pageId)) return navigateTimelineScope('workspace', extractWorkspaceId(pageId))
        return navigateTimelineScope('project', pageId)
    }
    else if (item.id === 'toggle') emit('toggle-sidebar')
    else if (item.id === 'speech') return stopSpeech()
    else emit(item.id)
}

// The indicator unmounts once stopped: hand its keyboard focus to the next bottom item, not to
// <body>. A mouse click also focuses the button, but without :focus-visible: no hand-off then, or
// the New session tooltip would open with no keyboard action.
async function stopSpeech() {
    const focused = document.activeElement
    const hadFocus = focused?.id === 'sidebar-rail-speech' && !!focused.matches?.(':focus-visible')
    tts.skip()
    if (!hadFocus) return
    await nextTick()
    document.getElementById('sidebar-rail-new-session')?.focus()
}
</script>

<template>
    <nav aria-label="Main navigation" class="sidebar-rail">
        <div class="panel-card">
            <AndroidAppMenuAnchor />
            <template v-for="item in top" :key="item.id">
                <button
                    :id="`sidebar-rail-${item.id}`" type="button" class="rail-button"
                    :aria-label="item.label" :aria-pressed="item.active" :disabled="item.disabled"
                    @click="activate(item)"
                >
                    <wa-icon :name="item.icon" />
                    <wa-icon
                        v-if="item.id === 'timeline' && speech.activePageId"
                        name="volume-up" class="rail-timeline-audio" aria-hidden="true"
                    />
                </button>
                <AppTooltip :for="`sidebar-rail-${item.id}`" placement="right">{{ item.label }}</AppTooltip>
            </template>
            <wa-divider v-if="hasCentralContent" class="rail-divider" />
            <div class="rail-spacer">
                <div
                    v-for="row in railRows" :key="row.session.id" class="rail-session"
                    :class="row.groupClass"
                >
                    <button
                        :id="`sidebar-rail-session-${row.session.id}`"
                        type="button" class="rail-button rail-session-button"
                        :aria-label="row.session.title || row.session.id"
                        :aria-pressed="row.session.id === currentSessionId"
                        @pointerdown.passive="sessionPointerDown(row.session, $event)"
                        @pointerenter="sessionPointerEnter"
                        @click="sessionClick(row.session, $event)"
                        @contextmenu.prevent
                    >
                        <span v-if="row.showMarkDot" class="rail-mark-dot" aria-hidden="true"></span>
                        <ProjectMark
                            v-else
                            :icon-url="store.resolvedProjectIcons[row.session.project_id] || null"
                            :color="projectColor(row.session.project_id)"
                        />
                        <ProcessActivityIndicator
                            v-if="row.cumulative" :summary="row.cumulative" size="small" :tooltip="false"
                        />
                        <template v-else>
                            <wa-icon v-if="row.pendingRequest && !row.hasUnread" name="hand" class="rail-pending" />
                            <ProcessIndicator
                                v-else :state="row.processState.state" size="small"
                                :has-active-crons="row.hasActiveCrons"
                                :background-shells="row.userTurnBackgroundShells"
                                :unread="row.hasUnread"
                                :style="row.hasUnread ? { fontSize: '0.75rem' } : undefined"
                                :animate-states="['assistant_turn']"
                            />
                        </template>
                    </button>
                    <AppTooltip
                        :for="`sidebar-rail-session-${row.session.id}`"
                        :ref="tooltip => setSessionTooltip(row.session.id, tooltip)"
                        :trigger="sessionTooltipTrigger"
                        @wa-hide="handleEntryTooltipHide(`session:${row.session.id}`, $event)"
                        force interactive hoist placement="right" lazy class="rail-session-tooltip"
                    >
                        <div
                            class="rail-session-preview"
                            @pointerdown.passive="longPress.holdAutoHide(`session:${row.session.id}`)"
                            @pointerup.passive="longPress.releaseAutoHide(`session:${row.session.id}`)"
                            @pointercancel.passive="longPress.releaseAutoHide(`session:${row.session.id}`)"
                            @scroll.capture.passive="longPress.extendAutoHide(`session:${row.session.id}`)"
                        >
                            <SessionListItem
                                :session="row.session" :active="row.session.id === currentSessionId" :highlight-active="false"
                                id-prefix="rail-preview-" :show-menu="false" :selection-enabled="false"
                                :compact-view="false" :show-project-name="true" :show-title-tooltip="false"
                                @select="openSession"
                            />
                            <template v-if="row.activeDescendants?.length">
                                <wa-divider class="rail-preview-divider" />
                                <button
                                    v-for="child in row.activeDescendants.slice(0, RAIL_PREVIEW_MAX_DESCENDANTS)" :key="child.session.id"
                                    type="button" class="rail-preview-child" @click="openSession(child.session)"
                                >
                                    <span class="rail-preview-child-title">{{ child.session.title || child.session.id }}</span>
                                    <wa-icon v-if="child.pendingRequest && !child.hasUnread" name="hand" class="rail-pending" />
                                    <ProcessIndicator
                                        v-else-if="child.processState" :state="child.processState.state" size="small"
                                        :has-active-crons="child.hasActiveCrons"
                                        :background-shells="child.userTurnBackgroundShells"
                                        :unread="child.hasUnread"
                                        :style="child.hasUnread ? { fontSize: '0.75rem' } : undefined"
                                        :animate-states="['assistant_turn']"
                                    />
                                    <wa-icon v-else-if="child.hasUnread" name="eye" class="rail-unread" />
                                </button>
                                <div v-if="row.activeDescendants.length > RAIL_PREVIEW_MAX_DESCENDANTS" class="rail-preview-more">
                                    +{{ row.activeDescendants.length - RAIL_PREVIEW_MAX_DESCENDANTS }} more
                                </div>
                            </template>
                        </div>
                    </AppTooltip>
                </div>
                <wa-divider v-if="rows.length && recentProjects.length" class="rail-divider rail-group-divider" />
                <div v-for="project in recentProjects" :key="`project:${project.id}`" class="rail-scope">
                    <button :id="`sidebar-rail-project-${project.id}`" type="button" class="rail-button rail-scope-button"
                        :aria-label="store.getProjectDisplayName(project.id)" :aria-pressed="scopeSelected('project', project.id)"
                        @pointerdown.passive="entryPointerDown(`project:${project.id}`, $event)"
                        @pointerenter="sessionPointerEnter" @click="scopeClick('project', project.id, $event)" @contextmenu.prevent>
                        <ProjectMark :icon-url="store.resolvedProjectIcons[project.id] || null" :color="projectColor(project.id)" />
                    </button>
                    <AppTooltip :for="`sidebar-rail-project-${project.id}`"
                        :ref="tooltip => setEntryTooltip(`project:${project.id}`, tooltip)" :trigger="sessionTooltipTrigger"
                        @wa-hide="handleEntryTooltipHide(`project:${project.id}`, $event)"
                        force interactive hoist placement="right" lazy class="rail-scope-tooltip">
                        <div class="rail-scope-preview">
                            <ProjectBadge :project-id="project.id" flag-missing-directory />
                            <AggregatedProcessIndicator :project-ids="[project.id]" />
                        </div>
                    </AppTooltip>
                </div>
                <wa-divider v-if="recentWorkspaces.length && (rows.length || recentProjects.length)" class="rail-divider rail-group-divider" />
                <div v-for="workspace in recentWorkspaces" :key="`workspace:${workspace.id}`" class="rail-scope">
                    <button :id="`sidebar-rail-workspace-${workspace.id}`" type="button" class="rail-button rail-scope-button"
                        :aria-label="workspace.name" :aria-pressed="scopeSelected('workspace', workspace.id)"
                        @pointerdown.passive="entryPointerDown(`workspace:${workspace.id}`, $event)"
                        @pointerenter="sessionPointerEnter" @click="scopeClick('workspace', workspace.id, $event)" @contextmenu.prevent>
                        <wa-icon name="layer-group" :style="{ color: workspace.color || undefined }" />
                    </button>
                    <AppTooltip :for="`sidebar-rail-workspace-${workspace.id}`"
                        :ref="tooltip => setEntryTooltip(`workspace:${workspace.id}`, tooltip)" :trigger="sessionTooltipTrigger"
                        @wa-hide="handleEntryTooltipHide(`workspace:${workspace.id}`, $event)"
                        force interactive hoist placement="right" lazy class="rail-scope-tooltip">
                        <div class="rail-scope-preview">
                            <wa-icon name="layer-group" :style="{ color: workspace.color || undefined }" />
                            <span class="rail-scope-name">{{ workspace.name }}</span>
                            <AggregatedProcessIndicator :project-ids="workspacesStore.getVisibleProjectIds(workspace.id)" />
                        </div>
                    </AppTooltip>
                </div>
            </div>
            <wa-divider v-if="hasCentralContent" class="rail-divider" />
            <template v-for="item in bottom" :key="item.id">
                <div v-if="item.id === 'settings'" class="rail-settings">
                    <SettingsPopover
                        trigger-appearance="plain" trigger-icon-only placement="right-end"
                        tooltip-placement="right" :trigger-label="item.label" :position-anchor="settingsAnchor"
                    />
                </div>
                <template v-else-if="item.id === 'new-session'">
                    <NewSessionProjectPicker placement="right-end" @select-project="emit('new-session', $event)">
                        <template #trigger>
                            <button id="sidebar-rail-new-session" slot="trigger" type="button" class="rail-button"
                                    :aria-label="item.label">
                                <wa-icon :name="item.icon" />
                            </button>
                        </template>
                    </NewSessionProjectPicker>
                    <AppTooltip for="sidebar-rail-new-session" placement="right">{{ item.label }}</AppTooltip>
                </template>
                <template v-else>
                    <button
                        :id="`sidebar-rail-${item.id}`" type="button" class="rail-button"
                        :class="{ 'rail-speech--playing': item.id === 'speech' && tts.status === 'playing' }"
                        :aria-label="item.label" :aria-pressed="item.active" :disabled="item.disabled"
                        @click="activate(item)"
                    >
                        <!-- While reading: a speaker whose three waves light up in turn ("it is speaking"). -->
                        <svg
                            v-if="item.id === 'speech' && tts.status === 'playing'"
                            class="rail-speech-waves" viewBox="0 0 24 24" aria-hidden="true"
                        >
                            <path class="rail-speech-speaker" d="M3 9h4l5-4v14l-5-4H3z" />
                            <path class="rail-speech-wave" d="M15 9.5a3.5 3.5 0 0 1 0 5" />
                            <path class="rail-speech-wave" d="M17.5 7a7 7 0 0 1 0 10" />
                            <path class="rail-speech-wave" d="M20 4.5a10.5 10.5 0 0 1 0 15" />
                        </svg>
                        <wa-icon v-else :name="item.icon" />
                        <PeerInboxBadge v-if="item.id === 'inbox'" :count="item.badge" />
                        <wa-spinner v-if="item.id === 'speech' && tts.status === 'loading'" class="rail-speech-spinner" />
                    </button>
                    <AppTooltip :for="`sidebar-rail-${item.id}`" placement="right">{{ item.label }}</AppTooltip>
                </template>
            </template>
        </div>
    </nav>
</template>

<style scoped>
.sidebar-rail {
    display: flex;
    flex: none;
    position: relative;
    width: var(--rail-width);
    container-type: size;
    container-name: rail;
    z-index: 3;
}

.panel-card {
    display: flex;
    flex-direction: column;
    gap: var(--rail-gap);
    padding: var(--rail-card-padding);
    flex: 1;
    min-height: 0;
    margin-block: var(--panel-gap);
    margin-inline-start: var(--panel-gap);
}

/* Android adds a control. Keep all controls reachable at any available height. */
.panel-card:has(.android-app-menu-host) {
    overflow-x: hidden;
    overflow-y: auto;
    scrollbar-width: none;
}

.rail-spacer {
    flex: 1;
    min-height: 0;
    overflow-x: hidden;
    overflow-y: auto;
    scrollbar-width: none;
}

.rail-spacer::-webkit-scrollbar {
    display: none;
}

.rail-session + .rail-session, .rail-scope + .rail-scope {
    margin-top: var(--rail-gap);
}

/* Tree mode: a short 1px segment chains the project marks of consecutive buttons of one tree.
   Each non-last row draws the segment from just below its mark to just above the next row's mark,
   so it crosses the row gap. .rail-session creates no stacking context, so z-index: 1 puts it above the buttons. It lies inside the button's horizontal extent, over the mark's centre. */
.rail-session {
    --rail-mark-size: 0.95rem;
    --project-mark-icon-size: var(--rail-mark-size);
    --rail-mark-free: calc((var(--rail-button-size) - var(--rail-mark-size)) / 2);
    --rail-mark-clearance: 2px;
    --rail-mark-dot-size: 4px;
    /* Segment span, measured from the row top: it starts below its own mark (or at its dot's center)
       and ends above the next row's mark (or at that row's dot center). */
    --rail-segment-start: calc(var(--rail-button-size) - var(--rail-mark-free) + var(--rail-mark-clearance));
    --rail-segment-end: calc(var(--rail-button-size) + var(--rail-gap) + var(--rail-mark-free) - var(--rail-mark-clearance));
}

.rail-session--dot {
    --rail-segment-start: calc(var(--rail-button-size) / 2);
}

.rail-session--next-dot {
    --rail-segment-end: calc(var(--rail-button-size) + var(--rail-gap) + var(--rail-button-size) / 2);
}

/* Repeated project mark: same box as the mark (nothing moves), a small dot in its center, above the segment. */
.rail-mark-dot {
    position: relative;
    z-index: 2;
    flex: none;
    align-self: center;
    display: grid;
    place-items: center;
    width: var(--rail-mark-size);
    height: var(--rail-mark-size);
}

.rail-mark-dot::before {
    content: '';
    width: var(--rail-mark-dot-size);
    height: var(--rail-mark-dot-size);
    border-radius: 50%;
    background: var(--sidebar-divider-color, var(--wa-color-surface-border));
}

.rail-session--grouped {
    position: relative;
}

.rail-session--group-first::after,
.rail-session--group-middle::after {
    content: '';
    position: absolute;
    /* Above the button's own background (pressed, hover, focus): ::after follows the button in paint order. */
    z-index: 1;
    inset-inline-start: calc(var(--rail-mark-size) / 2 - 0.5px);
    width: 1px;
    top: var(--rail-segment-start);
    height: calc(var(--rail-segment-end) - var(--rail-segment-start));
    background: var(--sidebar-divider-color, var(--wa-color-surface-border));
    pointer-events: none;
}

.rail-divider.rail-group-divider { margin-block: var(--rail-gap); }

.rail-scope-button {
    user-select: none;
    -webkit-touch-callout: none;
    display: flex;
    align-items: center;
    justify-content: center;
}

.rail-scope-tooltip {
    --max-width: min(24rem, calc(100vw - var(--rail-width) - 1.5rem));
}

.rail-scope-preview {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    max-width: min(22rem, calc(100vw - var(--rail-width) - 3rem));
    white-space: normal;
}

.rail-scope-name { min-width: 0; overflow-wrap: anywhere; }
.rail-scope-preview :deep(.project-badge-name) { white-space: normal; overflow-wrap: anywhere; }
.rail-scope-preview > :last-child { flex-shrink: 0; margin-inline-start: auto; }

.rail-divider {
    flex: none;
    --color: var(--sidebar-divider-color);
    margin: 0;
}

.rail-session-button {
    user-select: none;
    -webkit-touch-callout: none;
    display: flex;
    justify-content: center;
    gap: 0.2rem;
    --project-mark-size: 0.55rem;
}

.rail-session-button > wa-icon {
    font-size: 0.75rem;
}

.rail-unread, .rail-pending {
    color: var(--wa-color-warning-60);
    animation: motion-status-pulse 2.4s ease-in-out infinite;
}

.rail-pending {
    animation-duration: 1.5s;
}

.rail-session-tooltip {
    --max-width: min(24rem, calc(100vw - var(--rail-width) - 1.5rem));
}

.rail-session-preview {
    /* Reserve the rail, popup distance, body padding, and viewport edge. */
    width: min(22rem, calc(100vw - var(--rail-width) - 3rem));
    max-width: 100%;
    white-space: normal;
    container-type: inline-size;
    container-name: session-list;
}

.rail-preview-divider {
    --color: var(--sidebar-divider-color, var(--wa-color-surface-border));
    margin-block: var(--wa-space-xs);
}

.rail-preview-child {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    width: 100%;
    box-sizing: border-box;
    padding: var(--wa-space-2xs) var(--wa-space-s);
    border: 0;
    border-radius: var(--wa-form-control-border-radius);
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: var(--wa-font-size-s);
    text-align: start;
    cursor: pointer;
}

.rail-preview-child:hover, .rail-preview-child:focus-visible {
    background: var(--rail-button-fill-hover);
}

.rail-preview-child-title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.rail-preview-child > wa-icon { font-size: 0.75rem; }

.rail-preview-more {
    padding: var(--wa-space-2xs) var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.rail-session-preview :deep(.session-item-wrapper) {
    box-sizing: border-box;
}

.rail-session-preview :deep(.session-name) {
    min-width: 0;
}

.rail-session-preview :deep(wa-button)::part(base) {
    white-space: normal;
}

.rail-settings {
    display: flex;
    flex: none;
}

.rail-speech--playing {
    color: var(--wa-color-success-60);
}

.rail-timeline-audio {
    position: absolute;
    inset-inline-end: 0.1rem;
    bottom: 0.15rem;
    padding: 0.1rem;
    border-radius: 50%;
    background: var(--wa-color-surface-default);
    color: var(--wa-color-success-60);
    font-size: 0.65rem;
    pointer-events: none;
}

/* An opacity cascade, no movement: it stays under reduced motion, like the other status
   indicators (motion.css). */
.rail-speech-waves {
    width: 1.15em;
    height: 1.15em;
    overflow: visible;
}

.rail-speech-speaker {
    fill: currentColor;
}

.rail-speech-wave {
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    opacity: 0.2;
    animation: rail-speech-wave 1.2s ease-in-out infinite;
}

.rail-speech-wave:nth-of-type(3) {
    animation-delay: 0.2s;
}

.rail-speech-wave:nth-of-type(4) {
    animation-delay: 0.4s;
}

@keyframes rail-speech-wave {
    0%, 100% { opacity: 0.2; }
    40% { opacity: 1; }
}

/* Out of the button's centred grid, like the inbox badge: a ring around the icon. */
.rail-speech-spinner {
    position: absolute;
    inset: 0;
    margin: auto;
    font-size: calc(var(--rail-icon-size) * 1.6);
    pointer-events: none;
}

:deep(wa-tooltip) {
    position: absolute;
}

@media (width < 640px) {
    .sidebar-rail {
        z-index: 101;
        background: var(--canvas-background);
        background-attachment: fixed;
    }
}

@container rail (height < 30rem) {
    /* Keep controls reachable and bound the independently scrolling central groups. */
    .panel-card {
        overflow-x: hidden;
        overflow-y: auto;
        scrollbar-width: none;
    }

    .rail-spacer {
        flex: none;
        max-height: 8rem;
        overflow-y: auto;
    }

    .panel-card::-webkit-scrollbar {
        display: none;
    }
}
</style>
