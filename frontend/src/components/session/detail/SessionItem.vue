<script setup>
import { computed, ref } from 'vue'
import { PROVIDER, SYNTHETIC_ITEM, DISPLAY_MODE } from '../../../constants'
import { useDataStore } from '../../../stores/data'
import { useSettingsStore } from '../../../stores/settings'
import JsonViewer from '../../json/JsonViewer.vue'
import ClaudeCodeMessage from './items/claude_code/Message.vue'
import ApiError from './items/ApiError.vue'
import CompactSummary from './items/CompactSummary.vue'
import CodexMessage from './items/codex/Message.vue'
import CodexToolUse from './items/codex/ToolUse.vue'
import CodexReasoning from './items/codex/Reasoning.vue'
import CodexImageGeneration from './items/codex/ImageGeneration.vue'
import UnknownEntry from './items/UnknownEntry.vue'
import FailedSendBanner from './items/FailedSendBanner.vue'
import BackgroundWorkStatus from './items/BackgroundWorkStatus.vue'
import MessageTimestamp from './items/MessageTimestamp.vue'
import AppTooltip from '../../ui/AppTooltip.vue'
import CodeCommentsIndicator from '../../ui/CodeCommentsIndicator.vue'

const dataStore = useDataStore()
const settingsStore = useSettingsStore()

