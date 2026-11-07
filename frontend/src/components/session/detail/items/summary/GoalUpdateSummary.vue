<script setup>
import { useI18n } from 'vue-i18n'
const { t, locale } = useI18n()
import { computed } from 'vue'
// Summary line for Codex's ``update_goal`` tool. The changed fields are
// the keys of the call arguments (Codex only sends what it mutates). We
// surface the ``status`` value — the one field worth reading at a glance —
// and list the other changed field names *without* their values (the
// detail view carries the full picture). Notable statuses get a status
// icon mirroring the workflow vocabulary (see ``WorkflowStateBadge.vue``):
// a green ``circle-check`` when complete, an orange ``circle-pause`` when
// paused, a red ``circle-xmark`` when the goal can't progress (blocked, or
// a usage / budget cap hit).
const props = defineProps({
    // The new status value when ``status`` was among the changed fields,
    // else null.
    status: { type: String, default: null },
    // Names of the other changed fields (status excluded).
    changedKeys: { type: Array, default: () => [] },
})

// Statuses that render the red "blocked" xmark: an explicit block, or a
// usage / budget cap that stops the goal from making progress.
const statusLabel = computed(() => {
    const keys = { active: 'session.tools.goal.statuses.active', complete: 'session.tools.goal.statuses.complete', paused: 'session.tools.goal.statuses.paused', blocked: 'session.tools.goal.statuses.blocked', usageLimited: 'session.tools.goal.statuses.usageLimited', budgetLimited: 'session.tools.goal.statuses.budgetLimited' }
    return keys[props.status] ? t(keys[props.status]) : props.status
})
const changedFields = computed(() => new Intl.ListFormat(locale.value, { type: 'conjunction' }).format(props.changedKeys))

const BLOCKED_STATUSES = new Set(['blocked', 'usageLimited', 'budgetLimited'])

// Icon shown next to a terminal status. Other statuses (e.g. active) get
// none.
const statusIcon = computed(() => {
    if (props.status === 'complete') return { name: 'circle-check', cls: 'goal-update-done' }
    if (props.status === 'paused') return { name: 'circle-pause', cls: 'goal-update-paused' }
    if (BLOCKED_STATUSES.has(props.status)) return { name: 'circle-xmark', cls: 'goal-update-blocked' }
    return null
})
</script>

<template>
    <span class="items-details-summary-description goal-update-summary">
        <span v-if="status" class="goal-update-part">
            {{ $t('session.tools.goal.status', { status: statusLabel }) }}<wa-icon
                v-if="statusIcon"
                :name="statusIcon.name"
                class="goal-update-status-icon"
                :class="statusIcon.cls"
            ></wa-icon>
        </span>
        <span v-if="status && changedKeys.length" class="goal-update-sep">+</span>
        <span v-if="changedKeys.length" class="goal-update-part">
            {{ status ? $t('session.tools.goal.otherChanges', { fields: changedFields }) : $t('session.tools.goal.changes', { fields: changedFields }) }}
        </span>
    </span>
</template>

<style scoped>
.goal-update-summary {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
.goal-update-part {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
.goal-update-status-icon {
    font-size: 1.1em;
}
.goal-update-done {
    color: var(--wa-color-success-50);
}
.goal-update-blocked {
    color: var(--wa-color-danger-50);
}
.goal-update-paused {
    color: var(--wa-color-warning-60);
}
.goal-update-sep {
    color: var(--wa-color-text-quiet);
    flex-shrink: 0;
}
</style>
