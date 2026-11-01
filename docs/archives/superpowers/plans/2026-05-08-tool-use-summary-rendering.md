# Tool Use Shell — Summary & Error Rendering Pass 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Continuation of:** `2026-05-08-tool-use-shell-extraction.md` (already merged on this branch).

**Goal:** Eliminate the remaining Claude Code-specific knowledge from the generic shell at `frontend/src/components/session/detail/items/ToolUseContent.vue`. After Pass 1, the shell delegates the **logic** (capabilities, file-change stats, backend-patch fetch, etc.) to `ClaudeCodeToolHelpers`, but it still contains:

1. **A 7-branch template** (`v-else-if="summaryDescription"`, `summarySkill`, `summaryGrep`, `summaryGlob`, `summaryWebFetchUrl`, `summaryWebSearchQuery`, `summaryToolSearchQuery`, `summaryTodo`) that hardcodes the rendering of every Claude Code summary variant.
2. **The matching 7 computeds** that read `summary.value.rich.X` to drive those branches.
3. **Variant-specific CSS** (`.items-details-summary-grep`, `.grep-connector`, `.items-details-summary-link`, `.items-details-summary-link-icon`, `.todo-icon`, `.todo-icon-completed`).
4. **The `'ExitPlanMode'` literal** in the error callout (chooses markdown vs plain text rendering of `toolErrorText`).

This pass moves all of the above into provider-specific mini-components and helper hooks. After this pass, the shell contains **zero** tool-name string literal and **zero** template branch keyed by Claude Code-specific concepts.

**Architecture:** Same helpers approach. Two new contract methods on `BaseToolHelpers`:
- `getSummaryRendering(name, input, baseDir)` → `{ component, props } | null` — selects the mini-component that renders the summary description for this tool.
- `errorIsMarkdown(name)` → `bool` — whether `toolErrorText` should render via `<MarkdownContent>` or plain text.

The shell consumes these via the same `<component :is>` pattern already in place for `inputRendering` / `resultRendering`.

**Tech Stack:** Vue 3 Composition API + `<script setup>`, Web Awesome 3.1, ES modules.

**Project conventions:**
- All UI strings, comments, and identifiers must be in **English**.
- The project intentionally has **no tests and no linting** — verification is read-back code review + browser smoke testing.
- The user (not the implementer) restarts dev servers — Vue file edits are normally hot-reloaded by Vite.
- Composition API + `<script setup>` throughout.
- Avoid circular imports.
- Web Awesome custom events keep `wa-` prefix; native events do not.
- Use `git add <files>` only (NEVER `-A` / `-a`).

**Pass 1 minor advisories carried forward:**
- The `'ExitPlanMode'` literal flagged in Pass 1 review is resolved by this pass (Step in Task 4).
- The `summary` stub fallback for `helpers === null` (Codex) is touched by this pass — it can become tighter.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `frontend/src/components/session/detail/items/claude_code/summary/DescriptionSummary.vue` | Create | Description text with optional file icon (Edit/Write/Read with file_path; Bash/MCP description fallback) |
| `frontend/src/components/session/detail/items/claude_code/summary/SkillSummary.vue` | Create | Skill name + optional namespace in quiet mode |
| `frontend/src/components/session/detail/items/claude_code/summary/GrepSummary.vue` | Create | `pattern` in `<code>`, optional fileType filter, optional path with file icon |
| `frontend/src/components/session/detail/items/claude_code/summary/GlobSummary.vue` | Create | Glob pattern in `<code>` |
| `frontend/src/components/session/detail/items/claude_code/summary/WebFetchSummary.vue` | Create | URL as external link with arrow icon |
| `frontend/src/components/session/detail/items/claude_code/summary/WebSearchSummary.vue` | Create | Query as plain text |
| `frontend/src/components/session/detail/items/claude_code/summary/ToolSearchSummary.vue` | Create | Query as plain text (same shape as WebSearch but distinct file) |
| `frontend/src/components/session/detail/items/claude_code/summary/TodoSummary.vue` | Create | Multi-part progress with check icons for completed |
| `frontend/src/providers/baseHelpers.js` | Modify | Add `getSummaryRendering` and `errorIsMarkdown` methods to `BaseToolHelpers` |
| `frontend/src/providers/claude_code/toolHelpers.js` | Modify | Implement `getSummaryRendering` + `errorIsMarkdown`; remove `rich` from `computeToolSummary` return shape |
| `frontend/src/components/session/detail/items/ToolUseContent.vue` | Modify | Remove 7 `summaryX` computeds, the 7-branch summary template, the variant-specific CSS, and the `'ExitPlanMode'` literal |

The 8 mini-components live under a new sub-directory `summary/` to keep `items/claude_code/` from getting noisy. Their CSS is scoped to each component; classes that are common to multiple components (e.g. `.items-details-summary-description`, `.items-details-summary-file`) stay in the shell but in a **non-scoped `<style>` block** so the mini-components can use them by class name. This avoids duplication while keeping the variant-specific CSS local to each component.