const props = defineProps({
    content: {
        type: Object,
        default: null
    },
    kind: {
        type: String,
        default: null
    },
    syntheticKind: {
        type: String,
        default: null
    },
    // Context for store lookups (propagated to Message/ContentList)
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    lineNum: {
        type: Number,
        required: true
    },
    // True when simplified mode already controls this whole item through a
    // session-level GroupToggle. Claude's ContentList must then render the
    // item's blocks directly instead of adding a second internal toggle.
    externallyGrouped: {
        type: Boolean,
        default: false
    },
    // Group props for ALWAYS items with prefix/suffix
    groupHead: {
        type: Number,
        default: null
    },
    groupTail: {
        type: Number,
        default: null
    },
    prefixExpanded: {
        type: Boolean,
        default: false
    },
    suffixExpanded: {
        type: Boolean,
        default: false
    },
    // In conversation mode, the user_message line_num identifying the block this item's
    // detail toggle controls. Always set on the first non-user visual item after the last
    // user_message of a user block. null means no toggle on this item.
    detailToggleFor: {
        type: Number,
        default: null
    },
    blockCommentsCount: {
        type: Number,
        default: 0
    },
    // True when this item is the first of its conversation block (mirrors the
    // `.is-block-start` CSS class). Forwarded to the message renderers, which
    // use it with `isBlockEnd` to decide what an empty assistant message shows.
    isBlockStart: {
        type: Boolean,
        default: false
    },
    // True when this item is the last of its conversation block (mirrors the
    // `.is-block-end` CSS class). Drives the per-block timestamp below.
    isBlockEnd: {
        type: Boolean,
        default: false
    },
    // True when this item is the last real message of a block that still ends
    // with a live placeholder (agent working): it carries the block's time
    // until the turn ends (see markLiveTimestampAnchor).
    isLiveTimestampAnchor: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['toggle-suffix'])

// Conversation mode per-block detail toggle.
// detailToggleFor is set by computeVisualItems to indicate this item should show the toggle.
const showDetailToggle = computed(() => props.detailToggleFor != null)

const isBlockDetailed = computed(() => {
    if (!showDetailToggle.value) return false
    return dataStore.isBlockDetailed(props.sessionId, props.detailToggleFor)
})

function toggleBlockDetailed() {
    dataStore.toggleBlockDetailedMode(props.sessionId, props.detailToggleFor)
}

// Toggle for showing raw JSON
const showJson = ref(false)

// Get the entry type from parsed JSON (for unknown kind display)
const entryType = computed(() => props.content?.type || 'unknown')

const sessionProvider = computed(() => dataStore.getSession(props.sessionId)?.provider)

// Whether this item's session renders in debug mode (global mode or the
// per-session dev-mode override) — drives the "show JSON" toggle visibility.
const isEffectiveDebug = computed(() => dataStore.getEffectiveDisplayMode(props.sessionId) === DISPLAY_MODE.DEBUG)

// Failed-send bubble (messaging pattern): shows the failure banner under the
// regular user-message rendering.
const isFailedSend = computed(() => props.syntheticKind === SYNTHETIC_ITEM.FAILED_USER_MESSAGE.kind)

// USER_TURN bottom status line (background shells, active crons): provider-
// agnostic, rendered here rather than through the provider message renderers.
const isBackgroundWorkStatus = computed(() => props.syntheticKind === SYNTHETIC_ITEM.BACKGROUND_WORK_STATUS.kind)

// Timestamp (date/time) shown at the very bottom of the LAST item of each
// conversation block (the one rendered with `.is-block-end`), so a multi-item
// turn carries a single timestamp at its end rather than one per message.
// Skipped for synthetic / optimistic / streaming placeholders (no real
// timestamp). While the agent works, the block-end is such a placeholder, so
// the last real message of the block shows its own time instead.
const showTimestamp = computed(() =>
    settingsStore.areMessageTimestampsShown
    && (props.isBlockEnd || props.isLiveTimestampAnchor)
    && !props.content?.syntheticKind
    && !!props.content?.timestamp
)

// Track collapsed state for JSON view
const collapsedPaths = ref(new Set())

function toggleCollapse(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
}

function toggleJsonView() {
    showJson.value = !showJson.value
}
</script>

<template>
    <div class="session-item" :class="{ 'effective-debug': isEffectiveDebug }" :data-kind="kind" :data-synthetic-kind="syntheticKind" :data-line-num="lineNum">
        <div><!-- all non-content stuff must be in this div for complex css rules of content stuff assuming they always start at 2nd place-->
            <!-- Detail toggle button for conversation mode (on assistant_message when collapsed,
                 or on first visible item of block when detailed) -->
            <div v-if="showDetailToggle" class="detail-toggle-wrapper">
                <wa-button
                    :id="`detail-toggle-${sessionId}-${detailToggleFor}`"
                    class="detail-toggle"
                    :variant="isBlockDetailed ? 'brand' : 'neutral'"
                    size="small"
                    @click="toggleBlockDetailed"
                >
                    <wa-icon :name="isBlockDetailed ? 'compress' : 'expand'"></wa-icon>
                </wa-button>
                <CodeCommentsIndicator :count="blockCommentsCount" :show-tooltip="false" class="detail-toggle-comments" />
            </div>
            <AppTooltip v-if="showDetailToggle" :for="`detail-toggle-${sessionId}-${detailToggleFor}`">
                {{ isBlockDetailed ? 'Show conversation' : 'Show details' }}
            </AppTooltip>

            <!-- JSON toggle button (visible on hover) -->
            <div class="json-toggle-container">
                <wa-button
                    v-if="!showJson"
                    :id="`json-toggle-${sessionId}-${lineNum}`"
                    class="json-toggle"
                    :variant="showJson ? 'warning' : 'neutral'"
                    size="small"
                    @click="toggleJsonView"
                >
                    <wa-icon name="code"></wa-icon>
                </wa-button>
                <AppTooltip v-if="!showJson" :for="`json-toggle-${sessionId}-${lineNum}`">Show JSON</AppTooltip>
            </div>
        </div>

        <!-- JSON view -->
        <wa-callout variant="neutral" v-if="showJson" class="json-view">
            <wa-button
                :id="`json-toggle-hide-${sessionId}-${lineNum}`"
                class="json-toggle"
                :variant="showJson ? 'warning' : 'neutral'"
                size="small"
                @click="toggleJsonView"
            >
                <wa-icon name="code"></wa-icon>
            </wa-button>
            <AppTooltip :for="`json-toggle-hide-${sessionId}-${lineNum}`">Hide JSON</AppTooltip>
            <wa-tag :id="`line-number-${sessionId}-${lineNum}`" size="small" variant="brand" class="line-number">{{ lineNum }}</wa-tag>
            <AppTooltip :for="`line-number-${sessionId}-${lineNum}`">Line number</AppTooltip>
            <div class="json-tree">
                <JsonViewer
                    :data="content"
                    :path="'root'"
                    :collapsed-paths="collapsedPaths"
                    @toggle="toggleCollapse"
                />
            </div>
        </wa-callout>

        <!-- Formatted view based on kind -->
        <template v-else>
            <BackgroundWorkStatus v-if="isBackgroundWorkStatus" :lines="content?.lines || []" />
            <template v-else-if="sessionProvider === PROVIDER.CLAUDE_CODE">
                <ClaudeCodeMessage
                    v-if="kind === 'user_message' || kind === 'assistant_message'"
                    :data="content"
                    :role="kind === 'user_message' ? 'user' : 'assistant'"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :parent-session-id="parentSessionId"
                    :line-num="lineNum"
                    :externally-grouped="externallyGrouped"
                    :group-head="groupHead"
                    :group-tail="groupTail"
                    :prefix-expanded="prefixExpanded"
                    :suffix-expanded="suffixExpanded"
                    :is-block-start="isBlockStart"
                    :is-block-end="isBlockEnd"
                    @toggle-suffix="emit('toggle-suffix')"
                />
                <ClaudeCodeMessage
                    v-else-if="kind === 'content_items'"
                    :data="content"
                    role="items"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :parent-session-id="parentSessionId"
                    :line-num="lineNum"
                />
                <ApiError
                    v-else-if="kind === 'api_error'"
                    :data="content"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :line-num="lineNum"
                />
                <CompactSummary
                    v-else-if="kind === 'compact_summary'"
                    :content="content?.message?.content || ''"
                    :provider="sessionProvider"
                    :session-id="sessionId"
                    :detail-key="`compact:${lineNum}`"
                />
                <UnknownEntry
                    v-else
                    :type="entryType"
                    :data="content"
                    :session-id="sessionId"
                    :detail-key="`line:${lineNum}`"
                />
            </template>
            <template v-else-if="sessionProvider === PROVIDER.CODEX">
                <CodexMessage
                    v-if="kind === 'user_message' || kind === 'assistant_message'"
                    :data="content"
                    :kind="kind"
                    :session-id="sessionId"
                    :line-num="lineNum"
                    :is-block-start="isBlockStart"
                    :is-block-end="isBlockEnd"
                />
                <CodexToolUse
                    v-else-if="kind === 'tool_use'"
                    :content="content"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :parent-session-id="parentSessionId"
                    :line-num="lineNum"
                />
                <CodexReasoning
                    v-else-if="kind === 'reasoning'"
                    :data="content"
                    :session-id="sessionId"
                    :line-num="lineNum"
                />
                <CodexImageGeneration
                    v-else-if="kind === 'image'"
                    :data="content"
                    :session-id="sessionId"
                    :line-num="lineNum"
                />
                <ApiError
                    v-else-if="kind === 'api_error'"
                    :data="content"
                    :project-id="projectId"
                    :session-id="sessionId"
                    :line-num="lineNum"
                />
                <CompactSummary
                    v-else-if="kind === 'compact_summary'"
                    :content="content?.payload?.message || ''"
                    :provider="sessionProvider"
                    :session-id="sessionId"
                    :detail-key="`compact:${lineNum}`"
                />
                <UnknownEntry
                    v-else
                    :type="entryType"
                    :sub-type="content?.payload?.type || null"
                    :data="content"
                    :session-id="sessionId"
                    :detail-key="`line:${lineNum}`"
                />
            </template>
            <UnknownEntry
                v-else
                :type="entryType"
                :data="content"
                :session-id="sessionId"
                :detail-key="`line:${lineNum}`"
            />

            <!-- Failed-send banner (messaging pattern): reason + Retry /
                 Edit / Delete under the bubble, provider-agnostic -->
            <FailedSendBanner
                v-if="isFailedSend"
                :content="content"
                :project-id="projectId"
                :session-id="sessionId"
            />

            <!-- Per-block timestamp: very last, after the rendered markdown -->
            <MessageTimestamp
                v-if="showTimestamp"
                :timestamp="content.timestamp"
            />
        </template>
    </div>
</template>

<style scoped>
.session-item {
    position: relative;
    font-size: var(--wa-font-size-s);
    line-height: 1.5;
}

.detail-toggle-wrapper {
    position: absolute;
    top: calc(-1 * var(--wa-space-xs));
    right: calc(-1 * var(--wa-space-xs));
    z-index: 2;
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    scale: 0.6;
    transform-origin: top right;
    &:has(.detail-toggle-comments) {
        right: calc(-1 * var(--wa-space-l));
    }
}

.detail-toggle {
    opacity: 0.5;
    transition: opacity 0.2s;
    &::part(label) {
        scale: 1.3;
    }
    &:hover {
        opacity: 1;
    }
}
/* The inactive toggle shows only while the item is hovered (or the toggle has keyboard
   focus), like the markdown toolbar; once active (details shown) it stays visible. */
.session-item:not(:hover) .detail-toggle[variant="neutral"]:not(:focus-within) {
    opacity: 0;
}

.detail-toggle-comments {
    font-size: var(--wa-font-size-xs);
    scale: 1.6;
    transform-origin: center;
}

.json-toggle {
    position: absolute;
    top: -.75em;
    right: -1.75em;
    opacity: 0;
    transition: opacity 0.2s;
    z-index: 1;
    transform-origin: top center;
    scale: 0.5;
    &::part(label) {
        scale: 1.5;
    }
    &[variant="warning"] {
        opacity: 1 !important;
    }
}
.session-item:not(.effective-debug) .json-toggle {
    display: none;
}

.session-item:hover .json-toggle {
    opacity: .5;
}

.session-item:hover .json-toggle:hover {
    opacity: 1;
}

.json-view {
    display: flex;
    background: var(--wa-color-surface-default);

    .json-viewer {
        position: static;
    }
    :deep(.json-viewer-wrap-toggle) {
        top: -1.51em;
        opacity: 1;
    }

}

.line-number {
    position: absolute;
    left: 5px;
    top: 5px;
    translate: -50% -50%;
    height: 2em;
    padding: 0 0.5em;
}

.json-tree {
    flex: 1;
    min-width: 0;
    overflow: auto;
}
</style>


<style>

.session-item:has(.json-view:first-child) {
    padding-top: var(--wa-space-s) !important;
}

.session-items-list {
    container-type: inline-size;
    container-name: session-items-list;
}

.session-items {
    --card-spacing: var(--wa-space-l);
    --max-card-width: 85%;
    /* Breathing room the block navigation leaves above a block it pins to the
       top of the viewport — the same gap the cards keep between them (a user
       card's own top margin, below). It only matters for blocks that carry no
       such margin: everything the agent writes spaces itself with padding, so
       pinned flush it would touch the top edge, while a user card would not.
       Declared as scroll-padding because that is exactly what it means, and
       because it is then readable in pixels whatever the container query says
       --card-spacing is. */
    scroll-padding-top: calc(var(--card-spacing) - var(--main-shadow-size));
    .session-item, .group-toggle {
        max-width: calc(var(--max-card-width) - var(--card-spacing) * 2);
        margin-left: var(--card-spacing);
    }
}

/* Style user message as a whole */
.session-items .session-item[data-kind="user_message"] {
    /* style from wa-card except color that we redefine later */
    border-style: var(--wa-panel-border-style);
    padding-inline: var(--card-spacing);
    border-radius: var(--wa-panel-border-radius);
    border-width: var(--wa-panel-border-width);
    padding: var(--card-spacing);

    width: max-content;
    /* A minimum width, never beyond the maximum the items share (--max-card-width, below): a
       min-width wins over a max-width, so it is capped by the same expression. */
    min-width: min(12rem, calc(var(--max-card-width) - var(--card-spacing) * 2));
    margin:
        calc(var(--card-spacing) - var(--main-shadow-size))  /* size of box-shadow of previous card */
        var(--card-spacing)
        calc(var(--card-spacing) * 2)
        auto;
    /* A lifted bubble: a firm accent border and a shadow that floats it; it stands out of the
       cards around it without recolouring what is inside it (the text stays dark in light mode).
       Light: a pale centre, the edges tinted, a faint inner glow. Dark (below): a strong tint. */
    --user-card-solid: var(--surface-solid, var(--wa-color-surface-default));
    /* Rounded, but for one squared corner (bottom right), in both schemes. */
    border-radius: 18px 18px 4px 18px;
    border-width: 1.5px;
    border-color: color-mix(in oklab, var(--wa-color-brand-60) 72%, transparent);
    background-color: var(--user-card-solid);
    background-image: radial-gradient(120% 140% at 50% 45%, color-mix(in oklab, var(--wa-color-brand-60) 4%, var(--user-card-solid)) 40%, color-mix(in oklab, var(--wa-color-brand-60) 15%, var(--user-card-solid)));
    box-shadow: 0 8px 22px -8px color-mix(in oklab, var(--wa-color-brand-60) 45%, transparent),
        inset 0 0 18px -6px color-mix(in oklab, var(--wa-color-brand-60) 18%, transparent);
}
.wa-dark .session-items .session-item[data-kind="user_message"] {
    border-color: color-mix(in oklab, var(--wa-color-brand-60) 70%, transparent);
    background-image: linear-gradient(180deg, color-mix(in oklab, var(--wa-color-brand-60) 38%, var(--user-card-solid)), color-mix(in oklab, var(--wa-color-brand-60) 26%, var(--user-card-solid)));
    box-shadow: 0 8px 22px -8px color-mix(in oklab, var(--wa-color-brand-60) 80%, transparent),
        inset 0 1px 0 oklch(1 0 0 / 0.15);
}
/* Light only: a solid lit bubble, the accent itself (a little lighter at the top) with white text: the
   only filled surface of the chat. Its other content (quotes, code, links) is not adapted yet. */
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] {
    color: #fff;
    border-color: transparent;
    background-color: transparent;
    background-image: linear-gradient(180deg, oklch(from var(--wa-color-brand-60) calc(l + 0.005) c h), oklch(from var(--wa-color-brand-60) calc(l - 0.06) c h));
    box-shadow: 0 6px 16px -8px color-mix(in oklab, var(--wa-color-brand-60) 80%, transparent),
        inset 0 1px 0 oklch(1 0 0 / 0.35);
}
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .markdown-body {
    color: #fff;
}
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .message-timestamp {
    color: oklch(1 0 0 / 0.78);
}
/* What sits directly on the filled bubble (not in a quote, a container or a code block, which are
   light cards with their own colours): links, inline code, rules and tables read in white. */
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .markdown-body a:not(blockquote *, .md-container *, .code-tools *, pre *) {
    color: #fff;
    text-decoration: underline;
    text-underline-offset: 2px;
}
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .markdown-body code:not(blockquote *, .md-container *, .code-tools *, pre *) {
    background: oklch(1 0 0 / 0.2);
    color: #fff;
}
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .markdown-body hr:not(blockquote *, .md-container *, .code-tools *, pre *) {
    height: 1px;
    border: 0;
    background: oklch(1 0 0 / 0.45);
}
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .markdown-body table:not(blockquote *, .md-container *, .code-tools *, pre *) tr {
    background: transparent;
}
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .markdown-body table:not(blockquote *, .md-container *, .code-tools *, pre *) tr:nth-child(2n) {
    background: oklch(1 0 0 / 0.1);
}
html:not(.wa-dark) .session-items .session-item[data-kind="user_message"] .markdown-body table:not(blockquote *, .md-container *, .code-tools *, pre *) :is(th, td) {
    border-color: oklch(1 0 0 / 0.35);
}
/* More room above a user message that follows another item; the first message of the session
   needs none. */
