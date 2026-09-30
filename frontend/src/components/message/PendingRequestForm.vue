<script setup>
// PendingRequestForm.vue - Thin shell for pending request forms.
//
// Owns only:
// - The card wrapper + wa-divider
// - The shared header (icon + title + count badge + expand toggle)
// - Per-provider body routing via <component :is="bodyComponent" />
// - The isResponding guard + the provider-agnostic respondToPendingRequest dispatch
//   (triggered by the body's @submit event)

import { ref, computed, watch, useId, nextTick } from 'vue'
import { getProviderLabel, respondToPendingRequest } from '../../providers'
import { useDataStore } from '../../stores/data'
import { PROVIDER } from '../../constants'
import { useFooterBlockMotion } from '../../composables/useFooterMotion.js'
import AppTooltip from '../ui/AppTooltip.vue'
import CollapsedBar from './CollapsedBar.vue'
import ClaudePendingRequestBody from '../session/detail/items/claude_code/PendingRequestBody.vue'
import CodexPendingRequestBody from '../session/detail/items/codex/PendingRequestBody.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    pendingRequest: {
        type: Object,
        required: true
    },
    /**
     * Total number of pending requests for this session, including this one.
     * When > 1, a counter badge is shown to indicate that more requests are queued
     * behind the current one (parallel concurrency-safe tools like Read + Glob can
     * each have their own permission ask within a single assistant turn).
     */
    pendingCount: {
        type: Number,
        default: 1
    }
})

// `expand` fires when the request is opened (restored, or a new request takes the
// slot), so the parent can reduce the composer (at most one footer panel expanded).
const emit = defineEmits(['request-open', 'request-collapse'])

// Number of additional pending requests waiting behind this one (>= 0).
const extraPendingCount = computed(() => Math.max(0, props.pendingCount - 1))

// Human-readable provider label for the current session, used in the form header.
const dataStore = useDataStore()
const providerLabel = computed(() => getProviderLabel(dataStore.getSession(props.sessionId)?.provider))

// Wire-key provider for the current session, used to route responses
// through the provider-agnostic dispatcher.
const provider = computed(() => dataStore.getSession(props.sessionId)?.provider)

// Whether a response has been sent and we're waiting for the store to clear the pending request
const isResponding = ref(false)

// Display size of the form, as a single mutually-exclusive state:
//   'normal'    — default, capped at 50dvh
//   'minimized' — header only (body hidden), to free room to read the conversation above
//   'maximized' — fills the whole session area
// The two toggle buttons below each flip between their own extreme and 'normal'
// (window-controls style); the single enum guarantees we can never be both
// minimized and maximized at once.
const viewState = ref('normal')
const isMinimized = computed(() => viewState.value === 'minimized')
const isMaximized = computed(() => viewState.value === 'maximized')

// Unique IDs for tooltip anchoring on the size toggle buttons
const minimizeToggleId = useId()
const maximizeToggleId = useId()

/**
 * Reduce the form to its single-line bar (the conversation/composer get the room).
 * Independent of the composer: minimizing here never expands anything else.
 */
function minimize() {
    viewState.value = 'minimized'
}

/**
 * Restore the form from its minimized bar back to the normal size.
 * Opening the request reduces the composer (at most one expanded).
 */
function restore() {
    viewState.value = 'normal'
}

/**
 * Restore from the minimized bar only — no-op in normal/maximized states.
 * Used by the hybrid terminal block's pending badge to bring the form back.
 */
function restoreIfMinimized() {
    if (isMinimized.value) restore()
}

// ── Focus on (accordion-driven) open ─────────────────────────────────────────
// Order-independent, like the composer's requestFocus: if the form is still
// minimized when asked, defer until the restore watch shows the body; otherwise
// focus now. Targets the body's designated primary control — both provider
// bodies mark it with `.auto-focused` (Approve button / first option card /
// deny input) — and falls back to the first focusable control.
const rootRef = ref(null)
const FOCUSABLE_SELECTOR = 'a[href], button, input:not([type="hidden"]), textarea, select, '
    + 'wa-button, wa-input, wa-textarea, wa-select, wa-radio, wa-checkbox, '
    + '[tabindex]:not([tabindex="-1"])'