**Common classes that move from `<style scoped>` to a non-scoped `<style>` block in the shell** (so mini-components can use them):
- `.items-details-summary-separator` (the em-dash)
- `.items-details-summary-description` + `.items-details-summary-description.no-wrap`
- `.items-details-summary-quiet`
- `.items-details-summary-file` + `.items-details-summary-file-icon`

**Variant-specific classes that move INTO mini-components** (deleted from shell):
- `.items-details-summary-grep` + `.grep-connector` → `GrepSummary.vue`
- `.items-details-summary-link` + `.items-details-summary-link-icon` → `WebFetchSummary.vue`
- `.todo-icon` + `.todo-icon-completed` → `TodoSummary.vue`
- `.items-details-summary-description code` → split between `GlobSummary.vue` and `GrepSummary.vue` (the two variants that use `<code>` in their description)

---

## Task 1: Create the 8 mini-components summary

**Files:**
- Create: `frontend/src/components/session/detail/items/claude_code/summary/DescriptionSummary.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/summary/SkillSummary.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/summary/GrepSummary.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/summary/GlobSummary.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/summary/WebFetchSummary.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/summary/WebSearchSummary.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/summary/ToolSearchSummary.vue`
- Create: `frontend/src/components/session/detail/items/claude_code/summary/TodoSummary.vue`

Each component is a leaf SFC: no store access, no router, just rendering its props. Common classes (`items-details-summary-description`, `items-details-summary-file`, `items-details-summary-file-icon`, `items-details-summary-quiet`) are used by name; their styles will be defined globally in the shell (Task 4).

- [ ] **Step 1: Create `DescriptionSummary.vue`**

```vue
<script setup>
defineProps({
    description: { type: String, required: true },
    fileIconSrc: { type: String, default: null },
})
</script>

<template>
    <span v-if="fileIconSrc" class="items-details-summary-file">
        <img :src="fileIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
        <span class="items-details-summary-description">{{ description }}</span>
    </span>
    <span v-else class="items-details-summary-description">{{ description }}</span>
</template>
```

- [ ] **Step 2: Create `SkillSummary.vue`**

```vue
<script setup>
defineProps({
    name: { type: String, required: true },
    namespace: { type: String, default: null },
})
</script>

<template>
    <span class="items-details-summary-description">{{ name }}<span v-if="namespace" class="items-details-summary-quiet"> ({{ namespace }})</span></span>
</template>
```

- [ ] **Step 3: Create `GrepSummary.vue`**

```vue
<script setup>
defineProps({
    pattern: { type: String, default: null },
    fileType: { type: String, default: null },
    path: { type: String, default: null },
    pathIconSrc: { type: String, default: null },
})
</script>

<template>
    <span class="items-details-summary-description grep-summary">
        <code v-if="pattern">{{ pattern }}</code>
        <span v-if="fileType"><span class="grep-connector">in</span> <code>{{ fileType }}</code> <span class="grep-connector">files</span></span>
        <span v-if="path"><span class="grep-connector">in</span>
            <span v-if="pathIconSrc" class="items-details-summary-file">
                <img :src="pathIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
                <span>{{ path }}</span>
            </span>
            <span v-else>{{ path }}</span>
        </span>
    </span>
</template>

<style scoped>
.grep-summary {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
    & > span {
        display: inline-flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
    code {
        font-size: 1em;
        background: var(--wa-color-neutral-fill-quiet);
        border-radius: var(--wa-border-radius-s);
    }
}
.grep-connector {
    white-space: nowrap;
    flex-shrink: 0;
}
</style>
```

- [ ] **Step 4: Create `GlobSummary.vue`**

```vue
<script setup>
defineProps({
    pattern: { type: String, required: true },
})
</script>

<template>
    <span class="items-details-summary-description glob-summary"><code>{{ pattern }}</code></span>
</template>

<style scoped>
.glob-summary code {
    font-size: 1em;
    background: var(--wa-color-neutral-fill-quiet);
    border-radius: var(--wa-border-radius-s);
}
</style>
```

- [ ] **Step 5: Create `WebFetchSummary.vue`**

```vue
<script setup>
defineProps({
    url: { type: String, required: true },
})
</script>

<template>
    <a :href="url" target="_blank" rel="noopener noreferrer nofollow" class="items-details-summary-description webfetch-link" @click.stop>{{ url }}<wa-icon name="arrow-up-right-from-square" class="webfetch-link-icon"></wa-icon></a>
</template>

<style scoped>
.webfetch-link {
    color: inherit;
    text-decoration: none;
    &:hover {
        text-decoration: underline;
    }
}
.webfetch-link-icon {
    font-size: var(--wa-font-size-2xs);
    margin-left: var(--wa-space-3xs);
    opacity: 0.6;
}
</style>
```