.session-items .virtual-scroller-item + .virtual-scroller-item .session-item[data-kind="user_message"] {
    margin-top: calc(var(--card-spacing) * 2.25 - var(--main-shadow-size));
}

.session-items {
    --markdown-toolbar-offset: -2.5rem;
    /* The assistant block keeps only a right padding: its toolbars move left by half of
       it. */
    --assistant-markdown-toolbar-offset: calc(var(--markdown-toolbar-offset) - var(--card-spacing) / 2);
}
/* React to the chat's own width (center zone / dock), not the viewport. */
@container session-items-list (width < 40rem) {
    .session-items {
        --markdown-toolbar-offset: -3rem;
    }
}

.session-items .session-item[data-kind="user_message"] .text-content > .markdown-content-wrapper > .markdown-toolbar {
    right: calc(100% + var(--markdown-toolbar-offset) + 1.25rem);
    top: -1rem;
    width: 6rem;
}

.session-items .session-item[data-kind="assistant_message"] > .text-content > .markdown-content-wrapper > .markdown-toolbar {
    right: auto;
    left: calc(100% + var(--assistant-markdown-toolbar-offset));
    top: 0;
    width: 6rem;
    display: flex;
    justify-content: flex-end;
}

/* Hide the floating markdown toolbar (raw toggle + copy) when the session
   renders in debug view — global debug mode or the per-session override.
   !important overrides the `display: flex` set by the positioning rules above. */
