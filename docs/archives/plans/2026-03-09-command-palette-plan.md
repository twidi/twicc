# Command Palette Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a command palette (Ctrl+K / Cmd+K) providing quick keyboard-driven access to ~33 existing UI actions, with fuzzy search, categories, and nested sub-selection.

**Architecture:** A singleton composable (`useCommandRegistry`) manages command registration and palette state. A single `CommandPalette.vue` component (mounted in `App.vue`) renders the searchable, keyboard-navigable modal. Commands are registered decentrally by the stores and components that own the actions.

**Tech Stack:** Vue 3 Composition API, Web Awesome `wa-dialog`/`wa-icon`, custom fuzzy matching.

**Note:** This project does not use tests or linting (per CLAUDE.md). Steps skip TDD cycles.

---

### Task 1: Fuzzy Match Utility

**Files:**
- Create: `frontend/src/utils/fuzzyMatch.js`

**Goal:** Provide a `fuzzyMatch(query, text)` function that returns match info with score and highlight ranges.

**Step 1: Create `fuzzyMatch.js`**

```js
/**
 * Fuzzy match a query against a text string.
 *
 * @param {string} query - The search query (case-insensitive)
 * @param {string} text - The text to match against
 * @returns {{ match: boolean, score: number, ranges: number[][] }}
 *   - match: whether all query chars were found in order
 *   - score: higher is better (consecutive matches, word-start matches, prefix matches)
 *   - ranges: array of [start, end] pairs (inclusive) for highlighting
 */
export function fuzzyMatch(query, text) {
  if (!query) return { match: true, score: 0, ranges: [] }

  const queryLower = query.toLowerCase()
  const textLower = text.toLowerCase()

  // Try to find the best match using a greedy approach that favors:
  // 1. Exact prefix matches
  // 2. Word-start matches (char after space, hyphen, slash, or uppercase in camelCase)
  // 3. Consecutive character matches

  const queryLen = queryLower.length
  const textLen = textLower.length

  // Quick check: all chars present?
  let checkIdx = 0
  for (let i = 0; i < textLen && checkIdx < queryLen; i++) {
    if (textLower[i] === queryLower[checkIdx]) checkIdx++
  }
  if (checkIdx < queryLen) return { match: false, score: 0, ranges: [] }

  // Find match positions with scoring
  const positions = []
  let score = 0
  let qi = 0
  let lastMatchIdx = -2 // track consecutive

  // Bonus: exact prefix
  if (textLower.startsWith(queryLower)) {
    score += queryLen * 10
    for (let i = 0; i < queryLen; i++) positions.push(i)
    // Build ranges and return early with high score
    return { match: true, score, ranges: buildRanges(positions) }
  }

  // Greedy match favoring word starts
  for (let ti = 0; ti < textLen && qi < queryLen; ti++) {
    if (textLower[ti] === queryLower[qi]) {
      positions.push(ti)

      // Consecutive bonus
      if (ti === lastMatchIdx + 1) {
        score += 5
      }

      // Word-start bonus
      if (ti === 0 || isWordBoundary(text, ti)) {
        score += 8
      }

      // Earlier position bonus (prefer matches near the start)
      score += Math.max(0, 3 - Math.floor(ti / 5))

      lastMatchIdx = ti
      qi++
    }
  }

  if (qi < queryLen) return { match: false, score: 0, ranges: [] }

  return { match: true, score, ranges: buildRanges(positions) }
}

function isWordBoundary(text, index) {
  if (index === 0) return true
  const prev = text[index - 1]
  const curr = text[index]
  // After separator
  if (' -_/'.includes(prev)) return true
  // camelCase boundary
  if (prev === prev.toLowerCase() && curr === curr.toUpperCase() && curr !== curr.toLowerCase()) return true
  return false
}

function buildRanges(positions) {
  if (!positions.length) return []
  const ranges = []
  let start = positions[0]
  let end = positions[0]
  for (let i = 1; i < positions.length; i++) {
    if (positions[i] === end + 1) {
      end = positions[i]
    } else {
      ranges.push([start, end])
      start = positions[i]
      end = positions[i]
    }
  }
  ranges.push([start, end])
  return ranges
}
```

**Step 2: Commit**

```bash
git add frontend/src/utils/fuzzyMatch.js
git commit -m "feat: add fuzzy match utility for command palette"
```

---

### Task 2: Command Registry Composable

**Files:**
- Create: `frontend/src/composables/useCommandRegistry.js`

**Goal:** Singleton composable that manages command registration, filtering, and palette open/close state.

**Step 1: Create `useCommandRegistry.js`**

