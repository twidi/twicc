# Project Tree Display Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the flat project list with a two-section layout: named projects (flat, mtime-sorted) then unnamed projects (compressed path tree, alphabetical).

**Architecture:** A pure utility module builds the radix tree from project directories. A new recursive Vue component renders tree nodes on the Home page. The sidebar selector uses a pre-flattened version of the same tree. No backend changes.

**Tech Stack:** Vue 3 (Composition API, `<script setup>`), Web Awesome components (`wa-card`, `wa-icon`, `wa-select`, `wa-option`, `wa-divider`), existing Pinia store unchanged.

**Design doc:** `docs/plans/2026-03-01-project-tree-display-design.md`

---

### Task 1: Create the project tree utility module

**Files:**
- Create: `frontend/src/utils/projectTree.js`

**Step 1: Write `buildProjectTree(projects)`**

This function takes an array of project objects (each with a `directory` string) and returns an array of root tree nodes.

```javascript
// frontend/src/utils/projectTree.js

/**
 * Build a compressed path tree (radix tree) from a list of projects.
 *
 * Algorithm:
 * 1. Build a trie by inserting each project's directory, split on '/'.
 * 2. Compress: if a non-project node has exactly one child, merge segments.
 * 3. Sort children alphabetically at each level.
 *
 * Each node has:
 *   - segment: string (display label, e.g. "dev" or "OBS/web/characters" after compression)
 *   - children: array of child nodes (sorted alphabetically by segment)
 *   - project: the project object, or null for intermediate folder nodes
 *
 * @param {Array<Object>} projects - projects with `directory` field
 * @returns {Array<Object>} array of root tree nodes
 */
export function buildProjectTree(projects) {
    // Step 1: Build trie
    // Use a virtual root node to handle multiple top-level paths
    const root = { segment: '', children: new Map(), project: null }

    for (const project of projects) {
        const segments = project.directory.split('/').filter(s => s !== '')
        let current = root
        for (const seg of segments) {
            if (!current.children.has(seg)) {
                current.children.set(seg, { segment: seg, children: new Map(), project: null })
            }
            current = current.children.get(seg)
        }
        current.project = project
    }

    // Step 2: Compress — post-order traversal
    function compress(node) {
        // Recurse into children first (post-order)
        for (const child of node.children.values()) {
            compress(child)
        }
        // If this node is NOT a project and has exactly ONE child, merge
        if (!node.project && node.children.size === 1) {
            const onlyChild = node.children.values().next().value
            node.segment = node.segment ? `${node.segment}/${onlyChild.segment}` : onlyChild.segment
            node.project = onlyChild.project
            node.children = onlyChild.children
        }
    }

    compress(root)

    // Step 3: Convert Map children to sorted arrays, recursively
    function toSortedArrays(node) {
        const childArray = Array.from(node.children.values())
            .sort((a, b) => a.segment.localeCompare(b.segment))
        node.children = childArray
        for (const child of childArray) {
            toSortedArrays(child)
        }
        return node
    }

    // If the virtual root was compressed into a single node (all projects share a
    // common prefix), return it as the sole root. Otherwise return its children.
    if (root.segment) {
        // Root itself was compressed — it IS a meaningful node
        return [toSortedArrays(root)]
    }
    // Return the root's children as top-level roots
    const roots = Array.from(root.children.values())
        .sort((a, b) => a.segment.localeCompare(b.segment))
    for (const r of roots) {
        toSortedArrays(r)
    }
    return roots
}
```

**Step 2: Write `flattenProjectTree(roots)`**

This function takes the tree roots and produces a flat array for use in `wa-select` dropdowns.

```javascript
/**
 * Flatten a project tree into a linear array for use in <wa-select>.
 *
 * Each item has:
 *   - isFolder: boolean (true for intermediate directory nodes)
 *   - segment: string (display label)
 *   - project: project object or null
 *   - depth: number (nesting level, for computing indent)
 *   - key: string (unique key for v-for)
 *
 * @param {Array<Object>} roots - tree root nodes from buildProjectTree()
 * @returns {Array<Object>} flat array of items
 */
export function flattenProjectTree(roots) {
    const result = []
    let folderIndex = 0

    function walk(node, depth) {
        const isFolder = !node.project
        result.push({
            isFolder,
            segment: node.segment,
            project: node.project,
            depth,
            key: isFolder ? `__folder_${folderIndex++}` : node.project.id,
        })
        for (const child of node.children) {
            walk(child, depth + 1)
        }
    }

    for (const root of roots) {
        walk(root, 0)
    }
    return result
}
```