- [ ] **Step 6: Create `WebSearchSummary.vue`**

```vue
<script setup>
defineProps({
    query: { type: String, required: true },
})
</script>

<template>
    <span class="items-details-summary-description">{{ query }}</span>
</template>
```

- [ ] **Step 7: Create `ToolSearchSummary.vue`**

```vue
<script setup>
defineProps({
    query: { type: String, required: true },
})
</script>

<template>
    <span class="items-details-summary-description">{{ query }}</span>
</template>
```

- [ ] **Step 8: Create `TodoSummary.vue`**

The TodoWrite summary is a sequence of "parts" — each is `{ text, status? }`. Each part is preceded by an em-dash separator and followed by a check icon if `status === 'completed'`.

```vue
<script setup>
defineProps({
    parts: { type: Array, required: true },
})
</script>

<template>
    <template v-for="(part, i) in parts" :key="i">
        <span v-if="i > 0" class="items-details-summary-separator"> — </span>
        <span class="items-details-summary-description" :class="{ 'no-wrap': !part.status }">{{ part.text }}<wa-icon v-if="part.status === 'completed'" name="check" class="todo-icon todo-icon-completed"></wa-icon></span>
    </template>
</template>

<style scoped>
.todo-icon {
    margin-left: var(--wa-space-xs);
    font-size: 0.85em;
    vertical-align: baseline;
}
.todo-icon-completed {
    color: var(--wa-color-success-60);
}
</style>
```

Note on the separator: in the current shell, the first part of TodoWrite is preceded by a leading separator (` — `). With the new structure, the shell renders **one** separator before the `<component :is>`, so `TodoSummary` only renders separators **between** parts (`v-if="i > 0"`).

- [ ] **Step 9: Visual sanity-check that all 8 files compile**

The 8 components are not yet imported anywhere, so no on-screen change is expected. Verify by tailing the frontend log — there should be no Vite errors mentioning these files. (Vite parses files on import, so the absence of an HMR event for them is fine.)

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/session/detail/items/claude_code/summary/DescriptionSummary.vue \
        frontend/src/components/session/detail/items/claude_code/summary/SkillSummary.vue \
        frontend/src/components/session/detail/items/claude_code/summary/GrepSummary.vue \
        frontend/src/components/session/detail/items/claude_code/summary/GlobSummary.vue \
        frontend/src/components/session/detail/items/claude_code/summary/WebFetchSummary.vue \
        frontend/src/components/session/detail/items/claude_code/summary/WebSearchSummary.vue \
        frontend/src/components/session/detail/items/claude_code/summary/ToolSearchSummary.vue \
        frontend/src/components/session/detail/items/claude_code/summary/TodoSummary.vue
git commit -m "refactor(tool-use): extract per-tool summary mini-components

Each Claude Code-specific summary variant (file description, skill, grep,
glob, web fetch, web search, tool search, todo) is now its own SFC.
The shell will branch through getSummaryRendering helper instead of
hardcoding 7 v-else-if branches in the template. Not wired yet."
```

---

## Task 2: Add `getSummaryRendering` and `errorIsMarkdown` to BaseToolHelpers

**Files:**
- Modify: `frontend/src/providers/baseHelpers.js`

- [ ] **Step 1: Append the two new methods to `BaseToolHelpers`**

Locate the `BaseToolHelpers` class (around line 559 — anchor: `export class BaseToolHelpers extends`). The `Input / Result rendering` section already documents the `{ component, props } | null` descriptor pattern. Add `getSummaryRendering` to that section, immediately after `getResultRendering` and before `getInputOverrides`:

```javascript
    /**
     * Return ``{ component, props }`` for the tool's summary description (the
     * text shown after the tool name and the em-dash separator in the rich
     * card header), or ``null`` to render no description. ``ctx`` may carry
     * runtime state in the future; current callers don't pass anything.
     * Default: no description.
     */
    getSummaryRendering(/* name, input, baseDir, ctx */) {
        return null
    }
```

Add `errorIsMarkdown` at the end of the `Capability flags` section, right after `showsResultOnUnknownError`:

```javascript
    /**
     * Whether the error text for this tool should be rendered as Markdown
     * (via ``MarkdownContent``) instead of plain text. Used for tools whose
     * error message is itself markdown content (e.g. ExitPlanMode emits a
     * markdown-formatted plan that the user wants to read rendered).
     * Default: false (render as plain text).
     */
    errorIsMarkdown(/* name */) {
        return false
    }
```

- [ ] **Step 2: Verify clean compile**

`tail -n 30 logs/frontend.log` from the worktree root. The file should have hot-reloaded cleanly.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/providers/baseHelpers.js
git commit -m "refactor(providers): add getSummaryRendering and errorIsMarkdown

Two new BaseToolHelpers methods. getSummaryRendering returns a
{ component, props } descriptor (same pattern as getInputRendering /
getResultRendering) so the generic shell can render any provider's
summary variants without hardcoding their structure. errorIsMarkdown
is a small flag for tools whose error text is markdown-formatted."
```