let wantsFocus = false
function focusBodyNow() {
    // Retry: the caller may invoke this right after a cross-tab navigation
    // (Alt+Shift+PageUp from another session tab), where the chat panel is still
    // display:none on the first try and focus() is a no-op — keep trying until it
    // lands inside the form or the budget runs out (~0.5s).
    let tries = 12
    const attempt = () => {
        const root = rootRef.value
        if (root) {
            const target = root.querySelector('.auto-focused') || root.querySelector(FOCUSABLE_SELECTOR)
            if (target) {
                target.focus()
                if (root.contains(document.activeElement)) return
            }
        }
        if (tries-- > 0) setTimeout(attempt, 40)
    }
    nextTick(attempt)
}
function requestFocus() {
    if (isMinimized.value) wantsFocus = true
    else focusBodyNow()
}
watch(isMinimized, (min) => {
    if (!min && wantsFocus) {
        wantsFocus = false
        focusBodyNow()
    }
})

// Expose minimize so the parent can reduce this request when the composer
// opens, restoreIfMinimized for the hybrid pending badge, and requestFocus so
// the accordion can move focus into the form when it opens it.
defineExpose({ minimize, restoreIfMinimized, requestFocus })

/**
 * Toggle between the maximized state and the normal size.
 */
function toggleMaximized() {
    viewState.value = isMaximized.value ? 'normal' : 'maximized'
}

// Footer motion (visual refresh step 7d): the single root animates its height on minimize /
// restore, the form its inset on maximize / restore.
const wrapperRef = ref(null)
const footerShape = computed(() => isMinimized.value ? 'minimized' : 'normal')
useFooterBlockMotion({
    wrapperRef,
    blockRef: rootRef,
    shape: footerShape,
    maximized: isMaximized,
})

// Request type for conditional rendering of the header icon/title
const requestType = computed(() => props.pendingRequest.request_type)

// Icon + title, shared by the normal header and the minimized bar.
const headerIcon = computed(() => requestType.value === 'ask_user_question' ? 'circle-question' : 'shield-halved')
const headerTitle = computed(() => requestType.value === 'ask_user_question'
    ? `${providerLabel.value} needs your input`
    : 'Tool approval requested')

// Route to the appropriate body component based on provider
const bodyComponent = computed(() => {
    if (provider.value === PROVIDER.CODEX) return CodexPendingRequestBody
    if (provider.value === PROVIDER.CLAUDE_CODE) return ClaudePendingRequestBody
    return null
})

/**
 * Dispatch the response when the body emits 'submit'.
 * Sets isResponding to guard against double-submission.
 * @param {Object} payload - The response payload from the body component
 */
function onBodySubmit(payload) {
    if (isResponding.value) return
    isResponding.value = true
    respondToPendingRequest(
        provider.value,
        props.sessionId,
        props.pendingRequest.request_id,
        payload,
    )
}

// Reset isResponding when the pending request changes (e.g., a new one arrives
// after the previous was resolved). The body owns its own internal state reset.
watch(() => props.pendingRequest?.request_id, (newId, oldId) => {
    isResponding.value = false
    // A new request taking over the slot should never inherit the previous
    // request's minimized/maximized size — reset to the default so the new
    // request is always shown at normal size (and never hidden by a leftover
    // minimized state).
    viewState.value = 'normal'
    // Taking over the slot (open this form + reduce the others) is handled by the
    // footer accordion in SessionItemsList, which watches the pending request id.
})
</script>

<template>
    <div ref="wrapperRef" class="footer-block">
    <!--
        Shell-only component. Per-provider rendering lives in the
        ``bodyComponent`` resolved by ``session.provider`` (Claude vs
        Codex). The dynamic ``:is="bodyComponent"`` avoids the SFC
        compiler limitation that bit PR2b when we tried to nest
        ``<template v-else-if>`` branches. The single root is the footer
        motion's wrapper (its height animates).
    -->
    <wa-divider></wa-divider>
    <div ref="rootRef" class="pending-request-form" :class="{ maximized: isMaximized, minimized: isMinimized }">
        <!-- Minimized: a single-line bar identical in look/behaviour to the message
             input's collapsed bar (clickable anywhere + chevron to restore), shown
             in place of the normal header's window controls. Independent of the
             composer: the minimize/restore here never expands anything else. The
             body stays mounted (hidden by CSS) so its in-progress state survives. -->
        <CollapsedBar
            v-if="isMinimized"
            :icon="headerIcon"
            :label="headerTitle"
            expand-tooltip="Expand the request"
            @expand="$emit('request-open')"
        >
            <template #trailing>
                <span
                    v-if="extraPendingCount > 0"
                    class="pending-count-badge"
                    :id="`pending-count-${sessionId}`"
                    role="status"
                >+{{ extraPendingCount }} pending</span>
                <AppTooltip
                    v-if="extraPendingCount > 0"
                    :for="`pending-count-${sessionId}`"
                >{{ extraPendingCount }} more request{{ extraPendingCount > 1 ? 's' : '' }} waiting after this one</AppTooltip>
            </template>
        </CollapsedBar>
        <!-- Normal header (window controls). Title + icon vary on requestType. -->
        <div v-else class="pending-request-header">
            <wa-icon
                :name="headerIcon"
                class="pending-request-icon"
                :class="{ 'question-icon': requestType === 'ask_user_question' }"
            ></wa-icon>
            <span class="pending-request-title">{{ headerTitle }}</span>
            <span
                v-if="extraPendingCount > 0"
                class="pending-count-badge"
                :id="`pending-count-${sessionId}`"
                role="status"
            >+{{ extraPendingCount }} pending</span>
            <AppTooltip
                v-if="extraPendingCount > 0"
                :for="`pending-count-${sessionId}`"
            >{{ extraPendingCount }} more request{{ extraPendingCount > 1 ? 's' : '' }} waiting after this one</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="minimizeToggleId"
                @click="$emit('request-collapse')"
            >
                <wa-icon name="window-minimize" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="minimizeToggleId">Minimize</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="maximizeToggleId"
                @click="toggleMaximized"
            >
                <wa-icon :name="isMaximized ? 'compress' : 'expand'" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="maximizeToggleId">{{ isMaximized ? 'Restore' : 'Maximize' }}</AppTooltip>
        </div>

        <!-- Provider-routed body. Always mounted (hidden by CSS while minimized) so
             in-progress state survives a minimize/restore round-trip. -->
        <component
            :is="bodyComponent"
            v-if="bodyComponent"
            :session-id="sessionId"
            :pending-request="pendingRequest"
            :is-responding="isResponding"
            @submit="onBodySubmit"
        />
    </div>
    </div>
