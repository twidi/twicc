<script setup>
// JsonHumanView.vue - Recursive component that renders any JSON value in a human-friendly way.
//
// Renders objects, arrays, strings, numbers, booleans, and null/undefined
// with appropriate formatting. Multi-line strings are analyzed for markdown patterns
// and rendered with MarkdownContent when detected, otherwise as plain preformatted text.
// No collapse/expand, no truncation, no tool-specific logic.
//
// Supports an `editable` mode where each field becomes an input widget matching its detected type:
// - boolean → wa-switch
// - number → wa-input[type=number]
// - string (single-line) → wa-input
// - string (multi-line/markdown/code) → wa-textarea
// - null → not editable (displayed as-is)
// - object/array → recursive editing of children
// Changes propagate upward via update:value emit.

import { computed } from 'vue'
import MarkdownContent from './MarkdownContent.vue'
import { getIconUrl, getFileIconId } from '../utils/fileIcons'
import { getLanguageFromPath } from '../utils/languages'
import { structuredPatch } from 'diff'

defineOptions({ name: 'JsonHumanView' })

const props = defineProps({
    value: {
        required: true
    },
    name: {
        type: String,
        default: null
    },
    depth: {
        type: Number,
        default: 0
    },
    overrides: {
        type: Object,
        default: () => ({})
    },
    override: {
        type: Object,
        default: null
    },
    editable: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['update:value'])

/**
 * Determine the base JSON type of a value (ignoring display sub-types).
 * Used in edit mode to pick the right input widget.
 * @param {*} val
 * @returns {'null'|'boolean'|'number'|'string'|'array'|'object'}
 */
function baseType(val) {
    if (val === null || val === undefined) return 'null'
    if (typeof val === 'boolean') return 'boolean'
    if (typeof val === 'number') return 'number'
    if (typeof val === 'string') return 'string'
    if (Array.isArray(val)) return 'array'
    return 'object'
}

/**
 * Determine the edit widget type for a value.
 * For strings, distinguishes single-line vs multi-line to choose between wa-input and wa-textarea.
 * @param {*} val
 * @returns {'null'|'boolean'|'number'|'string'|'string-multiline'|'array'|'object'}
 */
function editType(val) {
    const bt = baseType(val)
    if (bt === 'string' && isMultiLine(val)) return 'string-multiline'
    return bt
}

/**
 * Emit an updated value for this node.
 * @param {*} newValue
 */
function emitUpdate(newValue) {
    emit('update:value', newValue)
}

/**
 * Handle a child value update within an object.
 * Rebuilds the object with the updated key and emits the new object.
 * @param {string} key - The key that was updated
 * @param {*} newChildValue - The new value for that key
 */
function onChildObjectUpdate(key, newChildValue) {
    const updated = { ...props.value, [key]: newChildValue }
    emitUpdate(updated)
}

/**
 * Handle a child value update within an array.
 * Rebuilds the array with the updated index and emits the new array.
 * @param {number} index - The array index that was updated
 * @param {*} newChildValue - The new value at that index
 */
function onChildArrayUpdate(index, newChildValue) {
    const updated = [...props.value]
    updated[index] = newChildValue
    emitUpdate(updated)
}

/**
 * Handle input event from a wa-input or wa-textarea for string values.
 * @param {Event} event
 */
function onStringInput(event) {
    emitUpdate(event.target.value)
}

/**
 * Handle input event from a wa-input[type=number] for number values.
 * Parses the string value as a number (int or float).
 * @param {Event} event
 */
function onNumberInput(event) {
    const raw = event.target.value
    if (raw === '' || raw === '-') return
    const parsed = Number(raw)
    if (!Number.isNaN(parsed)) {
        emitUpdate(parsed)
    }
}

/**
 * Handle change event from a wa-switch for boolean values.
 * @param {Event} event
 */
function onBooleanChange(event) {
    emitUpdate(event.target.checked)
}

/**
 * Handle input event for a scalar array item.
 * Updates the single item at the given index and emits the new array.
 * @param {number} index - The array index
 * @param {Event} event
 */
function onScalarArrayItemInput(index, event) {
    const updated = [...props.value]
    const raw = event.target.value
    const original = props.value[index]
    // Preserve the original type when possible
    if (typeof original === 'number') {
        const parsed = Number(raw)
        updated[index] = Number.isNaN(parsed) ? raw : parsed
    } else if (typeof original === 'boolean') {
        updated[index] = raw === 'true'
    } else {
        updated[index] = raw
    }
    emitUpdate(updated)
}

/**
 * Check if a lowercased key matches any of the given keywords (singular and plural forms).
 * For each keyword, tests: exact match, starts with "keyword_", ends with "_keyword", or contains "_keyword_".
 * Automatically generates the plural form by appending "s".
 * @param {string} key - Already lowercased key
 * @param {string[]} keywords - Base keywords (singular form)
 * @returns {boolean}
 */
function keyMatches(key, keywords) {
    for (const word of keywords) {
        for (const form of [word, word + 's']) {
            if (key === form || key.startsWith(form + '_') || key.endsWith('_' + form) || key.includes('_' + form + '_')) return true
        }
    }
    return false
}

/**
 * Check if a key name refers to a file path.
 * @param {string|null} key
 * @returns {boolean}
 */
function keyLooksLikePath(key) {
    if (!key) return false
    return keyMatches(key.toLowerCase(), ['path', 'file'])
}

/**
 * Check if a key name refers to a URL.
 * @param {string|null} key
 * @returns {boolean}
 */
function keyLooksLikeUrl(key) {
    if (!key) return false
    return keyMatches(key.toLowerCase(), ['url', 'domain'])
}

/**
 * Check if a key name refers to code or a pattern.
 * @param {string|null} key
 * @returns {boolean}
 */
function keyLooksLikeCode(key) {
    if (!key) return false
    return keyMatches(key.toLowerCase(), ['pattern', 'code', 'glob', 'source', 'command', 'id', 'type', 'uuid'])
}

/**
 * Check if a key name refers to code content (text body of a file).
 * Matches: content, contents, *_content, content_*, *_contents, contents_*.
 * @param {string|null} key
 * @returns {boolean}
 */
function keyLooksLikeCodeContent(key) {
    if (!key) return false
    return keyMatches(key.toLowerCase(), ['content'])
}

/**
 * Determine the display type of a value.
 * Returns fine-grained types for strings (string, string-multiline, string-markdown, string-file, string-url).
 * When a key is provided, uses it to auto-detect file paths and URLs.
 * @param {*} val
 * @param {string|null} [key]
 * @returns {'null'|'boolean'|'number'|'string'|'string-multiline'|'string-markdown'|'string-code'|'string-file'|'string-url'|'array'|'array-string-url'|'object'}
 */
function valueType(val, key = null) {
    if (val === null || val === undefined) return 'null'
    if (typeof val === 'boolean') return 'boolean'
    if (typeof val === 'number') return 'number'
    if (typeof val === 'string') {
        if (isMultiLine(val)) {
            return looksLikeMarkdown(val) ? 'string-markdown' : 'string-multiline'
        }
        if (keyLooksLikePath(key)) return 'string-file'
        if (keyLooksLikeUrl(key)) return 'string-url'
        if (keyLooksLikeCode(key)) return 'string-code'
        return 'string'
    }
    if (Array.isArray(val)) {
        if (keyLooksLikeUrl(key) && isScalarArray(val)) return 'array-string-url'
        return 'array'
    }
    return 'object'
}

/**
 * Get the effective display type for this node's value.
 * Uses override if provided (from parent's overrides), otherwise auto-detects.
 * @returns {string}
 */
function effectiveType() {
    return props.override?.valueType ?? valueType(props.value, props.name)
}

/**
 * Computed content block source info for the current value (only meaningful when value is an object).
 * Detects source objects with embedded data (image or binary) so the "data" key can be rendered specially.
 */
const contentBlockSource = computed(() => {
    if (typeof props.value === 'object' && props.value !== null && !Array.isArray(props.value)) {
        return detectContentBlockSource(props.value)
    }
    return null
})

/**
 * Computed diff pairs for the current value (only meaningful when value is an object).
 * Cached to avoid recalculating in v-for loops.
 */
const diffPairs = computed(() => {
    if (typeof props.value === 'object' && props.value !== null && !Array.isArray(props.value)) {
        return findDiffPairs(props.value)
    }
    return { pairs: [], consumed: new Set() }
})

/**
 * Computed overrides derived from sibling keys when the value is an object.
 * When an object has a path-like key (e.g. "file_path"), extracts the programming language
 * from its value and generates string-code overrides for content-like sibling keys
 * (e.g. "content") and for diff pair old/new keys.
 *
 * Returns a flat object: { [key]: { valueType: 'string-code', language } }
 * Language may be null if the extension is unknown (still renders as string-code, just without highlighting).
 *
 * Explicit parent overrides always take priority over these (merged in the template).
 */
const siblingOverrides = computed(() => {
    if (typeof props.value !== 'object' || props.value === null || Array.isArray(props.value)) {
        return {}
    }

    // Find the first path-like key with a string value
    const keys = Object.keys(props.value)
    let pathFound = false
    let language = null
    for (const key of keys) {
        if (keyLooksLikePath(key) && typeof props.value[key] === 'string') {
            pathFound = true
            language = getLanguageFromPath(props.value[key])
            break
        }
    }
    // No path sibling found → no overrides to generate
    if (!pathFound) return {}

    const result = {}
    const codeOverride = { valueType: 'string-code', ...(language != null && { language }) }

    // Generate overrides for content-like keys
    for (const key of keys) {
        if (keyLooksLikeCodeContent(key) && typeof props.value[key] === 'string') {
            result[key] = codeOverride
        }
    }

    // Generate overrides for diff pair old/new keys
    for (const pair of diffPairs.value.pairs) {
        result[pair.oldKey] = codeOverride
        result[pair.newKey] = codeOverride
    }

    return result
})

/**
 * Check if a value is a simple scalar (null, boolean, number, or single-line string).
 * @param {*} val
 * @returns {boolean}
 */
function isScalar(val) {
    const t = valueType(val)
    return t === 'null' || t === 'boolean' || t === 'number' || t === 'string'
}

/**
 * Check if a value is a short inline value (scalar with reasonable length).
 * Used to decide whether to show key:value on the same line.
 * @param {*} val
 * @returns {boolean}
 */
function isInlineValue(val) {
    if (Array.isArray(val) && isScalarArray(val)) return true
    if (!isScalar(val)) return false
    if (typeof val === 'string' && val.length > 120) return false
    return true
}

/**
 * Check if all items in an array are simple scalars (for inline comma-separated display).
 * @param {Array} arr
 * @returns {boolean}
 */
function isScalarArray(arr) {
    return arr.length > 0 && arr.every(isScalar)
}

/**
 * Format a scalar value as a display string.
 * @param {*} val
 * @returns {string}
 */
function formatScalar(val) {
    if (val === null || val === undefined) return '\u2014'
    if (typeof val === 'boolean') return val ? 'true' : 'false'
    if (typeof val === 'number') return String(val)
    return String(val)
}

/**
 * Format a JSON key name into a human-friendly label.
 * Replaces underscores with spaces and splits camelCase into separate words.
 * @param {string} key
 * @returns {string}
 */
function formatLabel(key) {
    return key
        .replaceAll('_', ' ')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .toLowerCase()
}

/**
 * Get the icon URL for a file path based on its filename/extension.
 * Returns null if no specific icon is found (default-file).
 * @param {string} filePath
 * @returns {string|null}
 */
function fileIconUrl(filePath) {
    const filename = filePath.split('/').pop() || filePath
    const iconId = getFileIconId(filename)
    return iconId !== 'default-file' ? getIconUrl(iconId) : null
}

/**
 * Check if a string is multi-line.
 * @param {string} str
 * @returns {boolean}
 */
function isMultiLine(str) {
    return typeof str === 'string' && str.includes('\n')
}

/**
 * Detect if a multi-line string looks like markdown.
 * Uses heuristics to check for common markdown patterns.
 * @param {string} str
 * @returns {boolean}
 */
function looksLikeMarkdown(str) {
    if (!isMultiLine(str)) return false

    // Patterns that strongly suggest markdown
    const markdownPatterns = [
        /^#{1,6}\s+/m,          // Headings: # Title, ## Subtitle, etc.
        /^\s*[-*+]\s+/m,        // Unordered lists: - item, * item, + item
        /^\s*\d+\.\s+/m,        // Ordered lists: 1. item, 2. item
        /\*\*[^*]+\*\*/,        // Bold: **text**
        /\*[^*]+\*/,            // Italic: *text*
        /`[^`]+`/,              // Inline code: `code`
        /^```/m,                // Fenced code blocks: ```
        /\[.+?\]\(.+?\)/,       // Links: [text](url)
        /^>\s+/m,               // Blockquotes: > text
        /^\s*\|.+\|/m,          // Tables: | col | col |
        /^---+$/m,              // Horizontal rules: ---
    ]

    // Count how many patterns match
    let matches = 0
    for (const pattern of markdownPatterns) {
        if (pattern.test(str)) matches++
    }

    // If 2+ patterns match, it's likely markdown
    return matches >= 2
}