---

## Task 3: Implement methods in ClaudeCodeToolHelpers + tighten `computeToolSummary`

**Files:**
- Modify: `frontend/src/providers/claude_code/toolHelpers.js`

This task does three things:
1. Adds 8 imports (the new summary mini-components).
2. Implements `getSummaryRendering` and `errorIsMarkdown`.
3. Removes `rich` from `computeToolSummary`'s return shape (no consumer needs it anymore — the shell will use `getSummaryRendering`, and `WorkingAssistantMessage` only uses `inline` and `displayName`).

- [ ] **Step 1: Add the 8 component imports**

In `frontend/src/providers/claude_code/toolHelpers.js`, locate the existing import block (after `import WebContentResult from ...`). Add:

```javascript
import DescriptionSummary from '../../components/session/detail/items/claude_code/summary/DescriptionSummary.vue'
import SkillSummary from '../../components/session/detail/items/claude_code/summary/SkillSummary.vue'
import GrepSummary from '../../components/session/detail/items/claude_code/summary/GrepSummary.vue'
import GlobSummary from '../../components/session/detail/items/claude_code/summary/GlobSummary.vue'
import WebFetchSummary from '../../components/session/detail/items/claude_code/summary/WebFetchSummary.vue'
import WebSearchSummary from '../../components/session/detail/items/claude_code/summary/WebSearchSummary.vue'
import ToolSearchSummary from '../../components/session/detail/items/claude_code/summary/ToolSearchSummary.vue'
import TodoSummary from '../../components/session/detail/items/claude_code/summary/TodoSummary.vue'
```

- [ ] **Step 2: Implement `getSummaryRendering`**

Insert this method on the `ClaudeCodeToolHelpers` class, **right after `getResultRendering`** and before `getInputOverrides`. It mirrors the per-tool branching that the existing `computeToolSummary` does to populate the `rich.X` fields:

```javascript
    getSummaryRendering(name, input, baseDir /*, ctx */) {
        const safeInput = input || {}

        if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
            return {
                component: DescriptionSummary,
                props: {
                    description: formatRelativePath(safeInput.file_path, baseDir),
                    fileIconSrc: fileIconFor(safeInput.file_path),
                },
            }
        }

        if (name === 'Skill') {
            const dn = getDisplayName(name, safeInput)
            if (dn) {
                return {
                    component: SkillSummary,
                    props: { name: dn.name, namespace: dn.namespace },
                }
            }
        }

        if (name === 'Grep') {
            const pattern = safeInput.pattern || null
            const fileType = safeInput.type || safeInput.glob || null
            const rawPath = safeInput.path || null
            if (pattern || fileType || rawPath) {
                return {
                    component: GrepSummary,
                    props: {
                        pattern,
                        fileType,
                        path: rawPath ? formatRelativePath(rawPath, baseDir) : null,
                        pathIconSrc: rawPath ? fileIconFor(rawPath) : null,
                    },
                }
            }
        }

        if (name === 'Glob' && safeInput.pattern) {
            return {
                component: GlobSummary,
                props: { pattern: safeInput.pattern },
            }
        }

        if (name === 'WebFetch' && safeInput.url) {
            return {
                component: WebFetchSummary,
                props: { url: safeInput.url },
            }
        }

        if (name === 'WebSearch' && safeInput.query) {
            return {
                component: WebSearchSummary,
                props: { query: safeInput.query },
            }
        }

        if (name === 'ToolSearch' && safeInput.query) {
            return {
                component: ToolSearchSummary,
                props: { query: safeInput.query },
            }
        }

        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return {
                component: TodoSummary,
                props: { parts: getTodoDescription(safeInput.todos) },
            }
        }

        // Fallback: any tool whose input carries a `description` field.
        if (safeInput.description) {
            return {
                component: DescriptionSummary,
                props: { description: safeInput.description, fileIconSrc: null },
            }
        }

        return null
    }
```

- [ ] **Step 3: Implement `errorIsMarkdown`**

Insert this method on the `ClaudeCodeToolHelpers` class, **right after `showsResultOnUnknownError`**:

```javascript
    errorIsMarkdown(name) {
        // ExitPlanMode's error text is the user-rejected plan, formatted as Markdown.
        return name === 'ExitPlanMode'
    }
```

- [ ] **Step 4: Tighten `computeToolSummary` — drop the `rich` field**

The current `computeToolSummary` returns `{ displayName, inline, rich: {...} }`. After Task 4 (the shell refactor), no consumer will read `rich` anymore: the shell uses `getSummaryRendering`, and `WorkingAssistantMessage` only reads `inline` and `displayName`. Drop the field to keep the API surface tight.

In `ClaudeCodeToolHelpers.computeToolSummary`, replace each `return { displayName, inline, rich: ... }` with `return { displayName, inline }`. This means simplifying every branch:

