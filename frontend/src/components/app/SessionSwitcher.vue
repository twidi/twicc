<script setup>
/**
 * SessionSwitcher — the overlay panel for the Ctrl+` session switcher.
 *
 * Purely presentational + pointer interactions: all keyboard driving (engage,
 * cycle, commit on Ctrl release, cancel on Escape) happens in App.vue's global
 * handlers, which mutate the shared state exposed by useSessionSwitcher. This
 * component just renders the snapshotted MRU list when `visible` and forwards
 * mouse hover/click to the same state machine.
 *
 * The row markup deliberately replicates the compact SessionList row (project
 * color dot · provider icon · "Arch." tag for archived sessions / "Draft" tag
 * for drafts · ellipsized title · state indicator) rather than sharing a
 * component — it's a handful of elements and stays self-contained.
 */
import { computed, ref, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { isSessionUnread } from '../../utils/sessions'
import { userTurnBackgroundShellCount } from '../../utils/backgroundWork'
import { getProviderIcon } from '../../providers'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import ProjectMark from '../project/ProjectMark.vue'
import { useSessionSwitcher } from '../../composables/useSessionSwitcher'

const route = useRoute()
const store = useDataStore()
const { visible, mode, items, cursor, pointTo, commitTo, cancel } = useSessionSwitcher()

const modeLabel = computed(() => (mode.value === 'list' ? 'Displayed sessions' : 'Recent sessions'))

/**
 * Color for the project dot: the project's own color, or — for a worktree with
 * no color of its own — its main repository's color. Mirrors SessionListItem.
 */
function projectDotColor(session) {
    const p = store.getProject(session.project_id)
    if (!p) return null
    if (p.worktree_of) return p.color || store.getProject(p.worktree_of)?.color || null
    return p.color || null
}

function displayName(session) {
    if (session.draft && !session.title) return 'New session'
    return session.title || session.id
}

/**
 * State indicator for a row, by priority: unread → pending request → process.
 * The currently-viewed session never shows "unread" (you're looking at it).
 */
function stateOf(session) {
    const processState = store.getProcessState(session.id)
    const isCurrent = session.id === route.params.sessionId
    if (!isCurrent && isSessionUnread(session, processState)) return { kind: 'unread', processState }
    if (store.getPendingRequests(session.id).length > 0) return { kind: 'pending', processState }
    if (processState) return { kind: 'process', processState }
    return { kind: null, processState: null }
}

const rows = computed(() =>
    items.value.map(({ session, path }, index) => {
        const state = stateOf(session)
        return {
            session,
            path,
            index,
            dotColor: projectDotColor(session),
            iconUrl: store.resolvedProjectIcons[session.project_id] || null,
            providerIcon: getProviderIcon(session.provider),
            name: displayName(session),
            state,
            // Mirrors SessionListItem: a "Draft" tag for not-yet-started sessions
            // that have no live process (archived takes priority over draft).
            isDraft: session.draft && !state.processState,
        }
    })
)

// Keep the highlighted row in view as the cursor moves (or when the panel first
// appears).
const listRef = ref(null)
watch([cursor, visible], async () => {
    if (!visible.value) return
    await nextTick()
    listRef.value?.querySelector('.switcher-row--active')?.scrollIntoView({ block: 'nearest' })
})
</script>

<template>
    <Teleport to="body">
        <div v-if="visible" class="switcher-overlay" @mousedown.self="cancel">
            <div class="switcher-panel" role="listbox" :aria-label="modeLabel">
                <div class="switcher-header">
                    <span class="switcher-mode">{{ modeLabel }}</span>
                    <span class="switcher-hint"><kbd>⇧</kbd> to switch</span>
                </div>
                <div ref="listRef" class="switcher-list">
                    <button
                        v-for="row in rows"
                        :key="row.session.id"
                        type="button"
                        class="switcher-row"
                        :class="{ 'switcher-row--active': row.index === cursor }"
                        role="option"
                        :aria-selected="row.index === cursor"
                        @mouseenter="pointTo(row.index)"
                        @click="commitTo(row.index)"
                    >
                        <ProjectMark :icon-url="row.iconUrl" :color="row.dotColor" class="switcher-mark" />
                        <wa-icon
                            v-if="row.providerIcon"
                            auto-width
                            family="brands"
                            :name="row.providerIcon"
                            class="switcher-provider"
                        ></wa-icon>
                        <wa-tag
                            v-if="row.session.archived"
                            size="small"
                            variant="neutral"
                            class="switcher-archived-tag"
                        >Arch.</wa-tag>
                        <wa-tag
                            v-else-if="row.isDraft"
                            size="small"
                            variant="warning"
                            class="switcher-draft-tag"
                        >Draft</wa-tag>
                        <span class="switcher-name">{{ row.name }}</span>
                        <span class="switcher-state">
                            <wa-icon v-if="row.state.kind === 'unread'" name="eye" class="switcher-unread"></wa-icon>
                            <wa-icon v-else-if="row.state.kind === 'pending'" name="hand" class="switcher-pending"></wa-icon>
                            <ProcessIndicator
                                v-else-if="row.state.kind === 'process'"
                                :state="row.state.processState.state"
                                :has-active-crons="row.state.processState.active_crons?.length > 0"
                                :background-shells="userTurnBackgroundShellCount(row.state.processState)"
                                size="small"
                            />
                        </span>
                    </button>
                </div>
            </div>
        </div>
    </Teleport>
</template>

<style scoped>
.switcher-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.4);
    padding: var(--wa-space-l);
}

.switcher-panel {
    width: min(440px, calc(100vw - 2rem));
    max-height: min(70vh, 560px);
    display: flex;
    flex-direction: column;
    background: var(--wa-color-surface-default);
    border: var(--wa-border-width-s) solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-l);
    box-shadow: var(--wa-shadow-l);
    overflow: hidden;
}

.switcher-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-bottom: var(--wa-border-width-s) solid var(--wa-color-surface-border);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-muted);
}

.switcher-mode {
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

.switcher-hint {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.switcher-hint kbd {
    font: inherit;
    padding: 0 var(--wa-space-2xs);
    border: var(--wa-border-width-s) solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-lowered);
}

.switcher-list {
    overflow-y: auto;
    padding: var(--wa-space-2xs);
}

.switcher-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    width: 100%;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border: none;
    border-radius: var(--wa-border-radius-m);
    background: transparent;
    color: var(--wa-color-text-normal);
    font: inherit;
    font-size: var(--wa-font-size-s);
    text-align: start;
    cursor: pointer;
}

.switcher-row--active {
    background: var(--wa-color-brand-fill-quiet);
    color: var(--wa-color-brand-on-quiet);
}

.switcher-mark {
    flex: 0 0 auto;
}

.switcher-provider {
    flex: 0 0 auto;
    color: var(--wa-color-text-muted);
}

.switcher-row--active .switcher-provider {
    color: inherit;
}

/* "Arch."/"Draft" markers — mirror SessionListItem's archived-tag/draft-tag. */
.switcher-archived-tag,
.switcher-draft-tag {
    flex: 0 0 auto;
    line-height: unset;
    height: unset;
}

.switcher-name {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.switcher-state {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    color: var(--wa-color-text-muted);
}

.switcher-unread {
    color: var(--wa-color-brand-60);
}

.switcher-pending {
    color: var(--wa-color-warning-60);
}
</style>
