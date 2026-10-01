<script setup>
// Read-only view of a session's latest task/todo/plan snapshot, rendered with
// the SAME provider-agnostic component as the conversation timeline
// (TodoContent). The data lives on Session.tasks — normalised across providers
// (Claude Code TodoWrite/Task*, Codex update_plan) and kept in sync via the
// session_updated broadcast — so this pane is purely reactive: no fetch, no
// endpoint, no WS wiring of its own. The Tasks tab is only present when the
// session has tasks, so a snapshot normally exists; the empty state is just a
// safety net for the brief window where it flips empty before the tab hides.
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'
import { countTasks } from '../../utils/todoList'
import TodoContent from '../session/detail/items/TodoContent.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    // True while the pane is on screen (tab shown, session active). Gates the check pop
    // of a task that becomes completed: an animation started while hidden would only
    // play later, when the pane is shown again.
    active: { type: Boolean, default: false },
})

const store = useDataStore()
const tasks = computed(() => store.getSessionTasks(props.sessionId))
const progress = computed(() => countTasks(tasks.value?.items))
</script>

<template>
    <div class="task-pane">
        <div v-if="!tasks" class="task-state">
            <wa-icon name="square-check"></wa-icon>
            <span>No tasks</span>
        </div>
        <template v-else>
            <div v-if="progress.total > 0" class="task-progress">
                <span class="task-progress-label">{{ progress.done }} of {{ progress.total }} done</span>
                <div
                    class="task-progress-track"
                    role="progressbar"
                    :aria-valuemin="0"
                    :aria-valuemax="progress.total"
                    :aria-valuenow="progress.done"
                    aria-label="Tasks done"
                >
                    <div class="task-progress-fill" :style="{ width: progress.percent + '%' }"></div>
                </div>
            </div>
            <div class="task-scroll">
                <TodoContent
                    :key="sessionId"
                    :todos="tasks.items"
                    :explanation="tasks.explanation"
                    :animate="active"
                />
            </div>
        </template>
    </div>
</template>

<style scoped>
.task-pane {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}
.task-scroll {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-s);
}
/* TodoContent is shared with the conversation timeline; tighten it only here. */
.task-scroll :deep(.todo-list) {
    font-size: var(--wa-font-size-s);
}
.task-scroll :deep(.todo-item) {
    margin-inline-start: 0;
}
/* Completed tasks are struck through, here only (the timeline blocks keep grey text). The
   line is a background so it can draw; it follows the text across wrapped lines. */
.task-scroll :deep(.todo-item-completed .todo-item-strike) {
    background-image: linear-gradient(currentColor, currentColor);
    background-repeat: no-repeat;
    background-position: 0 62%;
    background-size: 100% 1px;
}
.task-scroll :deep(.todo-item-ticking .todo-item-text) {
    transition: color var(--motion-dur-3) var(--motion-ease);
    transition-delay: calc(var(--tick-rank, 0) * 150ms);
}
.task-scroll :deep(.todo-item-ticking .todo-item-strike) {
    animation: task-strike-draw 300ms var(--motion-ease-out) backwards;
    animation-delay: calc(var(--tick-rank, 0) * 150ms);
}
@keyframes task-strike-draw {
    from { background-size: 0% 1px; }
}
.task-progress {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-s) var(--wa-space-s) 0;
}
.task-progress-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
.task-progress-track {
    height: 6px;
    border-radius: var(--wa-border-radius-pill);
    background: var(--progress-track);
}
.task-progress-fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, oklch(from var(--wa-color-success-60) calc(l + 0.08) c h), var(--wa-color-success-60));
    box-shadow: 0 0 0.25rem color-mix(in oklab, var(--wa-color-success-60) 30%, transparent);
    transition: width 600ms var(--motion-ease-out);
    animation: task-progress-fill 600ms var(--motion-ease-out) backwards;
}
@keyframes task-progress-fill {
    from { width: 0; }
}
/* Reduced motion: the strike and the bar snap (the colour fade stays). After the rules above. */
@media (prefers-reduced-motion: reduce) {
    .task-scroll :deep(.todo-item-ticking .todo-item-strike) {
        animation: none;
    }
    .task-progress-fill {
        transition: none;
        animation: none;
    }
}
.task-state {
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
</style>
