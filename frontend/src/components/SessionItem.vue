<script setup>
import { computed, ref } from 'vue'
import JsonViewer from './JsonViewer.vue'
import Message from './items/Message.vue'
import ApiError from './items/ApiError.vue'
import UnknownEntry from './items/UnknownEntry.vue'

const props = defineProps({
    content: {
        type: String,
        required: true
    },
    kind: {
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
    }
})

const emit = defineEmits(['toggle-suffix'])

// Toggle for showing raw JSON
const showJson = ref(false)

// Parse JSON content
const parsedContent = computed(() => {
    try {
        return JSON.parse(props.content)
    } catch {
        return { error: 'Invalid JSON', raw: props.content }
    }
})

// Get the entry type from parsed JSON (for unknown kind display)
const entryType = computed(() => parsedContent.value?.type || 'unknown')

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
    <div class="session-item" :data-kind="kind">
        <!-- JSON toggle button (visible on hover) -->
        <wa-button
            v-if="!showJson"
            :id="`json-toggle-${lineNum}`"
            class="json-toggle"
            :variant="showJson ? 'warning' : 'neutral'"
            size="small"
            @click="toggleJsonView"
        >
            <wa-icon name="code"></wa-icon>
        </wa-button>
        <wa-tooltip v-if="!showJson" :for="`json-toggle-${lineNum}`">Show JSON</wa-tooltip>

        <!-- JSON view -->
        <wa-callout appearance="outlined" variant="neutral" v-if="showJson" class="json-view">
            <wa-button
                :id="`json-toggle-hide-${lineNum}`"
                class="json-toggle"
                :variant="showJson ? 'warning' : 'neutral'"
                size="small"
                @click="toggleJsonView"
            >
                <wa-icon name="code"></wa-icon>
            </wa-button>
            <wa-tooltip :for="`json-toggle-hide-${lineNum}`">Hide JSON</wa-tooltip>
            <wa-tag :id="`line-number-${lineNum}`" size="small"  appearance="filled-outlined" variant="brand" class="line-number">{{ lineNum }}</wa-tag>
            <wa-tooltip :for="`line-number-${lineNum}`">Line number</wa-tooltip>
            <div class="json-tree">
                <JsonViewer
                    :data="parsedContent"
                    :path="'root'"
                    :collapsed-paths="collapsedPaths"
                    @toggle="toggleCollapse"
                />
            </div>
        </wa-callout>

        <!-- Formatted view based on kind -->
        <template v-else>
            <Message
                v-if="kind === 'user_message' || kind === 'assistant_message'"
                :data="parsedContent"
                :role="kind === 'user_message' ? 'user' : 'assistant'"
                :project-id="projectId"
                :session-id="sessionId"
                :parent-session-id="parentSessionId"
                :line-num="lineNum"
                :group-head="groupHead"
                :group-tail="groupTail"
                :prefix-expanded="prefixExpanded"
                :suffix-expanded="suffixExpanded"
                @toggle-suffix="emit('toggle-suffix')"
            />
            <Message
                v-else-if="kind === 'content_items'"
                :data="parsedContent"
                role="items"
                :project-id="projectId"
                :session-id="sessionId"
                :parent-session-id="parentSessionId"
                :line-num="lineNum"
            />
            <ApiError
                v-else-if="kind === 'api_error'"
                :data="parsedContent"
            />
            <UnknownEntry
                v-else
                :type="entryType"
                :data="parsedContent"
            />
        </template>
    </div>
</template>

<style scoped>
.session-item {
    position: relative;
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    line-height: 1.5;
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
body:not([data-display-mode="debug"]) .json-toggle {
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
    margin:
        calc(var(--card-spacing) - 4px)  /* size of box-shadow of previous card */
        var(--card-spacing)
        var(--card-spacing)
        auto;
    --user-card-bg-color: oklch(from var(--user-card-base-color) calc(l * 1.00) c h);
    --user-card-border-color: oklch(from var(--user-card-bg-color) calc(l / 1.05) c h);
    background-color: var(--user-card-bg-color);
    border-color: var(--user-card-border-color);
    box-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) var(--wa-shadow-blur-s) var(--wa-shadow-spread-s) var(--user-card-border-color);
}

/* Style assistant messages in parts, the whole looking like a wa-card
   But as we have many items, the first one handles the top, the last one handles the bottom, and all have left/right sides
 */
