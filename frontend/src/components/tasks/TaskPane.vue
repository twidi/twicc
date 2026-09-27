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
</script>

<template>
    <div class="task-pane">
        <div v-if="!tasks" class="task-state">
            <wa-icon name="square-check"></wa-icon>
            <span>No tasks</span>
        </div>
        <div v-else class="task-scroll">
            <TodoContent
                :key="sessionId"
                :todos="tasks.items"
                :explanation="tasks.explanation"
                :animate="active"
            />
        </div>
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
.task-state {
    display: flex;
    flex: 1;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}
</style>