.session-item.effective-debug .markdown-content-wrapper .markdown-toolbar {
    display: none !important;
}

.session-items .session-item[data-kind="content_items"] {
    .thinking-body, .compact-summary-body {
        > .markdown-content-wrapper > .markdown-toolbar {
            right: auto;
            left: calc(100% + var(--assistant-markdown-toolbar-offset));
            top: 0;
            width: 7rem;
            display: flex;
            justify-content: flex-end;
        }
    }
}

/* Codex reasoning renders as its own item kind: same placement as Thinking. */
.session-items .session-item[data-kind="reasoning"] .reasoning-body > .markdown-content-wrapper > .markdown-toolbar {
    right: auto;
    left: calc(100% + var(--assistant-markdown-toolbar-offset));
    top: 0;
    width: 7rem;
    display: flex;
    justify-content: flex-end;
}

/* Style assistant messages in parts, the whole looking like a wa-card
   But as we have many items, the first one handles the top, the last one handles the bottom, and all have left/right sides
 */
.session-items {
    .virtual-scroller-item:not(:has(.session-item[data-kind="user_message"])) {

        /* define our own properties */
        /* No card chrome: the assistant block sits straight on the session background
           (no border, no inner spacing but on the right; background and shadow cleared
           below). The card
           structure stays, so the per-row variables keep working (e.g. the gap above
           the working pill). */
        --assistant-card-border-width: 0;
        --assistant-card-border-radius: var(--wa-panel-border-radius);
        --assistant-card-spacing: 0;

        /* by default no radius because default style is only for "inner" (not first/last) rows */
        --assistant-card-border-top-left-radius: 0;
        --assistant-card-border-top-right-radius: 0;
        --assistant-card-border-bottom-left-radius: 0;
        --assistant-card-border-bottom-right-radius: 0;

        /* by default no top/bottom border because default style is only for "inner" (not first/last) rows */
        --assistant-card-border-top-width: 0;
        --assistant-card-border-bottom-width: 0;

        /* by default no block spacing because default style is only for "inner" (not first/last) rows */
        --assistant-card-top-spacing: 0;
        --assistant-card-bottom-spacing: 0;

        /* by default no shadow because default style is only for "inner" (not last) rows.
           A transparent layer, not `none`: the row's box-shadow is a list (shadow, edge). */
        --assistant-card-shadow: 0 0 transparent;
        /* Dark-mode top edge: only on the first row (set with the top radius below). */
        --assistant-card-edge: 0 0 transparent;

        /* To be able to apply some style differently on components for items at start/middle/end */
        --content-card-start-item: 0;
        --content-card-inner-item: 1;
        --content-card-end-item: 0;
        --content-card-not-start-item: 1;
        --content-card-not-inner-item: 0;
        --content-card-not-end-item: 1;

        & > .session-item, & > .group-toggle {

            /* common styles */
            --assistant-card-bg-color: var(--assistant-card-base-color);
            --xxxassistant-card-bg-color: oklch(from var(--assistant-card-base-color) calc(l*1.025) c h);
            --assistant-card-border-color: oklch(from var(--assistant-card-bg-color) calc(l / 1.05) c h);
            /* Transparent: the session background shows through. */
            background: transparent;
            border-color: var(--assistant-card-border-color);
            border-style: var(--wa-panel-border-style);
            /* No card shadow (a transparent layer: the row's box-shadow is a list). */
            --assistant-card-default-shadow: 0 0 transparent;

            border-radius:
                var(--assistant-card-border-top-left-radius)
                var(--assistant-card-border-top-right-radius)
                var(--assistant-card-border-bottom-right-radius)
                var(--assistant-card-border-bottom-left-radius);

            border-width:
                var(--assistant-card-border-top-width)
                var(--assistant-card-border-width)
                var(--assistant-card-border-bottom-width)
                var(--assistant-card-border-width);

            /* The right padding keeps the card's spacing. */
            padding:
                var(--assistant-card-top-spacing)
                var(--card-spacing)
                var(--assistant-card-bottom-spacing)
                var(--assistant-card-spacing);

            box-shadow: var(--assistant-card-shadow), var(--assistant-card-edge);

        }
    }
    .virtual-scroller-item:has(.session-item[data-kind="user_message"]),
    .virtual-scroller-item:has(.day-separator) {
        + .virtual-scroller-item:not(:has(.session-item[data-kind="user_message"])) {
            /* First non-user after a user message (or a day separator, which
               breaks the direct user→assistant adjacency) */
            .session-item.is-block-start, .group-toggle.is-block-start {
                --content-card-start-item: 1;
                --content-card-inner-item: 0;
                --content-card-not-start-item: 0;
                --content-card-not-inner-item: 1;

                --assistant-card-border-top-left-radius: var(--assistant-card-border-radius);
                --assistant-card-border-top-right-radius: var(--assistant-card-border-radius);
                --assistant-card-border-top-width: var(--assistant-card-border-width);
                --assistant-card-top-spacing: var(--assistant-card-spacing);
            }
        }
    }

    .virtual-scroller-item:not(:has(.session-item[data-kind="user_message"])) {
        /* Last non-user wih nothing after */
        &:not(:has(+ .virtual-scroller-item)),
        /* Last non-user before a user message */
        &:has(+ .virtual-scroller-item .session-item[data-kind="user_message"]),
        /* Last non-user before a day separator (which precedes the next block) */
        &:has(+ .virtual-scroller-item .day-separator)
        {
            .session-item.is-block-end, .group-toggle.is-block-end {
                --content-card-end-item: 1;
                --content-card-inner-item: 0;
                --content-card-not-end-item: 0;
                --content-card-not-inner-item: 1;

                --assistant-card-border-bottom-left-radius: var(--assistant-card-border-radius);
                --assistant-card-border-bottom-right-radius: var(--assistant-card-border-radius);
                --assistant-card-border-bottom-width: var(--assistant-card-border-width);
                --assistant-card-bottom-spacing: var(--assistant-card-spacing);
                --assistant-card-shadow: var(--assistant-card-default-shadow);
                /* For the shadow to appear on the last element with virtual scroller "cropping" if
                   we don't have this. */
                margin-bottom: calc(var(--depth-card-reach) + 1px);
            }
        }
    }

    .virtual-scroller-item:has(.session-item[data-kind="user_message"]) {
        + .virtual-scroller-item:has( > .day-separator) {
            .day-separator {
                margin-top: calc(-1 * (var(--card-spacing) - var(--main-shadow-size)) / 2);
            }
        }
    }
    .virtual-scroller-item:has(> .day-separator) {
        &:has(+ .virtual-scroller-item .session-item[data-kind="user_message"]) {
            .day-separator {
                margin-bottom: calc(-1 * (var(--card-spacing) - var(--main-shadow-size)) / 2);
            }
        }
    }

}

