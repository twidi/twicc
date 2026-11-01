# Project Tree Display Design

## Problem

Users with many projects (e.g. 60+) see a flat list of cards sorted by mtime on the Home page and in the sidebar project selector. This makes it hard to find projects, especially when many are worktrees or subdirectories of the same parent repository.

## Solution

Replace the flat project list with a two-section layout:

1. **Named projects** (top): projects where the user has explicitly set a `name`. Flat list, sorted by `mtime` desc (same as today).
2. **Other projects** (below): projects without a user-defined name, organized as a compressed path tree (radix tree), sorted alphabetically at each level.

This applies to both the Home page (`ProjectList.vue`) and the project selector dropdown in the sidebar (`ProjectView.vue`).

## Algorithm: Compressed Path Tree (Radix Tree)

### Input

All projects (no filtering). Each project has a `directory` field (always populated).

### Steps

1. **Build trie**: Insert each project's `directory` into a trie, splitting on `/`. Each node has:
   - `segment`: directory name (e.g. `dev`)
   - `children`: array of child nodes, sorted alphabetically by segment
   - `project`: the associated project object, or `null` for intermediate directory nodes

2. **Compress**: Post-order traversal. If a node is NOT a project AND has exactly one child, merge with child by joining segments with `/`. Repeat until stable.

3. **Sort**: At each level, children are sorted alphabetically by segment.

### Example

```
Input:
  /home/twidi (project)
  /home/twidi/OBS/web/characters (project)
  /home/twidi/dev/claude-code-viewer (project)
  /home/twidi/dev/twicc-poc (project)
  /home/twidi/dev/twistt (project)

After compression:
  home/twidi [project]
  ├── OBS/web/characters [project]       ← 3 levels compressed
  └── dev
      ├── claude-code-viewer [project]
      ├── twicc-poc [project]
      └── twistt [project]
```

### Compression rule

A node is compressed (merged with its only child) when:
- It is NOT a project (no project associated)
- It has exactly ONE child

A node that IS a project is never compressed, even if it has one child. Example: `/a/b` is a project and `/a/b/c` is also a project. `b` stays as a separate node with `c` as its child.

## Home Page Rendering

### ProjectList.vue

The component splits projects into two groups:

- `namedProjects`: projects where `project.name !== null`, sorted by `mtime` desc
- `treeRoots`: result of `buildProjectTree()` on projects where `project.name === null`

Template structure:
```
Section "Named projects" (if any)
  wa-card for each named project (same card content as today)

Section "Other projects" (if any)
  ProjectTreeNode for each root
```

### ProjectTreeNode.vue (new, recursive)

Two rendering modes based on node type:

**Folder node** (no project): chevron icon + segment label, clickable to toggle open/closed.

**Project node** (has project): `wa-card` with the same content as today (ProjectBadge, stats, sparkline, edit button). If the project node also has children, they render below it.

### Indentation

Handled purely by CSS via DOM nesting. Each `.tree-children` container has a fixed `padding-left`. No depth calculation needed since components are recursively nested.

### Open/closed state

All nodes open by default. Toggle via local `ref(true)` in each component instance. No persistence.

## Sidebar Project Selector

The `wa-select` in `ProjectView.vue` uses the same two-section structure:

```
All Projects
───────────
[Named projects by mtime desc]
───────────
[Tree flattened with indentation]
```

Since `wa-select` requires a flat list of `wa-option`, the tree is **pre-flattened** into a linear array. Each item has:
- `isFolder`: boolean
- `segment`: display label
- `project`: project object (null for folders)
- `indent`: CSS padding-left value (computed from depth, since no DOM nesting here)

Folder items are rendered as `disabled` `wa-option` elements (non-selectable labels). Project items are normal selectable options with `ProjectBadge`.

The same `flattenProjectTree(roots)` utility function produces this flat list.

## New Files

| File | Purpose |
|------|---------|
| `frontend/src/utils/projectTree.js` | `buildProjectTree(projects)` and `flattenProjectTree(roots)` |
| `frontend/src/components/ProjectTreeNode.vue` | Recursive tree node component |

## Modified Files

| File | Change |
|------|--------|
| `frontend/src/components/ProjectList.vue` | Split into named (flat) + unnamed (tree) sections |
| `frontend/src/views/ProjectView.vue` | Adapt `wa-select` with named + flattened tree |

## Unchanged

- `ProjectBadge.vue`, `ActivitySparkline.vue`, `ProjectEditDialog.vue`, `HomeView.vue`
- Backend, API, models, store