/**
 * Find old/new key pairs in an object that can be rendered as diffs.
 * Looks for keys matching "old_*" / "new_*" where both values are strings.
 * @param {Object} obj
 * @returns {{ pairs: Array<{ baseName: string, oldKey: string, newKey: string }>, consumed: Set<string> }}
 */
function findDiffPairs(obj) {
    const keys = Object.keys(obj)
    const pairs = []
    const consumed = new Set()

    for (const key of keys) {
        if (!key.startsWith('old_') && !key.startsWith('old')) continue
        // Extract the base name: "old_string" → "string", "oldString" → "String"
        let baseName, newKey
        if (key.startsWith('old_')) {
            baseName = key.slice(4)
            newKey = 'new_' + baseName
        } else {
            // camelCase: oldString → newString
            baseName = key.slice(3)
            newKey = 'new' + baseName
        }
        if (!(newKey in obj)) continue
        if (typeof obj[key] !== 'string' || typeof obj[newKey] !== 'string') continue
        pairs.push({ baseName, oldKey: key, newKey })
        consumed.add(key)
        consumed.add(newKey)
    }
    return { pairs, consumed }
}

/**
 * Detect if an object is a content block source with embedded data (image, document, etc.).
 * Matches the Claude SDK source structure: { type, media_type, data }.
 * Returns null if not a content block source, or an info object describing how to render the data field.
 * @param {Object} obj
 * @returns {{ kind: 'image', mediaType: string, data: string } | { kind: 'binary', mediaType: string } | null}
 */