.session-items .session-item > *:nth-child(n + 2):not(:last-child) {    /* 1 is json toggle and its tooltip */
    margin-bottom: var(--wa-space-s);
}
/* Assistant side: half the gap above the block's timestamp. */
.session-items .session-item:not([data-kind="user_message"]) > *:nth-child(n + 2):has(+ .message-timestamp) {
    margin-bottom: calc(var(--wa-space-s) / 2);
}

/* Handle many wa-details one after the other */
wa-details {
    &:has(+wa-details) {
        padding-bottom: 0;
        &::part(base) {
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
            border-bottom-width: 0;
        }
    }
    & + wa-details {
        padding-top: 0;
        &::part(base) {
            border-top-left-radius: 0;
            border-top-right-radius: 0;
        }
    }
}
/* A tool card joined to the next one casts no shadow: only the last card of a run does.
   Needs .item-details to beat `wa-details.item-details::part(base)` below (0,1,3 > 0,1,2). */
wa-details.item-details:has(+ wa-details)::part(base) {
    box-shadow: 0 0 transparent;
}
/* Same but in different items */
.session-items {
    .virtual-scroller-item:has(wa-details.item-details:last-child) {
        &:has(
            + .virtual-scroller-item wa-details.item-details:nth-child(2)  /* 1 is json toggle and its tooltip */
        ),
        &:not(:has(+ .virtual-scroller-item)):not(:has(.session-item.is-block-end)) {
            wa-details.item-details:last-child {
                padding-bottom: 0;
                &::part(base) {
                    border-bottom-left-radius: 0;
                    border-bottom-right-radius: 0;
                    border-bottom-width: 0;
                    box-shadow: 0 0 transparent;
                }
            }
        }
        & + .virtual-scroller-item
        wa-details.item-details:nth-child(2) {  /* 1 is json toggle and its tooltip */
            padding-top: 0;
            &::part(base) {
                border-top-left-radius: 0;
                border-top-right-radius: 0;
            }
        }
    }
    .virtual-scroller-spacer-before + .virtual-scroller-item > .session-item:not(.is-block-start) > wa-details.item-details:nth-child(2) {
        padding-top: var(--spacing-top);
    }

}