.session-items {
    .virtual-scroller-item:not(:has(.session-item[data-kind="user_message"])) {

        /* define our own properties */
        --assistant-card-border-width: var(--wa-panel-border-width);
        --assistant-card-border-radius: var(--wa-panel-border-radius);
        --assistant-card-spacing: var(--card-spacing);

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

        /* by default no shadow because default style is only for "inner" (not last) rows */
        --assistant-card-shadow: none;

        /* To be able to apply some style differently on components for items at start/middle/end */
        --content-card-start-item: 0;
        --content-card-inner-item: 1;
        --content-card-end-item: 0;
        --content-card-not-start-item: 1;
        --content-card-not-inner-item: 0;
        --content-card-not-end-item: 1;

        & > .session-item, & > .group-toggle {

            /* common styles */
            --assistant-card-bg-color: oklch(from var(--assistant-card-base-color) calc(l*1.025) c h);
            --assistant-card-border-color: oklch(from var(--assistant-card-bg-color) calc(l / 1.05) c h);
            background: var(--assistant-card-bg-color);
            border-color: var(--assistant-card-border-color);
            border-style: var(--wa-panel-border-style);
            padding-inline: var(--card-spacing);
            --assistant-card-default-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) var(--wa-shadow-blur-s) var(--wa-shadow-spread-s) var(--assistant-card-border-color);

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

            padding:
                var(--assistant-card-top-spacing)
                var(--assistant-card-spacing)
                var(--assistant-card-bottom-spacing)
                var(--assistant-card-spacing);

            box-shadow: var(--assistant-card-shadow);

        }
    }
    .virtual-scroller-item:has(.session-item[data-kind="user_message"]) {
        + .virtual-scroller-item:not(:has(.session-item[data-kind="user_message"])) {
            /* First non-user after a user message */
            .session-item:first-child, .group-toggle:first-child {
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
        &:has(+ .virtual-scroller-item .session-item[data-kind="user_message"])
        {
            .session-item:last-child, .group-toggle:last-child {
                --content-card-end-item: 1;
                --content-card-inner-item: 0;
                --content-card-not-end-item: 0;
                --content-card-not-inner-item: 1;

                --assistant-card-border-bottom-left-radius: var(--assistant-card-border-radius);
                --assistant-card-border-bottom-right-radius: var(--assistant-card-border-radius);
                --assistant-card-border-bottom-width: var(--assistant-card-border-width);
                --assistant-card-bottom-spacing: var(--assistant-card-spacing);
                --assistant-card-shadow: var(--assistant-card-default-shadow);
                margin-bottom: 5px; /* For the shadow to appear on the last element with virtual scroller "cropping" if we don't have this */;
            }
        }
    }

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
/* Same but in different items */
.session-items {
    .virtual-scroller-item:has(wa-details.item-details:last-child) {
        &:has(
            + .virtual-scroller-item wa-details.item-details:nth-child(3)  /* 1 is json toggle and 2 its tooltip */
        ) wa-details.item-details:last-child {
            padding-bottom: 0;
            &::part(base) {
                border-bottom-left-radius: 0;
                border-bottom-right-radius: 0;
                border-bottom-width: 0;
            }
        }
        & + .virtual-scroller-item
        wa-details.item-details:nth-child(3) {  /* 1 is json toggle and 2 its tooltip */
            padding-top: 0;
            &::part(base) {
                border-top-left-radius: 0;
                border-top-right-radius: 0;
            }
        }
    }
}

/* Common style for wa-detail and wa-detail.items-details */
wa-details {
    --spacing: min(var(--card-spacing), var(--wa-space-m));
}

wa-details.item-details {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    --spacing-top: calc(var(--content-card-not-start-item, 1) * var(--spacing));
    --spacing-bottom: calc(var(--content-card-not-end-item, 1) * var(--spacing));
    padding-top: var(--spacing-top);
    padding-bottom: var(--spacing-bottom);

    .items-details-summary {
        display: inline;
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
    }
}

/* checked "toggles" (usually) before wa-details must have some removed space to keep spacing harmonious */
.group-toggle:not(:has(+.session-item > .json-view:first-child)) wa-switch:state(checked) {
    margin-bottom: calc(var(--card-spacing) * -1/4);
    z-index: 1;
}

/* Responsive styles for narrow containers */
@container session-items-list (width <= 800px) {
    .session-items {
        --max-card-width: 95%;
    }
}
@container session-items-list (width <= 600px) {
    .session-items {
        --card-spacing: var(--wa-space-m) !important;
    }
}
@container session-items-list (width <= 400px) {
    .session-items {
        --card-spacing: var(--wa-space-s) !important;
    }
    wa-details.item-details {
        .items-details-summary {
            flex-direction: column;
            .items-details-summary-left {
                 & + :not(wa-button) {
                    align-self: center;
                    translate: -2em 0; /* due to arrow and spacing on the left of the summary */
                }
                & + wa-button {
                    align-self: end;
                    &::part(base) {
                        margin-bottom: var(--card-spacing);
                    }
                }
            }
        }
    }
}
</style>
