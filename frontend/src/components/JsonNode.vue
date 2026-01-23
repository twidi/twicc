<script setup>
import { computed } from 'vue'

const props = defineProps({
    data: { required: true },
    path: { type: String, required: true },
    keyName: { type: String, default: null },
    collapsedPaths: { type: Set, required: true }
})

const emit = defineEmits(['toggle'])

const valueType = computed(() => {
    if (props.data === null) return 'null'
    if (typeof props.data === 'boolean') return 'boolean'
    if (typeof props.data === 'number') return 'number'
    if (typeof props.data === 'string') return 'string'
    if (Array.isArray(props.data)) return 'array'
    if (typeof props.data === 'object') return 'object'
    return 'unknown'
})

const isPrimitive = computed(() => {
    return props.data === null || typeof props.data !== 'object'
})

const isCollapsed = computed(() => {
    return props.collapsedPaths.has(props.path)
})

const displayValue = computed(() => {
    if (props.data === null) return 'null'
    if (typeof props.data === 'boolean') return props.data.toString()
    if (typeof props.data === 'number') return props.data.toString()
    if (typeof props.data === 'string') return `"${props.data}"`
    return ''
})

const entries = computed(() => {
    if (Array.isArray(props.data)) {
        return props.data.map((val, idx) => ({
            key: idx.toString(),
            value: val,
            isArrayIndex: true
        }))
    }
    if (typeof props.data === 'object' && props.data !== null) {
        return Object.entries(props.data).map(([key, val]) => ({
            key,
            value: val,
            isArrayIndex: false
        }))
    }
    return []
})

const collapsedPreview = computed(() => {
    if (Array.isArray(props.data)) {
        return `[${props.data.length} items]`
    }
    const keys = Object.keys(props.data)
    return `{${keys.length} keys}`
})

function handleToggle() {
    emit('toggle', props.path)
}

function forwardToggle(path) {
    emit('toggle', path)
}
</script>

<template>
    <div class="json-node">
        <template v-if="isPrimitive">
            <span v-if="keyName !== null" class="json-key">{{ keyName }}: </span>
            <span :class="['json-value', 'json-value--' + valueType]">{{ displayValue }}</span>
        </template>
        <template v-else>
            <span
                class="json-toggle"
                @click="handleToggle"
                :class="{ 'json-toggle--collapsed': isCollapsed }"
            >
                {{ isCollapsed ? '+' : '-' }}
            </span>
            <span v-if="keyName !== null" class="json-key">{{ keyName }}: </span>
            <span class="json-bracket">{{ valueType === 'array' ? '[' : '{' }}</span>
            <template v-if="isCollapsed">
                <span class="json-preview">{{ collapsedPreview }}</span>
                <span class="json-bracket">{{ valueType === 'array' ? ']' : '}' }}</span>
            </template>
            <template v-else>
                <div class="json-children">
                    <JsonNode
                        v-for="entry in entries"
                        :key="entry.key"
                        :data="entry.value"
                        :path="path + '.' + entry.key"
                        :key-name="entry.key"
                        :collapsed-paths="collapsedPaths"
                        @toggle="forwardToggle"
                    />
                </div>
                <span class="json-bracket">{{ valueType === 'array' ? ']' : '}' }}</span>
            </template>
        </template>
    </div>
</template>

<style scoped>
.json-node {
    white-space: nowrap;
}

.json-key {
    color: var(--wa-color-brand);
    font-weight: 500;
}

.json-value--string {
    color: #22863a;
}

.json-value--number {
    color: #005cc5;
}

.json-value--boolean {
    color: #d73a49;
}

.json-value--null {
    color: #6a737d;
    font-style: italic;
}

.json-bracket {
    color: var(--wa-color-text-subtle);
}

.json-toggle {
    display: inline-block;
    width: 16px;
    height: 16px;
    text-align: center;
    cursor: pointer;
    color: var(--wa-color-text-subtle);
    font-weight: bold;
    user-select: none;
}

.json-toggle:hover {
    color: var(--wa-color-brand);
}

.json-preview {
    color: var(--wa-color-text-subtle);
    font-style: italic;
    margin: 0 var(--wa-space-2xs);
}

.json-children {
    padding-left: 2ch;
}
</style>