/* Common style for wa-detail and wa-detail.items-details */
wa-details {
    /* Fall back to --wa-space-m when --card-spacing is undefined (e.g. a
       wa-details outside a session card, like the hybrid dialog): without the
       fallback, var(--card-spacing) resolves to nothing, the whole min() is
       invalid, and --spacing collapses to 0 — leaving the details with no
       padding. */
    --spacing: min(var(--card-spacing, var(--wa-space-m)), var(--wa-space-m));
}

wa-details.item-details {
    font-size: var(--wa-font-size-s);
    --spacing-top: calc(var(--content-card-not-start-item, 1) * var(--spacing));
    --spacing-bottom: calc(var(--content-card-not-end-item, 1) * var(--spacing));
    padding-top: var(--spacing-top);
    padding-bottom: var(--spacing-bottom);
    /* Downward only, like chat cards: the next card of a joined run touches its top edge.
       No dark top edge (it would draw a seam at each join). */
    &::part(base) {
        box-shadow: var(--depth-card);
    }
    --header-padding: 6px;

    &::part(content) {
        padding-top: 0;
    }

    &[disabled]::part(header) {
        cursor: default;
    }

    &::part(header) {
        user-select: text;
        -webkit-user-select: text;
    }
    &:has(.items-details-summary-right > wa-button) {
        &::part(header) {
            padding-block: var(--header-padding);
        }
    }
    &:has(.items-details-summary-right wa-button) {
        &::part(header) {
            padding-right: var(--header-padding);
        }
    }

    .items-details-summary {
        display: flex;
        min-width: 0; /* Allow shrinking as flex item in wa-details shadow DOM header */
        align-items: center;
        column-gap: var(--wa-space-m);
        width: 100%;

        wa-button {
            margin-left: auto; /* Stay right-aligned when wrapped */
        }
        wa-spinner {
            font-size: 1.2em;
        }
    }
    .items-details-summary-left {
        display: inline-flex;
        align-items: center;
        flex-wrap: wrap;
        gap: var(--wa-space-xs);
        max-width: 100%; /* Constrain to parent width so text can wrap */
        /* Without this, `min-width: auto` floors the block at its min-content
           width — and a `nowrap` summary (DescriptionSummary's `truncate`
           mode) contributes its FULL text width there, whatever its own
           `overflow: hidden` / `min-width: 0`. Clamped by `max-width: 100%`,
           that floor becomes the whole row, leaving nothing for the right
           block: the spinner then renders outside the card while the text
           still ellipsises, hiding the cause. */
        min-width: 0;
    }
    /* Compact right-hand indicators (running spinner, diff stats, error icon)
       must share the summary's first line. With the default `flex-basis: auto`
       the left block's hypothetical size fills the whole row, so the narrow
       container's `flex-wrap: wrap` (see the container query below) breaks the
       line and drops them underneath. A zero basis keeps the line unbroken and
       lets the left block take whatever is left. Rows whose right block holds a
       button keep the historical wrapping — the `row-gap` rule below styles it. */
    &:not(:has(.items-details-summary-right wa-button)) {
        .items-details-summary-left {
            flex: 1 1 0;
        }
    }
    &:has(.items-details-summary-right > wa-button) {
        .items-details-summary {
            row-gap: var(--wa-space-s);
        }
        .items-details-summary-left {
            margin-block: calc(var(--spacing) - 2 * var(--header-padding));
        }
    }

    .items-details-summary-right {
        margin-left: auto;
        display: flex;
        align-items: center;
        column-gap: var(--wa-space-xs);
        flex-shrink: 0; /* Indicators and buttons keep their place; the left block shrinks */

        & > :not(wa-button, wa-icon, wa-spinner):last-child {
            margin-right: var(--spacing);
        }
    }

    .items-details-summary-name {
        color: var(--wa-color-text-normal);
    }
    .items-details-summary-separator {
        color: var(--wa-color-text-quiet);
    }
    .items-details-summary-description {
        color: var(--wa-color-text-normal);
        font-weight: normal;
        overflow-wrap: anywhere; /* Break long strings that have no spaces */
    }
}