```js
import { ref, computed, shallowRef } from 'vue'

/**
 * Command schema:
 * {
 *   id: string,           // unique identifier (e.g. 'session.archive')
 *   label: string,        // display label
 *   icon: string,         // Web Awesome icon name (Font Awesome 6 kebab-case)
 *   category: string,     // grouping key (one of CATEGORIES)
 *   action: () => void,   // executed on selection (for commands without items)
 *   when: () => boolean,  // optional, controls visibility (default: always visible)
 *   items: () => Array<{ id: string, label: string, action: () => void, active?: boolean }>,
 *                         // optional, for nested sub-selection
 * }
 */

export const CATEGORIES = [
  { key: 'navigation', label: 'Navigation' },
  { key: 'session', label: 'Session' },
  { key: 'creation', label: 'Creation' },
  { key: 'display', label: 'Display' },
  { key: 'claude', label: 'Claude Defaults' },
  { key: 'ui', label: 'UI' },
]

// Module-level singleton state
const commandMap = ref(new Map())  // id → command
const isOpen = ref(false)

// Trigger for re-evaluation of available commands
// Components that register context-dependent commands bump this
const contextVersion = ref(0)

const availableCommands = computed(() => {
  // Reference contextVersion to re-evaluate when context changes
  void contextVersion.value
  const result = []
  for (const cmd of commandMap.value.values()) {
    if (!cmd.when || cmd.when()) {
      result.push(cmd)
    }
  }
  return result
})

const commandsByCategory = computed(() => {
  const grouped = new Map()
  for (const cat of CATEGORIES) {
    grouped.set(cat.key, [])
  }
  for (const cmd of availableCommands.value) {
    const list = grouped.get(cmd.category)
    if (list) list.push(cmd)
  }
  // Remove empty categories
  const result = []
  for (const cat of CATEGORIES) {
    const cmds = grouped.get(cat.key)
    if (cmds.length > 0) {
      result.push({ ...cat, commands: cmds })
    }
  }
  return result
})

export function useCommandRegistry() {
  function registerCommand(command) {
    commandMap.value = new Map(commandMap.value).set(command.id, command)
  }

  function registerCommands(commands) {
    const newMap = new Map(commandMap.value)
    for (const cmd of commands) {
      newMap.set(cmd.id, cmd)
    }
    commandMap.value = newMap
  }

  function unregisterCommand(id) {
    const newMap = new Map(commandMap.value)
    newMap.delete(id)
    commandMap.value = newMap
  }

  function unregisterCommands(ids) {
    const newMap = new Map(commandMap.value)
    for (const id of ids) {
      newMap.delete(id)
    }
    commandMap.value = newMap
  }

  function openPalette() {
    contextVersion.value++
    isOpen.value = true
  }

  function closePalette() {
    isOpen.value = false
  }

  function bumpContext() {
    contextVersion.value++
  }

  return {
    registerCommand,
    registerCommands,
    unregisterCommand,
    unregisterCommands,
    openPalette,
    closePalette,
    bumpContext,
    isOpen,
    availableCommands,
    commandsByCategory,
  }
}
```

**Step 2: Commit**

```bash
git add frontend/src/composables/useCommandRegistry.js
git commit -m "feat: add command registry composable for palette"
```

---

### Task 3: CommandPalette Component — Structure, Search & Keyboard Navigation

**Files:**
- Create: `frontend/src/components/CommandPalette.vue`

**Goal:** Full command palette component with search input, categorized list, fuzzy filtering, keyboard navigation, and nested sub-selection mode.

**Reference files to read before implementing:**
- `frontend/src/components/SlashCommandPickerPopup.vue` — keyboard nav pattern, scroll-into-view, active index tracking
- `frontend/src/components/ProjectEditDialog.vue` — `wa-dialog` usage, `@wa-after-show` focus pattern
- `frontend/src/stores/settings.js` — `isMac` computed for Cmd vs Ctrl display

**Step 1: Create `CommandPalette.vue`**

The component should implement:

**Template structure:**
```
<wa-dialog ref="dialogRef" without-header @wa-after-show="onAfterShow" @wa-hide="onHide">
  <div class="command-palette">
    <!-- Search bar -->
    <div class="palette-header">
      <span v-if="parentCommand" class="breadcrumb">
        <wa-icon :name="parentCommand.icon" />
        <span>{{ parentCommand.label }}</span>
        <wa-icon name="chevron-right" />
      </span>
      <input
        ref="searchInputRef"
        type="text"
        v-model="query"
        :placeholder="parentCommand ? 'Filter...' : 'Type a command...'"
        @keydown="handleSearchKeydown"
        autocomplete="off"
        spellcheck="false"
      />
    </div>
    <wa-divider />
    <!-- Command list -->
    <div ref="listRef" class="palette-list" @keydown="handleListKeydown">
      <!-- Category mode (no search, root level) -->
      <template v-if="!query && !parentCommand">
        <template v-for="group in filteredByCategory" :key="group.key">
          <div class="category-label">{{ group.label }}</div>
          <div
            v-for="cmd in group.commands"
            :key="cmd.id"
            class="command-item"
            :class="{ active: cmd.id === activeId }"
            :data-id="cmd.id"
            @click="selectCommand(cmd)"
            @mouseenter="activeId = cmd.id"
          >
            <wa-icon :name="cmd.icon" class="command-icon" />
            <span class="command-label">{{ cmd.label }}</span>
            <span v-if="cmd.items" class="command-chevron"><wa-icon name="chevron-right" /></span>
            <span v-if="isToggle(cmd)" class="command-toggle">
              <wa-icon :name="getToggleState(cmd) ? 'check' : ''" />
            </span>
          </div>
        </template>
      </template>
      <!-- Search results mode -->
      <template v-else-if="!parentCommand">
        <div
          v-for="item in searchResults"
          :key="item.cmd.id"
          class="command-item"
          :class="{ active: item.cmd.id === activeId }"
          :data-id="item.cmd.id"
          @click="selectCommand(item.cmd)"
          @mouseenter="activeId = item.cmd.id"
        >
          <wa-icon :name="item.cmd.icon" class="command-icon" />
          <span class="command-label" v-html="item.highlighted" />
          <span v-if="item.cmd.items" class="command-chevron"><wa-icon name="chevron-right" /></span>
          <span v-if="isToggle(item.cmd)" class="command-toggle">
            <wa-icon :name="getToggleState(item.cmd) ? 'check' : ''" />
          </span>
        </div>
      </template>
      <!-- Nested sub-selection mode -->
      <template v-else>
        <div
          v-for="item in nestedItems"
          :key="item.id"
          class="command-item"
          :class="{ active: item.id === activeId }"
          :data-id="item.id"
          @click="selectNestedItem(item)"
          @mouseenter="activeId = item.id"
        >
          <wa-icon v-if="item.active" name="check" class="command-icon active-check" />
          <span v-else class="command-icon-spacer" />
          <span class="command-label" v-html="item.highlighted || item.label" />
        </div>
      </template>
      <!-- Empty state -->
      <div v-if="flatList.length === 0" class="palette-empty">No matching commands</div>
    </div>
  </div>
</wa-dialog>
```

**Script setup logic:**

Key reactive state:
- `query` (ref string) — search input
- `activeId` (ref string) — currently highlighted command/item id
- `parentCommand` (ref or shallowRef) — the command we drilled into for sub-selection, `null` at root
- `dialogRef`, `searchInputRef`, `listRef` — template refs

Key computeds:
- `filteredByCategory` — from `commandsByCategory`, used when no query and no parent
- `searchResults` — fuzzy-filtered and scored from `availableCommands`, each entry has `{ cmd, score, highlighted }` where `highlighted` is the label with `<mark>` tags around matched chars based on `ranges`
- `nestedItems` — from `parentCommand.items()`, optionally fuzzy-filtered by `query`, each item gets `highlighted` if query is active
- `flatList` — a flat array of all currently visible items (commands or nested items), used for keyboard navigation index computation

Key methods:
- `selectCommand(cmd)` — if `cmd.items`, enter nested mode (set `parentCommand = cmd`, clear `query`, reset `activeId`). Otherwise, execute `cmd.action()` and close palette.
- `selectNestedItem(item)` — execute `item.action()` and close palette.
- `handleSearchKeydown(e)` — `Escape` (if parentCommand → go back to root; else close), `ArrowDown` (move to next), `ArrowUp` (move to prev), `Enter` (select active), `Home`/`End`/`PageUp`/`PageDown`.
- `onAfterShow()` — focus the search input.
- `onHide(e)` — reset state (query, parentCommand, activeId).
- `open()` / `close()` — exposed methods.
- `scrollActiveIntoView()` — call after activeId changes, using `listRef.value.querySelector('[data-id="..."]')?.scrollIntoView({ block: 'nearest' })`.

Helper functions:
- `highlightMatches(text, ranges)` — returns HTML string with `<mark>` tags.
- `isToggle(cmd)` — returns `true` if the command id contains `.toggle` or similar convention.
- `getToggleState(cmd)` — calls a getter function on the command to get current on/off state.

**Toggle state approach:** Instead of `isToggle()`/`getToggleState()`, extend the command schema with an optional `toggled: () => boolean` function. If present, the palette shows the toggle indicator. This is cleaner than naming conventions.

**Expose:**
```js
defineExpose({ open, close })
```

**Step 2: Style the component**

CSS scoped to the component. Key design tokens (use Web Awesome CSS custom properties):

