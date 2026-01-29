<script setup>
import { computed, ref } from 'vue'
import JsonNode from './JsonNode.vue'
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
            class="json-toggle"
            :variant="showJson ? 'warning' : 'neutral'"
            size="small"
            @click="toggleJsonView"
            :title="showJson ? 'Hide JSON' : 'Show JSON'"
        >
            <wa-icon name="code"></wa-icon>
        </wa-button>

        <!-- JSON view -->
        <div v-if="showJson" class="json-view">
            <div class="line-number">{{ lineNum }}</div>
            <div class="json-tree">
                <JsonNode
                    :data="parsedContent"
                    :path="'root'"
                    :collapsed-paths="collapsedPaths"
                    @toggle="toggleCollapse"
                />
            </div>
        </div>

        <!-- Formatted view based on kind -->
        <template v-else>
            <Message
                v-if="kind === 'user_message' || kind === 'assistant_message'"
                :data="parsedContent"
                :role="kind === 'user_message' ? 'user' : 'assistant'"
                :project-id="projectId"
                :session-id="sessionId"
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
    top: 0;
    right: 0;
    opacity: 0;
    transition: opacity 0.2s;
    z-index: 1;
}

.session-item:hover .json-toggle {
    opacity: 1;
}

.session-item:hover .json-toggle:hover {
    opacity: 1;
}

.json-view {
    display: flex;
    gap: var(--wa-space-m);
}

.line-number {
    flex-shrink: 0;
    width: 40px;
    text-align: right;
    color: var(--wa-color-text-subtle);
    font-weight: 500;
    user-select: none;
}

.json-tree {
    flex: 1;
    min-width: 0;
    overflow: auto;
}
</style>


<style>
.session-items .item-wrapper {
    & > .session-item, & > .group-toggle {
        max-width: 90%;
    }
}

/* Style user message as a whole */
.session-items .session-item[data-kind="user_message"] {
    /* style from wa-card except color that we redefine later */
    border-style: var(--wa-panel-border-style);
    padding-inline: var(--wa-space-l);
    border-radius: var(--wa-panel-border-radius);
    border-width: var(--wa-panel-border-width);
    padding: var(--wa-space-l);

    width: max-content;
    margin-left: auto;
    margin-top: var(--wa-space-l);
    margin-bottom: var(--wa-space-l);
    --user-card-border-color: var(--wa-color-blue-95);
    --user-card-bg-color: oklch(from var(--user-card-border-color) calc(l * 1.05) c h);
    background-color: var(--user-card-bg-color);
    border-color: var(--user-card-border-color);
    box-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) var(--wa-shadow-blur-s) var(--wa-shadow-spread-s) var(--user-card-border-color);
}

/* Style assistant messages in parts, the whole looking like a wa-card
   But as we have many items, the first one handles the top, the last one handles the bottom, and all have left/right sides
 */
.session-items {
    .vue-recycle-scroller__item-view:not(:has(.session-item[data-kind="user_message"])) {

        /* define our own properties */
        --assistant-card-border-width: var(--wa-panel-border-width);
        --assistant-card-border-radius: var(--wa-panel-border-radius);
        --assistant-card-default-shadow: var(--wa-shadow-s);
        --assistant-card-spacing: var(--wa-space-l);

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

        & > .item-wrapper {

            & > .session-item, & > .group-toggle {
                /* common styles */
                --assistant-card-bg-color: oklch(from var(--wa-color-gray-95) calc(l*1.025) c h);
                background: var(--assistant-card-bg-color);
                border-color: var(--wa-color-surface-border);
                border-style: var(--wa-panel-border-style);
                padding-inline: var(--wa-space-l);

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
    }
    .vue-recycle-scroller__item-view:has(.session-item[data-kind="user_message"]) {
        + .vue-recycle-scroller__item-view:not(:has(.session-item[data-kind="user_message"])) {
            /* First non-user after a user message */
            .session-item:first-child, .group-toggle:first-child {
                --assistant-card-border-top-left-radius: var(--assistant-card-border-radius);
                --assistant-card-border-top-right-radius: var(--assistant-card-border-radius);
                --assistant-card-border-top-width: var(--assistant-card-border-width);
                --assistant-card-top-spacing: var(--assistant-card-spacing);
            }
        }
    }

    .vue-recycle-scroller__item-view:not(:has(.session-item[data-kind="user_message"])) {
        /* Last non-user wih nothing after */
        &:not(:has(+ .vue-recycle-scroller__item-view)),
        /* Last non-user before a user message */
        &:has(+ .vue-recycle-scroller__item-view .session-item[data-kind="user_message"])
        {
            .session-item:last-child, .group-toggle:last-child {
                --assistant-card-border-bottom-left-radius: var(--assistant-card-border-radius);
                --assistant-card-border-bottom-right-radius: var(--assistant-card-border-radius);
                --assistant-card-border-bottom-width: var(--assistant-card-border-width);
                --assistant-card-bottom-spacing: var(--assistant-card-spacing);
                --assistant-card-shadow: var(--assistant-card-default-shadow);;
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
    .vue-recycle-scroller__item-view:has(wa-details.tool-use:last-child) {
        &:has(
            + .vue-recycle-scroller__item-view wa-details.tool-use:nth-child(2)  /* 1 is json toggle */
        ) wa-details.tool-use:last-child {
            padding-bottom: 0;
            &::part(base) {
                border-bottom-left-radius: 0;
                border-bottom-right-radius: 0;
                border-bottom-width: 0;
            }
        }
        & + .vue-recycle-scroller__item-view
        wa-details.tool-use:nth-child(2) {  /* 1 is json toggle */
            padding-top: 0;
            &::part(base) {
                border-top-left-radius: 0;
                border-top-right-radius: 0;
            }
        }
    }
}
</style>
