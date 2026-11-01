# Tool Use Multi-Provider Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the provider-agnostic logic of `frontend/src/components/session/detail/items/claude_code/ToolUseContent.vue` (1149 lines) into a generic shell at `items/ToolUseContent.vue`, and move all Claude Code-specific tool rendering (summary, input/result components, capabilities, JsonHumanView overrides) into a new per-provider helpers module `providers/claude_code/toolHelpers.js`. This unblocks adding tool rendering for other providers (Codex first) without duplicating the shell logic — state machine, polling, abort, subagent UI, code-comments indicator, file-change stats, error handling, View-in-Files button, auto-open of live diffs, etc.

**Architecture:** Helpers approach — the shell asks normalized questions to a per-provider `ToolHelpers` instance (`getInputRendering`, `getResultRendering`, `buildToolSummary`, capability flags, …) and renders the answers. Mirrors the existing `BaseProviderHelpers` pattern: a base class in `providers/baseHelpers.js` defines neutral defaults; each provider ships a subclass that overrides the behaviour it actually has. A registry in `providers/index.js` resolves the right instance from a session's `provider` field. The contract for input/result components uses `{ component, props }` descriptors so the helper controls **which** component renders **and** what props it gets — that lets `EditContent` / `WriteContent` keep their existing prop shape (`backendPatch`, `originalFile`, `isSubagent`) without leaking Claude Code knowledge into the shell.

**Tech Stack:** Vue 3 (Composition API + `<script setup>`), Pinia, Web Awesome 3.1, ES modules.