```javascript
    computeToolSummary(name, input, baseDir) {
        const safeInput = input || {}
        const displayName = getDisplayName(name, safeInput)

        if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
            const description = formatRelativePath(safeInput.file_path, baseDir)
            return { displayName, inline: description }
        }

        if (name === 'Skill' && displayName) {
            return { displayName, inline: displayName.name }
        }

        if (name === 'Grep') {
            const pattern = safeInput.pattern || null
            const fileType = safeInput.type || safeInput.glob || null
            const rawPath = safeInput.path || null
            if (pattern || fileType || rawPath) {
                const path = rawPath ? formatRelativePath(rawPath, baseDir) : null
                return {
                    displayName,
                    inline: buildGrepInline(pattern, fileType, path),
                }
            }
        }

        if (name === 'Glob' && safeInput.pattern) {
            return { displayName, inline: safeInput.pattern }
        }

        if (name === 'WebFetch' && safeInput.url) {
            return { displayName, inline: safeInput.url }
        }

        if (name === 'WebSearch' && safeInput.query) {
            return { displayName, inline: safeInput.query }
        }

        if (name === 'ToolSearch' && safeInput.query) {
            return { displayName, inline: safeInput.query }
        }

        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return { displayName, inline: null }
        }

        const description = safeInput.description || null
        return { displayName, inline: description }
    }
```

The `emptyRich` private helper function becomes dead code after this — delete it from the file as well (locate the `function emptyRich` declaration and remove the function entirely).

- [ ] **Step 5: Update the `BaseToolHelpers.computeToolSummary` default to match**

In `frontend/src/providers/baseHelpers.js`, the `computeToolSummary` default currently returns `{ displayName: null, inline: null, rich: { kind: null, description: null, ... } }`. Tighten to:

```javascript
    /** Build the summary descriptor for a tool_use. Default: minimal stub. */
    computeToolSummary(/* name, input, baseDir */) {
        return { displayName: null, inline: null }
    }
```

- [ ] **Step 6: Verify clean compile**

`tail -n 30 logs/frontend.log` from the worktree root. Both the helpers file and the base helpers file should hot-reload cleanly.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/providers/claude_code/toolHelpers.js \
        frontend/src/providers/baseHelpers.js
git commit -m "refactor(providers): implement summary rendering helpers

ClaudeCodeToolHelpers now exposes getSummaryRendering (returns the right
mini-component for each Claude Code tool) and errorIsMarkdown (true for
ExitPlanMode). computeToolSummary's return shape is tightened: the
'rich' field is dropped — no consumer needs it anymore (the shell will
use getSummaryRendering in the next commit). Same default change in
BaseToolHelpers."
```

---

## Task 4: Refactor the shell to consume the new helpers

**Files:**
- Modify: `frontend/src/components/session/detail/items/ToolUseContent.vue`

This is the largest task in this pass. It removes ~7 computeds, ~7 template branches, and ~30 lines of variant CSS, replacing them with a single `<component :is>` substitution and a single `MarkdownContent` substitution for the error callout.

- [ ] **Step 1: Remove the 7 `summaryX` computeds**

Locate the block of computed declarations that read `summary.value.rich.X` (anchor: `const summaryDescription = computed`, around line 343):

```javascript
const summaryDescription = computed(() => summary.value.rich.description)
const summaryFileIconSrc = computed(() => summary.value.rich.fileIconSrc)
const summarySkill = computed(() => summary.value.rich.skill)
const summaryGrep = computed(() => summary.value.rich.grep)
const summaryGlob = computed(() => summary.value.rich.globPattern)
const summaryWebFetchUrl = computed(() => summary.value.rich.webFetchUrl)
const summaryWebSearchQuery = computed(() => summary.value.rich.webSearchQuery)
const summaryToolSearchQuery = computed(() => summary.value.rich.toolSearchQuery)
const summaryTodo = computed(() => summary.value.rich.todoDescription)
```

DELETE all 9 lines (yes there are 9 — `summaryDescription`, `summaryFileIconSrc`, `summarySkill`, `summaryGrep`, `summaryGlob`, `summaryWebFetchUrl`, `summaryWebSearchQuery`, `summaryToolSearchQuery`, `summaryTodo`).

- [ ] **Step 2: Add `summaryRendering` computed**

In their place, add:

```javascript
// Per-tool summary description rendering, resolved via the helper.
// Returns { component, props } or null. The shell renders it via
// <component :is> after the em-dash separator.
const summaryRendering = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers || !props.input) return null
    return helpers.getSummaryRendering(props.name, props.input, sessionBaseDir.value)
})
```

- [ ] **Step 3: Tighten the `summary` fallback**

The `summary` computed currently has a fallback for when `helpers` is null:

```javascript
const summary = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return { displayName: null, inline: null, rich: { kind: null } }
    return helpers.computeToolSummary(props.name, props.input, sessionBaseDir.value)
})
```

Now that `rich` is gone from `computeToolSummary`'s return shape, the fallback should match:

```javascript
const summary = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return { displayName: null, inline: null }
    return helpers.computeToolSummary(props.name, props.input, sessionBaseDir.value)
})
```

- [ ] **Step 4: Replace the 7-branch summary template**

Locate the template chain that starts with `<template v-if="summaryDescription">` (around line 666) and ends right before the `<!-- View Agent indicator -->` block (around line 723). The current code:

```html
<template v-if="summaryDescription">
    <span class="items-details-summary-separator"> — </span>
    <span v-if="summaryFileIconSrc" class="items-details-summary-file">
        <img :src="summaryFileIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
        <span class="items-details-summary-description">{{ summaryDescription }}</span>
    </span>
    <span v-else class="items-details-summary-description">{{ summaryDescription }}</span>
    <CodeCommentsIndicator :count="toolCommentsCount" :show-tooltip="false" class="tool-comments-indicator" />