/* checked "toggles" (usually) before wa-details must have some removed space to keep spacing harmonious */
.group-toggle:not(:has(+.session-item > .json-view:first-child)) wa-switch:state(checked) {
    margin-bottom: calc(var(--card-spacing) * -1/4);
    z-index: 1;
}

/* Two successive "markdown" blocks should have a space between them to improve readability */
.session-items {
    .virtual-scroller-item:has( > .session-item[data-kind="assistant_message"] > .text-content:last-child)
    + .virtual-scroller-item > .session-item[data-kind="assistant_message"] > .text-content:nth-child(2) {
        padding-top: var(--wa-space-xl);
    }
    /* The working pill after a text block: same gap, as the card's own top padding (a pill
       margin would collapse out of the card). */
    .virtual-scroller-item:has( > .session-item[data-kind="assistant_message"] > .text-content:last-child)
    + .virtual-scroller-item > .session-item[data-kind="assistant_message"]:has(> .working-assistant-message:nth-child(2)) {
        --assistant-card-top-spacing: var(--wa-space-xl);
    }
    /* The USER_TURN background-work status line, or a live placeholder while
       the agent works (see markLiveTimestampAnchor), under a timestamped
       message: the time keeps its own line (see MessageTimestamp), the next
       row starts below it. */
    .virtual-scroller-item:has( > .session-item > .message-timestamp:last-child)
    + .virtual-scroller-item > .session-item:is(
        [data-synthetic-kind="background-work-status"],
        [data-synthetic-kind="starting-assistant-message"],
        [data-synthetic-kind="working-assistant-message"],
        [data-synthetic-kind="streaming-block"]
    ) > .text-content:nth-child(2) {
        padding-top: var(--wa-space-s);
    }
}

/* Responsive styles for narrow containers */
@container session-items-list (width <= 50rem) {
    .session-items {
        --max-card-width: 95%;
    }
}
@container session-items-list (width <= 40rem) {
    .session-items {
        --card-spacing: var(--wa-space-m) !important;
    }
}
@container session-items-list (width <= 25rem) {
    .session-items {
        --card-spacing: var(--wa-space-s) !important;
    }
}
@container session-items-list (width <= 25rem) {
    .items-details-summary {
        flex-wrap: wrap;
    }
}

.session-item {
    .jhv-pre, .jhv-markdown .markdown-body {
        max-height: 20rem;
    }
}

</style>
