<script setup>
// TimelineEventRow.vue - One event line of the Timeline (display design section 5.1):
//   [indent] icon | [group head: provider icon, session title, project name, markers] wording, tag, badge,
//   author label | time on the right.
//
// All the decisions (wording, icon, severity, links, time) are made by the pure modules of
// utils/timeline/; this component only shows them. Every string from data is rendered as TEXT with
// interpolation, never as HTML.

import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { describeEvent } from '../../utils/timeline/describeEvent'
import ProjectBadge from '../project/ProjectBadge.vue'
import TimelineSessionMention from './TimelineSessionMention.vue'

const props = defineProps({
    /** An event row of `buildTimelineRows`. */
    row: { type: Object, required: true },
    /** Facts the wording needs: `{sessions, labelsFor, isSubagent}`. */
    context: { type: Object, required: true },
})

const dataStore = useDataStore()

const vm = computed(() => describeEvent(props.row.event, props.context))
const identity = computed(() => props.row.identity)
// A project the store does not know (deleted) is shown as plain text, never as a broken link.
const knownProject = computed(() => (identity.value?.projectId ? !!dataStore.getProject(identity.value.projectId) : false))

const expanded = ref(false)
// Unique per row, so the toggle can point at the text it shows.
const errorRestId = computed(() => `timeline-error-rest-${props.row.event.id}`)
</script>

<template>
    <div
        class="timeline-row"
        :class="[`timeline-row--${vm.severity}`, { 'timeline-row--indented': row.indented, 'timeline-row--head': row.head }]"
        :data-kind="vm.kind"
    >
        <wa-icon :name="vm.icon" class="timeline-row-icon" auto-width :title="vm.tooltip"></wa-icon>
        <div class="timeline-row-main">
            <span v-if="identity" class="timeline-identity">
                <TimelineSessionMention :mention="identity.mention" markers />
                <template v-if="identity.showProject && identity.projectId">
                    <!-- A jump to that project on purpose: no workspace query. -->
                    <RouterLink
                        v-if="knownProject"
                        :to="{ name: 'project', params: { projectId: identity.projectId } }"
                        class="timeline-project"
                    ><ProjectBadge :project-id="identity.projectId" /></RouterLink>
                    <span v-else class="timeline-project timeline-project--plain">{{ identity.projectId }}</span>
                </template>
            </span>
            <span class="timeline-wording">
                <template v-for="(part, index) in vm.parts" :key="index">
                    <span v-if="part.type === 'text'">{{ part.text }}</span>
                    <TimelineSessionMention v-else :mention="part.mention" />
                </template>
            </span>
            <span v-if="vm.tag" class="timeline-tag">{{ vm.tag }}</span>
            <wa-badge v-if="vm.badge" variant="warning" appearance="outlined" class="timeline-badge" :title="vm.badge.title">{{ vm.badge.text }}</wa-badge>
            <span v-if="vm.author.parts.length" class="timeline-author">
                <template v-for="(part, index) in vm.author.parts" :key="index">
                    <span v-if="part.type === 'text'">{{ part.text }}</span>
                    <TimelineSessionMention v-else :mention="part.mention" />
                </template>
            </span>
            <span v-if="vm.detail" class="timeline-error">
                <code class="timeline-error-line">{{ vm.detail.first }}</code>
                <wa-button
                    v-if="vm.detail.rest"
                    size="small"
                    appearance="plain"
                    class="timeline-error-toggle"
                    :aria-expanded="expanded ? 'true' : 'false'"
                    :aria-controls="errorRestId"
                    @click="expanded = !expanded"
                >{{ expanded ? 'Show less' : 'Show more' }}</wa-button>
                <pre v-if="expanded && vm.detail.rest" :id="errorRestId" class="timeline-error-rest">{{ vm.detail.rest }}</pre>
            </span>
        </div>
        <span class="timeline-time" :class="{ 'timeline-time--estimated': vm.time.estimated }" :title="vm.tooltip">{{ vm.time.text }}</span>
    </div>
</template>

<style scoped>
.timeline-row {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xs) var(--wa-space-l);
    min-height: 2rem;
    font-size: var(--wa-font-size-s);
}

.timeline-row--indented {
    padding-inline-start: calc(var(--wa-space-l) + var(--wa-space-xl));
}

.timeline-row-icon {
    flex-shrink: 0;
    align-self: center;
    color: var(--wa-color-text-quiet);
}

.timeline-row--success .timeline-row-icon { color: var(--wa-color-success-60); }
.timeline-row--warning .timeline-row-icon { color: var(--wa-color-warning-60); }
.timeline-row--danger .timeline-row-icon { color: var(--wa-color-danger-60); }

.timeline-row-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    column-gap: var(--wa-space-s);
    row-gap: 0;
    overflow-wrap: anywhere;
}

.timeline-identity {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.timeline-project {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

a.timeline-project {
    text-decoration: none;
}

.timeline-tag,
.timeline-author {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.timeline-badge {
    align-self: center;
}

.timeline-error {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--wa-space-xs);
    flex-basis: 100%;
}

.timeline-error-line,
.timeline-error-rest {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-60);
}

.timeline-error-rest {
    flex-basis: 100%;
    margin: 0;
    white-space: pre-wrap;
}

.timeline-time {
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.timeline-time--estimated {
    opacity: 0.6;
}
</style>