**Step 3: Commit**

```bash
git add frontend/src/utils/projectTree.js
git commit -m "feat: add project tree utility (radix tree builder + flattener)"
```

---

### Task 2: Create the ProjectTreeNode component

**Files:**
- Create: `frontend/src/components/ProjectTreeNode.vue`

**Step 1: Write the recursive component**

This component renders a single tree node. For folder nodes: a chevron + label. For project nodes: the same `wa-card` content as `ProjectList.vue` currently shows. Children are rendered recursively inside a `.tree-children` div (natural CSS nesting handles indentation).

```vue
<script setup>
import { ref, computed } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate } from '../utils/date'
import { SESSION_TIME_FORMAT } from '../constants'
import ProjectBadge from './ProjectBadge.vue'
import ProjectProcessIndicator from './ProjectProcessIndicator.vue'
import ActivitySparkline from './ActivitySparkline.vue'
import CostDisplay from './CostDisplay.vue'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    node: {
        type: Object,
        required: true,
    },
})

const emit = defineEmits(['select', 'edit'])

const store = useDataStore()
const settingsStore = useSettingsStore()

const showCosts = computed(() => settingsStore.areCostsShown)
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

function timestampToDate(timestamp) {
    return new Date(timestamp * 1000)
}

const isOpen = ref(true)

function toggleOpen() {
    isOpen.value = !isOpen.value
}

const isFolder = computed(() => !props.node.project)
const hasChildren = computed(() => props.node.children.length > 0)
</script>

<template>
    <div class="tree-node">
        <!-- Folder node (no project) -->
        <div v-if="isFolder" class="tree-folder" @click="toggleOpen">
            <wa-icon :name="isOpen ? 'chevron-down' : 'chevron-right'" class="tree-chevron"></wa-icon>
            <span class="tree-folder-label">{{ node.segment }}</span>
        </div>

        <!-- Project node -->
        <template v-else>
            <wa-card
                class="project-card"
                appearance="outlined"
                @click="emit('select', node.project)"
            >
                <!-- Same card content as ProjectList.vue -->
                <div class="project-info">
                    <div class="project-title-row">
                        <div class="project-title-with-chevron" v-if="hasChildren">
                            <wa-icon
                                :name="isOpen ? 'chevron-down' : 'chevron-right'"
                                class="tree-chevron"
                                @click.stop="toggleOpen"
                            ></wa-icon>
                        </div>
                        <ProjectBadge :project-id="node.project.id" class="project-title" />
                        <ProjectProcessIndicator :project-id="node.project.id" size="small" />
                    </div>
                    <wa-button
                        :id="`edit-button-${node.project.id}`"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="edit-button"
                        @click.stop="emit('edit', node.project)"
                    >
                        <wa-icon name="pencil"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="`edit-button-${node.project.id}`">Edit project (name and color)</AppTooltip>
                    <div v-if="node.project.directory" class="project-directory">{{ node.project.directory }}</div>
                    <div class="project-meta-wrapper">
                        <div class="project-meta">
                            <span :id="`sessions-count-${node.project.id}`" class="sessions-count">
                                <wa-icon auto-width name="folder-open" variant="regular"></wa-icon>
                                <span>{{ node.project.sessions_count }} session{{ node.project.sessions_count !== 1 ? 's' : '' }}</span>
                            </span>
                            <AppTooltip :for="`sessions-count-${node.project.id}`">Number of sessions</AppTooltip>
                            <template v-if="showCosts">
                                <CostDisplay :id="`project-cost-${node.project.id}`" :cost="node.project.total_cost" class="project-cost" />
                                <AppTooltip :for="`project-cost-${node.project.id}`">Total project cost</AppTooltip>
                            </template>
                            <span :id="`project-mtime-${node.project.id}`" class="project-mtime">
                                <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                                <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(node.project.mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                                <span v-else>{{ formatDate(node.project.mtime) }}</span>
                            </span>
                            <AppTooltip :for="`project-mtime-${node.project.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(node.project.mtime)}` : 'Last activity' }}</AppTooltip>
                        </div>
                        <div :id="`project-sparkline-${node.project.id}`" class="project-graph">
                            <ActivitySparkline :id-suffix="node.project.id" :data="store.weeklyActivity[node.project.id] || []" />
                        </div>
                        <AppTooltip :for="`project-sparkline-${node.project.id}`">Project activity (message turns per week)</AppTooltip>
                    </div>
                </div>
            </wa-card>
        </template>

        <!-- Children (rendered inside tree-children for CSS nesting indentation) -->
        <div v-if="hasChildren && isOpen" class="tree-children">
            <ProjectTreeNode
                v-for="child in node.children"
                :key="child.project ? child.project.id : child.segment"
                :node="child"
                @select="(p) => emit('select', p)"
                @edit="(p) => emit('edit', p)"
            />
        </div>
    </div>
</template>

<style scoped>
.tree-node {
    /* No special layout — just a container for the node + children */
}

.tree-folder {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) 0;
    cursor: pointer;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    user-select: none;
}

.tree-folder:hover {
    color: var(--wa-color-text-default);
}

.tree-folder-label {
    font-family: var(--wa-font-mono);
}

.tree-chevron {
    font-size: var(--wa-font-size-xs);
    flex-shrink: 0;
}

.tree-children {
    padding-left: var(--wa-space-m);
}

.project-card {
    cursor: pointer;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    &::part(body) {
        position: relative;
    }
}

.project-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--wa-shadow-m);
}

.project-info {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.project-title-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding-right: calc(var(--wa-space-s) + 1.5em);
}

.project-title-with-chevron {
    display: flex;
    align-items: center;
    cursor: pointer;
}

.project-title {
    font-weight: 600;
    font-size: var(--wa-font-size-m);
    min-width: 0;
}

.edit-button {
    position: absolute;
    top: calc(var(--spacing) / 2);
    right: calc(var(--spacing) / 2);
}

.project-directory {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
}

.project-meta-wrapper {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.project-meta {
    display: flex;
    justify-content: start;
    gap: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);

    & > span {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
}
</style>
```