</template>
<!-- Skill tool: show skill name, with namespace in quiet mode -->
<template v-else-if="summarySkill">
    <span class="items-details-summary-separator"> — </span>
    <span class="items-details-summary-description">{{ summarySkill.name }}<span v-if="summarySkill.namespace" class="items-details-summary-quiet"> ({{ summarySkill.namespace }})</span></span>
</template>
<!-- Grep tool: "`pattern` in `type` files in [path]" -->
<template v-else-if="summaryGrep">
    <span class="items-details-summary-separator"> — </span>
    <span class="items-details-summary-description items-details-summary-grep">
        <code v-if="summaryGrep.pattern">{{ summaryGrep.pattern }}</code>
        <span v-if="summaryGrep.fileType"><span class="grep-connector">in</span> <code>{{ summaryGrep.fileType }}</code> <span class="grep-connector">files</span></span>
        <span v-if="summaryGrep.path"><span class="grep-connector">in</span>
            <span v-if="summaryGrep.pathIconSrc" class="items-details-summary-file">
                <img :src="summaryGrep.pathIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
                <span>{{ summaryGrep.path }}</span>
            </span>
            <span v-else>{{ summaryGrep.path }}</span>
        </span>
    </span>
</template>
<!-- Glob tool: show pattern in code -->
<template v-else-if="summaryGlob">
    <span class="items-details-summary-separator"> — </span>
    <span class="items-details-summary-description"><code>{{ summaryGlob }}</code></span>
</template>
<!-- WebFetch tool: show URL as a link -->
<template v-else-if="summaryWebFetchUrl">
    <span class="items-details-summary-separator"> — </span>
    <a :href="summaryWebFetchUrl" target="_blank" rel="noopener noreferrer nofollow" class="items-details-summary-description items-details-summary-link" @click.stop>{{ summaryWebFetchUrl }}<wa-icon name="arrow-up-right-from-square" class="items-details-summary-link-icon"></wa-icon></a>
</template>
<!-- WebSearch tool: show query -->
<template v-else-if="summaryWebSearchQuery">
    <span class="items-details-summary-separator"> — </span>
    <span class="items-details-summary-description">{{ summaryWebSearchQuery }}</span>
</template>
<!-- ToolSearch tool: show query -->
<template v-else-if="summaryToolSearchQuery">
    <span class="items-details-summary-separator"> — </span>
    <span class="items-details-summary-description">{{ summaryToolSearchQuery }}</span>
</template>
<!-- TodoWrite tool: show progress description -->
<template v-else-if="summaryTodo">
    <template v-for="(part, i) in summaryTodo" :key="i">
        <span class="items-details-summary-separator"> — </span>
        <span class="items-details-summary-description" :class="{ 'no-wrap': !part.status }">{{ part.text }}<wa-icon v-if="part.status === 'completed'" name="check" class="todo-icon todo-icon-completed"></wa-icon></span>
    </template>
</template>
```

Replace the **entire chain above** with:

```html
<template v-if="summaryRendering">
    <span class="items-details-summary-separator"> — </span>
    <component :is="summaryRendering.component" v-bind="summaryRendering.props" />
    <CodeCommentsIndicator :count="toolCommentsCount" :show-tooltip="false" class="tool-comments-indicator" />
</template>
```

The `CodeCommentsIndicator` is now ALWAYS rendered next to the summary (when there is one), but it still self-hides when `count <= 0` (the indicator's internal `v-if`). Previously it was only rendered for the `summaryDescription` branch (Edit/Write/Read with file_path); for any other branch (Skill, Grep, Glob, WebFetch, WebSearch, ToolSearch, TodoWrite) it didn't render. Behaviour-wise this is identical: those tools never have `toolCommentsCount > 0` (the count is gated on `isEditOrWrite && file_path` in the existing `toolCommentsCount` computed), so the indicator stays invisible. No regression.

- [ ] **Step 5: Replace the `'ExitPlanMode'` literal in the error callout**

Locate the error callout (anchor: `<wa-callout v-if="isToolError"`, around line 800):

```html
<wa-callout v-if="isToolError" variant="danger" appearance="outlined" class="tool-error-message">
    <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
    <MarkdownContent v-if="props.name === 'ExitPlanMode'" :source="toolErrorText" />
    <template v-else>{{ toolErrorText }}</template>