```css
wa-dialog::part(panel) {
  --width: min(560px, calc(100vw - 2rem));
  border-radius: var(--wa-border-radius-l);
  overflow: hidden;
  margin-top: 15vh; /* Upper third positioning */
}
wa-dialog::part(body) {
  padding: 0;
}

.command-palette { ... }

.palette-header {
  display: flex;
  align-items: center;
  padding: var(--wa-space-s) var(--wa-space-m);
  gap: var(--wa-space-s);
}
.palette-header input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--wa-color-text-normal);
  font-size: var(--wa-font-size-m);
  font-family: inherit;
}
.breadcrumb {
  display: flex;
  align-items: center;
  gap: var(--wa-space-2xs);
  color: var(--wa-color-text-muted);
  font-size: var(--wa-font-size-s);
  white-space: nowrap;
}

.palette-list {
  max-height: min(400px, 60vh);
  overflow-y: auto;
  padding: var(--wa-space-xs) 0;
}

.category-label {
  padding: var(--wa-space-xs) var(--wa-space-m);
  font-size: var(--wa-font-size-xs);
  color: var(--wa-color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  /* Not first category gets extra top margin */
}
.category-label:not(:first-child) {
  margin-top: var(--wa-space-xs);
}

.command-item {
  display: flex;
  align-items: center;
  padding: var(--wa-space-xs) var(--wa-space-m);
  cursor: pointer;
  gap: var(--wa-space-s);
  border-radius: var(--wa-border-radius-s);
  margin: 0 var(--wa-space-xs);
}
.command-item.active {
  background: var(--wa-color-surface-alt);
}
.command-icon {
  width: 1.2em;
  text-align: center;
  color: var(--wa-color-text-muted);
  flex-shrink: 0;
}
.command-icon-spacer {
  width: 1.2em;
  flex-shrink: 0;
}
.command-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.command-label :deep(mark) {
  background: transparent;
  color: var(--wa-color-primary-text);
  font-weight: 600;
}
.command-chevron, .command-toggle {
  color: var(--wa-color-text-muted);
  flex-shrink: 0;
}
.active-check {
  color: var(--wa-color-success-text);
}

.palette-empty {
  padding: var(--wa-space-l) var(--wa-space-m);
  text-align: center;
  color: var(--wa-color-text-muted);
}
```

**Step 3: Commit**

```bash
git add frontend/src/components/CommandPalette.vue
git commit -m "feat: add CommandPalette component with search, keyboard nav, nested mode"
```

---

### Task 4: Mount CommandPalette in App.vue + Global Shortcut

**Files:**
- Modify: `frontend/src/App.vue`

**Reference:** Read `frontend/src/App.vue` for current structure.

**Step 1: Add CommandPalette to App.vue**

Add the import and component:
```js
import CommandPalette from './components/CommandPalette.vue'
import { useCommandRegistry } from './composables/useCommandRegistry'
```

Add the template ref and global keydown handler:
```js
const commandPaletteRef = ref(null)
const { openPalette } = useCommandRegistry()

onMounted(() => {
  document.addEventListener('keydown', handleGlobalKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleGlobalKeydown)
})

function handleGlobalKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault()
    e.stopPropagation()
    commandPaletteRef.value?.open()
  }
}
```

Add to template (as a sibling of `<div class="app-container">`):
```html
<CommandPalette ref="commandPaletteRef" />
```

**Step 2: Commit**

```bash
git add frontend/src/App.vue
git commit -m "feat: mount command palette in App.vue with Ctrl+K shortcut"
```

---

### Task 5: Register Static Commands — Navigation, Creation, Display, Claude Defaults

**Files:**
- Create: `frontend/src/commands/staticCommands.js`

**Goal:** Register all commands that don't depend on component lifecycle (always available or dependent only on store/route state). This file is imported once at app startup.

**Reference files to read before implementing:**
- `frontend/src/stores/settings.js` — all setting getters, setters, and enum constants (`DISPLAY_MODE`, `THEME_MODE`, `PERMISSION_MODE`, `MODEL`, `EFFORT`)
- `frontend/src/stores/data.js` — `getProjects`, `getProjectDisplayName`, `getAllSessions`, `createDraftSession`
- `frontend/src/router.js` — route names and params

**Step 1: Create `staticCommands.js`**

This module exports an `initStaticCommands()` function called once from `App.vue` after stores and router are available.

