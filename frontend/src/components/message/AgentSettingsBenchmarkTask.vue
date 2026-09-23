<script setup>
// Controls of the model × effort score, right below the matrix (spec §5.1):
// Task type, Task difficulty and Favor (Cost / Speed), then the auto-select
// switches. The benchmarkTask store holds the state; the parent popover runs
// the actual matrix pick. Score help lives in the heading above the matrix.
import { useId } from 'vue'
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'
import { TASK_TYPES } from '../../utils/benchmarkScores'

defineProps({
    // Providers shown in the matrix; the "Default provider only" switch only
    // surfaces when there is more than one.
    providerCount: { type: Number, default: 1 },
    // Whether to render the "Auto-select best" line. The per-session popover
    // wants it (default); the per-provider defaults editor hides it — the store
    // is global and the always-mounted popover watches it, so a Settings-side
    // auto-select would silently mutate the live session. The task controls
    // stay in both surfaces; with Auto-select on in the popover, changing them
    // here also re-picks the live session's cell (the store is shared).
    showAutoSelect: { type: Boolean, default: true },
})

const store = useBenchmarkTaskStore()
const uid = useId()
const taskTypes = TASK_TYPES

function onTaskTypeChange(event) {
    store.setTaskType(event.target.value)
}

function onDifficultyInput(event) {
    store.setDifficulty(event.target.value)
}

function onFavorChange(event) {
    store.setFavor(event.target.value)
}
</script>

<template>
    <div class="benchmark-task">
        <div class="task-rows">
            <label class="task-label" :for="`${uid}-type`">Task type</label>
            <wa-select
                :id="`${uid}-type`"
                class="task-control"
                size="small"
                aria-label="Task type"
                :value.prop="store.taskType"
                @change="onTaskTypeChange"
            >
                <wa-option v-for="t in taskTypes" :key="t.id" :value="t.id">{{ t.label }}</wa-option>
            </wa-select>

            <span class="task-label">Task difficulty</span>
            <wa-slider
                class="task-control"
                size="small"
                :min.prop="0"
                :max.prop="100"
                :step.prop="1"
                :value.prop="store.difficulty"
                aria-label="Task difficulty"
                @input="onDifficultyInput"
            ></wa-slider>

            <span class="task-label">Favor</span>
            <!-- label="Favor" gives the inner radiogroup its accessible name
                 (an aria-label on the host does not reach it); the visible
                 label is the span above, so the group's own label is hidden. -->
            <wa-radio-group
                class="task-control task-favor"
                size="small"
                orientation="horizontal"
                label="Favor"
                :value.prop="store.favor"
                @change="onFavorChange"
            >
                <wa-radio appearance="button" value="cost">Cost</wa-radio>
                <wa-radio appearance="button" value="speed">Speed</wa-radio>
            </wa-radio-group>
        </div>

        <!-- Auto-select controls, at the end. Hidden where showAutoSelect is
             false (per-provider defaults editor). -->
        <div v-if="showAutoSelect" class="task-autoselect">
            <wa-switch
                size="small"
                :checked="store.autoSelectBest"
                @change="store.autoSelectBest = $event.target.checked"
            >Auto-select best</wa-switch>
            <wa-switch
                v-if="store.autoSelectBest && providerCount > 1"
                size="small"
                :checked="store.defaultProviderOnly"
                @change="store.defaultProviderOnly = $event.target.checked"
            >Default provider only</wa-switch>
        </div>

        <!-- Closes the task block. -->
        <wa-divider class="task-divider"></wa-divider>
    </div>
</template>

<style scoped>
.benchmark-task {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

/* Labels in the first column, controls in the second. */
.task-rows {
    display: grid;
    grid-template-columns: auto 1fr;
    align-items: center;
    gap: var(--wa-space-2xs) var(--wa-space-xs);
}

.task-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
}

.task-control {
    min-width: 6rem;
}

/* The two Favor buttons keep their natural width. */
.task-favor {
    justify-self: start;
}

/* Visible label is the grid's span; keep the group's label for screen readers only. */
.task-favor::part(form-control-label) {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
}

/* Auto-select switch line — same wrap + gaps as the popover's switch row. */
.task-autoselect {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-m);
    row-gap: var(--wa-space-xs);
}

/* A little breathing room above the closing divider. */
.task-divider {
    margin: var(--wa-space-s) 0 0;
}
</style>