</template>

<style scoped>
/* The single root (footer motion, step 7d): flow-root keeps the card's margin inside, so an
   animated height never clips it. */
.footer-block {
    display: flow-root;
}

wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.pending-request-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-default);
    max-height: 50dvh;
    &.maximized {
        max-height: unset;
        position: absolute;
        inset: 0;
        /* On a main session the composer is always mounted in the same footer and
           is itself positioned (position: relative), so it would otherwise paint
           over the bottom of this overlay. Lift the maximized form above it (still
           below the drag/drop overlay at z-index 100). */
        z-index: 2;
    }
}

/* Minimized: show only the collapsed bar. The body is hidden (not unmounted), so
   the per-provider body component keeps its in-progress state (deny reason draft,
   question selections, edit mode) across a minimize/restore round-trip. Strip the
   card chrome so the bar reads exactly like the message input's collapsed bar.
   :deep() is required because the body root belongs to a child component: Vue's
   scoped CSS only forwards this component's scope id to a *single*-root child
   (Codex body), not to the Claude body's fragment root — so without :deep the
   rule would silently miss the Claude body. We drop the scope requirement on the
   target and hide every direct child of the form that isn't the bar (i.e. the
   body root, whatever provider owns it). The `>` keeps it to direct children. */
.pending-request-form.minimized {
    padding: 0;
    gap: 0;
    max-height: none;
}
.pending-request-form.minimized > :deep(:not(.collapsed-bar)) {
    display: none;
}

/* Not minimized: an inset card with a gradient accent border and a soft accent glow
   (live states design §8.1). The minimized bar keeps its plain look. */
.pending-request-form:not(.minimized) {
    margin: var(--wa-space-xs);
    border: 1px solid transparent;
    border-radius: var(--wa-border-radius-l);
    background:
        linear-gradient(var(--wa-color-surface-default), var(--wa-color-surface-default)) padding-box,
        linear-gradient(120deg, var(--glow-accent), var(--glow-accent-shifted), var(--glow-accent)) border-box;
    box-shadow: 0 0 1rem -0.5rem color-mix(in oklab, var(--glow-accent) 45%, transparent);
}
/* Maximized: edge to edge (a margin would show the transcript around it), no glow (the
   list's overflow clips it). The corners follow the host card's clip
   (SessionItemsList.vue). After the rule above: same specificity, source order decides. */
.pending-request-form.maximized {
    margin: 0;
    border-radius: var(--pending-maximized-radius, 0);
    box-shadow: none;
}

.pending-request-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-warning-60);
    font-weight: 600;
}

.pending-request-title {
    flex: 1;
}

.pending-count-badge {
    display: inline-flex;
    align-items: center;
    background: var(--wa-color-warning-fill-loud);
    color: var(--wa-color-warning-on-loud);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    padding: 2px var(--wa-space-xs);
    border-radius: var(--wa-border-radius-pill);
    line-height: 1;
    white-space: nowrap;
}

.size-toggle-btn {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.question-icon {
    color: var(--wa-color-brand-60);
}

</style>