```js
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'

export function initStaticCommands(router) {
  const { registerCommands } = useCommandRegistry()
  const store = useDataStore()
  const settings = useSettingsStore()

  registerCommands([
    // ── Navigation ──
    {
      id: 'nav.home',
      label: 'Go to Home',
      icon: 'house',
      category: 'navigation',
      action: () => router.push({ name: 'home' }),
    },
    {
      id: 'nav.project',
      label: 'Go to Project…',
      icon: 'folder',
      category: 'navigation',
      items: () =>
        store.getProjects
          .filter((p) => !p.archived)
          .map((p) => ({
            id: p.id,
            label: store.getProjectDisplayName(p.id),
            action: () => router.push({ name: 'project', params: { projectId: p.id } }),
          })),
    },
    {
      id: 'nav.session',
      label: 'Go to Session…',
      icon: 'message',
      category: 'navigation',
      items: () =>
        store.getAllSessions
          .filter((s) => !s.draft)
          .slice(0, 100) // Limit for performance
          .map((s) => ({
            id: s.id,
            label: s.title || s.id,
            action: () =>
              router.push({
                name: 'session',
                params: { projectId: s.project_id, sessionId: s.id },
              }),
          })),
    },
    {
      id: 'nav.all-projects',
      label: 'Go to All Projects',
      icon: 'layer-group',
      category: 'navigation',
    // ── Navigation: Tab switching ──
    // (registered with when: () based on route)
    },
    {
      id: 'nav.all-projects',
      label: 'Go to All Projects',
      icon: 'layer-group',
      category: 'navigation',
      action: () => router.push({ name: 'projects-all' }),
    },
    {
      id: 'nav.tab.chat',
      label: 'Switch to Chat Tab',
      icon: 'comment',
      category: 'navigation',
      when: () => !!router.currentRoute.value.params.sessionId,
      action: () => {
        const route = router.currentRoute.value
        const name = route.name?.startsWith('projects-') ? 'projects-session' : 'session'
        router.push({ name, params: { projectId: route.params.projectId, sessionId: route.params.sessionId } })
      },
    },
    {
      id: 'nav.tab.files',
      label: 'Switch to Files Tab',
      icon: 'file-code',
      category: 'navigation',
      when: () => !!router.currentRoute.value.params.sessionId,
      action: () => {
        const route = router.currentRoute.value
        const prefix = route.name?.startsWith('projects-') ? 'projects-' : ''
        router.push({ name: `${prefix}session-files`, params: { projectId: route.params.projectId, sessionId: route.params.sessionId } })
      },
    },
    {
      id: 'nav.tab.git',
      label: 'Switch to Git Tab',
      icon: 'code-branch',
      category: 'navigation',
      when: () => {
        const route = router.currentRoute.value
        if (!route.params.sessionId) return false
        const session = store.getSession(route.params.sessionId)
        return !!session?.git_directory
      },
      action: () => {
        const route = router.currentRoute.value
        const prefix = route.name?.startsWith('projects-') ? 'projects-' : ''
        router.push({ name: `${prefix}session-git`, params: { projectId: route.params.projectId, sessionId: route.params.sessionId } })
      },
    },
    {
      id: 'nav.tab.terminal',
      label: 'Switch to Terminal Tab',
      icon: 'terminal',
      category: 'navigation',
      when: () => !!router.currentRoute.value.params.sessionId,
      action: () => {
        const route = router.currentRoute.value
        const prefix = route.name?.startsWith('projects-') ? 'projects-' : ''
        router.push({ name: `${prefix}session-terminal`, params: { projectId: route.params.projectId, sessionId: route.params.sessionId } })
      },
    },

    // ── Creation ──
    {
      id: 'create.session',
      label: 'New Session',
      icon: 'plus',
      category: 'creation',
      when: () => !!router.currentRoute.value.params.projectId,
      action: () => {
        const route = router.currentRoute.value
        const projectId = route.params.projectId
        const sessionId = store.createDraftSession(projectId)
        router.push({ name: 'session', params: { projectId, sessionId } })
      },
    },
    {
      id: 'create.session-in',
      label: 'New Session in…',
      icon: 'square-plus',
      category: 'creation',
      items: () =>
        store.getProjects
          .filter((p) => !p.archived)
          .map((p) => ({
            id: p.id,
            label: store.getProjectDisplayName(p.id),
            action: () => {
              const sessionId = store.createDraftSession(p.id)
              router.push({ name: 'session', params: { projectId: p.id, sessionId } })
            },
          })),
    },
    {
      id: 'create.project',
      label: 'New Project',
      icon: 'folder-plus',
      category: 'creation',
      // action will be set by ProjectView/HomeView via a callback pattern
      // For now, this command emits an event — see Task 7
      action: () => {
        // Dispatch a custom event that HomeView/ProjectView listen to
        window.dispatchEvent(new CustomEvent('twicc:open-new-project-dialog'))
      },
    },

    // ── Display ──
    {
      id: 'display.theme',
      label: 'Change Theme…',
      icon: 'circle-half-stroke',
      category: 'display',
      items: () => {
        const modes = [
          { id: 'system', label: 'System' },
          { id: 'light', label: 'Light' },
          { id: 'dark', label: 'Dark' },
        ]
        return modes.map((m) => ({
          ...m,
          active: settings.themeMode === m.id,
          action: () => settings.setThemeMode(m.id),
        }))
      },
    },
    {
      id: 'display.mode',
      label: 'Change Display Mode…',
      icon: 'eye',
      category: 'display',
      items: () => {
        const modes = [
          { id: 'conversation', label: 'Conversation' },
          { id: 'simplified', label: 'Simplified' },
          { id: 'normal', label: 'Detailed' },
          { id: 'debug', label: 'Debug' },
        ]
        return modes.map((m) => ({
          ...m,
          active: settings.displayMode === m.id,
          action: () => settings.setDisplayMode(m.id),
        }))
      },
    },
    {
      id: 'display.toggle-costs',
      label: 'Toggle Show Costs',
      icon: 'coins',
      category: 'display',
      toggled: () => settings.areCostsShown,
      action: () => settings.setShowCosts(!settings.areCostsShown),
    },
    {
      id: 'display.toggle-compact',
      label: 'Toggle Compact Session List',
      icon: 'bars',
      category: 'display',
      toggled: () => settings.isCompactSessionList,
      action: () => settings.setCompactSessionList(!settings.isCompactSessionList),
    },
    {
      id: 'display.font-increase',
      label: 'Increase Font Size',
      icon: 'magnifying-glass-plus',
      category: 'display',
      action: () => {
        const newSize = Math.min(32, settings.fontSize + 1)
        settings.setFontSize(newSize)
      },
    },
    {
      id: 'display.font-decrease',
      label: 'Decrease Font Size',
      icon: 'magnifying-glass-minus',
      category: 'display',
      action: () => {
        const newSize = Math.max(12, settings.fontSize - 1)
        settings.setFontSize(newSize)
      },
    },
    {
      id: 'display.toggle-word-wrap',
      label: 'Toggle Editor Word Wrap',
      icon: 'text-width',
      category: 'display',
      toggled: () => settings.isEditorWordWrap,
      action: () => settings.setEditorWordWrap(!settings.isEditorWordWrap),
    },
    {
      id: 'display.toggle-diff-layout',
      label: 'Toggle Side-by-Side Diff',
      icon: 'columns',
      category: 'display',
      toggled: () => settings.isDiffSideBySide,
      action: () => settings.setDiffSideBySide(!settings.isDiffSideBySide),
    },

    // ── Claude Defaults ──
    {
      id: 'claude.model',
      label: 'Change Default Model…',
      icon: 'robot',
      category: 'claude',
      items: () => {
        // Read available models from settings store constants
        // The actual model list should be read from the store or constants
        const models = settings.availableModels || [
          { id: 'opus', label: 'Opus' },
          { id: 'sonnet', label: 'Sonnet' },
        ]
        return models.map((m) => ({
          id: m.id,
          label: m.label || m.id,
          active: settings.defaultModel === m.id,
          action: () => settings.setDefaultModel(m.id),
        }))
      },
    },
    {
      id: 'claude.effort',
      label: 'Change Default Effort…',
      icon: 'gauge',
      category: 'claude',
      items: () => {
        const levels = [
          { id: 'low', label: 'Low' },
          { id: 'medium', label: 'Medium' },
          { id: 'high', label: 'High' },
        ]
        return levels.map((l) => ({
          ...l,
          active: settings.defaultEffort === l.id,
          action: () => settings.setDefaultEffort(l.id),
        }))
      },
    },
    {
      id: 'claude.permission',
      label: 'Change Default Permission Mode…',
      icon: 'shield-halved',
      category: 'claude',
      items: () => {
        const modes = settings.availablePermissionModes || [
          { id: 'default', label: 'Default' },
          { id: 'accept-edits', label: 'Accept Edits' },
          { id: 'plan', label: 'Plan' },
          { id: 'dont-ask', label: "Don't Ask" },
          { id: 'bypass', label: 'Bypass Permissions' },
        ]
        return modes.map((m) => ({
          id: m.id,
          label: m.label || m.id,
          active: settings.defaultPermissionMode === m.id,
          action: () => settings.setDefaultPermissionMode(m.id),
        }))
      },
    },
    {
      id: 'claude.thinking',
      label: 'Change Default Thinking…',
      icon: 'brain',
      category: 'claude',
      items: () => {
        const modes = [
          { id: 'adaptive', label: 'Adaptive' },
          { id: 'disabled', label: 'Disabled' },
        ]
        return modes.map((m) => ({
          ...m,
          active: settings.defaultThinking === m.id,
          action: () => settings.setDefaultThinking(m.id),
        }))
      },
    },

    // ── UI ──
    {
      id: 'ui.settings',
      label: 'Open Settings',
      icon: 'gear',
      category: 'ui',
      action: () => {
        // Programmatic click on the settings trigger button
        document.querySelector('#settings-trigger')?.click()
      },
    },
  ])
}
```