**Notes on the component:**
- The `wa-card` content (project-info, project-meta, etc.) is copied from the existing `ProjectList.vue`. This duplication is intentional — the card rendering logic belongs to the tree node when displayed in a tree context.
- The chevron on project nodes (those with children) is placed inside the title row and stops event propagation to avoid triggering card click.
- Events `select` and `edit` bubble up through the recursive tree to `ProjectList.vue`.

**Step 2: Commit**

```bash
git add frontend/src/components/ProjectTreeNode.vue
git commit -m "feat: add ProjectTreeNode recursive component"
```

---

### Task 3: Update ProjectList.vue to use two-section layout

**Files:**
- Modify: `frontend/src/components/ProjectList.vue`

**Step 1: Update the script and template**

Replace the flat `v-for` loop with two sections. The script section gains:
- Import of `buildProjectTree` from `../utils/projectTree`
- Import of `ProjectTreeNode`
- A `namedProjects` computed: projects with `name !== null`, sorted by `mtime` desc
- A `treeRoots` computed: `buildProjectTree()` applied to projects with `name === null`

The template becomes:

```html
<template>
    <div class="project-list">
        <!-- Section 1: Named projects (flat, by mtime) -->
        <template v-if="namedProjects.length">
            <div class="section-header">Named projects</div>
            <wa-card
                v-for="project in namedProjects"
                :key="project.id"
                class="project-card"
                appearance="outlined"
                @click="handleSelect(project)"
            >
                <!-- Same card content as today (unchanged) -->
                <div class="project-info">
                    <!-- ... exact same content currently in the v-for ... -->
                </div>
            </wa-card>
        </template>

        <!-- Section 2: Unnamed projects (tree) -->
        <template v-if="treeRoots.length">
            <div v-if="namedProjects.length" class="section-header">Other projects</div>
            <ProjectTreeNode
                v-for="root in treeRoots"
                :key="root.project ? root.project.id : root.segment"
                :node="root"
                @select="handleSelect"
                @edit="handleEditClick"
            />
        </template>

        <div v-if="namedProjects.length === 0 && treeRoots.length === 0" class="empty-state">
            No projects found
        </div>
    </div>

    <ProjectEditDialog ref="editDialogRef" :project="editingProject" />
</template>
```

Key changes to the script:
- Add imports: `import { buildProjectTree } from '../utils/projectTree'` and `import ProjectTreeNode from './ProjectTreeNode.vue'`
- Add computed `namedProjects`:
  ```javascript
  const namedProjects = computed(() =>
      store.getProjects.filter(p => p.name !== null)
  )
  ```
- Add computed `treeRoots`:
  ```javascript
  const treeRoots = computed(() => {
      const unnamed = store.getProjects.filter(p => p.name === null)
      return buildProjectTree(unnamed)
  })
  ```
- Modify `handleEditClick` to accept just the project (no event, since `ProjectTreeNode` emits `edit` with the project directly). For the named section cards, keep `event.stopPropagation()` inline via `@click.stop`.

