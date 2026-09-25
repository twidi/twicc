<script setup>
import { computed } from 'vue'
import { useDataStore } from '../../../../stores/data'
import { getProviderLabel, getToolHelpers } from '../../../../providers'
import ProcessIndicator from '../../../ui/ProcessIndicator.vue'

const props = defineProps({
    label: { type: String, default: null },
    processState: { type: String, default: 'assistant_turn' },
    tools: { type: Array, default: () => [] },
    lastStartedToolId: { type: String, default: null },
    lastToolVisible: { type: Boolean, default: true },
    sessionId: { type: String, default: null },
})

const dataStore = useDataStore()

const sessionBaseDir = computed(() => {
    if (!props.sessionId) return null
    const session = dataStore.getSession(props.sessionId)
    return session?.git_directory || session?.cwd || null
})

const providerLabel = computed(() => {
    if (!props.sessionId) return getProviderLabel(null)
    const session = dataStore.getSession(props.sessionId)
    return getProviderLabel(session?.provider)
})

// Pending requests (tool approval / AskUserQuestion / hybrid terminal) keep the
// backend in ASSISTANT_TURN, so without this the placeholder would keep claiming
// the agent is "thinking" while it is in fact blocked waiting for the user. Read
// straight from the store: this component stays mounted for the whole turn, and
// setProcessState reassigns processStates[sessionId] when a request appears or
// clears, so the computed re-evaluates on its own (no visual-item recompute).
const pendingRequests = computed(() =>
    props.sessionId ? dataStore.getPendingRequests(props.sessionId) : [])

const isAwaiting = computed(() => pendingRequests.value.length > 0)

// Verb shown while waiting, keyed off the first request (the bottom pending
// panel surfaces pendingRequests[0] too). Unknown / mixed types fall back to the
// generic phrasing.
const PENDING_VERBS = {
    tool_approval: 'waiting for your approval',
    ask_user_question: 'waiting for your answer',
    hybrid_terminal: 'waiting for you in the terminal',
}
const pendingVerb = computed(() => {
    if (!isAwaiting.value) return null
    return PENDING_VERBS[pendingRequests.value[0]?.request_type] || 'waiting for your input'
})

const plainPhrase = computed(() => {
    // A pending request wins over everything else: the agent is blocked on the
    // user, not thinking or running a tool.
    if (pendingVerb.value) return pendingVerb.value
    if (props.label) return props.label
    const tools = props.tools || []
    if (tools.length === 0) return 'thinking'
    return null
})

const phraseGroups = computed(() => {
    if (plainPhrase.value !== null) return null
    if (!props.sessionId) return []
    const session = dataStore.getSession(props.sessionId)
    const helpers = getToolHelpers(session?.provider)
    return buildPhraseGroups(props.tools, sessionBaseDir.value, props.lastStartedToolId, props.lastToolVisible, helpers)
})

function buildPhraseGroups(tools, baseDir, lastStartedToolId, lastToolVisible, toolHelpers) {
    // Group tools by verb, preserving first-occurrence order from the current frame.
    const map = new Map()
    if (!toolHelpers) return []
    for (const t of tools) {
        const verb = toolHelpers.getVerb(t.name, t.input)
        if (!verb) continue
        const { inline } = toolHelpers.computeToolSummary(t.name, t.input, baseDir)
        if (!map.has(verb)) map.set(verb, [])
        map.get(verb).push(inline)
    }

    // Skip parens when there's a single active tool AND it's the most recently
    // started one — its tool card sits right above, so the parenthesised target
    // would be redundant. Otherwise (multiple tools, or a single survivor that
    // isn't the latest), parens disambiguate which tool we're talking about.
    // Exception 1: while the latest tool is still streaming its input, no real
    // tool card exists yet, so we always show the summary to give the user
    // visible feedback as the input fills in.
    // Exception 2: in display modes where the tool card is hidden by default
    // (collapsed group in simplified, hidden block in conversation), keep the
    // summary so the user still has context on what's running.
    const latest = tools.length === 1 ? tools[0] : null
    const isLoneLatest = latest && latest.id === lastStartedToolId && !latest.streaming
    const showSummaries = !isLoneLatest || !lastToolVisible

    return Array.from(map, ([verb, summaries]) => {
        if (!showSummaries) return { verb, targets: null }
        const filtered = summaries.filter(s => s != null)
        return { verb, targets: filtered.length > 0 ? filtered : null }
    })
}
</script>

<template>
    <div class="working-assistant-message text-content">
        <wa-icon
            v-if="isAwaiting"
            name="hand"
            class="working-assistant-message__awaiting"
        ></wa-icon>
        <ProcessIndicator v-else :state="processState" size="small" :animate-states="['starting', 'assistant_turn']" />
        <span v-if="plainPhrase !== null">{{ providerLabel }} is {{ plainPhrase }}...</span>
        <span v-else>{{ providerLabel }} is <template v-for="(group, gi) in phraseGroups" :key="gi"><template v-if="gi > 0 && gi === phraseGroups.length - 1"> and </template><template v-else-if="gi > 0">, </template><template v-if="phraseGroups.length > 1"><strong>{{ group.verb }}</strong></template><template v-else>{{ group.verb }}</template><template v-if="group.targets"> (<template v-for="(t, ti) in group.targets" :key="`${gi}-${ti}`"><template v-if="ti > 0">, </template><code>{{ t }}</code></template>)</template></template>...</span>
    </div>
</template>

<style scoped>
.working-assistant-message {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-style: italic;
    font-size: var(--wa-font-size-m);
}

code {
    background: var(--wa-color-neutral-fill-quiet);
    border-radius: var(--wa-border-radius-s);
    padding: 0 var(--wa-space-3xs);
    font-size: 0.95em;
}

/* Awaiting-user indicator: amber hand with the slow pending pulse (matches the
   session-list pending indicator and the orchestration awaiting state), set
   apart from the blue animated "thinking" robot. */
.working-assistant-message__awaiting {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-s);
    animation: awaiting-pulse 1.5s ease-in-out infinite;
}

@keyframes awaiting-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}
</style>