**Step 2: Import and call from `App.vue`**

In `App.vue`, after stores are initialized:
```js
import { initStaticCommands } from './commands/staticCommands'
const router = useRouter()
// Call after onMounted or in setup, after stores are ready
initStaticCommands(router)
```

**Step 3: Commit**

```bash
git add frontend/src/commands/staticCommands.js frontend/src/App.vue
git commit -m "feat: register static commands (navigation, creation, display, claude)"
```

---

### Task 6: Register Contextual Session Commands

**Files:**
- Modify: `frontend/src/views/SessionView.vue`

**Goal:** Register session-specific commands (archive, pin, rename, stop, delete draft, focus input) when a session is active, unregister when deactivated (KeepAlive lifecycle).

**Reference files to read before implementing:**
- `frontend/src/views/SessionView.vue` — current structure, `onActivated`/`onDeactivated` pattern, exposed refs
- `frontend/src/components/SessionHeader.vue` — action methods (archive, pin, rename, stop)
- `frontend/src/composables/useWebSocket.js` — `killProcess` import

**Step 1: Add command registration in `SessionView.vue`**

In the `<script setup>`:
```js
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { killProcess } from '../composables/useWebSocket'

const { registerCommands, unregisterCommands } = useCommandRegistry()

const SESSION_COMMAND_IDS = [
  'session.rename', 'session.archive', 'session.unarchive',
  'session.pin', 'session.unpin', 'session.stop',
  'session.delete-draft', 'session.focus-input',
]

function registerSessionCommands() {
  const sid = props.sessionId  // or from route params
  const session = computed(() => store.getSession(sid))
  const processState = computed(() => store.getProcessState(sid))

  registerCommands([
    {
      id: 'session.rename',
      label: 'Rename Session',
      icon: 'pencil',
      category: 'session',
      when: () => session.value && !session.value.draft,
      action: () => sessionHeaderRef.value?.openRenameDialog(),
    },
    {
      id: 'session.archive',
      label: 'Archive Session',
      icon: 'box-archive',
      category: 'session',
      when: () => session.value && !session.value.draft && !session.value.archived,
      action: () => {
        const s = session.value
        if (!s) return
        const ps = processState.value
        if (ps && !ps.synthetic && ps.state && ps.state !== 'dead') killProcess(sid)
        store.setSessionArchived(s.project_id, sid, true)
      },
    },
    {
      id: 'session.unarchive',
      label: 'Unarchive Session',
      icon: 'box-open',
      category: 'session',
      when: () => session.value?.archived === true,
      action: () => {
        const s = session.value
        if (s) store.setSessionArchived(s.project_id, sid, false)
      },
    },
    {
      id: 'session.pin',
      label: 'Pin Session',
      icon: 'thumbtack',
      category: 'session',
      when: () => session.value && !session.value.pinned,
      action: () => {
        const s = session.value
        if (s) store.setSessionPinned(s.project_id, sid, true)
      },
    },
    {
      id: 'session.unpin',
      label: 'Unpin Session',
      icon: 'thumbtack',  // same icon, different label
      category: 'session',
      when: () => session.value?.pinned === true,
      action: () => {
        const s = session.value
        if (s) store.setSessionPinned(s.project_id, sid, false)
      },
    },
    {
      id: 'session.stop',
      label: 'Stop Process',
      icon: 'stop',
      category: 'session',
      when: () => {
        const ps = processState.value
        return ps && !ps.synthetic && ps.state && ps.state !== 'dead'
      },
      action: () => killProcess(sid),
    },
    {
      id: 'session.delete-draft',
      label: 'Delete Draft',
      icon: 'trash',
      category: 'session',
      when: () => session.value?.draft === true,
      action: () => {
        store.deleteDraftSession(sid)
        // Navigate away — to project or home
        const route = router.currentRoute.value
        if (route.params.projectId) {
          router.push({ name: 'project', params: { projectId: route.params.projectId } })
        } else {
          router.push({ name: 'home' })
        }
      },
    },
    {
      id: 'session.focus-input',
      label: 'Focus Message Input',
      icon: 'keyboard',
      category: 'session',
      action: () => {
        // Switch to chat tab first if not on it, then focus
        // Use a custom event or direct DOM query
        const textarea = document.querySelector('.message-input-area textarea')
        if (textarea) textarea.focus()
      },
    },
  ])
}

// KeepAlive lifecycle
onActivated(() => {
  registerSessionCommands()
})
onDeactivated(() => {
  unregisterCommands(SESSION_COMMAND_IDS)
})
// Also handle initial mount (onActivated runs on first mount too with KeepAlive)
onBeforeUnmount(() => {
  unregisterCommands(SESSION_COMMAND_IDS)
})
```