CSS additions:
```css
.section-header {
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text-quiet);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: var(--wa-space-xs) 0;
}
```

**Step 2: Commit**

```bash
git add frontend/src/components/ProjectList.vue
git commit -m "feat: split project list into named (flat) + unnamed (tree) sections"
```

---

### Task 4: Update the sidebar project selector in ProjectView.vue

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

**Step 1: Add imports and computed properties**

At the top of the script, add:
```javascript
import { buildProjectTree, flattenProjectTree } from '../utils/projectTree'
```

Add new computed properties alongside the existing `allProjects`:
```javascript
const namedProjects = computed(() =>
    allProjects.value.filter(p => p.name !== null)
)

const flatTree = computed(() => {
    const unnamed = allProjects.value.filter(p => p.name === null)
    const roots = buildProjectTree(unnamed)
    return flattenProjectTree(roots)
})
```

**Step 2: Update the `wa-select` template (around line 631-659)**

Replace the current flat `v-for` of all projects with the two-section layout:

```html
<wa-select
    id="project-selector"
    :value.attr="isAllProjectsMode ? ALL_PROJECTS_ID : projectId"
    @change="handleProjectChange"
    class="project-selector"
    size="small"
>
    <span
        v-if="!isAllProjectsMode"
        slot="start"
        class="selected-project-dot"
        :style="selectedProjectColor ? { '--dot-color': selectedProjectColor } : null"
    ></span>
    <wa-option :value="ALL_PROJECTS_ID">
        All Projects
    </wa-option>

    <!-- Named projects -->
    <wa-divider v-if="namedProjects.length"></wa-divider>
    <wa-option
        v-for="p in namedProjects"
        :key="p.id"
        :value="p.id"
        :label="store.getProjectDisplayName(p.id)"
    >
        <span class="project-option">
            <ProjectBadge :project-id="p.id" />
            <ProjectProcessIndicator :project-id="p.id" size="small" />
        </span>
    </wa-option>

    <!-- Unnamed projects (flattened tree) -->
    <wa-divider v-if="flatTree.length"></wa-divider>
    <template v-for="item in flatTree" :key="item.key">
        <wa-option
            v-if="item.isFolder"
            disabled
            class="tree-folder-option"
        >
            <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                {{ item.segment }}
            </span>
        </wa-option>
        <wa-option
            v-else
            :value="item.project.id"
            :label="store.getProjectDisplayName(item.project.id)"
        >
            <span class="project-option" :style="{ paddingLeft: `${item.depth * 12}px` }">
                <ProjectBadge :project-id="item.project.id" />
                <ProjectProcessIndicator :project-id="item.project.id" size="small" />
            </span>
        </wa-option>
    </template>
</wa-select>
```

**Step 3: Add CSS for tree folder options**

```css
.tree-folder-option {
    &::part(base) {
        opacity: 0.6;
    }
}

.tree-folder-label {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
}
```

**Step 4: Commit**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "feat: update sidebar project selector with named + tree sections"
```

---

### Task 5: Visual polish and edge case handling

**Files:**
- Modify: `frontend/src/components/ProjectTreeNode.vue`
- Modify: `frontend/src/components/ProjectList.vue`

**Step 1: Handle edge case — all projects named**

If all projects have names, the tree section is empty. Only the named section shows, with no section header (since there's only one section, no need for a label). Already handled by the `v-if` conditions in Task 3.

**Step 2: Handle edge case — no projects named**

If no projects have names, only the tree section shows, with no section header. Already handled by the `v-if="namedProjects.length"` guard on the "Other projects" header in Task 3.

**Step 3: Verify visual consistency**

The project cards in `ProjectTreeNode.vue` must look identical to those in the named section. Verify:
- Same `wa-card` appearance (`outlined`)
- Same hover effect (`translateY(-2px)`, `box-shadow`)
- Same font sizes and spacing
- Same `project-directory` display

**Step 4: Test with dev server**

Run the dev server and verify:
1. Home page shows named projects on top, tree below
2. Tree nodes can be collapsed/expanded
3. Project cards in the tree are clickable and navigate correctly
4. Edit button on tree cards opens the edit dialog
5. Sidebar selector shows named projects then indented tree
6. Folder items in selector are not selectable
7. Selecting a project in the tree section of the selector works

**Step 5: Final commit**

```bash
git add frontend/src/components/ProjectTreeNode.vue frontend/src/components/ProjectList.vue
git commit -m "fix: polish project tree display and edge cases"
```