function detectContentBlockSource(obj) {
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null
    if (!obj.media_type || !('data' in obj) || !obj.type) return null
    if (obj.type === 'text') return null
    const mediaType = obj.media_type
    if (typeof mediaType === 'string' && mediaType.startsWith('image/')) {
        return { kind: 'image', mediaType, data: obj.data }
    }
    return { kind: 'binary', mediaType }
}

/**
 * Generate a unified diff string from two strings, suitable for ```diff rendering.
 * @param {string} oldStr
 * @param {string} newStr
 * @returns {string}
 */
function generateDiff(oldStr, newStr) {
    const result = structuredPatch('', '', oldStr, newStr, '', '', { context: 3 })
    const lines = []
    for (const hunk of result.hunks) {
        for (const line of hunk.lines) {
            // Skip "No newline at end of file" markers
            if (line.startsWith('\\')) continue
            lines.push(line)
        }
    }
    return lines.join('\n')
}
</script>

<template>
    <div class="jhv-node" :class="{ 'jhv-nested': depth >= 1 }">
        <!-- Null / undefined (not editable — no meaningful type to edit into) -->
        <template v-if="effectiveType() === 'null'">
            <div class="jhv-entry">
                <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                <span v-if="name != null" class="jhv-separator">: </span>
                <span class="jhv-null">&mdash;</span>
            </div>
        </template>

        <!-- Boolean -->
        <template v-else-if="effectiveType() === 'boolean'">
            <div class="jhv-entry">
                <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                <span v-if="name != null" class="jhv-separator">: </span>
                <template v-if="editable">
                    <input type="checkbox" class="jhv-edit-checkbox" :checked="value" @change="onBooleanChange" />
                </template>
                <template v-else>
                    <template v-if="value">
                        <wa-icon name="square-check" class="jhv-boolean-icon jhv-boolean-true"></wa-icon>
                    </template>
                    <span v-else class="jhv-boolean-false">no</span>
                </template>
            </div>
        </template>

        <!-- Number -->
        <template v-else-if="effectiveType() === 'number'">
            <div class="jhv-entry">
                <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                <span v-if="name != null" class="jhv-separator">: </span>
                <template v-if="editable">
                    <wa-input
                        type="number"
                        size="small"
                        class="jhv-edit-input jhv-edit-number"
                        :value.prop="String(value)"
                        @input="onNumberInput"
                        no-spin-buttons
                    ></wa-input>
                </template>
                <span v-else class="jhv-number">{{ value }}</span>
            </div>
        </template>

        <!-- String: markdown -->
        <template v-else-if="effectiveType() === 'string-markdown'">
            <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
            <template v-if="editable">
                <wa-textarea
                    size="small"
                    class="jhv-edit-textarea"
                    :value.prop="value"
                    @input="onStringInput"
                    resize="auto"
                    rows="4"
                ></wa-textarea>
            </template>
            <div v-else class="jhv-markdown">
                <MarkdownContent :source="value" />
            </div>
        </template>

        <!-- String: code (not auto-detected, only via override) -->
        <template v-else-if="effectiveType() === 'string-code'">
            <template v-if="editable">
                <!-- Multi-line code: textarea -->
                <template v-if="override?.language || isMultiLine(value)">
                    <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
                    <wa-textarea
                        size="small"
                        class="jhv-edit-textarea jhv-edit-code"
                        :value.prop="value"
                        @input="onStringInput"
                        resize="auto"
                        rows="4"
                    ></wa-textarea>
                </template>
                <!-- Single-line code: input -->
                <template v-else>
                    <div class="jhv-entry">
                        <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                        <span v-if="name != null" class="jhv-separator">: </span>
                        <wa-input
                            size="small"
                            class="jhv-edit-input"
                            :value.prop="value"
                            @input="onStringInput"
                        ></wa-input>
                    </div>
                </template>
            </template>
            <template v-else>
                <!-- With language or multi-line: fenced code block -->
                <template v-if="override?.language || isMultiLine(value)">
                    <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
                    <div class="jhv-markdown">
                        <MarkdownContent :source="'```' + (override?.language ?? '') + '\n' + value + '\n```'" />
                    </div>
                </template>
                <!-- Single-line without language: inline code -->
                <template v-else>
                    <div class="jhv-entry">
                        <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                        <span v-if="name != null" class="jhv-separator">: </span>
                        <code class="jhv-code">{{ value }}</code>
                    </div>
                </template>
            </template>
        </template>

        <!-- String: file path (not auto-detected, only via override) -->
        <template v-else-if="effectiveType() === 'string-file'">
            <div class="jhv-entry">
                <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                <span v-if="name != null" class="jhv-separator">: </span>
                <template v-if="editable">
                    <wa-input
                        size="small"
                        class="jhv-edit-input"
                        :value.prop="value"
                        @input="onStringInput"
                    ></wa-input>
                </template>
                <span v-else class="jhv-file">
                    <img v-if="fileIconUrl(value)" :src="fileIconUrl(value)" class="jhv-file-icon" loading="lazy" width="16" height="16" />
                    <span>{{ value }}</span>
                </span>
            </div>
        </template>

        <!-- String: URL (not auto-detected, only via override) -->
        <template v-else-if="effectiveType() === 'string-url'">
            <div class="jhv-entry">
                <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                <span v-if="name != null" class="jhv-separator">: </span>
                <template v-if="editable">
                    <wa-input
                        size="small"
                        class="jhv-edit-input"
                        :value.prop="value"
                        @input="onStringInput"
                    ></wa-input>
                </template>
                <template v-else>
                    <a :href="value" target="_blank" rel="noopener noreferrer nofollow" class="jhv-url">{{ value }}<wa-icon name="arrow-up-right-from-square" class="jhv-url-external"></wa-icon></a>
                </template>
            </div>
        </template>

        <!-- String: multi-line plain text -->
        <template v-else-if="effectiveType() === 'string-multiline'">
            <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
            <template v-if="editable">
                <wa-textarea
                    size="small"
                    class="jhv-edit-textarea"
                    :value.prop="value"
                    @input="onStringInput"
                    resize="auto"
                    rows="4"
                ></wa-textarea>
            </template>
            <pre v-else class="jhv-pre">{{ value.trim() }}</pre>
        </template>

        <!-- String: single-line -->
        <template v-else-if="effectiveType() === 'string'">
            <div class="jhv-entry">
                <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                <span v-if="name != null" class="jhv-separator">: </span>
                <template v-if="editable">
                    <wa-input
                        size="small"
                        class="jhv-edit-input"
                        :value.prop="value"
                        @input="onStringInput"
                    ></wa-input>
                </template>
                <span v-else class="jhv-string">{{ value }}</span>
            </div>
        </template>

        <!-- Array of URLs: inline comma-separated links -->
        <template v-else-if="effectiveType() === 'array-string-url'">
            <template v-if="editable">
                <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
                <div class="jhv-edit-scalar-array">
                    <wa-input
                        v-for="(item, idx) in value"
                        :key="idx"
                        size="small"
                        class="jhv-edit-input"
                        :value.prop="String(item)"
                        @input="onScalarArrayItemInput(idx, $event)"
                    ></wa-input>
                </div>
            </template>
            <div v-else class="jhv-entry">
                <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                <span v-if="name != null" class="jhv-separator">: </span>
                <span class="jhv-url-list">
                    <template v-for="(item, idx) in value" :key="idx">
                        <span v-if="idx > 0" class="jhv-url-comma">, </span>
                        <a :href="item" target="_blank" rel="noopener noreferrer nofollow" class="jhv-url">{{ item }}<wa-icon name="arrow-up-right-from-square" class="jhv-url-external"></wa-icon></a>
                    </template>
                </span>
            </div>
        </template>

        <!-- Array -->
        <template v-else-if="effectiveType() === 'array'">
            <!-- Empty array -->
            <template v-if="value.length === 0">
                <div class="jhv-entry">
                    <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                    <span v-if="name != null" class="jhv-separator">: </span>
                    <span class="jhv-null">&mdash;</span>
                </div>
            </template>
            <!-- Scalar array: editable as individual inputs, read-only as comma-separated -->
            <template v-else-if="isScalarArray(value)">
                <template v-if="editable">
                    <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
                    <div class="jhv-edit-scalar-array">
                        <wa-input
                            v-for="(item, idx) in value"
                            :key="idx"
                            size="small"
                            class="jhv-edit-input"
                            :value.prop="formatScalar(item)"
                            @input="onScalarArrayItemInput(idx, $event)"
                        ></wa-input>
                    </div>
                </template>
                <div v-else class="jhv-entry">
                    <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                    <span v-if="name != null" class="jhv-separator">: </span>
                    <span class="jhv-string">{{ value.map(formatScalar).join(', ') }}</span>
                </div>
            </template>
            <!-- Complex array: each item recursively with index labels -->
            <template v-else>
                <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
                <div class="jhv-children">
                    <div v-for="(item, idx) in value" :key="idx" class="jhv-array-item">
                        <span class="jhv-array-index">#{{ idx + 1 }}</span>
                        <JsonHumanView
                            :value="item"
                            :depth="depth + 1"
                            :editable="editable"
                            @update:value="onChildArrayUpdate(idx, $event)"
                        />
                    </div>
                </div>
            </template>
        </template>

        <!-- Object -->
        <template v-else-if="effectiveType() === 'object'">
            <!-- Empty object -->
            <template v-if="Object.keys(value).length === 0">
                <div class="jhv-entry">
                    <span v-if="name != null" class="jhv-key">{{ formatLabel(name) }}</span>
                    <span v-if="name != null" class="jhv-separator">: </span>
                    <span class="jhv-null">&mdash;</span>
                </div>
            </template>
            <template v-else>
                <div v-if="name != null" class="jhv-key jhv-block-key">{{ formatLabel(name) }}:</div>
                <div class="jhv-children">
                    <!-- In edit mode: render ALL keys individually (no diff grouping) -->
                    <template v-if="editable">
                        <template v-for="key in Object.keys(value)" :key="key">
                            <JsonHumanView
                                :value="value[key]"
                                :name="key"
                                :depth="depth + 1"
                                :override="overrides[key] ?? siblingOverrides[key]"
                                :editable="true"
                                @update:value="onChildObjectUpdate(key, $event)"
                            />
                        </template>
                    </template>
                    <!-- In read mode: existing rendering with diff pairs and content block sources -->
                    <template v-else>
                        <template v-for="key in Object.keys(value)" :key="key">
                            <!-- Skip keys consumed by diff pairs (shown below in diff section) -->
                            <template v-if="!diffPairs.consumed.has(key)">
                                <!-- Content block source: render "data" as image or binary blob -->
                                <template v-if="key === 'data' && contentBlockSource">
                                    <!-- Image: show the image inline -->
                                    <template v-if="contentBlockSource.kind === 'image'">
                                        <div class="jhv-key jhv-block-key">{{ formatLabel('data') }}:</div>
                                        <div class="jhv-content-block">
                                            <img :src="'data:' + contentBlockSource.mediaType + ';base64,' + contentBlockSource.data" class="jhv-content-image" loading="lazy" />
                                        </div>
                                    </template>
                                    <!-- Binary: show placeholder instead of raw data -->
                                    <template v-else>
                                        <div class="jhv-entry">
                                            <span class="jhv-key">{{ formatLabel('data') }}</span>
                                            <span class="jhv-separator">: </span>
                                            <span class="jhv-null jhv-binary-blob">&lsquo;binary blob&rsquo;</span>
                                        </div>
                                    </template>
                                </template>
                                <!-- Normal key rendering -->
                                <JsonHumanView
                                    v-else
                                    :value="value[key]"
                                    :name="key"
                                    :depth="depth + 1"
                                    :override="overrides[key] ?? siblingOverrides[key]"
                                />
                            </template>
                        </template>
                        <!-- Render auto-generated diffs for old/new pairs -->
                        <template v-for="pair in diffPairs.pairs" :key="'diff_' + pair.baseName">
                            <div class="jhv-key jhv-block-key">{{ formatLabel(pair.baseName) }} diff:</div>
                            <div class="jhv-markdown jhv-diff">
                                <MarkdownContent :source="'```diff\n' + generateDiff(value[pair.oldKey], value[pair.newKey]) + '\n```'" />
                            </div>
                            <details class="jhv-diff-originals">
                                <summary class="jhv-diff-originals-toggle">Old/new values</summary>
                                <div class="jhv-children">
                                    <JsonHumanView
                                        :value="value[pair.oldKey]"
                                        :name="pair.oldKey"
                                        :depth="depth + 1"
                                        :override="overrides[pair.oldKey] ?? siblingOverrides[pair.oldKey] ?? { valueType: 'string-multiline' }"
                                    />
                                    <JsonHumanView
                                        :value="value[pair.newKey]"
                                        :name="pair.newKey"
                                        :depth="depth + 1"
                                        :override="overrides[pair.newKey] ?? siblingOverrides[pair.newKey] ?? { valueType: 'string-multiline' }"
                                    />
                                </div>
                            </details>
                        </template>
                    </template>
                </div>
            </template>
        </template>
    </div>