**Important:** Since `SessionView` uses KeepAlive, `onActivated`/`onDeactivated` will fire on tab switches. The session commands must be re-registered with the correct `sessionId` each time the session view becomes active.

**Step 2: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "feat: register contextual session commands in palette"
```

---

### Task 7: Register Contextual ProjectView Commands

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`
- Modify: `frontend/src/views/HomeView.vue` (for new project dialog event)

**Goal:** Register sidebar toggle, session search focus, and new project dialog commands.

**Reference files to read:**
- `frontend/src/views/ProjectView.vue` — sidebar toggle mechanism, `searchInputRef`
- `frontend/src/views/HomeView.vue` — project edit dialog trigger

**Step 1: Register commands in `ProjectView.vue`**

```js
import { useCommandRegistry } from '../composables/useCommandRegistry'

const { registerCommands, unregisterCommands } = useCommandRegistry()

const PROJECT_VIEW_COMMAND_IDS = ['ui.toggle-sidebar', 'ui.focus-search']

onMounted(() => {
  registerCommands([
    {
      id: 'ui.toggle-sidebar',
      label: 'Toggle Sidebar',
      icon: 'sidebar',  // or 'table-columns'
      category: 'ui',
      action: () => {
        const checkbox = document.getElementById('sidebar-toggle-state')
        if (checkbox) {
          checkbox.checked = !checkbox.checked
          checkbox.dispatchEvent(new Event('change'))
        }
      },
    },
    {
      id: 'ui.focus-search',
      label: 'Focus Session Search',
      icon: 'magnifying-glass',
      category: 'ui',
      action: () => {
        searchInputRef.value?.focus()
      },
    },
  ])
})

onBeforeUnmount(() => {
  unregisterCommands(PROJECT_VIEW_COMMAND_IDS)
})
```

