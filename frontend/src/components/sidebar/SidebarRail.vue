<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useRailActiveSessions } from '../../composables/useRailActiveSessions'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useRailRecentProjects } from '../../composables/useRailRecentProjects.js'
import { createRailScopeNavigation } from '../../utils/railScopeNavigation.js'
import ProjectBadge from '../project/ProjectBadge.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'
import ProjectMark from '../project/ProjectMark.vue'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import SessionListItem from '../session/list/SessionListItem.vue'
import { useSettingsStore } from '../../stores/settings'
import { resolveRailItems } from '../../utils/sidebarRail.js'
import AppTooltip, { onTooltipDismissal } from '../ui/AppTooltip.vue'
import { createRailSessionLongPress } from '../../utils/railSessionLongPress.js'
import SettingsPopover from '../app/SettingsPopover.vue'
import PeerInboxBadge from '../peer/PeerInboxBadge.vue'
import NewSessionProjectPicker from '../project/NewSessionProjectPicker.vue'

const props = defineProps({
    mode: { type: String, required: true, validator: value => ['home', 'sessions', 'artifacts'].includes(value) },
    sidebarOpen: { type: Boolean, default: false },
    peerConfigured: { type: Boolean, required: true },
    inboxCount: { type: Number, required: true },
    settingsAnchor: { type: Object, default: null },
})
const emit = defineEmits(['new-session', 'home', 'select-mode', 'search', 'palette', 'inbox', 'toggle-sidebar'])
const settingsStore = useSettingsStore()
const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const { recentProjects, recentWorkspaces } = useRailRecentProjects(store, workspacesStore, settingsStore)
const route = useRoute()
const router = useRouter()
const currentSessionId = computed(() => props.mode === 'sessions' ? route.params.sessionId || null : null)
const { rows } = useRailActiveSessions(store, () => currentSessionId.value)

const hasCentralContent = computed(() => rows.value.length || recentProjects.value.length || recentWorkspaces.value.length)
const navigateScope = createRailScopeNavigation(router)
function scopeSelected(kind, id) {
    return kind === 'project'
        ? route.name === 'project' && route.params.projectId === id && !route.query.workspace
        : route.name === 'projects-all' && route.query.workspace === id
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
    hide: () => { for (const tooltip of sessionTooltips.values()) tooltip.hide() },
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
}))
const top = computed(() => items.value.filter(item => item.group === 'top'))
const bottom = computed(() => items.value.filter(item => item.group === 'bottom'))

function activate(item) {
    if (item.id === 'sessions' || item.id === 'artifacts') emit('select-mode', item.id)
    else if (item.id === 'toggle') emit('toggle-sidebar')
    else emit(item.id)
}
</script>

<template>
    <nav aria-label="Main navigation" class="sidebar-rail">
        <div class="panel-card">
            <template v-for="item in top" :key="item.id">
                <button
                    :id="`sidebar-rail-${item.id}`" type="button" class="rail-button"
                    :aria-label="item.label" :aria-pressed="item.active" :disabled="item.disabled"
                    @click="activate(item)"
                >
                    <wa-icon :name="item.icon" />
                </button>
                <AppTooltip :for="`sidebar-rail-${item.id}`" placement="right">{{ item.label }}</AppTooltip>
            </template>
            <wa-divider v-if="hasCentralContent" class="rail-divider" />
            <div class="rail-spacer">
                <div v-for="row in rows" :key="row.session.id" class="rail-session">
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
                        <ProjectMark
                            :icon-url="store.resolvedProjectIcons[row.session.project_id] || null"
                            :color="projectColor(row.session.project_id)"
                        />
                        <wa-icon v-if="row.hasUnread" name="eye" class="rail-unread" />
                        <wa-icon v-else-if="row.pendingRequest" name="hand" class="rail-pending" />
                        <ProcessIndicator
                            v-else :state="row.processState.state" size="small"
                            :has-active-crons="row.hasActiveCrons"
                            :background-shells="row.userTurnBackgroundShells"
                            :animate-states="['assistant_turn']"
                        />
                    </button>
                    <AppTooltip
                        :for="`sidebar-rail-session-${row.session.id}`"
                        :ref="tooltip => setSessionTooltip(row.session.id, tooltip)"
                        :trigger="sessionTooltipTrigger"
                        force interactive hoist placement="right" lazy class="rail-session-tooltip"
                    >
                        <div class="rail-session-preview">
                            <SessionListItem
                                :session="row.session" :active="row.session.id === currentSessionId" :highlight-active="false"
                                id-prefix="rail-preview-" :show-menu="false" :selection-enabled="false"
                                :compact-view="false" :show-project-name="true" :show-title-tooltip="false"
                                @select="openSession"
                            />
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
                        :aria-label="item.label" :aria-pressed="item.active" :disabled="item.disabled"
                        @click="activate(item)"
                    >
                        <wa-icon :name="item.icon" />
                        <PeerInboxBadge v-if="item.id === 'inbox'" :count="item.badge" />
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
    --project-mark-icon-size: 0.95rem;
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

@container rail (height < 25rem) {
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
