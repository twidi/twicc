<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useRailActiveSessions } from '../../composables/useRailActiveSessions'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import ProjectMark from '../project/ProjectMark.vue'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import SessionListItem from '../session/list/SessionListItem.vue'
import { useSettingsStore } from '../../stores/settings'
import { resolveRailItems } from '../../utils/sidebarRail.js'
import AppTooltip from '../ui/AppTooltip.vue'
import SettingsPopover from '../app/SettingsPopover.vue'
import PeerInboxBadge from '../peer/PeerInboxBadge.vue'

const props = defineProps({
    mode: { type: String, required: true, validator: value => ['home', 'sessions', 'artifacts'].includes(value) },
    sidebarOpen: { type: Boolean, default: false },
    peerConfigured: { type: Boolean, required: true },
    inboxCount: { type: Number, required: true },
    settingsAnchor: { type: Object, default: null },
})
const emit = defineEmits(['home', 'select-mode', 'search', 'palette', 'inbox', 'toggle-sidebar'])
const settingsStore = useSettingsStore()
const store = useDataStore()
const route = useRoute()
const router = useRouter()
const currentSessionId = computed(() => props.mode === 'sessions' ? route.params.sessionId || null : null)
const { rows } = useRailActiveSessions(store, () => currentSessionId.value)

function openSession(session) {
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
            <wa-divider v-if="rows.length" class="rail-divider" />
            <div class="rail-spacer">
                <div v-for="row in rows" :key="row.session.id" class="rail-session">
                    <button
                        :id="`sidebar-rail-session-${row.session.id}`"
                        type="button" class="rail-button rail-session-button"
                        :aria-label="row.session.title || row.session.id"
                        :aria-pressed="row.session.id === currentSessionId"
                        @click="openSession(row.session)"
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
                        force interactive hoist placement="right" class="rail-session-tooltip"
                    >
                        <div class="rail-session-preview">
                            <SessionListItem
                                :session="row.session" :active="row.session.id === currentSessionId"
                                id-prefix="rail-preview-" :show-menu="false" :selection-enabled="false"
                                :compact-view="false" :show-project-name="true" :show-title-tooltip="false"
                                @select="openSession"
                            />
                        </div>
                    </AppTooltip>
                </div>
            </div>
            <wa-divider v-if="rows.length" class="rail-divider" />
            <template v-for="item in bottom" :key="item.id">
                <div v-if="item.id === 'settings'" class="rail-settings">
                    <SettingsPopover
                        trigger-appearance="plain" trigger-icon-only placement="right-end"
                        tooltip-placement="right" :trigger-label="item.label" :position-anchor="settingsAnchor"
                    />
                </div>
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

.rail-session + .rail-session {
    margin-top: var(--rail-gap);
}

.rail-divider {
    flex: none;
    margin: 0;
}

.rail-session-button {
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

@container rail (height < 22rem) {
    /* Navigation stays reachable when the fixed controls exceed the viewport. */
    .panel-card {
        overflow-x: hidden;
        overflow-y: auto;
        scrollbar-width: none;
    }

    .rail-spacer {
        flex: none;
        overflow: visible;
    }

    .panel-card::-webkit-scrollbar {
        display: none;
    }
}
</style>