</template>

<style scoped>
.jhv-node {
    line-height: 2;
}

.jhv-nested {
}

.jhv-entry {
    word-wrap: break-word;
    overflow-wrap: break-word;
}

.jhv-key {
    font-weight: 600;
    color: var(--wa-color-text-quiet);
    font-variant: small-caps;
}

.jhv-block-key {
    margin-bottom: var(--wa-space-3xs);
    & + * {
        margin-left: var(--wa-space-m);
    }
}

.jhv-separator {
    color: var(--wa-color-text-quiet);
}

.jhv-null {
    color: var(--wa-color-text-quiet);
    opacity: 0.6;
}

.jhv-boolean-icon {
    font-size: var(--wa-font-size-s);
}

.jhv-file {
    display: inline-flex;
    align-items: baseline;
    gap: var(--wa-space-3xs);
}

.jhv-file-icon {
    align-self: center;
    flex-shrink: 0;
}

.jhv-url {
    color: var(--wa-color-primary);
    text-decoration: none;
    &:hover {
        text-decoration: underline;
    }
}

.jhv-url-external {
    font-size: var(--wa-font-size-2xs);
    margin-left: var(--wa-space-3xs);
    opacity: 0.6;
}

.jhv-pre {
    white-space: pre-wrap;
    word-break: break-word;
}