</wa-callout>
```

Replace with:

```html
<wa-callout v-if="isToolError" variant="danger" appearance="outlined" class="tool-error-message">
    <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
    <MarkdownContent v-if="errorAsMarkdown" :source="toolErrorText" />
    <template v-else>{{ toolErrorText }}</template>
</wa-callout>
```

Add a corresponding computed in the script (anywhere after `toolHelpers` is declared, e.g. just after `headerLabel`):

```javascript
// Whether the error text should render as Markdown vs plain text.
const errorAsMarkdown = computed(() => !!toolHelpers.value?.errorIsMarkdown(props.name))
```

- [ ] **Step 6: Move common summary classes from `<style scoped>` to a non-scoped `<style>` block**

**Important context:** the parent component `SessionItem.vue` already declares `.items-details-summary-separator` and `.items-details-summary-description` (with `color`, `font-weight`, `overflow-wrap: anywhere`) in a **non-scoped** `<style>` block (around line 620). The shell at `ToolUseContent.vue` currently overrides `.items-details-summary-description`'s wrap behaviour to `word-wrap: break-word` (less aggressive than SessionItem.vue's `overflow-wrap: anywhere`) — we MUST preserve this override to keep the wrapping behaviour identical post-refactor. SessionItem.vue does NOT define `.items-details-summary-quiet`, `.items-details-summary-file`, or `.items-details-summary-file-icon` — those are shell-only declarations today and must move to non-scoped if the mini-components are to use them.

In the current `<style scoped>` block, locate (anchor strings shown):

- `.items-details-summary-description {` nested inside `wa-details { ... .items-details-summary-left { ... } ...}` (around line 917) — currently declares `word-wrap: break-word` and `&.no-wrap { white-space: nowrap }`. **Move** to non-scoped.
- `.items-details-summary-file {` (top level, around line 927) — **move** to non-scoped.
- `.items-details-summary-file-icon {` (top level, around line 933) — **move** to non-scoped.
- `.items-details-summary-quiet {` (top level, around line 938) — **move** to non-scoped.

`.items-details-summary-separator` has **no rule** in the current scoped block (only used as an HTML class name in the template). SessionItem.vue handles its color globally. Nothing to move for it.

The shell currently has one `<style scoped>` block. Add a new `<style>` block (no `scoped` attribute) at the end of the file (after `</style>` of the scoped one) with the moved classes:

```css
<style>
/* Common summary classes — non-scoped so per-tool summary mini-components
 * (under items/claude_code/summary/) can use them by name without scoped CSS
 * isolation. SessionItem.vue (parent) already declares
 * `.items-details-summary-separator` and base `.items-details-summary-description`
 * styles globally; we keep the shell-specific `word-wrap: break-word` override
 * here so the description still wraps the way it did before this refactor. */

.items-details-summary-description {
    /* Less aggressive than SessionItem.vue's `overflow-wrap: anywhere` —
     * preserves pre-refactor wrapping behaviour for tool descriptions. */
    word-wrap: break-word;

    &.no-wrap {
        white-space: nowrap;
    }
}

.items-details-summary-quiet {
    color: var(--wa-color-text-quiet);
}

.items-details-summary-file {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.items-details-summary-file-icon {
    vertical-align: text-bottom;
    flex-shrink: 0;
}
</style>
```

DELETE the moved selectors from the scoped block. Concretely:
- The nested `wa-details { ... .items-details-summary-description { word-wrap: break-word; &.no-wrap { white-space: nowrap; } } ... }` rule — remove the description rule (with its nested `&.no-wrap`).
- `.items-details-summary-file { ... }` (top level) — DELETE.
- `.items-details-summary-file-icon { ... }` (top level) — DELETE.
- `.items-details-summary-quiet { ... }` (top level) — DELETE.

Keep all other shell-internal layout rules (the `wa-details` outer rule, `.items-details-summary-left` layout, etc.) untouched — only the four selectors above leave the scoped block.

- [ ] **Step 7: Delete the variant-specific CSS that moved into mini-components**

In the scoped `<style scoped>` block, DELETE these selectors (all are top-level):

```css
.items-details-summary-grep { ... }       /* moved to GrepSummary.vue */
.items-details-summary-description code { ... }  /* moved to GrepSummary.vue and GlobSummary.vue */
.items-details-summary-link { ... }       /* moved to WebFetchSummary.vue */
.items-details-summary-link-icon { ... }  /* moved to WebFetchSummary.vue */
.todo-icon { ... }                        /* moved to TodoSummary.vue */
.todo-icon-completed { ... }              /* moved to TodoSummary.vue */
```

After this step, the scoped CSS block contains only shell-internal layout rules and zero variant-specific styling.

- [ ] **Step 8: Re-read the file end-to-end and verify no leftover Claude Code-specific literals**

Open `frontend/src/components/session/detail/items/ToolUseContent.vue` and grep for:

- Tool-name literals: `'Edit'`, `'Write'`, `'Read'`, `'Bash'`, `'WebFetch'`, `'WebSearch'`, `'ToolSearch'`, `'TodoWrite'`, `'Skill'`, `'Grep'`, `'Glob'`, `'Task'`, `'Agent'`, `'ExitPlanMode'` → **zero matches**. The previous documented exception (`'ExitPlanMode'`) is now gone.

- Old computeds: `summaryDescription`, `summaryFileIconSrc`, `summarySkill`, `summaryGrep`, `summaryGlob`, `summaryWebFetchUrl`, `summaryWebSearchQuery`, `summaryToolSearchQuery`, `summaryTodo` → **zero matches**.

- New things present:
  - `summaryRendering` (computed)
  - `errorAsMarkdown` (computed)
  - `<component :is="summaryRendering.component" v-bind="summaryRendering.props" />` (template)
  - `MarkdownContent v-if="errorAsMarkdown"` (template)
  - Two `<style>` blocks (one scoped, one non-scoped)

- [ ] **Step 9: Verify clean compile**

`tail -n 50 logs/frontend.log` from the worktree root. Look for the HMR event for `ToolUseContent.vue` (and possibly its scoped CSS). No errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/session/detail/items/ToolUseContent.vue
git commit -m "refactor(tool-use): delegate summary + error rendering to helpers

The shell no longer hardcodes the 7 summary variants (Edit/Write/Read
description, Skill, Grep, Glob, WebFetch, WebSearch, ToolSearch,
TodoWrite). Each variant is now a per-tool mini-component under
items/claude_code/summary/, picked by ClaudeCodeToolHelpers via
getSummaryRendering. The 9 summaryX computeds, the matching template
branches, and the variant-specific CSS are gone; a single <component>
plus a CodeCommentsIndicator replace them.

The 'ExitPlanMode' tool-name literal is also removed from the error
callout — replaced by an errorIsMarkdown helper hook.

Common summary classes (separator, description, file row, quiet text)
are exposed via a non-scoped <style> block so mini-components can use
them by name without duplicating CSS."
```

---

## Task 5: Smoke test in browser (user-driven)

Manual verification by the user. Walk through every summary variant and the ExitPlanMode error case, comparing visually against `main`.

- [ ] **Step 1: Edit / Write / Read tools** — Header shows the file path with the file icon. Spacing identical to before.

- [ ] **Step 2: Skill tool** — Header shows `Skill — <name> (<namespace>)` with namespace in quiet color.

- [ ] **Step 3: Grep tool** — Header shows `pattern` in `<code>`, then "in `type` files", then "in `path`" with file icon. All three pieces optional.

- [ ] **Step 4: Glob tool** — Header shows `Glob — <pattern>` with pattern in `<code>`.

- [ ] **Step 5: WebFetch tool** — Header shows the URL as an external link with the arrow icon. Link is clickable in a new tab.

- [ ] **Step 6: WebSearch / ToolSearch tools** — Header shows the query as plain text after the em-dash.

- [ ] **Step 7: TodoWrite tool** — Header shows the multi-part progress with check icons for completed items.

- [ ] **Step 8: Bash / MCP tools (description fallback)** — Header shows the description (the `description` input field) if present, no icon.

- [ ] **Step 9: ExitPlanMode error case** — Trigger ExitPlanMode and reject the plan. Verify the error callout renders the plan as Markdown (formatted), not plain text.

- [ ] **Step 10: CodeCommentsIndicator** — For an Edit/Write tool with attached code comments, the indicator still appears next to the file description with the count. For a tool without code comments, the indicator is invisible.

- [ ] **Step 11: Codex sessions** — Open a Codex session; tool-use rendering (if any tool_use is present in the JSONL) falls back to JsonHumanView for input AND result, the header shows the raw tool name, no summary description, no error markdown rendering. Graceful no-op as designed.

If everything checks out, this pass is done.

---

## Roll-back plan

Each task above produces a single commit. The four commits:

1. Add 8 mini-components summary
2. Add 2 contract methods on `BaseToolHelpers`
3. Implement methods in `ClaudeCodeToolHelpers` + tighten `computeToolSummary`
4. Refactor shell

Reverting commit (4) restores the 7-branch template in the shell. Commits (1)–(3) leave dead code (unimported components and unused helper methods) but no harm. Reverting them in order (4) → (3) → (2) → (1) is safe.