**Step 2: Add new project dialog event listener in `HomeView.vue` and `ProjectView.vue`**

Both views have `ProjectEditDialog`. Listen for the custom event dispatched by the static command:

```js
onMounted(() => {
  window.addEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
})
onBeforeUnmount(() => {
  window.removeEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
})

function openNewProjectDialog() {
  projectEditDialogRef.value?.open({ mode: 'create' })
}
```

**Step 3: Commit**

```bash
git add frontend/src/views/ProjectView.vue frontend/src/views/HomeView.vue
git commit -m "feat: register sidebar and search commands, wire new project dialog"
```

---

### Task 8: Polish & Final Adjustments

**Files:**
- Modify: `frontend/src/components/CommandPalette.vue` (adjustments after integration testing)

**Goal:** Visual polish and edge-case handling.

**Step 1: Handle edge cases**

- Ensure `Ctrl+K` is intercepted even when focus is in Monaco editor or xterm.js terminal (these may capture keyboard events). May need `capture: true` on the listener.
- Ensure the palette dialog does not trap focus permanently — `wa-dialog` handles this natively but verify behavior.
- When no session is open and only a few commands are available, the list should not look empty — the palette should feel useful even on the home page.
- Verify that `wa-dialog` `open` state resets properly when rapidly opened/closed.

**Step 2: Verify all actions work end-to-end**

Manually test each command category:
- Navigation commands navigate correctly and the palette closes
- Session actions (archive, pin, rename) work and reflect state changes
- Toggle commands update their indicator immediately on next open
- Sub-selection mode shows correct active item and closes after selection
- Keyboard navigation (↑↓ Enter Escape Home End PageUp PageDown) all work
- Fuzzy search filters correctly and highlights matches
- Breadcrumb appears in nested mode, Escape returns to root
- Empty state message shows when no results match

**Step 3: Commit**

```bash
git add frontend/src/components/CommandPalette.vue
git commit -m "fix: command palette edge cases and polish"
```

---

## Summary

| Task | Files | Description |
|------|-------|-------------|
| 1 | `utils/fuzzyMatch.js` | Fuzzy match utility |
| 2 | `composables/useCommandRegistry.js` | Command registry composable |
| 3 | `components/CommandPalette.vue` | Full palette component |
| 4 | `App.vue` | Mount palette + Ctrl+K shortcut |
| 5 | `commands/staticCommands.js`, `App.vue` | Static commands (nav, create, display, claude) |
| 6 | `views/SessionView.vue` | Contextual session commands |
| 7 | `views/ProjectView.vue`, `views/HomeView.vue` | Contextual UI commands + new project event |
| 8 | `components/CommandPalette.vue` | Polish & edge cases |