.jhv-pre, .jhv-markdown :deep(.markdown-body) {
    background: var(--wa-color-overlay-inline) !important;
    border-radius: var(--wa-border-radius-l) !important;
    overflow: auto;
    padding: var(--wa-space-m);
}

.jhv-markdown {
    & :deep(.markdown-body pre)  {
        background: transparent !important;
    }
    & :deep(.shiki span)  {
        --shiki-bg-color: transparent;
    }
}

.jhv-code {
    background: var(--wa-color-overlay-inline) !important;
    padding: var(--wa-space-xs);
}

.jhv-array-item {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
}

.jhv-array-index {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    opacity: 0.6;
    flex-shrink: 0;
}

.jhv-children {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.jhv-children .jhv-array-item + .jhv-array-item {
    margin-top: var(--wa-space-2xs);
}

.jhv-content-block {
    margin-left: var(--wa-space-m);
}

.jhv-content-image {
    max-width: 100%;
    max-height: 30rem;
    border-radius: var(--wa-border-radius-l);
    object-fit: contain;
}

.jhv-binary-blob {
    font-style: italic;
}

.jhv-diff-originals {
    margin-top: var(--wa-space-m);
    margin-left: var(--wa-space-m);
}

.jhv-diff-originals-toggle {
    cursor: pointer;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    opacity: 0.7;
    &:hover {
        opacity: 1;
    }
}

/* =========================================================================
   Edit mode styles
   ========================================================================= */

.jhv-edit-input {
    flex: 1;
    min-width: 0;
}

.jhv-edit-number {
    max-width: 12rem;
}

.jhv-edit-textarea {
    width: 100%;
}

.jhv-edit-code {
    font-family: var(--wa-font-mono);
}

.jhv-edit-checkbox {
    width: 1.1em;
    height: 1.1em;
    accent-color: var(--wa-color-primary);
    cursor: pointer;
    vertical-align: middle;
}

.jhv-edit-scalar-array {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    margin-left: var(--wa-space-m);
}
</style>