**Project conventions:**
- All UI strings, comments, and identifiers must be in **English**.
- The project intentionally has **no tests and no linting** (per CLAUDE.md). Verification steps below are read-back code reviews and end-of-task manual smoke tests in the browser.
- **The user — not Claude — restarts dev servers.** When changes need a Vite HMR reload that doesn't pick up automatically (rare for Vue), ask the user. Helpers / store / Vue file edits are normally hot-reloaded.
- Vue components use Composition API with `<script setup>`.
- Avoid circular imports (cf. CLAUDE.md): `toolHelpers.js` lives under `providers/claude_code/` and may import Vue components from `components/session/detail/items/claude_code/`. The shell at `items/ToolUseContent.vue` imports from `providers/index.js` (registry) and `JsonHumanView` (fallback) — it must NOT import any provider-specific component directly.
- Web Awesome custom events keep their `wa-` prefix (`@wa-show`, `@wa-hide`); native events do not (`@click`).
- Use the explicit `git add <files>` form (no `-A`/`-a`, per user's global instructions).

**Line number conventions:** the line ranges cited next to file paths in this plan are *approximate hints*; locate by anchor strings (e.g. `computeToolSummary`, `INPUT_OVERRIDES`, `editValid`, `shouldAutoOpen`, `parseCatNContent`, `fileChangeBackendPatch`, …) and treat ranges as "look around here." The current monolithic file is `frontend/src/components/session/detail/items/claude_code/ToolUseContent.vue`.

**Reviewer advisory items pre-addressed:**
- *Why a `BaseToolHelpers` class instead of a plain object/duck-typing?* Same reason `BaseProviderHelpers` is a class: each method is documented with a docstring + neutral default, the subclass only overrides what changes. Future providers add their tool rendering by extending the base.
- *Why a separate `toolHelpers.js` file instead of adding methods to `ClaudeCodeHelpers`?* The user explicitly chose separation in cadrage. Keeps `helpers.js` (auth/quota/synced-settings/agent-settings) cohesive and prevents `helpers.js` from re-importing Vue components (which would create import cycles for any non-tool consumer of `helpers.js`).
- *Why an `{ component, props }` descriptor instead of returning a bare component?* `EditContent` and `WriteContent` need props that come from the shell's runtime state (`backendPatch`, `originalFile`, `backendPatchLoading`). The descriptor lets the helper map a context object (provided by the shell) into the right prop names per tool, without the shell knowing which props belong to which tool.
- *Why three separate Result components (`ReadResultContent`, `BashResultContent`, `WebContentResult`) instead of inline rendering in the shell?* Symmetry with input rendering (Edit/Write/Todo are also extracted), and so the helper API is uniform: `getResultRendering` returns `{ component, props }` like `getInputRendering`. They are 15-30 lines each.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `frontend/src/providers/baseHelpers.js` | Modify | Add `BaseToolHelpers` class with neutral defaults |
| `frontend/src/providers/claude_code/toolHelpers.js` | Create | `ClaudeCodeToolHelpers` (extends base) + `claudeCodeToolHelpers` singleton; absorbs `utils/toolSummary.js` and per-tool logic |
| `frontend/src/providers/index.js` | Modify | Add `PROVIDER_TOOL_HELPERS` registry + `getToolHelpers(provider)` export |
| `frontend/src/components/session/detail/items/claude_code/ReadResultContent.vue` | Create | Header "Lines X–Y" + markdown for cat -n parsed Read result |
| `frontend/src/components/session/detail/items/claude_code/BashResultContent.vue` | Create | Markdown wrapper rendering Bash result as a fenced code block |
| `frontend/src/components/session/detail/items/claude_code/WebContentResult.vue` | Create | Markdown direct rendering for WebFetch / WebSearch result |
| `frontend/src/components/session/detail/items/ToolUseContent.vue` | Create | Provider-agnostic shell (state, polling, subagent UI, layout, CSS, common indicators) |
| `frontend/src/components/session/detail/items/claude_code/ContentList.vue` | Modify | Update `ToolUseContent` import to `'../ToolUseContent.vue'` |
| `frontend/src/components/session/detail/items/WorkingAssistantMessage.vue` | Modify | Resolve `getToolHelpers(provider)` and call its `computeToolSummary`/`getVerb` |
| `frontend/src/components/session/detail/items/claude_code/ToolUseContent.vue` | Delete | Replaced by the generic shell |
| `frontend/src/utils/toolSummary.js` | Delete | Moved into `providers/claude_code/toolHelpers.js` |

**Component prop shapes you'll need to know during the refactor:**

- `EditContent` props: `input` (required), `backendPatch` (Array, null), `backendPatchLoading` (bool, false), `originalFile` (String, null), `isSubagent` (bool, false).
- `WriteContent` props: same five as `EditContent`.
- `TodoContent` props: `todos`.
- `MarkdownContent` (for the new Result components) props: `source` (markdown string).

---

## Task 1: Mini-components for specialized tool results

Three small Vue SFCs extracted from the current `ToolUseContent.vue`. Created up front so `claudeCodeToolHelpers` (Task 3) can import them.

**Files:**
- Create: `frontend/src/components/session/detail/items/claude_code/ReadResultContent.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/BashResultContent.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/WebContentResult.vue`

- [ ] **Step 1: Create `ReadResultContent.vue`**

This component is given the result of a `Read` tool, the input (for the file path → language detection), and renders a small "Lines X–Y" header plus the syntax-highlighted code via `MarkdownContent`. Logic copied from the current `parseCatNContent` + `readResultCode` in `ToolUseContent.vue` (~lines 350-414).

```vue
<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'
import { getLanguageFromPath } from '../../../../../utils/languages'

const props = defineProps({
    result: { type: [Object, String, Array], required: true },
    input: { type: Object, default: () => ({}) },
})

// Regex to match a cat -n formatted line: optional spaces, digits, separator (→ or tab), then content.
// Old format used → (U+2192 arrow), new format uses \t (standard cat -n). Both must be supported.
const CAT_N_LINE_RE = /^(\s*\d+)[→\t](.*)$/

function parseCatNContent(content) {
    if (typeof content !== 'string') return null
    const lines = content.split('\n')
    if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    if (lines.length === 0) return null
    const firstNonEmpty = lines.find(l => l.length > 0)
    if (!firstNonEmpty || !CAT_N_LINE_RE.test(firstNonEmpty)) return null
    let startLine = null
    let endLine = null
    const codeLines = []
    for (const line of lines) {
        const match = line.match(CAT_N_LINE_RE)
        if (match) {
            const lineNum = parseInt(match[1], 10)
            if (startLine === null) startLine = lineNum
            endLine = lineNum
            codeLines.push(match[2])
        } else {
            codeLines.push(line)
        }
    }
    return { code: codeLines.join('\n'), startLine, endLine }
}

const parsed = computed(() => {
    const r = props.result
    const content = typeof r === 'string' ? r : r?.content
    return parseCatNContent(content)
})

const markdownSource = computed(() => {
    if (!parsed.value) return null
    const language = getLanguageFromPath(props.input?.file_path) || ''
    return '```' + language + '\n' + parsed.value.code + '\n```'
})
</script>

<template>
    <template v-if="parsed && markdownSource">
        <div class="read-result-header">Lines {{ parsed.startLine }}–{{ parsed.endLine }}</div>
        <MarkdownContent :source="markdownSource" />
    </template>
</template>

<style scoped>
.read-result-header {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin-bottom: var(--wa-space-xs);
}
</style>
```

Note: the empty `<template v-if>` (no fallback) is intentional — the helper's `getResultRendering` only returns this component when `parseCatNContent` is expected to succeed (it pre-checks via the same parser, see Task 3). If the runtime parse fails, the shell falls back to `JsonHumanView` because `getResultRendering` returned `null`.

- [ ] **Step 2: Create `BashResultContent.vue`**

Wraps the Bash result (a string) in a fenced code block and renders it as markdown. Logic copied from the `directContentSource` computed in `ToolUseContent.vue` (~lines 333-344, branch `props.name === 'Bash'`).

```vue
<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    result: { type: [Object, String, Array], required: true },
})

const markdownSource = computed(() => {
    const r = props.result
    const content = typeof r === 'string' ? r : r?.content
    if (typeof content !== 'string' || !content) return null
    return '```\n' + content + '\n```'
})
</script>

<template>
    <MarkdownContent v-if="markdownSource" :source="markdownSource" />
</template>
```

- [ ] **Step 3: Create `WebContentResult.vue`**

Renders WebFetch / WebSearch results (already markdown) directly via `MarkdownContent`. Logic copied from the `directContentSource` computed (~line 343, default branch for WebFetch/WebSearch).

```vue
<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    result: { type: [Object, String, Array], required: true },
})

const markdownSource = computed(() => {
    const r = props.result
    const content = typeof r === 'string' ? r : r?.content
    if (typeof content !== 'string' || !content) return null
    return content
})
</script>

<template>
    <MarkdownContent v-if="markdownSource" :source="markdownSource" />
</template>
```

- [ ] **Step 4: Visual sanity-check that the three files compile**

Vite HMR should pick them up automatically. They are not yet imported anywhere, so no on-screen change is expected. Verify by opening the dev tools console in the browser — there should be no Vite import errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/session/detail/items/claude_code/ReadResultContent.vue \
        frontend/src/components/session/detail/items/claude_code/BashResultContent.vue \
        frontend/src/components/session/detail/items/claude_code/WebContentResult.vue
git commit -m "refactor(tool-use): extract Read/Bash/Web result components

Pulled out of items/claude_code/ToolUseContent.vue ahead of the shell
extraction. No behavior change yet — these components are not imported
anywhere until the toolHelpers module is wired up."
```

---

## Task 2: BaseToolHelpers contract

Add the contract class with neutral defaults to `providers/baseHelpers.js`. Same style as the existing `BaseProviderHelpers` (docstring per method, neutral default that does the safe thing — typically `null` / `false` / empty objects). The class is exported alongside `BaseProviderHelpers`.

**Files:**
- Modify: `frontend/src/providers/baseHelpers.js`

- [ ] **Step 1: Add the `BaseToolHelpers` class at the end of the file**

Append to `providers/baseHelpers.js` (after the closing brace of `BaseProviderHelpers`, before any trailing newline):

```javascript
/**
 * Base class for per-provider tool-rendering helpers consumed by the generic
 * `items/ToolUseContent.vue` shell. Mirrors the `BaseProviderHelpers` pattern:
 * each provider ships a subclass that overrides only the behaviours that
 * differ from the neutral defaults defined here. The shell never branches on
 * provider identity — it asks normalized questions and renders the answers.
 *
 * Two return shapes are used by the rendering hooks:
 *
 *   { component: VueComponent, props: object }   — explicit descriptor
 *   null                                          — fall back to JsonHumanView
 *
 * The `ctx` argument passed to `getInputRendering` / `getResultRendering`
 * carries the shell's runtime state. Common fields:
 *   - ``isSubagent``   — boolean; whether this tool_use lives in a subagent
 *   - ``backendPatch`` / ``originalFile`` / ``backendPatchLoading`` — for
 *     Edit/Write rendering when the backend has computed structured patches
 */
export class BaseToolHelpers {
    static provider = null

    // ─── Summary surface (header + working-assistant inline) ─────────────
    //
    // Producers: ToolUseContent.vue (rich card header), WorkingAssistantMessage.vue
    // (status line). Returned shape is the same dict the legacy
    // ``computeToolSummary`` produced — kept as-is so existing consumers don't
    // have to be rewritten:
    //   { displayName: { name, namespace } | null,
    //     inline:      string | null,
    //     rich:        { kind, description, fileIconSrc, skill, grep,
    //                    globPattern, webFetchUrl, webSearchQuery,
    //                    toolSearchQuery, todoDescription } }

    /** Build the summary descriptor for a tool_use. Default: minimal stub. */
    computeToolSummary(/* name, input, baseDir */) {
        return {
            displayName: null,
            inline: null,
            rich: {
                kind: null,
                description: null,
                fileIconSrc: null,
                skill: null,
                grep: null,
                globPattern: null,
                webFetchUrl: null,
                webSearchQuery: null,
                toolSearchQuery: null,
                todoDescription: null,
            },
        }
    }

    /**
     * Convert a tool name + input to a gerund form for the
     * "{provider} is …ing" status line. Default: ``null`` (no verb known).
     */
    getVerb(/* name, input */) {
        return null
    }

    /**
     * Override for the rich card header label when the bare tool name is
     * not the right thing to display (e.g. a tool called ``TodoWrite``
     * should appear as ``Todo``). Returns a string, or ``null`` to let the
     * shell render the raw tool name (``name.replaceAll('__', ' ')``).
     * Note: this is for static per-name overrides; dynamic overrides driven
     * by the tool's input (e.g. Task ``subagent_type``) flow through
     * ``computeToolSummary().displayName`` and the ``isTask && displayName``
     * template branch. Default: no override.
     */
    getHeaderLabel(/* name */) {
        return null
    }

    // ─── Input / Result rendering ────────────────────────────────────────

    /**
     * Return ``{ component, props }`` for the tool's input area, or ``null``
     * to fall back to ``JsonHumanView``. ``ctx`` carries the shell's runtime
     * state (see class docstring). Default: always fall back.
     */
    getInputRendering(/* name, input, ctx */) {
        return null
    }

    /**
     * Return ``{ component, props }`` for the tool's Result area, or ``null``
     * to fall back to ``JsonHumanView``. Called when the tool result has been
     * fetched and is non-empty. Default: always fall back.
     */
    getResultRendering(/* name, result, input, ctx */) {
        return null
    }

    /**
     * Per-tool overrides for ``JsonHumanView`` when rendering the input
     * fallback (e.g. force ``Bash.command`` to render as a code block).
     * Default: no overrides.
     */
    getInputOverrides(/* name */) {
        return {}
    }

    /**
     * Per-tool overrides for ``JsonHumanView`` when rendering the result
     * fallback. Default: no overrides.
     */
    getResultOverrides(/* name */) {
        return {}
    }

    // ─── Capability flags driving shell-level features ───────────────────

    /**
     * Whether this tool's input carries a ``file_path`` field that should
     * power the View-in-Files button and related affordances. Default: false.
     */
    usesFilePath(/* name, input */) {
        return false
    }

    /**
     * Whether this tool modifies a file (so the shell shows ``+N -N`` stats
     * in the header and may auto-open it for live diffs). Default: false.
     */
    isFileChangeTool(/* name */) {
        return false
    }

    /**
     * Whether this tool is an "Agent / Task" spawner (the shell shows the
     * View-Agent button + spinner / robot icon). Default: false.
     */
    isAgentTool(/* name */) {
        return false
    }

    /**
     * Whether the shell should auto-open the details for this tool when the
     * item arrives live via WebSocket and the user has ``settings.showDiffs``
     * enabled. Default: false.
     */
    shouldAutoOpenLive(/* name, input */) {
        return false
    }

    /**
     * Whether the Result section should remain visible when this tool
     * errored, even though there is no specialized input renderer claiming
     * it. Used for tools whose error message is too terse on its own (e.g.
     * Bash's "Exit code N" — the user wants to see the full stdout/stderr
     * via the Result fallback). The shell also falls back to showing the
     * Result for the special ``'Unknown error'`` text regardless of what
     * this method returns. Default: false.
     */
    showsResultOnError(/* name */) {
        return false
    }

    /**
     * Whether the Result section should be shown when this tool has a
     * specialized input renderer AND the error is the special
     * ``'Unknown error'`` text. Used to surface diagnostic detail for tools
     * whose specialized input UI alone isn't enough (Edit/Write opt in;
     * TodoWrite stays hidden). Default: false.
     */
    showsResultOnUnknownError(/* name */) {
        return false
    }

    // ─── File-change stats (header ``+N -N``) ────────────────────────────
    //
    // The shell does the layout (``+N -N``); the helper provides the values.
    // ``toolState`` is the dataStore.getToolState(...) entry (carries
    // ``extra`` JSON when the backend computed stats). ``isSubagent`` flips
    // the source — backend stats for the main session, computed-from-input
    // for subagents.

    /**
     * Compute ``{ lines_added, lines_removed? }`` for the ``+N -N`` header,
     * or ``null`` when this tool doesn't carry stats. Default: null.
     */
    computeFileChangeStats(/* name, input, toolState, isSubagent */) {
        return null
    }

    // ─── Backend-patch fetcher (used by Edit/Write to draw full-file diffs)
    //
    // Some providers post-process the tool_result item to attach a
    // structured patch and the original file content. The shell handles
    // the fetch (the parsed item is read from the store or via
    // ``loadSessionItemsRanges``); the helper decides whether the fetch is
    // needed and how to extract the data.

    /**
     * Whether the shell should fetch the tool_result item and pass extracted
     * data through ``ctx`` to ``getInputRendering``. Default: false.
     */
    needsBackendPatchFetch(/* name */) {
        return false
    }

    /**
     * Extract ``{ patch, originalFile }`` from a parsed tool_result item, or
     * ``null`` when the data is absent. Default: never extracts.
     */
    extractBackendPatchData(/* parsedToolResult */) {
        return null
    }
}
```

- [ ] **Step 2: Re-read the file to verify the class is correctly appended**

Open `providers/baseHelpers.js` and confirm:
- The new `BaseToolHelpers` class sits after `BaseProviderHelpers`.
- Both classes are exported.
- No syntax errors (Vite would flag this in the browser dev console).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/providers/baseHelpers.js
git commit -m "refactor(providers): add BaseToolHelpers contract

Mirrors BaseProviderHelpers — neutral defaults for tool rendering hooks
that providers will subclass. Not yet wired up; no consumer."
```

---

## Task 3: ClaudeCodeToolHelpers — port the existing logic

Create `providers/claude_code/toolHelpers.js` with `ClaudeCodeToolHelpers extends BaseToolHelpers`. This is the bulk of the move: it absorbs `utils/toolSummary.js` *and* all per-tool logic that currently lives as constants / computed in `items/claude_code/ToolUseContent.vue`. After this task, the helpers module is complete but still unused.

**Files:**
- Create: `frontend/src/providers/claude_code/toolHelpers.js`

- [ ] **Step 1: Create the file with the full implementation**

```javascript
/**
 * Tool-rendering helpers for Claude Code sessions.
 *
 * Consumed by:
 *   - components/session/detail/items/ToolUseContent.vue (rich card)
 *   - components/session/detail/items/WorkingAssistantMessage.vue (status line)
 *
 * Encapsulates everything that's specific to Claude Code's tool catalogue
 * (Edit/Write/Read/Bash/Grep/Glob/Web{*}/Skill/Task/MCP/...) — naming,
 * summary formatting, which Vue component renders an input or a result, the
 * JsonHumanView overrides, the file-change stats source, and the backend-
 * patch extractor. The generic shell never branches on tool name itself.
 */

import { PROVIDER, AGENT_TOOL_NAMES } from '../../constants'
import { getIconUrl, getFileIconId } from '../../utils/fileIcons'
import { getTodoDescription, isValidTodos } from '../../utils/todoList'
import { BaseToolHelpers } from '../baseHelpers'

import EditContent from '../../components/session/detail/items/claude_code/EditContent.vue'
import WriteContent from '../../components/session/detail/items/claude_code/WriteContent.vue'
import TodoContent from '../../components/session/detail/items/claude_code/TodoContent.vue'
import ReadResultContent from '../../components/session/detail/items/claude_code/ReadResultContent.vue'
import BashResultContent from '../../components/session/detail/items/claude_code/BashResultContent.vue'
import WebContentResult from '../../components/session/detail/items/claude_code/WebContentResult.vue'

const FILE_PATH_TOOLS = new Set(['Edit', 'Write', 'Read'])
const FILE_CHANGE_TOOLS = new Set(['Edit', 'Write'])
const DIRECT_CONTENT_TOOLS = new Set(['Bash', 'WebFetch', 'WebSearch'])
const TASK_SUBAGENT_LABELS = {
    explore: 'exploring',
    plan: 'planning',
    bash: 'bashing',
}

const INPUT_OVERRIDES = {
    Bash: { command: { valueType: 'string-code', language: 'bash' } },
}

const CAT_N_LINE_RE = /^(\s*\d+)[→\t](.*)$/

// ─── Helpers private to this module ─────────────────────────────────────

function capitalize(str) {
    return str.replace(/-/g, ' ').replace(/^\w/, c => c.toUpperCase())
}

function fileIconFor(filePath) {
    if (!filePath) return null
    const filename = filePath.split('/').pop() || filePath
    const iconId = getFileIconId(filename)
    return iconId !== 'default-file' ? getIconUrl(iconId) : null
}

function formatRelativePath(path, baseDir) {
    if (!path) return path
    if (baseDir && path.startsWith(baseDir + '/')) {
        return path.slice(baseDir.length + 1)
    }
    return path
}

function getDisplayName(name, input) {
    if (AGENT_TOOL_NAMES.has(name)) {
        const sat = input?.subagent_type
        if (!sat || sat === 'general-purpose') return null
        const colonIdx = sat.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(sat.slice(colonIdx + 1)),
                namespace: capitalize(sat.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(sat), namespace: null }
    }
    if (name === 'Skill' && input?.skill) {
        const skill = input.skill
        const colonIdx = skill.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(skill.slice(colonIdx + 1)),
                namespace: capitalize(skill.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(skill), namespace: null }
    }
    return null
}

function buildGrepInline(pattern, fileType, path) {
    const parts = []
    if (pattern) parts.push(pattern)
    if (fileType) parts.push(`in ${fileType} files`)
    if (path) parts.push(`in ${path}`)
    return parts.length ? parts.join(' ') : null
}

function emptyRich(kind = null, overrides = {}) {
    return {
        kind,
        description: null,
        fileIconSrc: null,
        skill: null,
        grep: null,
        globPattern: null,
        webFetchUrl: null,
        webSearchQuery: null,
        toolSearchQuery: null,
        todoDescription: null,
        ...overrides,
    }
}

function parseCatNContent(content) {
    if (typeof content !== 'string') return null
    const lines = content.split('\n')
    if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    if (lines.length === 0) return null
    const firstNonEmpty = lines.find(l => l.length > 0)
    if (!firstNonEmpty || !CAT_N_LINE_RE.test(firstNonEmpty)) return null
    return true
}

function getDirectContent(name, result) {
    if (!DIRECT_CONTENT_TOOLS.has(name)) return null
    const content = typeof result === 'string' ? result : result?.content
    if (typeof content !== 'string' || !content) return null
    return content
}

// ─── ClaudeCodeToolHelpers ──────────────────────────────────────────────

export class ClaudeCodeToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.CLAUDE_CODE

    // ─── Summary ─────────────────────────────────────────────────────────

    computeToolSummary(name, input, baseDir) {
        const safeInput = input || {}
        const displayName = getDisplayName(name, safeInput)

        if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
            const description = formatRelativePath(safeInput.file_path, baseDir)
            return {
                displayName,
                inline: description,
                rich: emptyRich('description', {
                    description,
                    fileIconSrc: fileIconFor(safeInput.file_path),
                }),
            }
        }

        if (name === 'Skill' && displayName) {
            return {
                displayName,
                inline: displayName.name,
                rich: emptyRich('skill', { skill: displayName }),
            }
        }

        if (name === 'Grep') {
            const pattern = safeInput.pattern || null
            const fileType = safeInput.type || safeInput.glob || null
            const rawPath = safeInput.path || null
            if (pattern || fileType || rawPath) {
                const path = rawPath ? formatRelativePath(rawPath, baseDir) : null
                const pathIconSrc = rawPath ? fileIconFor(rawPath) : null
                return {
                    displayName,
                    inline: buildGrepInline(pattern, fileType, path),
                    rich: emptyRich('grep', {
                        grep: { pattern, fileType, path, pathIconSrc },
                    }),
                }
            }
        }

        if (name === 'Glob' && safeInput.pattern) {
            return {
                displayName,
                inline: safeInput.pattern,
                rich: emptyRich('glob', { globPattern: safeInput.pattern }),
            }
        }

        if (name === 'WebFetch' && safeInput.url) {
            return {
                displayName,
                inline: safeInput.url,
                rich: emptyRich('webFetch', { webFetchUrl: safeInput.url }),
            }
        }

        if (name === 'WebSearch' && safeInput.query) {
            return {
                displayName,
                inline: safeInput.query,
                rich: emptyRich('webSearch', { webSearchQuery: safeInput.query }),
            }
        }

        if (name === 'ToolSearch' && safeInput.query) {
            return {
                displayName,
                inline: safeInput.query,
                rich: emptyRich('toolSearch', { toolSearchQuery: safeInput.query }),
            }
        }

        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return {
                displayName,
                inline: null,
                rich: emptyRich('todo', {
                    todoDescription: getTodoDescription(safeInput.todos),
                }),
            }
        }

        const description = safeInput.description || null
        return {
            displayName,
            inline: description,
            rich: emptyRich(description ? 'description' : null, { description }),
        }
    }

    getVerb(name, input) {
        if (!name) return null
        if (AGENT_TOOL_NAMES.has(name)) {
            const subtype = input?.subagent_type?.toLowerCase()
            if (subtype && TASK_SUBAGENT_LABELS[subtype]) {
                return TASK_SUBAGENT_LABELS[subtype]
            }
            return 'agenting'
        }
        if (name.startsWith('mcp__')) {
            const parts = name.split('__')
            const server = parts[1] || 'mcp'
            return `mcping (${server})`
        }
        const lower = name.toLowerCase()
        return lower.replace(/[aeiou]+$/, '') + 'ing'
    }

    getHeaderLabel(name) {
        if (name === 'TodoWrite') return 'Todo'
        return null
    }

    // ─── Input / Result rendering ───────────────────────────────────────

    getInputRendering(name, input, ctx) {
        const safeInput = input || {}
        const editProps = {
            input: safeInput,
            backendPatch: ctx?.backendPatch ?? null,
            backendPatchLoading: ctx?.backendPatchLoading ?? false,
            originalFile: ctx?.originalFile ?? null,
            isSubagent: !!ctx?.isSubagent,
        }
        if (name === 'Edit' && 'old_string' in safeInput && 'new_string' in safeInput) {
            return { component: EditContent, props: editProps }
        }
        if (name === 'Write' && 'content' in safeInput) {
            return { component: WriteContent, props: editProps }
        }
        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return { component: TodoContent, props: { todos: safeInput.todos } }
        }
        return null
    }

    getResultRendering(name, result, input /*, ctx */) {
        if (name === 'Read') {
            const content = typeof result === 'string' ? result : result?.content
            if (parseCatNContent(content)) {
                return { component: ReadResultContent, props: { result, input } }
            }
            return null
        }
        if (name === 'Bash' && getDirectContent(name, result) != null) {
            return { component: BashResultContent, props: { result } }
        }
        if ((name === 'WebFetch' || name === 'WebSearch') && getDirectContent(name, result) != null) {
            return { component: WebContentResult, props: { result } }
        }
        return null
    }

    getInputOverrides(name) {
        return INPUT_OVERRIDES[name] ?? {}
    }

    // ─── Capability flags ────────────────────────────────────────────────

    usesFilePath(name, input) {
        return FILE_PATH_TOOLS.has(name) && !!input?.file_path
    }

    isFileChangeTool(name) {
        return FILE_CHANGE_TOOLS.has(name)
    }

    isAgentTool(name) {
        return AGENT_TOOL_NAMES.has(name)
    }

    shouldAutoOpenLive(name /*, input */) {
        return name === 'Edit' || name === 'Write'
    }

    showsResultOnError(name) {
        // Bash errors only carry "Exit code N" — the full output is in the result.
        return name === 'Bash'
    }

    showsResultOnUnknownError(name) {
        // Edit/Write benefit from the diagnostic detail in the Result section;
        // TodoWrite never shows the Result (its specialized renderer is enough).
        return name === 'Edit' || name === 'Write'
    }

    // ─── File-change stats ───────────────────────────────────────────────

    computeFileChangeStats(name, input, toolState, isSubagent) {
        if (!FILE_CHANGE_TOOLS.has(name)) return null

        // Subagent: stats come from the input directly (no backend patch).
        if (isSubagent) {
            if (name === 'Edit' && input?.old_string != null && input?.new_string != null) {
                return {
                    lines_removed: input.old_string.split('\n').length,
                    lines_added: input.new_string.split('\n').length,
                }
            }
            if (name === 'Write' && input?.content != null) {
                return { lines_added: input.content.split('\n').length }
            }
            return null
        }

        // Main session: stats come from the backend (ToolResultLink.extra JSON).
        if (!toolState?.extra) return null
        try {
            return JSON.parse(toolState.extra)
        } catch {
            return null
        }
    }

    // ─── Backend-patch fetcher ───────────────────────────────────────────

    needsBackendPatchFetch(name) {
        return FILE_CHANGE_TOOLS.has(name)
    }

    extractBackendPatchData(parsedToolResult) {
        const toolUseResult = parsedToolResult?.toolUseResult
        if (!toolUseResult) return null
        const patch = toolUseResult.structuredPatch
        const originalFile = toolUseResult.originalFile
        const out = {}
        if (Array.isArray(patch) && patch.length > 0) out.patch = patch
        if (typeof originalFile === 'string') out.originalFile = originalFile
        return Object.keys(out).length > 0 ? out : null
    }
}

export const claudeCodeToolHelpers = new ClaudeCodeToolHelpers()
```

- [ ] **Step 2: Visually verify the file compiles**

Vite should pick up the file. It is not yet imported, so the browser console should show no errors and no behaviour change.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/providers/claude_code/toolHelpers.js
git commit -m "refactor(providers): add ClaudeCodeToolHelpers

Absorbs utils/toolSummary.js (getDisplayName, computeToolSummary, getVerb)
plus all per-tool constants and logic currently embedded in
items/claude_code/ToolUseContent.vue (INPUT_OVERRIDES, FILE_CHANGE_TOOLS,
DIRECT_CONTENT_TOOLS, parseCatNContent, file-change stats source,
backend-patch extractor). Not wired yet."
```

---

## Task 4: Registry in providers/index.js

Add a `PROVIDER_TOOL_HELPERS` map and a `getToolHelpers(provider)` export. Same pattern as the existing `PROVIDER_HELPERS` / `getProviderHelpers`.

**Files:**
- Modify: `frontend/src/providers/index.js`

- [ ] **Step 1: Add the import + map + export**

In `providers/index.js`, after the existing `import { ClaudeCodeHelpers, claudeCodeHelpers } from './claude_code/helpers'` line, add the tool-helpers import:

```javascript
import { ClaudeCodeToolHelpers, claudeCodeToolHelpers } from './claude_code/toolHelpers'
```

After the existing `PROVIDER_HELPERS` const, add:

```javascript
const PROVIDER_TOOL_HELPERS = {
    [ClaudeCodeToolHelpers.provider]: claudeCodeToolHelpers,
}
```

After the existing `getProviderHelpers` function, add:

```javascript
export function getToolHelpers(provider) {
    return PROVIDER_TOOL_HELPERS[provider] ?? null
}
```

- [ ] **Step 2: Re-read the file to confirm the additions**

Open `providers/index.js` and verify the new import, map, and function sit logically next to their `Helpers` siblings.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/providers/index.js
git commit -m "refactor(providers): register ClaudeCodeToolHelpers in the registry

Adds PROVIDER_TOOL_HELPERS + getToolHelpers(provider). No consumer yet."
```

---

## Task 5: Generic shell `items/ToolUseContent.vue`

Convert the existing `items/claude_code/ToolUseContent.vue` into a generic shell at `items/ToolUseContent.vue`. This task is a **migration with substitutions**: most of the file (state machine, polling, abort, sessionActive watcher, subagent button, View-in-Files button, CodeCommentsIndicator, file-change stats UI, error callout, layout, ALL the CSS) is preserved verbatim. The substitutions replace local Claude Code knowledge with helper calls.

**Files:**
- Create: `frontend/src/components/session/detail/items/ToolUseContent.vue` (copied from the existing claude_code one, then modified)

The simplest physical move is: copy the existing file to its new location, then apply the substitutions below. We keep the old file in place until Task 6 — that way the app keeps working between commits.

- [ ] **Step 1: Copy the existing file to the new location**

```bash
cp frontend/src/components/session/detail/items/claude_code/ToolUseContent.vue \
   frontend/src/components/session/detail/items/ToolUseContent.vue
```

- [ ] **Step 2: Update import paths in the new file (depth changed: items/X → items/)**

The new shell sits one level shallower than the original, so every relative import that started with `'../../../../../'` (5 levels up to `frontend/src/`) becomes `'../../../../'` (4 levels). Apply this to all imports in the `<script setup>` block.

Concretely, in the new file's import block, replace:

```javascript
import { useCodeCommentsStore } from '../../../../../stores/codeComments'
import CodeCommentsIndicator from '../../../../ui/CodeCommentsIndicator.vue'
import { useDataStore } from '../../../../../stores/data'
import { useSettingsStore } from '../../../../../stores/settings'
import { apiFetch } from '../../../../../utils/api'
import { getLanguageFromPath } from '../../../../../utils/languages'
import { computeToolSummary } from '../../../../../utils/toolSummary'
import { AGENT_TOOL_NAMES, PROCESS_STATE, PROCESS_STATE_COLORS } from '../../../../../constants'
import { stopSubagent } from '../../../../../composables/useWebSocket'
import { getSessionCutoffMs } from '../../../../../utils/sessions'
import { getParsedContent, hasContent } from '../../../../../utils/parsedContent'
import { isValidTodos } from '../../../../../utils/todoList'
import JsonHumanView from '../../../../json/JsonHumanView.vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import ProcessDuration from '../../../../ui/ProcessDuration.vue'
import EditContent from './EditContent.vue'
import WriteContent from './WriteContent.vue'
import TodoContent from './TodoContent.vue'
```

with:

```javascript
import { useCodeCommentsStore } from '../../../../stores/codeComments'
import CodeCommentsIndicator from '../../../ui/CodeCommentsIndicator.vue'
import { useDataStore } from '../../../../stores/data'
import { useSettingsStore } from '../../../../stores/settings'
import { apiFetch } from '../../../../utils/api'
import { PROCESS_STATE, PROCESS_STATE_COLORS } from '../../../../constants'
import { stopSubagent } from '../../../../composables/useWebSocket'
import { getSessionCutoffMs } from '../../../../utils/sessions'
import { getParsedContent, hasContent } from '../../../../utils/parsedContent'
import { getToolHelpers } from '../../../../providers'
import JsonHumanView from '../../../json/JsonHumanView.vue'
import MarkdownContent from '../../../ui/MarkdownContent.vue'
import AppTooltip from '../../../ui/AppTooltip.vue'
import ProcessDuration from '../../../ui/ProcessDuration.vue'
```

Notes on what changed:
- Removed: `getLanguageFromPath`, `computeToolSummary`, `AGENT_TOOL_NAMES`, `isValidTodos`, `EditContent`, `WriteContent`, `TodoContent` — all moved into `claudeCodeToolHelpers`.
- Added: `getToolHelpers` from `'../../../../providers'`.
- Depth of every other path adjusted by removing one `../`.

- [ ] **Step 3: Resolve the helpers from the session's provider**

Right after the `const codeCommentsStore = useCodeCommentsStore()` line, add:

```javascript
// Resolve tool helpers from the session's provider. Resolved via a computed so
// it stays correct if the session reference changes (rare, but keeps reactivity).
const toolHelpers = computed(() => {
    const session = dataStore.getSession(props.sessionId)
    return getToolHelpers(session?.provider)
})
```

The `computed` is already imported from `vue` at the top of the file.

- [ ] **Step 4: Replace the local summary logic with helper calls**

Find the block (anchor: `const summary = computed`) around line 422:

```javascript
const summary = computed(() => computeToolSummary(props.name, props.input, sessionBaseDir.value))
```

Replace with:

```javascript
const summary = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return { displayName: null, inline: null, rich: { kind: null } }
    return helpers.computeToolSummary(props.name, props.input, sessionBaseDir.value)
})
```

The `displayName`, `summaryDescription`, `summaryFileIconSrc`, `summarySkill`, `summaryGrep`, `summaryGlob`, `summaryWebFetchUrl`, `summaryWebSearchQuery`, `summaryToolSearchQuery`, `summaryTodo` computed below it stay unchanged — they read from `summary.value`.

- [ ] **Step 5: Replace per-tool computed flags with helper-driven equivalents**

Find and replace each block:

| Anchor (current code, ~line) | New code |
|---|---|
| `const isTodoWrite = computed(() => props.name === 'TodoWrite')` (~437) | (delete — replaced by the new `headerLabel` computed below for the header label, and by `inputRendering` for the body) |
| `const todosValid = computed(() => isTodoWrite.value && isValidTodos(props.input?.todos))` (~438) | (delete — only used by the body template, which is replaced by `inputRendering`, and by `showResultDetails`, which is replaced separately) |
| `const usesFilePath = computed(() => (props.name === 'Edit' \|\| ...))` (~441) | `const usesFilePath = computed(() => !!toolHelpers.value?.usesFilePath(props.name, props.input))` |
| `const isEdit = computed(() => props.name === 'Edit')` (~479) | (delete) |
| `const editValid = computed(() => isEdit.value && 'old_string' in props.input && 'new_string' in props.input)` (~480) | (delete — encoded in `inputRendering`) |
| `const isWrite = computed(() => props.name === 'Write')` (~483) | (delete) |
| `const writeValid = computed(() => isWrite.value && 'content' in props.input)` (~484) | (delete) |
| `const isTask = computed(() => AGENT_TOOL_NAMES.has(props.name))` (~497) | `const isTask = computed(() => !!toolHelpers.value?.isAgentTool(props.name))` |
| `const isRead = computed(() => props.name === 'Read')` (~393) | (delete — superseded by helper-driven rendering) |
| `const readResultCode = computed(() => { ... })` (~399) plus the surrounding `parseCatNContent` function and `CAT_N_LINE_RE` constant (~350-414) | (delete all three — moved into `ClaudeCodeToolHelpers` and `ReadResultContent`) |
| `const INPUT_OVERRIDES = { ... }` (~317) and `const inputOverrides = computed(...)` (~321) | replace `inputOverrides` with: `const inputOverrides = computed(() => toolHelpers.value?.getInputOverrides(props.name) ?? {})`; delete the local `INPUT_OVERRIDES` constant |
| `const RESULT_OVERRIDES = {}` (~325) and `const resultOverrides = computed(...)` (~327) | replace `resultOverrides` with: `const resultOverrides = computed(() => toolHelpers.value?.getResultOverrides(props.name) ?? {})`; delete the local `RESULT_OVERRIDES` constant |
| `const DIRECT_CONTENT_TOOLS = new Set([...])` (~330) and `isDirectContentTool` (~331) and `directContentSource` (~333) | (delete all three — superseded by helper-driven `resultRendering`) |
| `const FILE_CHANGE_TOOLS = new Set(['Edit', 'Write'])` (~569; only the Set — the `fileChangeStats` computed is replaced in Step 6 below) | (delete) |
| `const isEditOrWrite = computed(() => props.name === 'Edit' \|\| props.name === 'Write')` (~709, used by `toolCommentsCount`) | `const isEditOrWrite = computed(() => !!toolHelpers.value?.isFileChangeTool(props.name))` |
| `const showResultDetailsOnError = computed(() => { if (!isToolError.value) return false; return props.name === 'Bash' \|\| toolErrorText.value === 'Unknown error' })` (~507-510) | `const showResultDetailsOnError = computed(() => { if (!isToolError.value) return false; return !!toolHelpers.value?.showsResultOnError(props.name) \|\| toolErrorText.value === 'Unknown error' })` |

Also **add** a new `headerLabel` computed (anywhere after `toolHelpers` is declared, e.g. just after the `summary` block):

```javascript
// Static per-name header label override (e.g. "TodoWrite" → "Todo").
// Dynamic overrides driven by the tool's input (Task subagent_type, Skill name)
// continue to flow through summary.displayName.
const headerLabel = computed(() => toolHelpers.value?.getHeaderLabel(props.name) ?? null)
```

After this step, the script section no longer has any `'Edit' / 'Write' / 'Read' / 'Bash' / 'TodoWrite' / 'WebFetch'` / `AGENT_TOOL_NAMES` literal references.

- [ ] **Step 5b: Update the header summary template to use `headerLabel`**

In the template, find the header `<strong>` chain (anchor: `class="items-details-summary-name"`, ~line 758-760):

```html
<strong v-if="isTask && displayName" class="items-details-summary-name">{{ displayName.name }}<span v-if="displayName.namespace" class="items-details-summary-quiet"> ({{ displayName.namespace }})</span></strong>
<strong v-else-if="isTodoWrite" class="items-details-summary-name">Todo</strong>
<strong v-else class="items-details-summary-name">{{ name.replaceAll('__', ' ') }}</strong>
```

Replace the middle line with:

```html
<strong v-else-if="headerLabel" class="items-details-summary-name">{{ headerLabel }}</strong>
```

The first and third lines are unchanged. After this step, the template no longer references `isTodoWrite` (it was only used at this site in the header and in the body, which is replaced in Step 10).

- [ ] **Step 6: Replace `fileChangeStats` with the helper call**

Find (anchor: `const FILE_CHANGE_TOOLS`, ~line 569):

```javascript
const FILE_CHANGE_TOOLS = new Set(['Edit', 'Write'])
const fileChangeStats = computed(() => {
    if (!FILE_CHANGE_TOOLS.has(props.name)) return null
    if (props.parentSessionId) {
        if (props.name === 'Edit' && props.input?.old_string != null && props.input?.new_string != null) {
            return {
                lines_removed: props.input.old_string.split('\n').length,
                lines_added: props.input.new_string.split('\n').length,
            }
        }
        if (props.name === 'Write' && props.input?.content != null) {
            return { lines_added: props.input.content.split('\n').length }
        }
        return null
    }
    if (!toolState.value?.extra) return null
    try {
        return JSON.parse(toolState.value.extra)
    } catch {
        return null
    }
})
```

Replace with:

```javascript
const fileChangeStats = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return null
    return helpers.computeFileChangeStats(
        props.name,
        props.input,
        toolState.value,
        !!props.parentSessionId,
    )
})
```

- [ ] **Step 7: Replace the backend-patch fetcher**

Find (anchor: `watchEffect(async`, ~line 605, the block starting with the comment `// In subagents, Edit/Write tools don't use backend patch data`):

The full existing block (~50 lines) gates the fetch on `editValid.value || writeValid.value`, fetches the parsed item, and reads `toolUseResult.structuredPatch` / `toolUseResult.originalFile`.

Replace with the helper-driven version:

```javascript
const fileChangeBackendPatch = ref(null)
const fileChangeOriginalFile = ref(null)
const fileChangeBackendPatchLoading = ref(false)

watchEffect(async () => {
    // Subagent path: helpers don't fetch backend patch (rendering uses input directly).
    if (props.parentSessionId) return

    const helpers = toolHelpers.value
    if (!helpers || !helpers.needsBackendPatchFetch(props.name) || !fileChangeStats.value) {
        fileChangeBackendPatch.value = null
        fileChangeOriginalFile.value = null
        return
    }
    const lineNum = toolState.value?.toolResultLineNum
    if (!lineNum) return
    if (fileChangeBackendPatch.value?._lineNum === lineNum) return

    function applyExtracted(parsed) {
        const data = helpers.extractBackendPatchData(parsed)
        if (!data) return
        if (data.patch) {
            fileChangeBackendPatch.value = Object.freeze(
                Object.assign([...data.patch], { _lineNum: lineNum })
            )
        }
        if (typeof data.originalFile === 'string') {
            fileChangeOriginalFile.value = data.originalFile
        }
    }

    const item = dataStore.getSessionItem(props.sessionId, lineNum)
    if (item && hasContent(item)) {
        applyExtracted(getParsedContent(item))
        return
    }
    fileChangeBackendPatchLoading.value = true
    try {
        await dataStore.loadSessionItemsRanges(
            props.projectId, props.sessionId, [lineNum], props.parentSessionId
        )
        const fetched = dataStore.getSessionItem(props.sessionId, lineNum)
        if (fetched && hasContent(fetched)) {
            applyExtracted(getParsedContent(fetched))
        }
    } finally {
        fileChangeBackendPatchLoading.value = false
    }
})
```

The two `ref` declarations (`fileChangeBackendPatch`, `fileChangeOriginalFile`, `fileChangeBackendPatchLoading`) are unchanged from the original.

- [ ] **Step 8: Replace `shouldAutoOpen` with the helper-driven version**

Find (anchor: `const shouldAutoOpen = computed`, ~line 530):

```javascript
const shouldAutoOpen = computed(() => {
    if (!settingsStore.showDiffs) return false
    if (isToolError.value) return false
    if (!isLive.value) return false
    return props.name === 'Edit' || props.name === 'Write'
})
```

Replace with:

```javascript
const shouldAutoOpen = computed(() => {
    if (!settingsStore.showDiffs) return false
    if (isToolError.value) return false
    if (!isLive.value) return false
    return !!toolHelpers.value?.shouldAutoOpenLive(props.name, props.input)
})
```

- [ ] **Step 9: Update the `with-right-part` class condition**

Find (anchor: `class="item-details tool-use"`, ~line 755 in the template):

```html
:class="{'with-right-part' : (isTask && !parentSessionId) || isToolRunning || isToolError || fileChangeStats || canViewInFilesTab}"
```

This stays exactly as-is — `isTask`, `isToolRunning`, `isToolError`, `fileChangeStats`, `canViewInFilesTab` all remain valid (they're either kept or replaced in-place above).

- [ ] **Step 10: Replace the `summaryDescription` template branch with `inputRendering`**

Find the body of the template inside `<template v-if="isOpen">` (~line 883). Today it has a chain:

```html
<TodoContent v-if="isTodoWrite && todosValid" :todos="input.todos" />
<EditContent v-else-if="editValid" :input="input" :backend-patch="fileChangeBackendPatch" ... />
<WriteContent v-else-if="writeValid" :input="input" :backend-patch="fileChangeBackendPatch" ... />
<div v-else-if="displayInput" class="tool-input">
    <JsonHumanView :value="displayInput" :overrides="inputOverrides" />
</div>
<div v-else class="tool-no-input">
    No input parameters
</div>
```

Replace with:

```html
<component
    v-if="inputRendering"
    :is="inputRendering.component"
    v-bind="inputRendering.props"
/>
<div v-else-if="displayInput" class="tool-input">
    <JsonHumanView :value="displayInput" :overrides="inputOverrides" />
</div>
<div v-else class="tool-no-input">
    No input parameters
</div>
```

Add the corresponding `inputRendering` computed in the script (anywhere after `toolHelpers` is declared, e.g. just after `displayInput`):

```javascript
const inputRendering = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers || !props.input) return null
    return helpers.getInputRendering(props.name, props.input, {
        isSubagent: !!props.parentSessionId,
        backendPatch: fileChangeBackendPatch.value,
        backendPatchLoading: fileChangeBackendPatchLoading.value,
        originalFile: fileChangeOriginalFile.value,
    })
})
```

- [ ] **Step 11: Replace the result-rendering template branches with `resultRendering`**

Find the result content section (~line 904):

```html
<div v-else-if="resultState === 'loaded' && displayResult" class="tool-result-data">
    <template v-if="readResultCode">
        <div class="read-result-header">Lines {{ readResultCode.startLine }}–{{ readResultCode.endLine }}</div>
        <MarkdownContent :source="readResultCode.markdownSource" />
    </template>
    <MarkdownContent
        v-else-if="directContentSource"
        :source="directContentSource"
    />
    <JsonHumanView
        v-else
        :value="displayResult"
        :overrides="resultOverrides"
    />
</div>
```

Replace with:

```html
<div v-else-if="resultState === 'loaded' && displayResult" class="tool-result-data">
    <component
        v-if="resultRendering"
        :is="resultRendering.component"
        v-bind="resultRendering.props"
    />
    <JsonHumanView
        v-else
        :value="displayResult"
        :overrides="resultOverrides"
    />
</div>
```

Add the `resultRendering` computed in the script:

```javascript
const resultRendering = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers || !displayResult.value) return null
    return helpers.getResultRendering(props.name, displayResult.value, props.input, {
        isSubagent: !!props.parentSessionId,
    })
})
```

The `MarkdownContent` import is still used (the ExitPlanMode error callout uses it on line 899), so keep its import.

- [ ] **Step 12: Update the `showResultDetails` computed**

Find (anchor: `const showResultDetails = computed`, ~line 513):

```javascript
const showResultDetails = computed(() => {
    if (isTodoWrite.value && todosValid.value) return false
    if (editValid.value || writeValid.value) return toolErrorText.value === 'Unknown error'
    if (isToolError.value) return showResultDetailsOnError.value
    return true
})
```

Replace with — the helpers tell us per-tool whether to surface the Result on `'Unknown error'` when a specialized renderer is in charge:

```javascript
const showResultDetails = computed(() => {
    const helpers = toolHelpers.value
    // A specialized input renderer (Edit/Write/TodoWrite) typically owns the
    // success-case UI on its own — the Result section stays hidden. Tools
    // that opt in via showsResultOnUnknownError still surface it for the
    // special "Unknown error" text (Edit/Write); TodoWrite does not.
    if (inputRendering.value) {
        if (toolErrorText.value === 'Unknown error') {
            return !!helpers?.showsResultOnUnknownError(props.name)
        }
        return false
    }
    if (isToolError.value) return showResultDetailsOnError.value
    return true
})
```

Behaviour mapping vs. the original (no semantic drift):

| Scenario | Original | New |
|---|---|---|
| TodoWrite valid, no error | `false` (early return) | `false` (`inputRendering` truthy, no error → false) |
| TodoWrite valid, `'Unknown error'` | `false` (early return) | `false` (`inputRendering` truthy, error → `helpers.showsResultOnUnknownError('TodoWrite')` returns `false`) |
| TodoWrite valid, any other error | `false` (early return) | `false` (same path as the no-error case) |
| Edit/Write valid, no error | `false` (`'Unknown error' === ...` is false) | `false` |
| Edit/Write valid, `'Unknown error'` | `true` | `true` (`helpers.showsResultOnUnknownError('Edit'\|'Write')` returns `true`) |
| Edit/Write valid, any other error | `false` | `false` |
| Edit/Write **invalid** (no `old_string`/etc.) | falls through to the `isToolError` / default branches | same — `inputRendering` returns `null`, falls through identically |
| Other tool, no error | `true` | `true` |
| Other tool, error → routes to `showResultDetailsOnError` | (Bash → true; Unknown → true; else → false) | same, with `showResultDetailsOnError` itself helper-driven |

The original semantics are preserved exactly. The `showsResultOnUnknownError` hook is what makes the helper-driven path equivalent to the original `if (isTodoWrite.value && todosValid.value) return false` short-circuit: TodoWrite returns `false` from that hook, and the early-return-on-error subtlety is reproduced one level deeper without re-introducing tool-name literals in the shell.

- [ ] **Step 13: Re-read the new file end-to-end and check for leftover Claude-Code-specific literals**

Open `frontend/src/components/session/detail/items/ToolUseContent.vue` and grep visually for:
- `'Edit'`, `'Write'`, `'Read'`, `'Bash'`, `'WebFetch'`, `'WebSearch'`, `'ToolSearch'`, `'TodoWrite'`, `'Skill'`, `'Grep'`, `'Glob'`, `'Task'`, `'Agent'` → there should be **exactly one remaining string literal** matching a tool name: `'ExitPlanMode'` (line ~899, used to render the error message as markdown — kept as a documented exception for now; future cleanup can route it through the helpers if needed). Any other tool-name literal is a missed substitution.
- `EditContent`, `WriteContent`, `TodoContent` (component names) → should be **gone**.
- `INPUT_OVERRIDES`, `RESULT_OVERRIDES`, `DIRECT_CONTENT_TOOLS`, `FILE_CHANGE_TOOLS`, `AGENT_TOOL_NAMES`, `parseCatNContent`, `CAT_N_LINE_RE`, `readResultCode`, `directContentSource`, `isEdit`, `editValid`, `isWrite`, `writeValid`, `isTodoWrite`, `todosValid`, `isRead` → should be **gone**.
- `headerLabel` (new computed) → should be **present**, used in the header summary template at the position where `isTodoWrite` was.

The CSS (`<style scoped>`) is unchanged — keep it verbatim.

- [ ] **Step 14: Verify the new file compiles**

Vite picks up the new file on save. Open the browser dev console; nothing should change visually yet because the new file is still not imported anywhere (the old `claude_code/ToolUseContent.vue` is still the one in use). Look for Vite errors related to the new file — there should be none.

If you see an error like "Cannot find module './EditContent.vue'", you missed a relative import path adjustment in Step 2. Fix and re-save.

- [ ] **Step 15: Commit**

```bash
git add frontend/src/components/session/detail/items/ToolUseContent.vue
git commit -m "refactor(tool-use): add provider-agnostic ToolUseContent shell

Same component as items/claude_code/ToolUseContent.vue, but with all
Claude Code-specific knowledge (tool name checks, INPUT_OVERRIDES,
file-change stats, backend-patch extractor, parseCatNContent, etc.)
delegated to ClaudeCodeToolHelpers via getToolHelpers(provider).

The new shell is not imported by anything yet — switch-over happens
in the next commit."
```

---

## Task 6: Wire up callers + cleanup

Switch the two consumers (`ContentList.vue` for the rich card, `WorkingAssistantMessage.vue` for the inline status line) to the new shell / new helpers, then delete the old monolithic file and the now-unused `utils/toolSummary.js`.

**Files:**
- Modify: `frontend/src/components/session/detail/items/claude_code/ContentList.vue`
- Modify: `frontend/src/components/session/detail/items/WorkingAssistantMessage.vue`
- Delete: `frontend/src/components/session/detail/items/claude_code/ToolUseContent.vue`
- Delete: `frontend/src/utils/toolSummary.js`

- [ ] **Step 1: Switch `ContentList.vue` to the generic shell**

Open `frontend/src/components/session/detail/items/claude_code/ContentList.vue:12`. Change:

```javascript
import ToolUseContent from './ToolUseContent.vue'
```

to:

```javascript
import ToolUseContent from '../ToolUseContent.vue'
```

Nothing else in `ContentList.vue` changes — the `ToolUseContent` props it passes (`name`, `input`, `tool-id`, `project-id`, `session-id`, `parent-session-id`, `line-num`, `timestamp`) are all still valid on the generic shell.

- [ ] **Step 2: Switch `WorkingAssistantMessage.vue` to provider-resolved tool helpers**

Open `frontend/src/components/session/detail/items/WorkingAssistantMessage.vue`.

Replace the existing import:

```javascript
import { computeToolSummary, getVerb } from '../../../../utils/toolSummary'
```

with:

```javascript
import { getToolHelpers } from '../../../../providers'
```

Then update the `buildPhraseGroups` function (line ~43) to take a `toolHelpers` argument:

```javascript
function buildPhraseGroups(tools, baseDir, lastStartedToolId, lastToolVisible, toolHelpers) {
    const map = new Map()
    if (!toolHelpers) return []
    for (const t of tools) {
        const verb = toolHelpers.getVerb(t.name, t.input)
        if (!verb) continue
        const { inline } = toolHelpers.computeToolSummary(t.name, t.input, baseDir)
        if (!map.has(verb)) map.set(verb, [])
        map.get(verb).push(inline)
    }
    // … rest of the function unchanged …
}
```

And update the `phraseGroups` computed to resolve the helpers:

```javascript
const phraseGroups = computed(() => {
    if (plainPhrase.value !== null) return null
    if (!props.sessionId) return []
    const session = dataStore.getSession(props.sessionId)
    const helpers = getToolHelpers(session?.provider)
    return buildPhraseGroups(props.tools, sessionBaseDir.value, props.lastStartedToolId, props.lastToolVisible, helpers)
})
```

Note: `dataStore` is already used by the existing `sessionBaseDir` and `providerLabel` computed, so the import is already there.

- [ ] **Step 3: Delete the old monolithic file**

```bash
git rm frontend/src/components/session/detail/items/claude_code/ToolUseContent.vue
```

- [ ] **Step 4: Delete the now-unused `utils/toolSummary.js`**

```bash
git rm frontend/src/utils/toolSummary.js
```

Sanity-check that nothing else imports it:

Use the Grep tool with pattern `from .*toolSummary` and check that no result remains in `frontend/src/`. The two known callers (`WorkingAssistantMessage.vue` and `claude_code/ToolUseContent.vue`) are handled — the latter no longer exists.

- [ ] **Step 5: Reload the browser tab and verify the app still loads**

Vite HMR may not apply file deletion cleanly; ask the user to do a hard reload of the browser tab (Ctrl+Shift+R). The app should load without errors. The dev console must be clean of import errors.

If there is an import error, the most likely culprit is a typo in the new import path in `ContentList.vue` (Step 1) or `WorkingAssistantMessage.vue` (Step 2).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/session/detail/items/claude_code/ContentList.vue \
        frontend/src/components/session/detail/items/WorkingAssistantMessage.vue
git commit -m "refactor(tool-use): switch consumers to the generic shell + helpers

ContentList now imports the generic items/ToolUseContent.vue shell, and
WorkingAssistantMessage resolves its summary/verb via getToolHelpers
from the session's provider. Old monolithic file and utils/toolSummary
are removed in this commit (their content lives in
providers/claude_code/toolHelpers.js)."
```

The `git rm` from Step 3 / Step 4 already staged the deletions, so they're included in the commit above (the explicit `git add` covers the modifications, the `git rm` covered the deletions).

---

## Task 7: Smoke test in browser

Manual verification that nothing regressed. Walk through each surface that the refactor touched. **The user runs the dev servers** — ask them to make sure backend + frontend are running before starting this task. If they're not, ask the user to run `uv run ./devctl.py start` in the worktree root.

This task is verification-only: no commits unless an issue is found and patched. If a regression appears, treat it as a small bugfix (separate commit referencing the relevant Task above).

- [ ] **Step 1: Open a Claude Code session that has a mix of tools**

Pick a session with at least Edit, Write, Read, Bash, Grep, Glob, WebFetch, Task, and a TodoWrite somewhere. The "All Projects" mode also works.

For each of the following, expand the tool's `<wa-details>` and verify rendering matches what it looked like before the refactor (compare against `main` if needed by switching branches in another tab):

- [ ] **Step 2: Edit tool**

- Header: filename relative to git root, with the file icon, `+N -N` stats (green/red).
- Expanded: the diff viewer renders (full-file mode if backend patch + originalFile are in the store, otherwise patch-only or fragment fallback).
- `View in Files tab` button visible (folder-open icon, top-right).
- For an Edit live diff (you can trigger one by sending a message that asks Claude to edit a file): the details auto-open with no animation.

- [ ] **Step 3: Write tool**

- Header: filename + icon + `+N` stats.
- Expanded: `WriteContent` renders the file content with diff/full toggle when `originalFile` is available.

- [ ] **Step 4: Read tool**

- Header: filename + icon.
- Expanded after Result is fetched: "Lines X–Y" header + syntax-highlighted code (via `ReadResultContent`).
- `View in Files tab` button visible.

- [ ] **Step 5: Bash tool**

- Header: shows the description (the `description` input field) if present, otherwise just "Bash".
- Expanded input: command rendered as a bash code block (no language label per the existing `.tool-input > .jhv-node :deep(pre.shiki[data-language="bash"])` CSS).
- Result section: the output rendered as a fenced code block (via `BashResultContent`).
- For a tool that errored: error callout with the error text; if the error is "Exit code N", the Result section also stays visible (the user wants the full output).

- [ ] **Step 6: Grep tool**

- Header: `pattern` in `<code>`, then `in <type> files in <path>` connectors, with the path's file icon. Pattern, fileType, and path can each be missing — only present ones render.

- [ ] **Step 7: Glob tool**

- Header: glob pattern in `<code>`.

- [ ] **Step 8: WebFetch / WebSearch**

- Header for WebFetch: URL as an external link (with the `arrow-up-right-from-square` icon).
- Header for WebSearch: query.
- Result expanded: markdown rendering.

- [ ] **Step 9: TodoWrite**

- Header: progress description with check icons for completed items.
- Expanded: `TodoContent` renders the full todo list.
- No Result section (TodoWrite never shows one when input is valid).

- [ ] **Step 10: Task / Agent**

- Header: subagent label (e.g. `Plan` for `subagent_type: "plan"` or default `Task`/`Agent`).
- Right side: spinner (orange) before `agentLink` arrives, then the `View Agent` button (with pulsing robot icon if the agent is still running).
- Background agent only: a red `Stop Agent` button next to View Agent.
- Click `View Agent` → routes to the subagent tab.
- For a subagent session: the Edit/Write tools inside the subagent show file-change stats computed from the input directly (not from the backend), and `EditContent` / `WriteContent` get `isSubagent=true`.

- [ ] **Step 11: MCP tool**

- Header: tool name with `__` replaced by spaces (e.g. `mcp gmail send-email`).
- Expanded: input rendered via `JsonHumanView` (fallback path).
- Result rendered via `JsonHumanView` (fallback path).

- [ ] **Step 12: Tool with an "Unknown error"**

- Header: red ✗ icon.
- Expanded: error callout with the error text, **and** the Result section remains visible (per `showResultDetails` for Unknown error).

- [ ] **Step 13: Working assistant message status line**

While Claude is processing a turn, the bottom of the session should show the inline `… is bashing (npm test), reading (file.txt) and editing (other.ts)…` status. Verify the verbs and the parenthesised inline targets render correctly. This consumes the new helper-based path in `WorkingAssistantMessage.vue`.

- [ ] **Step 14: Polling behaviour for slow tool results**

Send a message that triggers a tool with a delayed result (a long-running Bash or a Task that won't finish immediately). Open the Result section before the result arrives. Expected: spinner + "Result not yet available. Checking again shortly..." text, then the result populates without flicker. Closing and re-opening the Result section while the tool is still running should resume the polling.

- [ ] **Step 15: KeepAlive switch**

Switch to another session, then back. The previously-opened tool details remain in their open state (the per-toolId open flag is persisted in `dataStore`). Polling that was paused on deactivation resumes if there was still no result.

- [ ] **Step 16: Code Comments indicators**

For an Edit/Write tool with code comments attached, the indicator in the header shows the count. For a Task tool whose subagent has comments, the View-Agent button has the indicator.

- [ ] **Step 17: View in Files tab**

Click the folder-open icon on an Edit/Write/Read tool. Expected: navigate to the Files tab, with the file open and scrolled to the first modified line (Edit/Write) or the offset line (Read).

- [ ] **Step 18: All providers in the registry are sane**

Quick sanity check via the dev console — confirm that `getToolHelpers('claude_code')` returns the singleton (truthy) and `getToolHelpers('codex')` returns `null` (no Codex tool helpers yet, by design — codex sessions have no tool_use rendering surface; if someone happens to render a `<ToolUseContent>` for a codex session in the future, the shell falls back to JsonHumanView for input and result, displays the raw `name` in the header, and shows nothing for the rich summary).

If you want to convince yourself: in the dev console, run:

```javascript
import('/src/providers/index.js').then(m => console.log(m.getToolHelpers('claude_code'), m.getToolHelpers('codex')))
```

(The path may need to be adjusted depending on Vite's module resolution; otherwise check via the Vue dev tools by inspecting the computed `toolHelpers` of any `<ToolUseContent>`.)

If everything checks out, the refactor is done.

---

## Roll-back plan

If a regression is found late, the entire refactor is contained in 6 commits:

1. Add Read/Bash/Web result components
2. Add `BaseToolHelpers`
3. Add `ClaudeCodeToolHelpers`
4. Register helpers in `providers/index.js`
5. Add the generic shell
6. Switch consumers + delete old files

Reverting commit (6) restores the original `items/claude_code/ToolUseContent.vue` and `utils/toolSummary.js`, putting the consumers back on the old path. Commits (1)–(5) leave dead code that does nothing harmful — they can be reverted in any order or kept for a later attempt.
